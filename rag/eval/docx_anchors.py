"""Locate each excerpt in its Word file as a set of words with addresses (docs/rules/evaluation.md).

The rules are those of anchors.py, read on the file's text instead of PDF words:

1. The file is read by rag/ingest/docx/reader.py, so addresses agree with the parser's. A word is a
   run of non-space characters of one addressed text (a paragraph, a numbering label, a paragraph
   in a table cell or text box), recorded as (address, start, end) in that text. Table-of-contents
   text is not read.
2. A piece (an excerpt split at " | " and line breaks) matches a run of words read in order; it may
   continue from a paragraph's numbering label into that paragraph, and from one paragraph into the
   next of the same container (the same table cell, text box or part), never into another cell. Pieces found nowhere as one run are split into the longest runs, as for PDFs.
3. When pieces occur more than once, the occurrences are chosen so that the chosen words are as
   close together in reading order as possible, never reusing a word.
4. `locate_hint` (the header text of the excerpt's column) keeps only occurrences in cells of that
   table column. The hint must occur exactly once among the tables that hold every piece of the
   excerpt.
"""

from __future__ import annotations

import itertools
import math
import re
from dataclasses import dataclass
from rag.eval.anchors import MAX_COMBINATIONS, ExcerptAnchor, PieceResult, Status, _match_piece, _Stream, \
    normalize, split_pieces
from rag.eval.dataset import Excerpt
from rag.ingest.docx.reader import DocxFile, all_units

CELL = re.compile(r"^(.*)/r(\d+)c(\d+)/")


@dataclass(frozen=True)
class DocxWord:
    address: str
    start: int                 # character offsets in the addressed text
    end: int
    text: str
    norm: str
    unit: int                  # index of the addressed text in reading order


def file_words(file: DocxFile) -> list[DocxWord]:
    words: list[DocxWord] = []
    for index, unit in enumerate(all_units(file)):
        for match in re.finditer(r"\S+", unit.text):
            norm = normalize(match.group())
            if norm:
                words.append(DocxWord(unit.address, match.start(), match.end(), match.group(), norm, index))
    return words


class _WordStream(_Stream):
    def adjacent(self, position: int, length: int) -> bool:
        indices = self.span(position, length)
        for i, j in zip(indices, indices[1:]):
            a, b = self.words[i], self.words[j]
            if a.address == b.address or a.address == b.address + "#label":
                continue
            if _container(a.address) != _container(b.address) or b.unit != a.unit + 1:
                return False
        return True


def _container(address: str) -> str:
    return address.split("#", 1)[0].rsplit("/", 1)[0]


def _cost(points: list[int]) -> float:
    return float(max(points) - min(points)) if points else 0.0


def _choose(stream: _WordStream, options: list[list[tuple[int, ...]]]) -> list[tuple[int, ...]]:
    """One occurrence per piece: no word used twice, chosen words as close in reading order as possible."""
    place = [[stream.words[o[0]].unit for o in opts] for opts in options]
    if math.prod(len(o) for o in options) <= MAX_COMBINATIONS:
        best, best_cost = None, math.inf
        for combo in itertools.product(*(range(len(o)) for o in options)):
            chosen = [options[i][j] for i, j in enumerate(combo)]
            used = [w for words in chosen for w in words]
            if len(used) != len(set(used)):
                continue
            cost = _cost([place[i][j] for i, j in enumerate(combo)])
            if cost < best_cost:
                best, best_cost = chosen, cost
        if best is not None:
            return best
    order = sorted(range(len(options)), key=lambda i: len(options[i]))
    chosen: dict[int, tuple[int, ...]] = {}
    used: set[int] = set()
    for i in order:
        placed = [stream.words[chosen[k][0]].unit for k in chosen]
        free = [j for j, o in enumerate(options[i]) if not used & set(o)] or list(range(len(options[i])))
        j = min(free, key=lambda j: min((abs(place[i][j] - p) for p in placed), default=0))
        chosen[i] = options[i][j]
        used.update(options[i][j])
    return [chosen[i] for i in range(len(options))]


def _table(address: str) -> str | None:
    match = CELL.match(address)
    return match.group(1) if match else None


def _column(address: str) -> tuple[str, str] | None:
    match = CELL.match(address)
    return (match.group(1), match.group(3)) if match else None


def locate_excerpt(excerpt: Excerpt, words: list[DocxWord]) -> ExcerptAnchor:
    stream = _WordStream(words)   # type: ignore[arg-type]
    owners = [(text, sub) for text in split_pieces(excerpt.text) for sub in _match_piece(stream, text)]
    pieces = [sub for _, sub in owners]
    if excerpt.locate_hint:
        hint = _match_piece(stream, excerpt.locate_hint)
        tables = set.intersection(*[{_table(words[o[0]].address) for o in occ} for _, occ, _, _ in pieces])             if pieces else set()
        places = [o for o in hint[0][1] if _table(words[o[0]].address) in tables] if len(hint) == 1 else []
        column = _column(words[places[0][0]].address) if len(places) == 1 else None
        if column is None:
            pieces.append((f"定位提示：{excerpt.locate_hint}", [], ("定位提示须在表格单元格中恰好出现一次",), None))
        else:
            pieces = [(text, [o for o in occ if _column(words[o[0]].address) == column] or occ
                       if len(occ) > 1 else occ, miss, distance) for text, occ, miss, distance in pieces]
    found = [n for n, (_, occ, _, _) in enumerate(pieces) if occ]
    chosen = dict(zip(found, _choose(stream, [pieces[n][1] for n in found]))) if found else {}
    results = tuple(PieceResult(text, len(occ), tuple(words[i] for i in chosen.get(n, ())), tuple(miss), distance)
                    for n, (text, occ, miss, distance) in enumerate(pieces))   # type: ignore[arg-type]
    indices = sorted({i for words_ in chosen.values() for i in words_})
    status: Status = ("missing" if any(r.missing or not r.candidates for r in results)
                      else "ambiguous" if any(r.candidates > 1 for r in results) else "located")
    groups: dict[str, list[int]] = {}
    for n, (text, _) in enumerate(owners):
        groups.setdefault(text, []).extend(chosen.get(n, ()))
    return ExcerptAnchor(excerpt, status, results, tuple(words[i] for i in indices),   # type: ignore[arg-type]
                         tuple((text, tuple(words[i] for i in sorted(set(ids)))) for text, ids in groups.items()),
                         tuple(None for _ in groups))


def locate_all(excerpts: list[Excerpt], files: dict[str, DocxFile]) -> list[ExcerptAnchor]:
    words = {doc: file_words(file) for doc, file in files.items()}
    return [locate_excerpt(x, words[x.document_id]) for x in excerpts]


def location_labels(words, spans: list[tuple[str, str]]) -> tuple[str, ...]:
    """The parser's location labels ("第 N 块", "表 N", "页眉" …) of the located words, in order.
    `spans`: (address, label) of every element the parser produced."""
    labels: list[str] = []
    for word in words:
        address = word.address.split("#", 1)[0]
        best = max((s for s in spans if address == s[0] or address.startswith(s[0] + "/")),
                   key=lambda s: len(s[0]), default=None)
        if best is not None and best[1] not in labels:
            labels.append(best[1])
    return tuple(labels)


def parser_spans(source) -> list[tuple[str, str]]:
    from rag.ingest.parsers.docx_native import DocxNativeParser
    from rag.models import PrimaryList
    primary = DocxNativeParser().parse(source).primary
    spans: list[tuple[str, str]] = []
    for element in primary.elements:
        sources = [s for item in element.items for s in item.sources] if isinstance(element, PrimaryList) \
            else list(element.sources)
        spans += [(s.address, s.label) for s in sources]
    return spans
