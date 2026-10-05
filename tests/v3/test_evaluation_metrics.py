"""Business metric boundaries: AND groups, empty hits and unanswerable scope."""

from rag.v3.application.evaluation_metrics import EvidenceMetricCalculator
from rag.v3.contracts.evaluation import (
    CaseEvaluationFact, EvaluationDocumentResult, EvidenceExcerpt, EvidenceGroup,
    EvidenceGroupMapping, GroundTruthCase, GroundTruthDocument, RetrievedChunkSnapshot,
    TestCaseMapping as CaseMapping,
)
from rag.v3.contracts.documents import ChunkSource, PageSpan
from rag.v3.contracts.retrieval import RetrievalHit, RetrievalRequest, RetrievalResult
from rag.v3.application.assembly import builtin_configuration, index_identity


def hit(rank: int, chunk_id: str, *, cross: bool = False) -> RetrievedChunkSnapshot:
    return RetrievedChunkSnapshot(rank, chunk_id, "other" if cross else "doc",
        "Other" if cross else "Doc", "other.pdf" if cross else "doc.pdf",
        (1,), rank - 1, "text", chunk_id, float(rank), 1 / (1 + rank),
        (), chunk_id in {"a", "b", "c"}, cross)


def test_multichunk_and_group_coverage_and_group_rr():
    calculator = EvidenceMetricCalculator()
    groups = (EvidenceGroupMapping("e1", "mapped", (("a", "b"),)),
              EvidenceGroupMapping("e2", "mapped", (("c",),)))
    metrics = calculator.score_question(groups,
        (hit(1, "a"), hit(2, "c"), hit(3, "b"), hit(4, "x", cross=True)))
    assert metrics.relevant_chunk_count == 3
    assert metrics.covered_group_count == 2
    assert metrics.chunk_precision_at_k.value == 0.75
    assert metrics.complete_coverage_at_k.value == 1
    assert metrics.evidence_group_reciprocal_rank_at_k.value == (1 / 3 + 1 / 2) / 2
    assert metrics.cross_document_contamination_at_k.value == 0.25

    partial = calculator.score_question(groups, (hit(1, "a"),))
    assert partial.relevant_chunk_count == 1
    assert partial.covered_group_count == 0
    assert partial.question_hit_at_k.value == 0


def test_zero_return_and_unanswerable_only_aggregate():
    calculator = EvidenceMetricCalculator()
    groups = (EvidenceGroupMapping("e1", "unmappable", ()),)
    metrics = calculator.score_question(groups, ())
    assert metrics.chunk_precision_at_k.value == 0
    assert metrics.evidence_group_recall_at_k.value == 0
    assert metrics.cross_document_contamination_at_k.value == 0
    answerable = CaseEvaluationFact("q1", "completed", "question", True, "answer",
                                    (), (), ("e1",), metrics, None)
    unanswerable = CaseEvaluationFact("q2", "completed", "unknown", False,
                                      "insufficient evidence", (), (), (), None, None)
    document = EvaluationDocumentResult("evaluation_document_result_v3", "run", "doc-key",
                                        "doc", "Doc", "doc.pdf", "a" * 64,
                                        (answerable, unanswerable))
    aggregate = calculator.aggregate("run", (document,))
    assert aggregate.overall.metrics.included_question_count == 1
    assert aggregate.overall.unanswerable_count == 1
    assert aggregate.overall.unmappable_group_count == 1
    empty = calculator.aggregate("run-empty", (EvaluationDocumentResult(
        "evaluation_document_result_v3", "run-empty", "doc-key", "doc", "Doc", "doc.pdf",
        "a" * 64, (unanswerable,)),))
    assert empty.overall.metrics.question_hit_rate_at_k.status == "not_applicable"


def test_score_case_uses_actual_hit_sources_and_does_not_score_unanswerable():
    index = index_identity(builtin_configuration("plain_text"))
    excerpt = EvidenceExcerpt("x1", "doc", "Doc", "doc.pdf", (2,), "evidence")
    case = GroundTruthCase("q1", "question", "answer", True,
                           (EvidenceGroup("e1", (excerpt,)),))
    unanswerable = GroundTruthCase("q2", "unknown", "insufficient", False, ())
    document = GroundTruthDocument("ground_truth_document_v1", "doc-key", "doc",
        "Doc", "doc.pdf", "a" * 64, (case, unanswerable))
    source = ChunkSource("node", (PageSpan(2, None, None),), 0, 8, False, "none")
    retrieved = RetrievalResult(RetrievalRequest("question", 2, None, index, "plain_text"),
        (RetrievalHit("c1", "doc", "Doc", "doc.pdf", "evidence", 0, "text",
                      (source,), 0.2),), index)
    calculator = EvidenceMetricCalculator()
    scored = calculator.score_case(document, case,
        CaseMapping("q1", (EvidenceGroupMapping("e1", "mapped", (("c1",),)),)),
        retrieved)
    assert scored.retrieved_chunks[0].page_numbers == (2,)
    assert scored.retrieved_chunks[0].similarity == 0.8
    assert scored.retrieved_chunks[0].matched_evidence_group_ids == ("e1",)
    assert scored.metrics.question_hit_at_k.value == 1
    unknown = calculator.score_case(document, unanswerable, CaseMapping("q2", ()),
        RetrievalResult(RetrievalRequest("unknown", 2, None, index, "plain_text"),
            (RetrievalHit("x", "other", "Other", "other.pdf", "irrelevant", 0,
                          "text", (source,), 0.4),), index))
    assert unknown.metrics is None
    assert unknown.retrieved_chunks[0].cross_document
