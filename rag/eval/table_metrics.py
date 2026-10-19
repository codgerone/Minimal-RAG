"""Table evidence metrics (V3.13). Definitions: docs/rules/evaluation.md.

A table excerpt is an excerpt whose every word centre lies in a table's box given by the parser;
a group holding a table excerpt is a table evidence group, and a question with one is a table
question. Metrics read the full ranking of the question's scope:

- metric 1: per table group, the rank of the best chunk (before mapping chunks to return units)
  that covers any word of the group's table excerpts;
- metric 2: per table group, the fewest leading return units covering the table excerpts of some
  scheme; per question, the largest over its table groups (None when some group is never covered);
- metric 3: per question, the tokens of the first "metric 2" return units;
- fewest units: per question, choosing one acceptable set per group, the fewest return units the
  union spreads over (independent of ranking).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Callable

from rag.eval.dataset import Case, Excerpt
from rag.eval.judge import EDGE, CaseEvidence, GroupEvidence
from rag.eval.metrics import completion_ranks

Box = tuple[float, float, float, float]
CURVE_K = 20
MAX_COMBINATIONS = 20000


def is_table_excerpt(excerpt: Excerpt, tables: list[tuple[int, Box]]) -> bool:
    words = [word for _, piece_words in excerpt.anchor or () for word in piece_words]

    def inside(page: int, box: Box) -> bool:
        x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        return any(p == page and b[0] - EDGE <= x <= b[2] + EDGE and b[1] - EDGE <= y <= b[3] + EDGE
                   for p, b in tables)
    return bool(words) and all(inside(page, box) for page, box in words)


@dataclass(frozen=True)
class TableGroup:
    group_id: str
    excerpt_ids: tuple[str, ...]                         # the group's table excerpts
    any_word_chunks: frozenset[str]                      # chunks covering some word of them
    table_sets: tuple[frozenset[str], ...]               # acceptable sets of the table excerpts only


def table_groups(case: Case, evidence: CaseEvidence, table_excerpts: set[str]) -> list[TableGroup]:
    """`table_excerpts`: IDs of this case's table excerpts."""
    found = []
    for group in case.groups:
        own = [x for scheme in group.schemes for x in scheme.excerpt_ids if x in table_excerpts]
        if not own:
            continue
        sets: list[frozenset[str]] = []
        for scheme in group.schemes:
            parts = [evidence.excerpts[x].acceptable_sets for x in scheme.excerpt_ids if x in table_excerpts]
            if parts and all(parts):
                sets += [frozenset().union(*combo) for combo in itertools.product(*parts)]
        chunks = frozenset(cid for x in own for s in evidence.excerpts[x].acceptable_sets for cid in s)
        found.append(TableGroup(group.group_id, tuple(dict.fromkeys(own)), chunks, tuple(dict.fromkeys(sets))))
    return found


def chunk_rank(ranking: list[str], chunks: frozenset[str]) -> int | None:
    return next((rank for rank, cid in enumerate(ranking, start=1) if cid in chunks), None)


def required_units(units: list[tuple[str, ...]], groups: list[TableGroup]) -> tuple[dict[str, int], int | None]:
    """Per table group the units needed; the question's value is the largest (None if one is never covered)."""
    pseudo = [GroupEvidence(g.group_id, "coordinate", g.table_sets) for g in groups]
    per_group = completion_ranks(units, pseudo)
    if len(per_group) < len(groups):
        return per_group, None
    return per_group, max(per_group.values()) if per_group else None


def fewest_units(groups: list[GroupEvidence], owner: Callable[[str], str]) -> int | None:
    """Fewest return units over all choices of one acceptable set per group."""
    options = [g.acceptable_sets for g in groups]
    if not options or not all(options):
        return None
    best: int | None = None
    for count, combo in enumerate(itertools.product(*options)):
        if count >= MAX_COMBINATIONS:
            break
        units = {owner(cid) for chunk_set in combo for cid in chunk_set}
        best = len(units) if best is None else min(best, len(units))
    return best
