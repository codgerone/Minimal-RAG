"""Reviewed evidence, retrieval facts and metric results for V3 evaluation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from rag.v3.contracts.assembly import BuildProjection
from rag.v3.contracts.storage import IndexIdentity


@dataclass(frozen=True)
class EvidenceExcerpt:
    excerpt_id: str
    document_id: str
    document_name: str
    relative_path: str
    page_numbers: tuple[int, ...]
    text: str

    def __post_init__(self) -> None:
        if (not all((self.excerpt_id, self.document_id, self.document_name,
                     self.relative_path, self.text.strip()))
                or not self.page_numbers
                or self.page_numbers != tuple(sorted(set(self.page_numbers)))
                or any(type(page) is not int or page <= 0 for page in self.page_numbers)):
            raise ValueError("invalid evidence excerpt")


@dataclass(frozen=True)
class EvidenceGroup:
    evidence_group_id: str
    excerpts: tuple[EvidenceExcerpt, ...]

    def __post_init__(self) -> None:
        if (not self.evidence_group_id or not self.excerpts
                or len({item.excerpt_id for item in self.excerpts}) != len(self.excerpts)):
            raise ValueError("invalid evidence group")


@dataclass(frozen=True)
class GroundTruthCase:
    case_id: str
    question: str
    reference_answer: str
    answerable: bool
    evidence_groups: tuple[EvidenceGroup, ...]

    def __post_init__(self) -> None:
        if (not self.case_id or not self.question.strip() or not self.reference_answer.strip()
                or self.answerable != bool(self.evidence_groups)
                or len({item.evidence_group_id for item in self.evidence_groups})
                   != len(self.evidence_groups)):
            raise ValueError("invalid ground truth case")


@dataclass(frozen=True)
class ExcerptChunkMapping:
    excerpt_id: str
    status: Literal["mapped", "unmappable"]
    chunk_sets: tuple[tuple[str, ...], ...]

    def __post_init__(self) -> None:
        sets = self.chunk_sets
        if (not self.excerpt_id or self.status not in {"mapped", "unmappable"}
                or (self.status == "mapped") != bool(sets)
                or sets != tuple(sorted(set(sets)))
                or any(not item or item != tuple(sorted(set(item))) for item in sets)
                or any(set(left) < set(right) for left in sets for right in sets)):
            raise ValueError("invalid excerpt chunk mapping")


@dataclass(frozen=True)
class EvidenceGroupMapping:
    evidence_group_id: str
    status: Literal["mapped", "unmappable"]
    acceptable_chunk_sets: tuple[tuple[str, ...], ...]
    excerpt_mappings: tuple[ExcerptChunkMapping, ...] = ()

    def __post_init__(self) -> None:
        groups = self.acceptable_chunk_sets
        if (not self.evidence_group_id or self.status not in {"mapped", "unmappable"}
                or (self.status == "mapped") != bool(groups)
                or groups != tuple(sorted(set(groups)))
                or any(not group or group != tuple(sorted(set(group))) for group in groups)):
            raise ValueError("invalid acceptable chunk sets")
        materialized = tuple(frozenset(group) for group in groups)
        if any(left < right for left in materialized for right in materialized):
            raise ValueError("acceptable chunk set has a nonminimal superset")
        if (self.excerpt_mappings != tuple(sorted(self.excerpt_mappings,
                                                 key=lambda item: item.excerpt_id))
                or len({item.excerpt_id for item in self.excerpt_mappings})
                   != len(self.excerpt_mappings)):
            raise ValueError("excerpt mapping IDs are not unique and ordered")


@dataclass(frozen=True)
class TestCaseMapping:
    case_id: str
    groups: tuple[EvidenceGroupMapping, ...]

    def __post_init__(self) -> None:
        if (not self.case_id or len({item.evidence_group_id for item in self.groups})
                != len(self.groups)):
            raise ValueError("invalid test case mapping")


@dataclass(frozen=True)
class MetricValue:
    status: Literal["evaluated", "not_applicable"]
    numerator: float | None
    denominator: int | None
    value: float | None

    def __post_init__(self) -> None:
        if self.status == "not_applicable":
            if (self.numerator, self.denominator, self.value) != (None, None, None):
                raise ValueError("not applicable metric has numbers")
        elif (self.status != "evaluated" or self.numerator is None
              or self.denominator is None or self.value is None
              or type(self.denominator) is not int or self.denominator < 0
              or not math.isfinite(self.numerator) or not math.isfinite(self.value)):
            raise ValueError("invalid evaluated metric")


NOT_APPLICABLE = MetricValue("not_applicable", None, None, None)


def evaluated(numerator: float, denominator: int) -> MetricValue:
    if denominator < 0:
        raise ValueError("negative metric denominator")
    return MetricValue("evaluated", float(numerator), denominator,
                       float(numerator) / denominator if denominator else 0.0)


@dataclass(frozen=True)
class QuestionMetrics:
    returned_count: int
    relevant_chunk_count: int
    required_group_count: int
    covered_group_count: int
    cross_document_count: int
    first_relevant_rank: int | None
    chunk_precision_at_k: MetricValue
    evidence_group_recall_at_k: MetricValue
    question_hit_at_k: MetricValue
    complete_coverage_at_k: MetricValue
    reciprocal_rank_at_k: MetricValue
    evidence_group_reciprocal_rank_at_k: MetricValue
    cross_document_contamination_at_k: MetricValue


@dataclass(frozen=True)
class RetrievedChunkSnapshot:
    rank: int
    chunk_id: str
    document_id: str
    document_name: str
    relative_path: str
    page_numbers: tuple[int, ...]
    chunk_index: int
    chunk_kind: Literal["text", "list", "table"]
    text: str
    distance: float
    similarity: float
    matched_evidence_group_ids: tuple[str, ...]
    relevant: bool
    cross_document: bool


@dataclass(frozen=True)
class ExpectedEvidenceSnapshot:
    evidence_group_id: str
    mapping_status: Literal["mapped", "unmappable"]
    excerpts: tuple[EvidenceExcerpt, ...]
    acceptable_chunk_sets: tuple[tuple[str, ...], ...]
    excerpt_mappings: tuple[ExcerptChunkMapping, ...] = ()


@dataclass(frozen=True)
class EvaluationError:
    stage: Literal["preflight", "retrieval", "metric_calculation", "json_rendering",
                   "html_rendering", "aggregation", "publication"]
    code: Literal["ground_truth_incomplete", "test_set_incomplete",
                  "dataset_identity_mismatch", "index_identity_mismatch", "mapping_invalid",
                  "source_mismatch", "retrieval_failed", "metric_failed", "json_render_failed",
                  "html_render_failed", "aggregate_failed", "publication_failed"]
    scope: Literal["run", "document", "case"]
    document_id: str | None
    case_id: str | None
    message: str


@dataclass(frozen=True)
class CaseEvaluationFact:
    case_id: str
    status: Literal["completed", "failed", "not_run"]
    question: str
    answerable: bool
    reference_answer: str
    expected_evidence: tuple[ExpectedEvidenceSnapshot, ...]
    retrieved_chunks: tuple[RetrievedChunkSnapshot, ...]
    unmappable_evidence_group_ids: tuple[str, ...]
    metrics: QuestionMetrics | None
    error: EvaluationError | None


@dataclass(frozen=True)
class AggregateMetrics:
    included_question_count: int
    returned_chunk_count: int
    relevant_chunk_count: int
    required_group_count: int
    covered_group_count: int
    cross_document_count: int
    macro_chunk_precision_at_k: MetricValue
    micro_chunk_precision_at_k: MetricValue
    macro_evidence_group_recall_at_k: MetricValue
    micro_evidence_group_recall_at_k: MetricValue
    question_hit_rate_at_k: MetricValue
    complete_coverage_rate_at_k: MetricValue
    mean_reciprocal_rank_at_k: MetricValue
    evidence_group_mean_reciprocal_rank_at_k: MetricValue
    macro_cross_document_contamination_at_k: MetricValue
    micro_cross_document_contamination_at_k: MetricValue


@dataclass(frozen=True)
class EvaluationDocumentResult:
    schema_version: Literal["evaluation_document_result_v3"]
    run_id: str
    document_key: str
    document_id: str
    document_name: str
    relative_path: str
    file_hash: str
    cases: tuple[CaseEvaluationFact, ...]


@dataclass(frozen=True)
class AggregateScope:
    scope: Literal["document", "overall"]
    document_id: str | None
    answerable_count: int
    unanswerable_count: int
    unmappable_group_count: int
    metrics: AggregateMetrics


@dataclass(frozen=True)
class EvaluationAggregate:
    schema_version: Literal["evaluation_aggregate_v3"]
    run_id: str
    documents: tuple[AggregateScope, ...]
    overall: AggregateScope


@dataclass(frozen=True)
class GroundTruthFileEntry:
    document_key: str
    json_path: str
    json_sha256: str
    case_count: int
    document_id: str
    file_hash: str


@dataclass(frozen=True)
class GroundTruthDocument:
    schema_version: Literal["ground_truth_document_v1"]
    document_key: str
    document_id: str
    document_name: str
    relative_path: str
    file_hash: str
    cases: tuple[GroundTruthCase, ...]


@dataclass(frozen=True)
class GroundTruthManifest:
    schema_version: Literal["ground_truth_manifest_v1"]
    dataset_version: str
    ground_truth_fingerprint: str
    review_status: Literal["draft", "approved", "superseded"]
    files: tuple[GroundTruthFileEntry, ...]
    created_at: str
    reviewed_at: str | None
    superseded_by: str | None


@dataclass(frozen=True)
class TestSetFileEntry:
    document_key: str
    json_path: str
    json_sha256: str
    case_count: int
    document_id: str
    file_hash: str
    chunk_count: int


@dataclass(frozen=True)
class TestSetDocument:
    schema_version: Literal["test_set_document_v3"]
    document_key: str
    document_id: str
    document_name: str
    relative_path: str
    file_hash: str
    cases: tuple[TestCaseMapping, ...]


@dataclass(frozen=True)
class TestSetManifest:
    schema_version: Literal["test_set_manifest_v3"]
    test_set_version: str
    test_set_fingerprint: str
    review_status: Literal["draft", "approved", "superseded"]
    system_version: Literal["3.0"]
    configuration_name: str
    index_identity: IndexIdentity
    build_config: BuildProjection
    build_config_fingerprint: str
    ground_truth_version: str
    ground_truth_fingerprint: str
    annotation_rule_version: str
    files: tuple[TestSetFileEntry, ...]
    created_at: str
    reviewed_at: str | None
    superseded_by: str | None


@dataclass(frozen=True)
class EvaluationQueryConfig:
    top_k: int
    query_prefix: Literal["query: "]
    document_filter: None
    question_normalization: Literal["strip_v1"]
    distance_order: Literal["ascending"]
    tie_break_order: Literal["chunk_id_ascending"]
    strict_top_k: Literal[True]


@dataclass(frozen=True)
class GroundTruthIdentity:
    dataset_version: str
    ground_truth_fingerprint: str


@dataclass(frozen=True)
class TestSetIdentity:
    test_set_version: str
    test_set_fingerprint: str
    annotation_rule_version: str


@dataclass(frozen=True)
class LoadedEvaluationData:
    ground_truth_manifest: GroundTruthManifest
    ground_truth_documents: tuple[GroundTruthDocument, ...]
    test_set_manifest: TestSetManifest
    test_set_documents: tuple[TestSetDocument, ...]


@dataclass(frozen=True)
class EvaluationRunRequest:
    run_id: str
    configuration_name: str
    index_identity: IndexIdentity
    build_config: BuildProjection
    query_config: EvaluationQueryConfig
    ground_truth: GroundTruthIdentity
    test_set: TestSetIdentity
    evaluation_protocol_version: str
    started_at: str


@dataclass(frozen=True)
class RenderedEvaluationFile:
    location: Literal["run", "system_version", "comparison_root"]
    relative_path: str
    media_type: Literal["application/json", "text/html", "text/markdown"]
    content: bytes
    sha256: str

    def __post_init__(self) -> None:
        import hashlib
        if (not self.relative_path or self.relative_path.startswith(("/", "\\"))
                or "\\" in self.relative_path or ".." in self.relative_path.split("/")
                or hashlib.sha256(self.content).hexdigest() != self.sha256):
            raise ValueError("invalid rendered evaluation file")


@dataclass(frozen=True)
class RenderedEvaluation:
    run_id: str
    documents: tuple[EvaluationDocumentResult, ...]
    aggregate: EvaluationAggregate
    files: tuple[RenderedEvaluationFile, ...]


@dataclass(frozen=True)
class BaselineSelectionEntry:
    ground_truth_fingerprint: str
    evaluation_protocol_version: str
    query_config: EvaluationQueryConfig
    annotation_rule_version: str
    system_version: Literal["2.0", "3.0"]
    run_id: str
    reviewed_at: str


@dataclass(frozen=True)
class BaselineSelection:
    schema_version: Literal["evaluation_baselines_v3"]
    entries: tuple[BaselineSelectionEntry, ...]
    updated_at: str | None


@dataclass(frozen=True)
class ComparisonDocumentIdentity:
    document_id: str
    file_hash: str
    cases: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ComparisonQueryConfig:
    top_k: int
    query_prefix: str
    document_filter: None
    question_normalization: Literal["strip_v1"]
    distance_order: Literal["ascending"]
    tie_break_order: Literal["chunk_id_ascending"]
    strict_top_k: Literal[True]


@dataclass(frozen=True)
class ComparisonRunSnapshot:
    system_version: Literal["2.0", "3.0"]
    run_id: str
    source_run_path: str
    configuration_label: str
    collection_name: str
    build_config_schema: Literal["legacy_build_config_v2", "build_projection_v3"]
    build_config_json: bytes
    build_config_fingerprint: str
    ground_truth: GroundTruthIdentity
    documents: tuple[ComparisonDocumentIdentity, ...]
    query_config: ComparisonQueryConfig
    evaluation_protocol_version: str
    annotation_rule_version: str
    document_summaries: tuple[AggregateScope, ...]
    overall_metrics: AggregateMetrics
    summary_path: str


ComparisonDifferenceCode = Literal[
    "dataset_version", "ground_truth_fingerprint", "pdf_hash", "question_text",
    "top_k", "query_prefix", "document_filter", "question_normalization",
    "distance_order", "tie_break_order", "strict_top_k",
    "evaluation_protocol_version", "annotation_rule_version",
]
ComparisonMetricName = Literal[
    "question_hit_rate_at_k", "macro_evidence_group_recall_at_k",
    "complete_coverage_rate_at_k", "macro_chunk_precision_at_k",
    "mean_reciprocal_rank_at_k", "macro_cross_document_contamination_at_k",
]


@dataclass(frozen=True)
class ComparisonRunReference:
    system_version: Literal["2.0", "3.0"]
    run_id: str
    configuration_label: str
    build_config_fingerprint: str
    summary_path: str


@dataclass(frozen=True)
class ComparisonMetricDelta:
    metric_name: ComparisonMetricName
    left_value: float | None
    right_value: float | None
    absolute_delta: float | None


@dataclass(frozen=True)
class ComparisonPair:
    left: ComparisonRunReference
    right: ComparisonRunReference
    status: Literal["strictly_comparable", "protocol_changed", "dataset_changed"]
    difference_codes: tuple[ComparisonDifferenceCode, ...]
    metric_deltas: tuple[ComparisonMetricDelta, ...]


@dataclass(frozen=True)
class EvaluationComparisonReport:
    schema_version: Literal["evaluation_comparison_v3"]
    generated_at: str
    runs: tuple[ComparisonRunReference, ...]
    comparisons: tuple[ComparisonPair, ...]


@dataclass(frozen=True)
class RunDocumentEntry:
    document_key: str
    json_path: str
    json_sha256: str
    html_path: str
    html_sha256: str


@dataclass(frozen=True)
class EvaluationRun:
    run_id: str
    status: Literal["invalid", "failed", "completed"]
    configuration_name: str
    index_identity: IndexIdentity
    build_config: BuildProjection
    build_config_fingerprint: str
    query_config: EvaluationQueryConfig
    evaluation_protocol_version: str
    ground_truth: GroundTruthIdentity
    test_set: TestSetIdentity
    started_at: str
    completed_at: str | None
    documents: tuple[RunDocumentEntry, ...]
    aggregate_path: str | None
    aggregate_sha256: str | None
    error: EvaluationError | None
    schema_version: Literal["evaluation_run_v3"]


@dataclass(frozen=True)
class EvaluationVersionRestoreEntry:
    location: Literal["system_version", "comparison_root"]
    relative_path: str
    old_existed: bool
    old_sha256: str | None
    old_snapshot_path: str | None
    expected_new_sha256: str


@dataclass(frozen=True)
class EvaluationPublicationJournal:
    schema_version: Literal["evaluation_publication_journal_v3"]
    run_id: str
    index_identity: IndexIdentity
    state: Literal["prepared", "publishing", "restoring", "recovery_failed"]
    current_step: Literal["none", "run_publish", "version_publish", "run_marker", "restore"]
    staging_path: str
    final_run_path: str
    version_files: tuple[EvaluationVersionRestoreEntry, ...]
    created_at: str
    error: EvaluationError | None
