"""Deterministic V2 table admission and slot grouping."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Literal, TypeAlias

from rag.build_config import TableStrategy, ToolName
from rag.v2.common import BoundingBox, ProcessingWarning, enclosing_bbox
from rag.v2.table_models import TableCandidate


SlotStatus: TypeAlias = Literal["eligible", "deferred"]
CandidateStatus: TypeAlias = Literal["comparable", "deferred"]
EvaluationDecision: TypeAlias = Literal["accepted", "rejected"]
AdmissionStatus: TypeAlias = Literal["completed", "deferred"]
AdmissionDecision: TypeAlias = Literal["admitted", "rejected"]
GroupStatus: TypeAlias = Literal["ready_for_scoring", "unresolved"]
SlotDeferredReason: TypeAlias = Literal[
    "cross_page_slot", "missing_slot_provenance", "missing_slot_bbox",
    "slot_coordinate_conversion_failed", "invalid_slot_bbox", "invalid_slot_page_geometry",
]
CandidateDeferredReason: TypeAlias = Literal[
    "cross_page_candidate", "missing_candidate_region", "missing_candidate_bbox",
    "candidate_coordinate_conversion_failed", "invalid_candidate_bbox",
    "invalid_candidate_page_geometry",
]
AdmissionDeferredReason: TypeAlias = CandidateDeferredReason | Literal["slot_source_page_failed"]
GroupUnresolvedReason: TypeAlias = SlotDeferredReason | Literal["no_admitted_candidate"]


@dataclass(frozen=True)
class TableSlot:
    slot_id: str
    docling_table_ref: str
    page_number: int | None
    page_width: float | None
    page_height: float | None
    bbox: BoundingBox | None
    status: SlotStatus
    deferred_reason: SlotDeferredReason | None
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        complete = all(value is not None for value in (self.page_number, self.page_width, self.page_height, self.bbox))
        if self.status == "eligible":
            if not complete or self.deferred_reason is not None:
                raise ValueError("eligible slot 必须完整定位且无 deferred_reason。")
        elif self.deferred_reason is None:
            raise ValueError("deferred slot 必须有 deferred_reason。")


@dataclass(frozen=True)
class CandidateView:
    candidate_id: str
    tool: ToolName
    strategy: TableStrategy
    page_number: int | None
    page_width: float | None
    page_height: float | None
    bbox: BoundingBox | None
    processing_status: CandidateStatus
    deferred_reason: CandidateDeferredReason | None

    def __post_init__(self) -> None:
        complete = all(value is not None for value in (self.page_number, self.page_width, self.page_height, self.bbox))
        if self.processing_status == "comparable":
            if not complete or self.deferred_reason is not None:
                raise ValueError("comparable candidate 必须完整定位且无 deferred_reason。")
        elif self.deferred_reason is None:
            raise ValueError("deferred candidate 必须有 deferred_reason。")


@dataclass(frozen=True)
class SlotMatchMetrics:
    intersection_area: float
    union_area: float
    iou: float
    candidate_coverage: float
    slot_coverage: float

    def __post_init__(self) -> None:
        values = (self.intersection_area, self.union_area, self.iou, self.candidate_coverage, self.slot_coverage)
        if not all(math.isfinite(value) and value >= 0 for value in values):
            raise ValueError("SlotMatchMetrics 必须为有限非负数。")
        if self.union_area <= 0 or any(value > 1 for value in values[2:]):
            raise ValueError("SlotMatchMetrics 比例或 union_area 无效。")


@dataclass(frozen=True)
class SlotMatchEvaluation:
    evaluation_id: str
    candidate_id: str
    slot_id: str
    decision: EvaluationDecision
    reason_codes: tuple[str, ...]
    metrics: SlotMatchMetrics

    def __post_init__(self) -> None:
        if self.decision == "accepted":
            if self.reason_codes != ("bidirectional_coverage_passed",):
                raise ValueError("accepted evaluation 原因码无效。")
        elif not self.reason_codes or "bidirectional_coverage_passed" in self.reason_codes:
            raise ValueError("rejected evaluation 原因码无效。")


@dataclass(frozen=True)
class CandidateAdmissionResult:
    candidate_id: str
    processing_status: AdmissionStatus
    deferred_reason: AdmissionDeferredReason | None
    admission_decision: AdmissionDecision | None
    matched_slot_id: str | None
    evaluated_slot_ids: tuple[str, ...]
    accepted_slot_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.processing_status == "deferred":
            if (
                self.deferred_reason is None or self.admission_decision is not None
                or self.matched_slot_id is not None or self.evaluated_slot_ids
                or self.accepted_slot_ids
            ):
                raise ValueError("deferred admission 状态无效。")
        elif self.deferred_reason is not None or self.admission_decision is None:
            raise ValueError("completed admission 状态无效。")
        if self.admission_decision == "admitted":
            if self.matched_slot_id is None or self.accepted_slot_ids != (self.matched_slot_id,):
                raise ValueError("admitted candidate 必须唯一匹配一个 slot。")
        elif self.matched_slot_id is not None:
            raise ValueError("rejected candidate 不得有 matched_slot_id。")


@dataclass(frozen=True)
class TableGroup:
    group_id: str
    slot_id: str
    docling_table_ref: str
    page_number: int | None
    slot_bbox: BoundingBox | None
    status: GroupStatus
    unresolved_reason: GroupUnresolvedReason | None
    member_candidate_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.status == "ready_for_scoring":
            if not self.member_candidate_ids or self.unresolved_reason is not None:
                raise ValueError("ready group 必须有成员且无 unresolved_reason。")
        elif self.member_candidate_ids or self.unresolved_reason is None:
            raise ValueError("unresolved group 必须无成员且有 unresolved_reason。")


@dataclass(frozen=True)
class GroupingReport:
    table_slots: tuple[TableSlot, ...]
    candidate_views: tuple[CandidateView, ...]
    slot_match_evaluations: tuple[SlotMatchEvaluation, ...]
    candidate_admission_results: tuple[CandidateAdmissionResult, ...]
    groups: tuple[TableGroup, ...]
    warnings: tuple[ProcessingWarning, ...]
    schema_version: Literal["table_candidate_selection_v2"] = "table_candidate_selection_v2"

    def __post_init__(self) -> None:
        slots = {item.slot_id: item for item in self.table_slots}
        candidates = {item.candidate_id: item for item in self.candidate_views}
        admissions = {item.candidate_id: item for item in self.candidate_admission_results}
        if len(slots) != len(self.table_slots) or len(candidates) != len(self.candidate_views):
            raise ValueError("GroupingReport 包含重复 ID。")
        if set(candidates) != set(admissions):
            raise ValueError("每个 candidate 必须恰有一个 admission result。")
        if {item.slot_id for item in self.groups} != set(slots) or len(self.groups) != len(slots):
            raise ValueError("每个 slot 必须恰有一个 group。")


def candidate_view(candidate: TableCandidate) -> CandidateView:
    if not candidate.regions:
        return CandidateView(candidate.candidate_id, candidate.tool, candidate.strategy,
                             None, None, None, None, "deferred", "missing_candidate_region")
    page_numbers = {item.page_number for item in candidate.regions if item.page_number is not None}
    if len(page_numbers) != 1 or any(item.page_number is None for item in candidate.regions):
        return CandidateView(candidate.candidate_id, candidate.tool, candidate.strategy,
                             None, None, None, None, "deferred", "cross_page_candidate")
    page = next(iter(page_numbers))
    widths = tuple(item.page_width for item in candidate.regions)
    heights = tuple(item.page_height for item in candidate.regions)
    if any(value is None or not math.isfinite(value) or value <= 0 for value in widths + heights):
        return CandidateView(candidate.candidate_id, candidate.tool, candidate.strategy,
                             None, None, None, None, "deferred", "invalid_candidate_page_geometry")
    if max(widths) - min(widths) > 1.0 or max(heights) - min(heights) > 1.0:  # type: ignore[operator]
        return CandidateView(candidate.candidate_id, candidate.tool, candidate.strategy,
                             None, None, None, None, "deferred", "invalid_candidate_page_geometry")
    unavailable = tuple(item.unavailable_reason for item in candidate.regions if item.bbox is None)
    if unavailable:
        mapping = {
            "missing_bbox": "missing_candidate_bbox",
            "coordinate_conversion_failed": "candidate_coordinate_conversion_failed",
            "invalid_bbox": "invalid_candidate_bbox",
            "invalid_page_geometry": "invalid_candidate_page_geometry",
        }
        reason = mapping[unavailable[0]]
        return CandidateView(candidate.candidate_id, candidate.tool, candidate.strategy,
                             None, None, None, None, "deferred", reason)  # type: ignore[arg-type]
    boxes = tuple(item.bbox for item in candidate.regions if item.bbox is not None)
    box = enclosing_bbox(boxes)
    if box is None or box.area <= 0:
        return CandidateView(candidate.candidate_id, candidate.tool, candidate.strategy,
                             None, None, None, None, "deferred", "invalid_candidate_bbox")
    return CandidateView(candidate.candidate_id, candidate.tool, candidate.strategy,
                         page, widths[0], heights[0], box, "comparable", None)  # type: ignore[arg-type]


def slot_match_metrics(candidate: BoundingBox, slot: BoundingBox) -> SlotMatchMetrics:
    intersection_width = max(0.0, min(candidate.x1, slot.x1) - max(candidate.x0, slot.x0))
    intersection_height = max(0.0, min(candidate.y1, slot.y1) - max(candidate.y0, slot.y0))
    intersection = intersection_width * intersection_height
    union = candidate.area + slot.area - intersection
    if candidate.area <= 0 or slot.area <= 0 or union <= 0:
        raise ValueError("slot_match_metrics 要求正面积 bbox。")
    return SlotMatchMetrics(
        intersection, union, intersection / union,
        intersection / candidate.area, intersection / slot.area,
    )


def evaluate_slot_matches(
    candidates: tuple[CandidateView, ...], slots: tuple[TableSlot, ...],
    candidate_threshold: float = 0.65, slot_threshold: float = 0.77,
    comparison_epsilon: float = 1e-12,
) -> tuple[SlotMatchEvaluation, ...]:
    evaluations: list[SlotMatchEvaluation] = []
    for candidate in candidates:
        if candidate.processing_status != "comparable":
            continue
        assert candidate.bbox is not None
        for slot in slots:
            if slot.status != "eligible" or slot.page_number != candidate.page_number:
                continue
            assert slot.bbox is not None
            metrics = slot_match_metrics(candidate.bbox, slot.bbox)
            reasons: list[str] = []
            if metrics.candidate_coverage + comparison_epsilon < candidate_threshold:
                reasons.append("candidate_coverage_below_minimum")
            if metrics.slot_coverage + comparison_epsilon < slot_threshold:
                reasons.append("slot_coverage_below_minimum")
            accepted = not reasons
            evaluations.append(SlotMatchEvaluation(
                f"evaluation_{len(evaluations) + 1:04d}", candidate.candidate_id,
                slot.slot_id, "accepted" if accepted else "rejected",
                ("bidirectional_coverage_passed",) if accepted else tuple(reasons), metrics,
            ))
    return tuple(evaluations)


def decide_candidate_admissions(
    candidates: tuple[CandidateView, ...], evaluations: tuple[SlotMatchEvaluation, ...],
    failed_docling_pages: frozenset[int] = frozenset(),
) -> tuple[CandidateAdmissionResult, ...]:
    by_candidate: dict[str, list[SlotMatchEvaluation]] = defaultdict(list)
    for evaluation in evaluations:
        by_candidate[evaluation.candidate_id].append(evaluation)
    results: list[CandidateAdmissionResult] = []
    for candidate in candidates:
        if candidate.processing_status == "deferred":
            results.append(CandidateAdmissionResult(
                candidate.candidate_id, "deferred", candidate.deferred_reason,
                None, None, (), (), (),
            ))
            continue
        if candidate.page_number in failed_docling_pages:
            results.append(CandidateAdmissionResult(
                candidate.candidate_id, "deferred", "slot_source_page_failed",
                None, None, (), (), (),
            ))
            continue
        related = by_candidate[candidate.candidate_id]
        evaluated = tuple(item.slot_id for item in related)
        accepted = tuple(item.slot_id for item in related if item.decision == "accepted")
        if not related:
            decision, matched, reason = "rejected", None, "no_eligible_table_slot_on_page"
        elif not accepted:
            decision, matched, reason = "rejected", None, "no_table_slot_passed_bidirectional_coverage"
        elif len(accepted) == 1:
            decision, matched, reason = "admitted", accepted[0], "matched_single_table_slot"
        else:
            decision, matched, reason = "rejected", None, "multiple_table_slots_passed_bidirectional_coverage"
        results.append(CandidateAdmissionResult(
            candidate.candidate_id, "completed", None, decision, matched,
            evaluated, accepted, (reason,),
        ))
    return tuple(results)


def order_slots(slots: tuple[TableSlot, ...]) -> tuple[TableSlot, ...]:
    original = {slot.slot_id: index for index, slot in enumerate(slots)}
    return tuple(sorted(slots, key=lambda slot: (
        0 if slot.status == "eligible" else 1,
        slot.page_number if slot.page_number is not None else math.inf,
        slot.bbox.y0 if slot.bbox is not None else math.inf,
        slot.bbox.x0 if slot.bbox is not None else math.inf,
        original[slot.slot_id],
    )))


def build_slot_groups(
    slots: tuple[TableSlot, ...], candidates: tuple[CandidateView, ...],
    admissions: tuple[CandidateAdmissionResult, ...],
) -> tuple[TableGroup, ...]:
    views = {item.candidate_id: item for item in candidates}
    members: dict[str, list[str]] = {slot.slot_id: [] for slot in slots}
    for result in admissions:
        if result.admission_decision == "admitted" and result.matched_slot_id is not None:
            members[result.matched_slot_id].append(result.candidate_id)
    groups: list[TableGroup] = []
    for slot in order_slots(slots):
        identifiers = tuple(sorted(members[slot.slot_id], key=lambda identifier: (
            views[identifier].tool, views[identifier].strategy, identifier,
        )))
        if slot.status == "deferred":
            status, reason = "unresolved", slot.deferred_reason
        elif identifiers:
            status, reason = "ready_for_scoring", None
        else:
            status, reason = "unresolved", "no_admitted_candidate"
        suffix = slot.slot_id.removeprefix("slot_")
        groups.append(TableGroup(
            f"group_{suffix}", slot.slot_id, slot.docling_table_ref,
            slot.page_number, slot.bbox, status, reason, identifiers,
        ))
    return tuple(groups)
