"""Pure, auditable V3 evidence-group and chunk metric calculation."""

from __future__ import annotations

from collections.abc import Iterable

from rag.v3.contracts.evaluation import (
    AggregateMetrics, AggregateScope, CaseEvaluationFact, EvaluationAggregate,
    EvaluationDocumentResult, EvidenceGroupMapping, ExpectedEvidenceSnapshot,
    GroundTruthCase, GroundTruthDocument, MetricValue, NOT_APPLICABLE,
    QuestionMetrics, RetrievedChunkSnapshot, TestCaseMapping, evaluated,
)
from rag.v3.contracts.retrieval import RetrievalResult


class EvidenceMetricCalculator:
    def score_case(self, document: GroundTruthDocument, case: GroundTruthCase,
                   mapping: TestCaseMapping, retrieval: RetrievalResult) -> CaseEvaluationFact:
        if (mapping.case_id != case.case_id
                or retrieval.request.question != case.question.strip()
                or len(retrieval.hits) > retrieval.request.top_k):
            raise ValueError("case and retrieval identity or K differ")
        ground_groups = {item.evidence_group_id: item for item in case.evidence_groups}
        if set(ground_groups) != {item.evidence_group_id for item in mapping.groups}:
            raise ValueError("case evidence groups differ from reviewed mapping")
        expected = tuple(ExpectedEvidenceSnapshot(
            group.evidence_group_id,
            next(item.status for item in mapping.groups
                 if item.evidence_group_id == group.evidence_group_id),
            group.excerpts,
            next(item.acceptable_chunk_sets for item in mapping.groups
                 if item.evidence_group_id == group.evidence_group_id),
            next(item.excerpt_mappings for item in mapping.groups
                 if item.evidence_group_id == group.evidence_group_id),
        ) for group in case.evidence_groups)
        relevant_ids = {chunk_id for item in mapping.groups
                        for chunk_set in item.acceptable_chunk_sets
                        for chunk_id in chunk_set}
        evidence_document_ids = ({excerpt.document_id for group in case.evidence_groups
                                  for excerpt in group.excerpts} if case.answerable
                                 else {document.document_id})
        snapshots = []
        for rank, hit in enumerate(retrieval.hits, start=1):
            groups = tuple(item.evidence_group_id for item in mapping.groups
                           if any(hit.chunk_id in chunk_set
                                  for chunk_set in item.acceptable_chunk_sets))
            pages = tuple(dict.fromkeys(span.page_number for source in hit.sources
                                        for span in source.page_spans))
            snapshots.append(RetrievedChunkSnapshot(
                rank, hit.chunk_id, hit.document_id, hit.document_name, hit.relative_path,
                pages, hit.chunk_index, hit.kind, hit.text, hit.distance, 1.0 - hit.distance,
                groups, hit.chunk_id in relevant_ids,
                hit.document_id not in evidence_document_ids))
        snapshots = tuple(snapshots)
        metrics = self.score_question(mapping.groups, snapshots) if case.answerable else None
        return CaseEvaluationFact(
            case.case_id, "completed", case.question, case.answerable,
            case.reference_answer, expected, snapshots,
            tuple(item.evidence_group_id for item in mapping.groups
                  if item.status == "unmappable"), metrics, None)

    def score_question(self, mappings: tuple[EvidenceGroupMapping, ...],
                       hits: tuple[RetrievedChunkSnapshot, ...]) -> QuestionMetrics:
        if not mappings:
            raise ValueError("unanswerable case has no formal question metrics")
        if tuple(item.rank for item in hits) != tuple(range(1, len(hits) + 1)):
            raise ValueError("retrieval ranks must be contiguous and one based")
        ranks = {item.chunk_id: item.rank for item in hits}
        if len(ranks) != len(hits):
            raise ValueError("retrieval hit IDs must be unique")
        relevant_ids = {chunk_id for mapping in mappings
                        for group in mapping.acceptable_chunk_sets
                        for chunk_id in group}
        relevant = tuple(item for item in hits if item.chunk_id in relevant_ids)
        completion_ranks: dict[str, int] = {}
        for mapping in mappings:
            possible = [max(ranks[chunk_id] for chunk_id in group)
                        for group in mapping.acceptable_chunk_sets
                        if all(chunk_id in ranks for chunk_id in group)]
            if possible:
                completion_ranks[mapping.evidence_group_id] = min(possible)
        returned = len(hits)
        covered = len(completion_ranks)
        required = len(mappings)
        first_rank = relevant[0].rank if relevant else None
        cross = sum(item.cross_document for item in hits)
        group_rr_sum = sum(1 / completion_ranks[item.evidence_group_id]
                           for item in mappings if item.evidence_group_id in completion_ranks)
        return QuestionMetrics(
            returned, len(relevant), required, covered, cross, first_rank,
            evaluated(len(relevant), returned), evaluated(covered, required),
            evaluated(int(covered > 0), 1), evaluated(int(covered == required), 1),
            evaluated(1, first_rank) if first_rank is not None else evaluated(0, 1),
            evaluated(group_rr_sum, required), evaluated(cross, returned),
        )

    @staticmethod
    def _macro(values: Iterable[MetricValue]) -> MetricValue:
        materialized = tuple(values)
        if not materialized:
            return NOT_APPLICABLE
        if any(item.status != "evaluated" for item in materialized):
            raise ValueError("answerable case has an uncomputed metric")
        return evaluated(sum(item.value for item in materialized), len(materialized))

    def _scope(self, cases: tuple[CaseEvaluationFact, ...], scope: str,
               document_id: str | None) -> AggregateScope:
        if any(item.status != "completed" for item in cases):
            raise ValueError("incomplete case cannot enter formal aggregate")
        answerable = tuple(item for item in cases if item.answerable)
        if any(item.metrics is None for item in answerable):
            raise ValueError("answerable case lacks metrics")
        question = tuple(item.metrics for item in answerable)
        unanswerable = sum(not item.answerable for item in cases)
        unmappable = sum(len(item.unmappable_evidence_group_ids) for item in answerable)
        if not question:
            metrics = AggregateMetrics(0, 0, 0, 0, 0, 0,
                *([NOT_APPLICABLE] * 10))
            return AggregateScope(scope, document_id, 0, unanswerable, unmappable, metrics)
        returned = sum(item.returned_count for item in question)
        relevant = sum(item.relevant_chunk_count for item in question)
        required = sum(item.required_group_count for item in question)
        covered = sum(item.covered_group_count for item in question)
        cross = sum(item.cross_document_count for item in question)
        group_rr = sum(item.evidence_group_reciprocal_rank_at_k.numerator for item in question)
        metrics = AggregateMetrics(
            len(question), returned, relevant, required, covered, cross,
            self._macro(item.chunk_precision_at_k for item in question),
            evaluated(relevant, returned),
            self._macro(item.evidence_group_recall_at_k for item in question),
            evaluated(covered, required),
            self._macro(item.question_hit_at_k for item in question),
            self._macro(item.complete_coverage_at_k for item in question),
            self._macro(item.reciprocal_rank_at_k for item in question),
            evaluated(group_rr, required),
            self._macro(item.cross_document_contamination_at_k for item in question),
            evaluated(cross, returned),
        )
        return AggregateScope(scope, document_id, len(question), unanswerable,
                              unmappable, metrics)

    def aggregate(self, run_id: str,
                  documents: tuple[EvaluationDocumentResult, ...]) -> EvaluationAggregate:
        if not run_id or any(item.run_id != run_id for item in documents):
            raise ValueError("aggregate run identity differs")
        scopes = tuple(self._scope(item.cases, "document", item.document_id)
                       for item in documents)
        all_cases = tuple(case for document in documents for case in document.cases)
        return EvaluationAggregate("evaluation_aggregate_v3", run_id, scopes,
                                   self._scope(all_cases, "overall", None))
