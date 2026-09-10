"""表格候选准入、分组和审计输出的数据模型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from experiments.table_extraction.domain.models.tables import BoundingBox


SlotStatus = Literal["eligible", "deferred"]
CandidateStatus = Literal["comparable", "deferred"]
EvaluationDecision = Literal["accepted", "rejected"]
AdmissionStatus = Literal["completed", "deferred"]
AdmissionDecision = Literal["admitted", "rejected"]
GroupStatus = Literal["ready_for_scoring", "unresolved"]


@dataclass(frozen=True)
class TableSlot:
    """表示 Docling 文档结构中需要填充的一张表格。"""

    slot_id: str
    docling_table_ref: str
    page_number: int | None
    page_width: float | None
    page_height: float | None
    bbox: BoundingBox | None
    status: SlotStatus
    deferred_reason: str | None


@dataclass(frozen=True)
class CandidateView:
    """保存候选准入判断和人工审核所需的轻量信息。"""

    candidate_id: str
    tool: str
    strategy: str | None
    source_ref: str
    html_file: str | None
    page_number: int | None
    page_width: float | None
    page_height: float | None
    bbox: BoundingBox | None
    processing_status: CandidateStatus
    deferred_reason: str | None


@dataclass(frozen=True)
class SlotMatchMetrics:
    """记录 candidate 与 Table Slot 的几何重叠指标。"""

    intersection_area: float
    union_area: float
    iou: float
    candidate_coverage: float
    slot_coverage: float


@dataclass(frozen=True)
class SlotMatchEvaluation:
    """记录一个同页可比较 candidate-slot 组合的准入评价。"""

    evaluation_id: str
    candidate_id: str
    slot_id: str
    decision: EvaluationDecision
    reason_codes: list[str]
    metrics: SlotMatchMetrics


@dataclass(frozen=True)
class CandidateAdmissionResult:
    """汇总一个候选在全部同页 Table Slot 上的最终准入决定。"""

    candidate_id: str
    processing_status: AdmissionStatus
    deferred_reason: str | None
    admission_decision: AdmissionDecision | None
    matched_slot_id: str | None
    evaluated_slot_ids: list[str]
    accepted_slot_ids: list[str]
    reason_codes: list[str]


@dataclass(frozen=True)
class TableGroup:
    """表示一个 Table Slot 及其通过准入的候选集合。"""

    group_id: str
    slot_id: str
    docling_table_ref: str
    page_number: int | None
    slot_bbox: BoundingBox | None
    status: GroupStatus
    unresolved_reason: str | None
    member_candidate_ids: list[str]


@dataclass(frozen=True)
class GroupingReport:
    """汇总准入、分组及其完整审计证据。"""

    format_version: str
    groups: list[TableGroup]
    table_slots: list[TableSlot]
    candidate_views: list[CandidateView]
    candidate_admission_results: list[CandidateAdmissionResult]
    slot_match_evaluations: list[SlotMatchEvaluation]
    warnings: list[str]


