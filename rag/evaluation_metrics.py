"""Pure metric calculation for formal retrieval evaluation."""

from __future__ import annotations

from collections.abc import Iterable

from rag.evaluation_models import (
    AggregateMetrics, AggregateScope, CaseEvaluationFact, EvaluationAggregate,
    EvaluationDocumentResult, EvidenceGroupMapping, MetricValue, NOT_APPLICABLE,
    QuestionMetrics, RetrievedChunkSnapshot, evaluated,
)


def calculate_question_metrics(
    mappings: tuple[EvidenceGroupMapping, ...],
    hits: tuple[RetrievedChunkSnapshot, ...],
) -> QuestionMetrics:
    relevant_ids = {
        chunk_id
        for mapping in mappings
        for chunk_set in mapping.acceptable_chunk_sets
        for chunk_id in chunk_set
    }
    relevant = tuple(hit for hit in hits if hit.chunk_id in relevant_ids)
    ranks = {hit.chunk_id: hit.rank for hit in hits}
    group_completion_ranks: dict[str, int] = {}
    for mapping in mappings:
        completion_ranks = [
            max(ranks[chunk_id] for chunk_id in chunk_set)
            for chunk_set in mapping.acceptable_chunk_sets
            if all(chunk_id in ranks for chunk_id in chunk_set)
        ]
        if completion_ranks:
            group_completion_ranks[mapping.evidence_group_id] = min(completion_ranks)
    cross_count = sum(hit.cross_document for hit in hits)
    first_rank = min((hit.rank for hit in relevant), default=None)
    group_rr = [
        0.0 if mapping.evidence_group_id not in group_completion_ranks
        else 1.0 / group_completion_ranks[mapping.evidence_group_id]
        for mapping in mappings
    ]
    required = len(mappings)
    returned = len(hits)
    covered_count = len(group_completion_ranks)
    return QuestionMetrics(
        returned_count=returned,
        relevant_chunk_count=len(relevant),
        required_group_count=required,
        covered_group_count=covered_count,
        cross_document_count=cross_count,
        first_relevant_rank=first_rank,
        chunk_precision_at_k=evaluated(len(relevant), returned),
        evidence_group_recall_at_k=evaluated(covered_count, required),
        question_hit_at_k=evaluated(1 if covered_count else 0, 1),
        complete_coverage_at_k=evaluated(1 if covered_count == required else 0, 1),
        reciprocal_rank_at_k=(
            evaluated(0, 1) if first_rank is None else evaluated(1, first_rank)
        ),
        evidence_group_reciprocal_rank_at_k=evaluated(sum(group_rr), required),
        cross_document_contamination_at_k=evaluated(cross_count, returned),
    )


def _macro(values: Iterable[MetricValue]) -> MetricValue:
    materialized = tuple(item for item in values if item.status == "evaluated")
    if not materialized:
        return NOT_APPLICABLE
    return evaluated(sum(item.value or 0.0 for item in materialized), len(materialized))


def _scope(cases: tuple[CaseEvaluationFact, ...], scope: str, document_id: str | None) -> AggregateScope:
    answerable = tuple(
        case for case in cases
        if case.status == "completed" and case.answerable and case.metrics is not None
    )
    unanswerable = sum(
        case.status == "completed" and not case.answerable for case in cases
    )
    unmappable = sum(len(case.unmappable_evidence_group_ids) for case in answerable)
    if not answerable:
        metrics = AggregateMetrics(
            0, 0, 0, 0, 0, 0,
            NOT_APPLICABLE, NOT_APPLICABLE, NOT_APPLICABLE, NOT_APPLICABLE,
            NOT_APPLICABLE, NOT_APPLICABLE, NOT_APPLICABLE, NOT_APPLICABLE,
            NOT_APPLICABLE, NOT_APPLICABLE,
        )
        return AggregateScope(scope, document_id, 0, unanswerable, unmappable, metrics)  # type: ignore[arg-type]
    question_metrics = tuple(case.metrics for case in answerable if case.metrics is not None)
    returned = sum(item.returned_count for item in question_metrics)
    relevant = sum(item.relevant_chunk_count for item in question_metrics)
    required = sum(item.required_group_count for item in question_metrics)
    covered = sum(item.covered_group_count for item in question_metrics)
    cross = sum(item.cross_document_count for item in question_metrics)
    group_rr_sum = sum(
        item.evidence_group_reciprocal_rank_at_k.numerator or 0.0
        for item in question_metrics
    )
    metrics = AggregateMetrics(
        included_question_count=len(answerable),
        returned_chunk_count=returned,
        relevant_chunk_count=relevant,
        required_group_count=required,
        covered_group_count=covered,
        cross_document_count=cross,
        macro_chunk_precision_at_k=_macro(item.chunk_precision_at_k for item in question_metrics),
        micro_chunk_precision_at_k=evaluated(relevant, returned),
        macro_evidence_group_recall_at_k=_macro(
            item.evidence_group_recall_at_k for item in question_metrics
        ),
        micro_evidence_group_recall_at_k=evaluated(covered, required),
        question_hit_rate_at_k=_macro(item.question_hit_at_k for item in question_metrics),
        complete_coverage_rate_at_k=_macro(
            item.complete_coverage_at_k for item in question_metrics
        ),
        mean_reciprocal_rank_at_k=_macro(
            item.reciprocal_rank_at_k for item in question_metrics
        ),
        evidence_group_mean_reciprocal_rank_at_k=evaluated(group_rr_sum, required),
        macro_cross_document_contamination_at_k=_macro(
            item.cross_document_contamination_at_k for item in question_metrics
        ),
        micro_cross_document_contamination_at_k=evaluated(cross, returned),
    )
    return AggregateScope(scope, document_id, len(answerable), unanswerable, unmappable, metrics)  # type: ignore[arg-type]


def aggregate_documents(
    run_id: str, documents: tuple[EvaluationDocumentResult, ...]
) -> EvaluationAggregate:
    scopes = tuple(_scope(item.cases, "document", item.document_id) for item in documents)
    all_cases = tuple(case for document in documents for case in document.cases)
    return EvaluationAggregate(run_id, scopes, _scope(all_cases, "overall", None))
