"""Locate each excerpt on its PDF pages as a set of words with coordinates.

Excerpts are copied from the PDF, but table excerpts are cells joined with " | " and newlines, so
an excerpt is split into pieces at " | " and line breaks and each piece is matched on its own:

1. Text is compared after NFKC, casefolding and dropping everything but letters and digits.
2. PDF words are read in content-stream order, which keeps a table cell's text together.
   A piece matches a run of PDF words that starts and ends on word boundaries and reads as one
   text: each next word is on the same line close by, or wraps to the line below without starting
   left of the text's left edge, so a match never jumps over an empty cell or into another cell.
   A piece found nowhere as one run is split into the longest such runs of its words; a word found
   nowhere is reported missing rather than guessed.
3. When pieces occur more than once, the occurrences are chosen so that the chosen words are as
   close together as possible (smallest spanning tree; pieces on the same line, i.e. one table
   row, count as close, vertical distance between lines counts more than horizontal), never
   reusing the same PDF word twice.
4. When the same value repeats across columns of one row, only the column tells them apart: the
   excerpt's `locate_hint` (its column's header text, found once on the page) keeps only the
   occurrences that overlap that column horizontally. The hint is not part of the evidence.

The result is for human review; once confirmed it is written into the ground truth.
"""

from __future__ import annotations

import hashlib
import itertools
import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pymupdf

from rag.eval.dataset import Excerpt
from rag.jsonio import read_json, write_json

PAGE_GAP = 2000.0          # added to y per page so pieces on other pages count as far away
ROW_WEIGHT = 4.0           # vertical distance counts more between lines
SAME_LINE_WEIGHT = 0.25    # pieces sharing a line (a table row) count as close
MAX_CANDIDATES = 40        # occurrences kept per piece
MAX_COMBINATIONS = 200_000

Status = Literal["located", "ambiguous", "missing"]


def normalize(text: str) -> str:
    return "".join(re.findall(r"\w", unicodedata.normalize("NFKC", text).casefold()))


@dataclass(frozen=True)
class PdfWord:
    page: int
    bbox: tuple[float, float, float, float]
    text: str
    norm: str


def page_words(page: pymupdf.Page, page_number: int) -> list[PdfWord]:
    """Words in content-stream order (keeps each table cell's text together), split on whitespace."""
    words: list[PdfWord] = []
    for block in page.get_text("rawdict", sort=False)["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                current: list[dict] = []
                for char in [*span["chars"], None]:
                    if char is not None and not char["c"].isspace():
                        current.append(char)
                        continue
                    text = "".join(c["c"] for c in current)
                    if normalize(text):
                        boxes = [c["bbox"] for c in current]
                        words.append(PdfWord(page_number, (
                            min(b[0] for b in boxes), min(b[1] for b in boxes),
                            max(b[2] for b in boxes), max(b[3] for b in boxes)), text, normalize(text)))
                    current = []
    return words


@dataclass(frozen=True)
class PieceResult:
    text: str
    candidates: int                 # occurrences found (0 when nothing of it was found)
    words: tuple[PdfWord, ...]      # the chosen occurrence
    missing: tuple[str, ...]        # excerpt tokens that could not be found


@dataclass(frozen=True)
class ExcerptAnchor:
    excerpt: Excerpt
    status: Status
    pieces: tuple[PieceResult, ...]
    words: tuple[PdfWord, ...]


class _Stream:
    def __init__(self, words: list[PdfWord]):
        self.words = words
        self.text = "".join(w.norm for w in words)
        self.starts: list[int] = []
        offset = 0
        for word in words:
            self.starts.append(offset)
            offset += len(word.norm)
        self.start_set = set(self.starts)
        self.end_set = {s + len(w.norm) for s, w in zip(self.starts, words)}
        self.owner = [i for i, w in enumerate(words) for _ in w.norm]

    def find(self, target: str, whole_words: bool = False) -> list[int]:
        """Start positions of target at a word start; matches ending on a word end come first."""
        positions, start = [], self.text.find(target)
        while start >= 0:
            if start in self.start_set:
                positions.append(start)
            start = self.text.find(target, start + 1)
        whole = [p for p in positions if p + len(target) in self.end_set]
        return whole if whole or whole_words else positions

    def adjacent(self, position: int, length: int) -> bool:
        """The words read as one piece of text: each next word is on the same line close by, or wraps
        to the line below without starting clearly left of the text's left edge (centred cell text
        may shift a little; a jump into another cell may not)."""
        indices = self.span(position, length)
        left = self.words[indices[0]].bbox[0]
        for i, j in zip(indices, indices[1:]):
            a, b = self.words[i].bbox, self.words[j].bbox
            height = max(a[3] - a[1], b[3] - b[1])
            if self.words[i].page != self.words[j].page:
                return False
            same_line = min(a[3], b[3]) - max(a[1], b[1]) > 0.5 * height
            if same_line:
                if max(0.0, b[0] - a[2], a[0] - b[2]) > 2.5 * height:
                    return False
            elif not (-0.5 * height <= b[1] - a[3] <= 1.5 * height and b[0] >= left - 4 * height):
                return False
            left = min(left, b[0])
        return True

    def span(self, position: int, length: int) -> tuple[int, ...]:
        return tuple(range(self.owner[position], self.owner[position + length - 1] + 1))


def _match_piece(stream: _Stream, text: str) -> list[tuple[str, list[tuple[int, ...]], tuple[str, ...]]]:
    """Sub-pieces of one piece as (text, occurrences, missing tokens).

    A piece found contiguously is one sub-piece. Otherwise its tokens are grouped greedily into the
    longest runs that occur as touching whole words; each run keeps all its occurrences so that the
    choice between them is made together with the other pieces. A token found nowhere is missing.
    """
    target = normalize(text)
    whole = [p for p in stream.find(target) if stream.adjacent(p, len(target))]
    if whole:
        return [(text, [stream.span(p, len(target)) for p in whole[:MAX_CANDIDATES]], ())]
    tokens = [t for t in text.split() if normalize(t)]
    norms = [normalize(t) for t in tokens]
    result: list[tuple[str, list[tuple[int, ...]], tuple[str, ...]]] = []
    index = 0
    while index < len(norms):
        for end in range(len(norms), index, -1):
            run = "".join(norms[index:end])
            hits = [h for h in stream.find(run, whole_words=True) if stream.adjacent(h, len(run))]
            if hits:
                result.append((" ".join(tokens[index:end]),
                               [stream.span(h, len(run)) for h in hits[:MAX_CANDIDATES]], ()))
                index = end
                break
        else:
            result.append((tokens[index], [], (tokens[index],)))
            index += 1
    return result


@dataclass(frozen=True)
class _Place:
    """Where an occurrence sits: centre, vertical extent and page."""
    x: float
    y: float
    top: float
    bottom: float
    page: int


def _place(stream: _Stream, words: tuple[int, ...]) -> _Place:
    boxes = [stream.words[i].bbox for i in words]
    return _Place(sum((b[0] + b[2]) / 2 for b in boxes) / len(boxes),
                  sum((b[1] + b[3]) / 2 for b in boxes) / len(boxes),
                  min(b[1] for b in boxes), max(b[3] for b in boxes), stream.words[words[0]].page)


def _distance(a: _Place, b: _Place) -> float:
    """Pieces on one line (one table row) are close whatever the columns between them; otherwise
    vertical distance counts ROW_WEIGHT times and another page is far away."""
    if a.page == b.page and min(a.bottom, b.bottom) - max(a.top, b.top) > 0:
        return SAME_LINE_WEIGHT * abs(a.x - b.x)
    return math.hypot(a.x - b.x, ROW_WEIGHT * (a.y - b.y + PAGE_GAP * (a.page - b.page)))


def _tree_length(points: list[_Place]) -> float:
    if len(points) < 2:
        return 0.0
    total = 0.0
    best = {i: _distance(points[0], points[i]) for i in range(1, len(points))}
    while best:
        nxt = min(best, key=best.get)
        total += best.pop(nxt)
        for i in best:
            best[i] = min(best[i], _distance(points[nxt], points[i]))
    return total


def _in_column(stream: _Stream, words: tuple[int, ...], column: tuple[float, float]) -> bool:
    x0 = min(stream.words[i].bbox[0] for i in words)
    x1 = max(stream.words[i].bbox[2] for i in words)
    return x0 < column[1] and column[0] < x1


def _choose(stream: _Stream, options: list[list[tuple[int, ...]]]) -> list[tuple[int, ...]]:
    """One occurrence per piece: no PDF word used twice, chosen words as close together as possible."""
    centers = [[_place(stream, o) for o in opts] for opts in options]
    total = math.prod(len(o) for o in options)
    if total <= MAX_COMBINATIONS:
        best, best_cost = None, math.inf
        for combo in itertools.product(*(range(len(o)) for o in options)):
            chosen = [options[i][j] for i, j in enumerate(combo)]
            used = [w for words in chosen for w in words]
            if len(used) != len(set(used)):
                continue
            cost = _tree_length([centers[i][j] for i, j in enumerate(combo)])
            if cost < best_cost:
                best, best_cost = chosen, cost
        if best is not None:
            return best
    # Too many combinations (or none without reuse): place the least ambiguous pieces first.
    order = sorted(range(len(options)), key=lambda i: len(options[i]))
    chosen: dict[int, tuple[int, ...]] = {}
    used: set[int] = set()
    for i in order:
        placed = [_place(stream, chosen[k]) for k in chosen]
        free = [j for j, o in enumerate(options[i]) if not used & set(o)] or list(range(len(options[i])))
        j = min(free, key=lambda j: min((_distance(centers[i][j], p) for p in placed), default=0.0))
        chosen[i] = options[i][j]
        used.update(options[i][j])
    return [chosen[i] for i in range(len(options))]


def split_pieces(text: str) -> list[str]:
    return [p for p in re.split(r"\s*\|\s*|\n", text) if normalize(p)]


def locate_excerpt(excerpt: Excerpt, words: list[PdfWord]) -> ExcerptAnchor:
    stream = _Stream(words)
    pieces = [sub for p in split_pieces(excerpt.text) for sub in _match_piece(stream, p)]
    if excerpt.locate_hint:
        hint = _match_piece(stream, excerpt.locate_hint)
        if len(hint) != 1 or len(hint[0][1]) != 1:
            pieces.append((f"定位提示：{excerpt.locate_hint}", [], ("定位提示须在页面上恰好出现一次",)))
        else:
            column = (min(stream.words[i].bbox[0] for i in hint[0][1][0]),
                      max(stream.words[i].bbox[2] for i in hint[0][1][0]))
            pieces = [(text, [o for o in occ if _in_column(stream, o, column)] or occ if len(occ) > 1 else occ, miss)
                      for text, occ, miss in pieces]
    found = [n for n, (_, occ, _) in enumerate(pieces) if occ]
    chosen = dict(zip(found, _choose(stream, [pieces[n][1] for n in found]))) if found else {}
    results = tuple(PieceResult(text, len(occ), tuple(words[i] for i in chosen.get(n, ())), tuple(miss))
                    for n, (text, occ, miss) in enumerate(pieces))
    indices = sorted({i for words_ in chosen.values() for i in words_})
    status: Status = ("missing" if any(r.missing or not r.candidates for r in results)
                      else "ambiguous" if any(r.candidates > 1 for r in results) else "located")
    return ExcerptAnchor(excerpt, status, results, tuple(words[i] for i in indices))


def document_words(pdf_path: Path, pages: set[int]) -> dict[int, list[PdfWord]]:
    with pymupdf.open(pdf_path) as pdf:
        return {p: page_words(pdf[p - 1], p) for p in sorted(pages) if 0 < p <= len(pdf)}


def locate_all(excerpts: list[Excerpt], pdf_paths: dict[str, Path]) -> list[ExcerptAnchor]:
    """`pdf_paths` maps document_id → PDF file."""
    needed: dict[str, set[int]] = {}
    for x in excerpts:
        needed.setdefault(x.document_id, set()).update(x.pages)
    words = {doc: document_words(pdf_paths[doc], pages) for doc, pages in needed.items()}
    return [locate_excerpt(x, [w for p in x.pages for w in words[x.document_id].get(p, [])])
            for x in excerpts]


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def write_anchors(folder: Path, anchors: dict[tuple[str, str], ExcerptAnchor]) -> int:
    """Store confirmed locations in the ground-truth files; keyed by (case_id, excerpt_id)."""
    written = 0
    for path in sorted(folder.glob("*.json")):
        if path.name == "manifest.json":
            continue
        data = read_json(path)
        for case in data["cases"]:
            for x in case["excerpts"]:
                item = anchors.get((case["case_id"], x["excerpt_id"]))
                if item is None:
                    continue
                x["anchor"] = {"text_sha256": text_sha256(x["text"]),
                               "words": [[w.page, *(round(v, 2) for v in w.bbox)] for w in item.words]}
                written += 1
        write_json(path, data)
    return written
