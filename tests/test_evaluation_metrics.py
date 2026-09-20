import pytest

from rag.errors import EvaluationDataError
from rag.evaluation_metrics import aggregate_documents, calculate_question_metrics
from rag.evaluation_models import (
    CaseEvaluationFact, EvidenceGroupMapping, EvaluationDocumentResult,
    RetrievedChunkSnapshot,
)


def _hit(rank: int, groups: tuple[str, ...], *, cross: bool = False):
    return RetrievedChunkSnapshot(
        rank, f"c{rank}", "other" if cross else "doc", "doc.pdf", "doc.pdf",
        (1,), rank - 1, "text", f"text {rank}", float(rank), 1.0 - rank,
        groups, bool(groups), cross,
    )


def _mapping(group_id: str, *chunk_sets: tuple[str, ...]) -> EvidenceGroupMapping:
    return EvidenceGroupMapping(
        group_id, "mapped" if chunk_sets else "unmappable", tuple(chunk_sets)
    )


def test_mapping_rejects_nonminimal_chunk_superset() -> None:
    with pytest.raises(EvaluationDataError, match="非最小超集"):
        _mapping("e1", ("c1",), ("c1", "c2"))


def test_question_metrics_count_chunks_and_groups_separately() -> None:
    metrics = calculate_question_metrics(
        (_mapping("e1", ("c1",), ("c2",)), _mapping("e2", ("c1",))),
        (_hit(1, ("e1", "e2")), _hit(2, ("e1",)), _hit(3, (), cross=True)),
    )

    assert metrics.relevant_chunk_count == 2
    assert metrics.covered_group_count == 2
    assert metrics.chunk_precision_at_k.value == 2 / 3
    assert metrics.evidence_group_recall_at_k.value == 1.0
    assert metrics.complete_coverage_at_k.value == 1.0
    assert metrics.evidence_group_reciprocal_rank_at_k.value == 1.0
    assert metrics.cross_document_contamination_at_k.value == 1 / 3


def test_zero_results_are_evaluated_as_zero() -> None:
    metrics = calculate_question_metrics((_mapping("e1", ("c1",)),), ())

    assert metrics.chunk_precision_at_k.denominator == 0
    assert metrics.chunk_precision_at_k.value == 0
    assert metrics.evidence_group_recall_at_k.value == 0
    assert metrics.reciprocal_rank_at_k.value == 0


def test_group_requires_all_chunks_in_one_acceptable_set() -> None:
    mapping = _mapping("e1", ("c1", "c2"), ("c4",))

    partial = calculate_question_metrics((mapping,), (_hit(1, ("e1",)),))
    combined = calculate_question_metrics(
        (mapping,), (_hit(1, ("e1",)), _hit(2, ("e1",)))
    )
    alternative = calculate_question_metrics((mapping,), (_hit(4, ("e1",)),))

    assert partial.relevant_chunk_count == 1
    assert partial.covered_group_count == 0
    assert combined.covered_group_count == 1
    assert combined.evidence_group_reciprocal_rank_at_k.value == 1 / 2
    assert alternative.covered_group_count == 1
    assert alternative.evidence_group_reciprocal_rank_at_k.value == 1 / 4


def test_aggregate_preserves_macro_and_micro_denominators() -> None:
    first = calculate_question_metrics(
        (_mapping("e1", ("c1",)),), (_hit(1, ("e1",)),)
    )
    second = calculate_question_metrics(
        (_mapping("e1", ("c1",)), _mapping("e2", ("c4",))),
        (_hit(1, ("e1",)), _hit(2, ()), _hit(3, ())),
    )
    cases = (
        CaseEvaluationFact("q1", "completed", "q1", True, "a", (), (), (), first, None),
        CaseEvaluationFact("q2", "completed", "q2", True, "a", (), (), (), second, None),
        CaseEvaluationFact("q3", "completed", "q3", False, "none", (), (), (), None, None),
    )
    document = EvaluationDocumentResult(
        "run", "doc", "doc", "doc.pdf", "doc.pdf", "a" * 64, cases
    )

    aggregate = aggregate_documents("run", (document,)).overall

    assert aggregate.answerable_count == 2
    assert aggregate.unanswerable_count == 1
    assert aggregate.metrics.macro_chunk_precision_at_k.value == (1 + 1 / 3) / 2
    assert aggregate.metrics.micro_chunk_precision_at_k.value == 2 / 4
    assert aggregate.metrics.micro_evidence_group_recall_at_k.value == 2 / 3
