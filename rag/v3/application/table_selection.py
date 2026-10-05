"""V3 candidate geometry, bidirectional admission and slot grouping."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import replace

from rag.v3.contracts.geometry import enclosing_bbox
from rag.v3.contracts.documents import BoundingBox
from rag.v3.contracts.tables import (
    CandidateAdmissionResult, CandidateView, SlotMatchEvaluation,
    SlotMatchMetrics, TableCandidate, TableGroup, TableSlot,
)

def _page_box(box: BoundingBox, width: float, height: float,
              tolerance: float) -> BoundingBox | None:
    if (box.x0 < -tolerance or box.y0 < -tolerance
            or box.x1 > width + tolerance or box.y1 > height + tolerance):
        return None
    clipped = BoundingBox(max(0.0, box.x0), max(0.0, box.y0),
                          min(width, box.x1), min(height, box.y1))
    return clipped if clipped.area > 0 else None


def normalize_slot(slot: TableSlot, actual: tuple[float, float] | None,
                   *, page_size_tolerance: float = 1.0,
                   page_bounds_tolerance: float = 1e-6) -> TableSlot:
    if slot.status != "eligible":
        return slot
    if (actual is None or slot.page_width is None or slot.page_height is None
            or abs(slot.page_width - actual[0]) > page_size_tolerance
            or abs(slot.page_height - actual[1]) > page_size_tolerance):
        return replace(slot, status="deferred", deferred_reason="invalid_slot_page_geometry",
                       bbox=None)
    if slot.bbox is None:
        return replace(slot, status="deferred", deferred_reason="missing_slot_bbox")
    clipped = _page_box(slot.bbox, *actual, page_bounds_tolerance)
    if clipped is None:
        return replace(slot, status="deferred", deferred_reason="invalid_slot_bbox", bbox=None)
    return replace(slot, bbox=clipped)


def candidate_view(candidate: TableCandidate,
                   actual: tuple[float, float] | None = None,
                   *, page_size_tolerance: float = 1.0,
                   page_bounds_tolerance: float = 1e-6,
                   require_actual: bool = False) -> CandidateView:
    if not candidate.regions:
        return CandidateView(candidate.candidate_id, candidate.tool, candidate.strategy,
                             None, None, None, None, "deferred", "missing_candidate_region")
    page_numbers = {item.page_number for item in candidate.regions if item.page_number is not None}
    if len(page_numbers) != 1 or any(item.page_number is None for item in candidate.regions):
        return CandidateView(candidate.candidate_id, candidate.tool, candidate.strategy,
                             None, None, None, None, "deferred", "cross_page_candidate")
    page = next(iter(page_numbers))
    if require_actual and actual is None:
        return CandidateView(candidate.candidate_id, candidate.tool, candidate.strategy,
                             None, None, None, None, "deferred", "invalid_candidate_page_geometry")
    widths = tuple(item.page_width for item in candidate.regions)
    heights = tuple(item.page_height for item in candidate.regions)
    if any(value is None or not math.isfinite(value) or value <= 0 for value in widths + heights):
        return CandidateView(candidate.candidate_id, candidate.tool, candidate.strategy,
                             None, None, None, None, "deferred", "invalid_candidate_page_geometry")
    if (max(widths) - min(widths) > page_size_tolerance
            or max(heights) - min(heights) > page_size_tolerance):  # type: ignore[operator]
        return CandidateView(candidate.candidate_id, candidate.tool, candidate.strategy,
                             None, None, None, None, "deferred", "invalid_candidate_page_geometry")
    if actual is not None and (abs(widths[0] - actual[0]) > page_size_tolerance
                               or abs(heights[0] - actual[1]) > page_size_tolerance):
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
    if actual is not None:
        box = _page_box(box, *actual, page_bounds_tolerance)
        if box is None:
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
