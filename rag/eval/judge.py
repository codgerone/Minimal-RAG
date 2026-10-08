"""Decide which chunks of the current index count as evidence for each evidence group.

Each excerpt carries its confirmed position on the PDF (words with boxes, per piece); each chunk
carries the regions of the PDF its content comes from (rag/ingest/regions.py). Rules in
docs/rules/evaluation.md:

1. A word is covered by a chunk when the centre of its box lies in one of the chunk's fine regions.
2. A word inside no fine region but inside a chunk's coarse region falls back to text: the chunk
   covers it when the words of the word's piece occur in the chunk text in their order (letters and
   digits compared; table text puts field names between a row label and its values).
3. A set of chunks is acceptable for the excerpt when it covers every word; only minimal sets kept.
4. A table-header excerpt is also covered by each other chunk of the same split table whose text
   contains all its header pieces.
5. A group is covered by any of its schemes; a scheme needs every one of its excerpts.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Literal

from rag.eval.anchors import normalize
from rag.eval.dataset import Case, Excerpt

MAX_GROUP_SETS = 20
EDGE = 0.5          # points of tolerance when testing whether a word centre lies in a region

Mode = Literal["coordinate", "fallback", "unmapped"]
Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class JudgeChunk:
    """What judging needs to know about one chunk of the index."""
    chunk_id: str
    document_id: str
    text: str
    parent_unit_id: str | None
    fine: tuple[tuple[int, Box], ...]
    coarse: tuple[tuple[int, Box | None], ...]     # None: anywhere on the page


@dataclass(frozen=True)
class ExcerptEvidence:
    excerpt_id: str
    mode: Mode
    acceptable_sets: tuple[frozenset[str], ...]    # chunk IDs in the current index
    problem: str = ""                               # why it is unmapped


@dataclass(frozen=True)
class SchemeEvidence:
    scheme_id: str
    acceptable_sets: tuple[frozenset[str], ...]    # empty when an excerpt of the scheme is unmapped


@dataclass(frozen=True)
class GroupEvidence:
    group_id: str
    mode: Mode            # fallback: some excerpt needed the text fallback; unmapped: no scheme usable
    acceptable_sets: tuple[frozenset[str], ...]    # union over schemes
    schemes: tuple[SchemeEvidence, ...] = ()


@dataclass(frozen=True)
class CaseEvidence:
    excerpts: dict[str, ExcerptEvidence]
    groups: list[GroupEvidence]


def _inside(point: tuple[int, float, float], page: int, box: Box | None) -> bool:
    p, x, y = point
    return p == page and (box is None or (box[0] - EDGE <= x <= box[2] + EDGE
                                          and box[1] - EDGE <= y <= box[3] + EDGE))


def _minimal(sets: list[frozenset[str]]) -> tuple[frozenset[str], ...]:
    kept: list[frozenset[str]] = []
    for candidate in sorted(dict.fromkeys(sets), key=lambda s: (len(s), sorted(s))):
        if not any(existing <= candidate for existing in kept):
            kept.append(candidate)
        if len(kept) >= MAX_GROUP_SETS:
            break
    return tuple(kept)


def _hitting_sets(requirements: list[frozenset[str]]) -> tuple[frozenset[str], ...]:
    """Minimal chunk sets that contain at least one chunk of every requirement."""
    needs = list(_minimal(requirements)) if requirements else []
    found: list[frozenset[str]] = []

    def extend(chosen: frozenset[str]) -> None:
        if len(found) >= 10 * MAX_GROUP_SETS:
            return
        open_need = next((need for need in needs if not need & chosen), None)
        if open_need is None:
            found.append(chosen)
            return
        for chunk_id in sorted(open_need):
            extend(chosen | {chunk_id})

    extend(frozenset())
    return _minimal(found)


def contains_in_order(piece: str, text: str) -> bool:
    haystack, position = normalize(text), 0
    for token in filter(None, (normalize(t) for t in piece.split())):
        position = haystack.find(token, position)
        if position < 0:
            return False
        position += len(token)
    return True


def judge_excerpt(excerpt: Excerpt, chunks: list[JudgeChunk]) -> ExcerptEvidence:
    own = [c for c in chunks if c.document_id == excerpt.document_id]
    requirements: list[frozenset[str]] = []
    problems: list[str] = []
    used_text = False
    for piece, words in excerpt.anchor or ():
        for page, box in words:
            centre = (page, (box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
            fine = frozenset(c.chunk_id for c in own if any(_inside(centre, p, b) for p, b in c.fine))
            if fine:
                requirements.append(fine)
                continue
            coarse = [c for c in own if any(_inside(centre, p, b) for p, b in c.coarse)]
            matched = frozenset(c.chunk_id for c in coarse if contains_in_order(piece, c.text))
            if len(matched) == 1:
                requirements.append(matched)
                used_text = True
            elif not coarse:
                problems.append(f"片段“{piece}”的词不在任何 chunk 的区域内")
            elif not matched:
                problems.append(f"片段“{piece}”没有出现在区域相符的 chunk 正文中")
            else:
                problems.append(f"片段“{piece}”出现在多个区域相符的 chunk 中，需补环境文字")
    if problems or not requirements:
        return ExcerptEvidence(excerpt.excerpt_id, "unmapped", (),
                               "；".join(dict.fromkeys(problems)) or "excerpt 没有坐标")
    sets = list(_hitting_sets(requirements))
    if excerpt.table_header:
        pieces = [normalize(piece) for piece, _ in excerpt.anchor or ()]
        holders = {cid for s in sets for cid in s}
        parents = {c.parent_unit_id for c in own if c.chunk_id in holders and c.parent_unit_id}
        sets += [frozenset({c.chunk_id}) for c in own
                 if c.parent_unit_id in parents and c.chunk_id not in holders
                 and all(p in normalize(c.text) for p in pieces)]
    return ExcerptEvidence(excerpt.excerpt_id, "fallback" if used_text else "coordinate", _minimal(sets))


def judge_case(case: Case, chunks: list[JudgeChunk]) -> CaseEvidence:
    excerpts = {x.excerpt_id: judge_excerpt(x, chunks) for x in case.excerpts}
    groups: list[GroupEvidence] = []
    for group in case.groups:
        schemes = []
        for scheme in group.schemes:
            parts = [excerpts[x].acceptable_sets for x in scheme.excerpt_ids]
            unions = [frozenset().union(*combo) for combo in itertools.product(*parts)] if all(parts) else []
            schemes.append(SchemeEvidence(scheme.scheme_id, _minimal(unions)))
        modes = {excerpts[x].mode for scheme in group.schemes for x in scheme.excerpt_ids}
        union = _minimal([s for scheme in schemes for s in scheme.acceptable_sets])
        mode: Mode = "unmapped" if not union else "fallback" if "fallback" in modes else "coordinate"
        groups.append(GroupEvidence(group.group_id, mode, union, tuple(schemes)))
    return CaseEvidence(excerpts, groups)
