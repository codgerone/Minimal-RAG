"""Locate each excerpt in its Excel file as a set of words with addresses (docs/rules/evaluation.md).

The rules are those of anchors.py, read on the cells of the workbook instead of PDF words:

1. The file is read by rag/ingest/xlsx/reader.py, so cell text and addresses agree with the
   parser's. A word is a run of non-space characters of one cell's display value, recorded as
   (cell address, start, end) in that display value.
2. A piece (an excerpt split at " | " and line breaks) matches a run of words of one cell: the
   whole cell or part of a long text cell. Cells of a row written with " | " are separate pieces.
   Pieces found nowhere as one run are split into the longest runs, as for PDFs.
3. When pieces occur more than once, the occurrences are chosen so that the minimum spanning tree
   over the chosen pieces is shortest, never reusing a word. Distance between two cells of one
   sheet: row difference + column difference × 0.25; cells of different sheets are 1000 apart.
4. `locate_hint` (the header text of the excerpt's column) keeps only occurrences in the hint
   cell's columns. The hint must occur exactly once among the table regions that hold every piece.
"""

from __future__ import annotations

import itertools
import math
import re
from dataclasses import dataclass

from rag.eval.anchors import MAX_COMBINATIONS, ExcerptAnchor, PieceResult, Status, _match_piece, _Stream, \
    normalize, split_pieces
from rag.eval.dataset import Excerpt
from rag.ingest.xlsx.reader import XlsxFile, cell_address, cell_ref
from rag.ingest.xlsx.regions import Region, sheet_regions

SHEET_APART = 1000.0
COLUMN_WEIGHT = 0.25
ADDRESS = re.compile(r"^sheet(\d+)/r(\d+)c(\d+)$")


@dataclass(frozen=True)
class XlsxWord:
    address: str
    start: int                 # character offsets in the cell's display value
    end: int
    text: str
    norm: str
    sheet: int
    row: int
    column: int


def file_words(book: XlsxFile) -> list[XlsxWord]:
    words: list[XlsxWord] = []
    for sheet in book.sheets:
        for cell in sheet.cells:
            address = cell_address(sheet.index, cell.row, cell.column)
            for match in re.finditer(r"\S+", cell.display):
                norm = normalize(match.group())
                if norm:
                    words.append(XlsxWord(address, match.start(), match.end(), match.group(), norm,
                                          sheet.index, cell.row, cell.column))
    return words


class _CellStream(_Stream):
    def adjacent(self, position: int, length: int) -> bool:
        indices = self.span(position, length)
        return all(self.words[i].address == self.words[j].address for i, j in zip(indices, indices[1:]))


def _distance(a: XlsxWord, b: XlsxWord) -> float:
    if a.sheet != b.sheet:
        return SHEET_APART
    return abs(a.row - b.row) + COLUMN_WEIGHT * abs(a.column - b.column)


def _tree(points: list[XlsxWord]) -> float:
    """Total length of the minimum spanning tree over the points (Prim)."""
    if len(points) < 2:
        return 0.0
    inside, total = {0}, 0.0
    best = {i: _distance(points[0], points[i]) for i in range(1, len(points))}
    while best:
        i = min(best, key=best.get)
        total += best.pop(i)
        inside.add(i)
        for j in best:
            best[j] = min(best[j], _distance(points[i], points[j]))
    return total


def _choose(stream: _CellStream, options: list[list[tuple[int, ...]]]) -> list[tuple[int, ...]]:
    """One occurrence per piece: no word used twice, the shortest spanning tree over the pieces."""
    first = [[stream.words[o[0]] for o in opts] for opts in options]
    if math.prod(len(o) for o in options) <= MAX_COMBINATIONS:
        best, best_cost = None, math.inf
        for combo in itertools.product(*(range(len(o)) for o in options)):
            chosen = [options[i][j] for i, j in enumerate(combo)]
            used = [w for words in chosen for w in words]
            if len(used) != len(set(used)):
                continue
            cost = _tree([first[i][j] for i, j in enumerate(combo)])
            if cost < best_cost:
                best, best_cost = chosen, cost
        if best is not None:
            return best
    order = sorted(range(len(options)), key=lambda i: len(options[i]))
    chosen: dict[int, tuple[int, ...]] = {}
    used: set[int] = set()
    for i in order:
        placed = [stream.words[chosen[k][0]] for k in chosen]
        free = [j for j, o in enumerate(options[i]) if not used & set(o)] or list(range(len(options[i])))
        j = min(free, key=lambda j: min((_distance(first[i][j], p) for p in placed), default=0.0))
        chosen[i] = options[i][j]
        used.update(options[i][j])
    return [chosen[i] for i in range(len(options))]


def _tables(book: XlsxFile) -> dict[int, tuple[Region, ...]]:
    return {sheet.index: tuple(r for r in sheet_regions(sheet) if r.kind == "table") for sheet in book.sheets}


def _table_of(word: XlsxWord, tables: dict[int, tuple[Region, ...]]) -> tuple[int, int] | None:
    for n, region in enumerate(tables.get(word.sheet, ())):
        if region.first_row <= word.row <= region.last_row and region.first_column <= word.column <= region.last_column:
            return word.sheet, n
    return None


def _hint_columns(book: XlsxFile, word: XlsxWord) -> tuple[int, int, int]:
    """(sheet, first column, last column) of the hint's cell, a merge's columns included."""
    sheet = next(s for s in book.sheets if s.index == word.sheet)
    merge = next((m for m in sheet.merges if (m.first_row, m.first_column) == (word.row, word.column)), None)
    return word.sheet, word.column, merge.last_column if merge else word.column


def locate_excerpt(excerpt: Excerpt, words: list[XlsxWord], book: XlsxFile,
                   tables: dict[int, tuple[Region, ...]]) -> ExcerptAnchor:
    stream = _CellStream(words)   # type: ignore[arg-type]
    owners = [(text, sub) for text in split_pieces(excerpt.text) for sub in _match_piece(stream, text)]
    pieces = [sub for _, sub in owners]
    if excerpt.locate_hint:
        hint = _match_piece(stream, excerpt.locate_hint)
        common = set.intersection(*[{_table_of(words[o[0]], tables) for o in occ} for _, occ, _, _ in pieces]) \
            if pieces else set()
        common.discard(None)
        places = [o for o in hint[0][1] if _table_of(words[o[0]], tables) in common] if len(hint) == 1 else []
        if len(places) != 1:
            pieces.append((f"定位提示：{excerpt.locate_hint}", [], ("定位提示须在表格单元格中恰好出现一次",), None))
        else:
            sheet, first, last = _hint_columns(book, words[places[0][0]])

            def in_column(o) -> bool:
                w = words[o[0]]
                return w.sheet == sheet and first <= w.column <= last
            pieces = [(text, [o for o in occ if in_column(o)] or occ if len(occ) > 1 else occ, miss, distance)
                      for text, occ, miss, distance in pieces]
    found = [n for n, (_, occ, _, _) in enumerate(pieces) if occ]
    chosen = dict(zip(found, _choose(stream, [pieces[n][1] for n in found]))) if found else {}
    results = tuple(PieceResult(text, len(occ), tuple(words[i] for i in chosen.get(n, ())), tuple(miss), distance)
                    for n, (text, occ, miss, distance) in enumerate(pieces))   # type: ignore[arg-type]
    indices = sorted({i for ids in chosen.values() for i in ids})
    status: Status = ("missing" if any(r.missing or not r.candidates for r in results)
                      else "ambiguous" if any(r.candidates > 1 for r in results) else "located")
    groups: dict[str, list[int]] = {}
    for n, (text, _) in enumerate(owners):
        groups.setdefault(text, []).extend(chosen.get(n, ()))
    located = tuple(words[i] for i in indices)
    return ExcerptAnchor(excerpt, status, results, located,   # type: ignore[arg-type]
                         tuple((text, tuple(words[i] for i in sorted(set(ids)))) for text, ids in groups.items()),
                         tuple(None for _ in groups), location_labels(located, book))


def location_labels(words, book: XlsxFile) -> tuple[str, ...]:
    """The parser's location labels ("工作表!A1:C5", "工作表!B7") of the located words, in order."""
    labels: list[str] = []
    for sheet in book.sheets:
        regions = sheet_regions(sheet)
        for word in words:
            if word.sheet != sheet.index:
                continue
            region = next((r for r in regions if r.first_row <= word.row <= r.last_row
                           and r.first_column <= word.column <= r.last_column), None)
            if region is None:
                continue
            if region.kind == "column":
                label = f"{sheet.name}!{cell_ref(word.row, word.column)}"
            else:
                first, last = cell_ref(region.first_row, region.first_column), cell_ref(region.last_row, region.last_column)
                label = f"{sheet.name}!{first}" + (f":{last}" if first != last else "")
            if label not in labels:
                labels.append(label)
    return tuple(labels)


def locate_all(excerpts: list[Excerpt], books: dict[str, XlsxFile]) -> list[ExcerptAnchor]:
    words = {doc: file_words(book) for doc, book in books.items()}
    tables = {doc: _tables(book) for doc, book in books.items()}
    return [locate_excerpt(x, words[x.document_id], books[x.document_id], tables[x.document_id]) for x in excerpts]
