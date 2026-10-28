"""Retrieval metrics at K. Definitions: docs/rules/evaluation.md.

Results are return units (a chunk, or a whole table standing for all its chunks); each unit is
given as the chunk IDs whose content it returns.
"""

from __future__ import annotations

from dataclasses import dataclass

from rag.eval.judge import GroupEvidence


@dataclass(frozen=True)
class Ratio:
    numerator: float
    denominator: int

    @property
    def value(self) -> float | None:
        return self.numerator / self.denominator if self.denominator else None


@dataclass(frozen=True)
class QuestionScore:
    returned: int                 # return units
    relevant: int                 # returned units holding a chunk of some acceptable set
    groups_required: int
    groups_covered: int
    cross_document: int           # returned units from documents outside the evidence
    first_relevant_rank: int | None
    group_completion_ranks: dict[str, int]   # group → rank at which it became fully covered

    @property
    def hit(self) -> bool:
        return self.groups_covered > 0

    @property
    def complete(self) -> bool:
        return self.groups_covered == self.groups_required


def completion_ranks(units: list[tuple[str, ...]], evidence: list[GroupEvidence]) -> dict[str, int]:
    """Group → the number of leading units needed to cover it; groups never covered are left out."""
    ranks: dict[str, int] = {}
    for rank, chunk_ids in enumerate(units, start=1):
        for chunk_id in chunk_ids:
            ranks.setdefault(chunk_id, rank)
    completion: dict[str, int] = {}
    for group in evidence:
        reached = [max(ranks[cid] for cid in chunk_set) for chunk_set in group.acceptable_sets
                   if chunk_set and all(cid in ranks for cid in chunk_set)]
        if reached:
            completion[group.group_id] = min(reached)
    return completion


def routed_completion_rank(routes: list[list[tuple[str, ...]]],
                           schemes: list[list[tuple[frozenset[str], ...]]]) -> int | None:
    """Completion rank of one group over several rankings (routes): an excerpt's rank is the best,
    over the routes and over its acceptable chunk sets, of the rank by which the whole set is in that
    route; a scheme's is the worst of its excerpts'; the group's is the best of its schemes'. None:
    never complete. With one route this equals completion_ranks.

    `schemes`: per scheme, per excerpt, the excerpt's acceptable chunk sets."""
    firsts = []
    for units in routes:
        ranks: dict[str, int] = {}
        for rank, chunk_ids in enumerate(units, start=1):
            for chunk_id in chunk_ids:
                ranks.setdefault(chunk_id, rank)
        firsts.append(ranks)

    def excerpt_rank(sets: tuple[frozenset[str], ...]) -> int | None:
        reached = [max(ranks[c] for c in s) for ranks in firsts for s in sets
                   if s and all(c in ranks for c in s)]
        return min(reached) if reached else None

    best = None
    for scheme in schemes:
        parts = [excerpt_rank(sets) for sets in scheme]
        if parts and all(p is not None for p in parts):
            best = max(parts) if best is None else min(best, max(parts))
    return best


def first_ranks(units: list[tuple[str, ...]], evidence: list[GroupEvidence]) -> dict[str, int]:
    """Group → the rank of the first unit holding any chunk of any of its acceptable sets;
    groups with no such unit are left out."""
    first: dict[str, int] = {}
    for group in evidence:
        chunks = {cid for chunk_set in group.acceptable_sets for cid in chunk_set}
        rank = next((r for r, chunk_ids in enumerate(units, start=1) if chunks.intersection(chunk_ids)), None)
        if rank is not None:
            first[group.group_id] = rank
    return first


def score_question(units: list[tuple[str, ...]], unit_documents: list[str],
                   evidence: list[GroupEvidence], evidence_documents: set[str]) -> QuestionScore:
    relevant_ids = {cid for group in evidence for chunk_set in group.acceptable_sets for cid in chunk_set}
    relevant_ranks = [rank for rank, chunk_ids in enumerate(units, start=1)
                      if any(cid in relevant_ids for cid in chunk_ids)]
    completion = completion_ranks(units, evidence)
    return QuestionScore(
        len(units), len(relevant_ranks), len(evidence), len(completion),
        sum(doc not in evidence_documents for doc in unit_documents),
        min(relevant_ranks) if relevant_ranks else None, completion)


METRICS = (
    # key, label, higher_is_better
    ("hit_rate", "命中率", True),
    ("complete_coverage", "完整覆盖率", True),
    ("group_recall", "证据组召回", True),
    ("mrr", "MRR", True),
    ("chunk_precision", "Chunk 精确率", True),
    ("cross_document", "跨文档污染", False),
)


def aggregate(scores: list[QuestionScore]) -> dict[str, Ratio]:
    """Macro averages over answerable questions (each question weighs the same)."""
    n = len(scores)
    return {
        "hit_rate": Ratio(sum(s.hit for s in scores), n),
        "complete_coverage": Ratio(sum(s.complete for s in scores), n),
        "group_recall": Ratio(sum(s.groups_covered / s.groups_required
                                  for s in scores if s.groups_required), n),
        "mrr": Ratio(sum(1 / s.first_relevant_rank for s in scores if s.first_relevant_rank), n),
        "chunk_precision": Ratio(sum(s.relevant / s.returned for s in scores if s.returned), n),
        "cross_document": Ratio(sum(s.cross_document / s.returned for s in scores if s.returned), n),
    }
