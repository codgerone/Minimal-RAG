"""Immutable contracts for formal retrieval evaluation."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Literal, TypeAlias

from rag.build_config import BuildConfig
from rag.errors import EvaluationDataError
from rag.pipeline_types import PipelineId


DatasetReviewStatus: TypeAlias = Literal["draft", "approved", "superseded"]
EvidenceMappingStatus: TypeAlias = Literal["mapped", "unmappable"]
EvaluationRunStatus: TypeAlias = Literal["invalid", "failed", "completed"]
ComparisonStatus: TypeAlias = Literal[
    "strictly_comparable", "protocol_changed", "dataset_changed"
]
CaseExecutionStatus: TypeAlias = Literal["completed", "failed", "not_run"]
MetricStatus: TypeAlias = Literal["evaluated", "not_applicable"]
EvaluationFailureStage: TypeAlias = Literal[
    "preflight", "retrieval", "metric_calculation", "json_rendering",
    "html_rendering", "aggregation", "publication",
]


def canonical_json(value: Any) -> bytes:
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
        default=lambda item: asdict(item)
        if is_dataclass(item) and not isinstance(item, type)
        else _raise_not_serializable(item),
    ).encode("utf-8")


def _raise_not_serializable(value: Any) -> Any:
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _nonempty(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise EvaluationDataError(f"{label} 不能为空。")


def _sha256(value: str, label: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise EvaluationDataError(f"{label} 必须是小写 SHA-256。")


@dataclass(frozen=True)
class GroundTruthFileEntry:
    document_key: str
    json_path: str
    content_sha256: str
    case_count: int


@dataclass(frozen=True)
class GroundTruthManifest:
    dataset_version: str
    ground_truth_fingerprint: str
    review_status: DatasetReviewStatus
    files: tuple[GroundTruthFileEntry, ...]
    created_at: str
    reviewed_at: str | None
    superseded_by: str | None
    schema_version: Literal["ground_truth_manifest_v1"] = "ground_truth_manifest_v1"

    def __post_init__(self) -> None:
        if self.schema_version != "ground_truth_manifest_v1":
            raise EvaluationDataError("GroundTruthManifest schema 不受支持。")
        _nonempty(self.dataset_version, "dataset_version")
        _sha256(self.ground_truth_fingerprint, "ground_truth_fingerprint")
        if self.review_status not in {"draft", "approved", "superseded"}:
            raise EvaluationDataError("GroundTruthManifest review_status 无效。")
        if self.review_status == "draft" and (self.reviewed_at or self.superseded_by):
            raise EvaluationDataError("draft ground truth 不得有审核或替代字段。")
        if self.review_status == "approved" and (not self.reviewed_at or self.superseded_by):
            raise EvaluationDataError("approved ground truth 审核字段无效。")
        if self.review_status == "superseded" and (not self.reviewed_at or not self.superseded_by):
            raise EvaluationDataError("superseded ground truth 必须指向替代版本。")
        keys = [item.document_key for item in self.files]
        paths = [item.json_path for item in self.files]
        if len(keys) != len(set(keys)) or len(paths) != len(set(paths)):
            raise EvaluationDataError("Ground truth 文件 key/path 必须唯一。")


@dataclass(frozen=True)
class EvidenceExcerpt:
    excerpt_id: str
    document_id: str
    document_name: str
    relative_path: str
    page_numbers: tuple[int, ...]
    text: str

    def __post_init__(self) -> None:
        for label, value in (("excerpt_id", self.excerpt_id),
                             ("document_id", self.document_id),
                             ("document_name", self.document_name),
                             ("relative_path", self.relative_path), ("text", self.text)):
            _nonempty(value, label)
        if not self.page_numbers or tuple(sorted(set(self.page_numbers))) != self.page_numbers:
            raise EvaluationDataError("EvidenceExcerpt 页码必须升序、唯一且非空。")
        if any(isinstance(page, bool) or page <= 0 for page in self.page_numbers):
            raise EvaluationDataError("EvidenceExcerpt 页码必须从 1 开始。")


@dataclass(frozen=True)
class EvidenceGroup:
    evidence_group_id: str
    excerpts: tuple[EvidenceExcerpt, ...]

    def __post_init__(self) -> None:
        _nonempty(self.evidence_group_id, "evidence_group_id")
        if not self.excerpts or len({item.excerpt_id for item in self.excerpts}) != len(self.excerpts):
            raise EvaluationDataError("EvidenceGroup excerpts 必须非空且 ID 唯一。")


@dataclass(frozen=True)
class GroundTruthCase:
    case_id: str
    question: str
    answerable: bool
    reference_answer: str
    evidence_groups: tuple[EvidenceGroup, ...]

    def __post_init__(self) -> None:
        for label, value in (("case_id", self.case_id), ("question", self.question),
                             ("reference_answer", self.reference_answer)):
            _nonempty(value, label)
        if self.answerable != bool(self.evidence_groups):
            raise EvaluationDataError("answerable 与 evidence_groups 是否为空不一致。")
        ids = [item.evidence_group_id for item in self.evidence_groups]
        if len(ids) != len(set(ids)):
            raise EvaluationDataError("单题 evidence_group_id 必须唯一。")


@dataclass(frozen=True)
class GroundTruthDocument:
    document_key: str
    document_id: str
    document_name: str
    relative_path: str
    file_hash: str
    cases: tuple[GroundTruthCase, ...]
    schema_version: Literal["ground_truth_document_v1"] = "ground_truth_document_v1"

    def __post_init__(self) -> None:
        if self.schema_version != "ground_truth_document_v1":
            raise EvaluationDataError("GroundTruthDocument schema 不受支持。")
        for label, value in (("document_key", self.document_key),
                             ("document_id", self.document_id),
                             ("document_name", self.document_name),
                             ("relative_path", self.relative_path)):
            _nonempty(value, label)
        _sha256(self.file_hash, "file_hash")


@dataclass(frozen=True)
class GroundTruthDataset:
    manifest: GroundTruthManifest
    documents: tuple[GroundTruthDocument, ...]


@dataclass(frozen=True)
class TestSetFileEntry:
    document_key: str
    json_path: str
    content_sha256: str
    case_count: int


@dataclass(frozen=True)
class TestSetManifest:
    test_set_version: str
    test_set_fingerprint: str
    review_status: DatasetReviewStatus
    system_version: str
    pipeline_id: PipelineId
    collection_name: str
    ground_truth_version: str
    ground_truth_fingerprint: str
    annotation_rule_version: str
    build_config: BuildConfig
    build_config_fingerprint: str
    files: tuple[TestSetFileEntry, ...]
    created_at: str
    reviewed_at: str | None
    superseded_by: str | None
    schema_version: Literal["test_set_manifest_v1"] = "test_set_manifest_v1"


@dataclass(frozen=True)
class EvidenceGroupMapping:
    evidence_group_id: str
    status: EvidenceMappingStatus
    acceptable_chunk_sets: tuple[tuple[str, ...], ...]

    def __post_init__(self) -> None:
        _nonempty(self.evidence_group_id, "evidence_group_id")
        if self.status not in {"mapped", "unmappable"}:
            raise EvaluationDataError("EvidenceMappingStatus 无效。")
        if self.status == "mapped" and not self.acceptable_chunk_sets:
            raise EvaluationDataError("mapped group 必须有 acceptable chunk set。")
        if self.status == "unmappable" and self.acceptable_chunk_sets:
            raise EvaluationDataError("unmappable group 不得有 acceptable chunk set。")
        normalized: list[tuple[str, ...]] = []
        for chunk_set in self.acceptable_chunk_sets:
            if not chunk_set:
                raise EvaluationDataError("acceptable chunk set 不得为空。")
            if tuple(sorted(set(chunk_set))) != chunk_set:
                raise EvaluationDataError("acceptable chunk set 内的 ID 必须升序且唯一。")
            normalized.append(chunk_set)
        if tuple(sorted(set(normalized))) != self.acceptable_chunk_sets:
            raise EvaluationDataError("acceptable_chunk_sets 必须升序且唯一。")
        sets = tuple(frozenset(item) for item in self.acceptable_chunk_sets)
        if any(left < right for left in sets for right in sets if left is not right):
            raise EvaluationDataError("acceptable_chunk_sets 不得包含非最小超集。")


@dataclass(frozen=True)
class TestCaseMapping:
    case_id: str
    evidence_groups: tuple[EvidenceGroupMapping, ...]


@dataclass(frozen=True)
class TestSetDocument:
    document_key: str
    document_id: str
    document_name: str
    relative_path: str
    file_hash: str
    cases: tuple[TestCaseMapping, ...]
    schema_version: Literal["test_set_document_v1"] = "test_set_document_v1"


@dataclass(frozen=True)
class TestSetDataset:
    manifest: TestSetManifest
    documents: tuple[TestSetDocument, ...]


@dataclass(frozen=True)
class MetricValue:
    status: MetricStatus
    numerator: float | None
    denominator: int | None
    value: float | None

    def __post_init__(self) -> None:
        if self.status == "not_applicable":
            if any(value is not None for value in (self.numerator, self.denominator, self.value)):
                raise EvaluationDataError("not_applicable metric 必须全部为空。")
            return
        if self.status != "evaluated" or self.numerator is None or self.denominator is None or self.value is None:
            raise EvaluationDataError("evaluated metric 字段不完整。")
        if self.denominator < 0 or not math.isfinite(self.numerator) or not math.isfinite(self.value):
            raise EvaluationDataError("MetricValue 数值无效。")


NOT_APPLICABLE = MetricValue("not_applicable", None, None, None)


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
class ExpectedEvidenceSnapshot:
    evidence_group_id: str
    mapping_status: EvidenceMappingStatus
    excerpts: tuple[EvidenceExcerpt, ...]
    acceptable_chunk_sets: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class RetrievedChunkSnapshot:
    rank: int
    chunk_id: str
    document_id: str
    document_name: str
    relative_path: str
    page_numbers: tuple[int, ...]
    chunk_index: int
    chunk_kind: Literal["text", "list", "table"] | None
    text: str
    distance: float
    similarity: float
    matched_evidence_group_ids: tuple[str, ...]
    relevant: bool
    cross_document: bool


@dataclass(frozen=True)
class CaseEvaluationFact:
    case_id: str
    status: CaseExecutionStatus
    question: str
    answerable: bool
    reference_answer: str
    expected_evidence: tuple[ExpectedEvidenceSnapshot, ...]
    retrieved_chunks: tuple[RetrievedChunkSnapshot, ...]
    unmappable_evidence_group_ids: tuple[str, ...]
    metrics: QuestionMetrics | None
    error: dict[str, Any] | None


@dataclass(frozen=True)
class EvaluationDocumentResult:
    run_id: str
    document_key: str
    document_id: str
    document_name: str
    relative_path: str
    file_hash: str
    cases: tuple[CaseEvaluationFact, ...]
    schema_version: Literal["evaluation_document_result_v1"] = "evaluation_document_result_v1"


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
class AggregateScope:
    scope: Literal["document", "overall"]
    document_id: str | None
    answerable_count: int
    unanswerable_count: int
    unmappable_group_count: int
    metrics: AggregateMetrics


@dataclass(frozen=True)
class EvaluationAggregate:
    run_id: str
    documents: tuple[AggregateScope, ...]
    overall: AggregateScope
    schema_version: Literal["evaluation_aggregate_v1"] = "evaluation_aggregate_v1"


@dataclass(frozen=True)
class EvaluationQueryConfig:
    top_k: int
    query_prefix: str
    document_filter: None = None
    question_normalization: Literal["strip_v1"] = "strip_v1"
    distance_order: Literal["ascending"] = "ascending"
    tie_break_order: Literal["chunk_id_ascending"] = "chunk_id_ascending"
    strict_top_k: Literal[True] = True


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
class RunDocumentEntry:
    document_key: str
    json_path: str
    json_sha256: str
    html_path: str
    html_sha256: str


@dataclass(frozen=True)
class EvaluationRunRecord:
    run_id: str
    status: EvaluationRunStatus
    system_version: str
    pipeline_id: PipelineId
    collection_name: str
    build_config: BuildConfig
    build_config_fingerprint: str
    query_config: EvaluationQueryConfig
    evaluation_protocol_version: str
    ground_truth: GroundTruthIdentity
    test_set: TestSetIdentity
    started_at: str
    completed_at: str
    documents: tuple[RunDocumentEntry, ...]
    aggregate_path: str
    aggregate_sha256: str
    error: dict[str, Any] | None = None
    schema_version: Literal["evaluation_run_v1"] = "evaluation_run_v1"


def evaluated(numerator: float, denominator: int, *, zero_value: float = 0.0) -> MetricValue:
    value = zero_value if denominator == 0 else numerator / denominator
    return MetricValue("evaluated", numerator, denominator, value)
