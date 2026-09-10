"""按 Table Slot 构建候选组并校验完整审计链。"""

from __future__ import annotations

import math

from experiments.table_extraction.domain.models.selection import (
    CandidateAdmissionResult,
    CandidateView,
    GroupingReport,
    SlotMatchEvaluation,
    TableGroup,
    TableSlot,
)


def _slot_index(slot: TableSlot) -> int:
    """从稳定 slot_id 取得 Docling 原生顺序。"""
    return int(slot.slot_id.removeprefix("slot_"))


def order_slots(slots: list[TableSlot]) -> list[TableSlot]:
    """按页面位置排序 eligible slot，并把 deferred slot 放在末尾。"""
    def key(slot: TableSlot) -> tuple[float, ...]:
        if slot.status == "eligible" and slot.bbox is not None and slot.page_number is not None:
            return (0, slot.page_number, slot.bbox.y0, slot.bbox.x0, _slot_index(slot))
        return (1, math.inf, math.inf, math.inf, _slot_index(slot))

    return sorted(slots, key=key)


def build_slot_groups(
    slots: list[TableSlot],
    candidates: list[CandidateView],
    admissions: list[CandidateAdmissionResult],
) -> list[TableGroup]:
    """为每个 slot 建立一个组，并只收纳唯一匹配该 slot 的候选。"""
    views = {candidate.candidate_id: candidate for candidate in candidates}
    members: dict[str, list[str]] = {slot.slot_id: [] for slot in slots}
    for result in admissions:
        if result.admission_decision == "admitted" and result.matched_slot_id is not None:
            members[result.matched_slot_id].append(result.candidate_id)

    groups: list[TableGroup] = []
    for index, slot in enumerate(slots, start=1):
        identifiers = sorted(
            members[slot.slot_id],
            key=lambda identifier: (
                views[identifier].tool,
                views[identifier].strategy or "",
                identifier,
            ),
        )
        if slot.status == "deferred":
            status, reason = "unresolved", slot.deferred_reason
        elif identifiers:
            status, reason = "ready_for_scoring", None
        else:
            status, reason = "unresolved", "no_admitted_candidate"
        groups.append(TableGroup(
            group_id=f"group_{index:03d}",
            slot_id=slot.slot_id,
            docling_table_ref=slot.docling_table_ref,
            page_number=slot.page_number,
            slot_bbox=slot.bbox,
            status=status,
            unresolved_reason=reason,
            member_candidate_ids=identifiers,
        ))
    return groups


def _unique(values: list[str], label: str) -> None:
    """保证一组模型标识不存在重复值。"""
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate {label}")


def validate_report_invariants(report: GroupingReport) -> None:
    """验证 slot、评价、准入决定和组之间的因果关系。"""
    slot_by_id = {slot.slot_id: slot for slot in report.table_slots}
    view_by_id = {view.candidate_id: view for view in report.candidate_views}
    admission_by_id = {item.candidate_id: item for item in report.candidate_admission_results}
    _unique([slot.slot_id for slot in report.table_slots], "slot_id")
    _unique([view.candidate_id for view in report.candidate_views], "candidate_id")
    _unique([item.candidate_id for item in report.candidate_admission_results], "admission candidate_id")
    _unique([group.group_id for group in report.groups], "group_id")
    _unique([group.slot_id for group in report.groups], "group slot_id")
    _unique([item.evaluation_id for item in report.slot_match_evaluations], "evaluation_id")

    if set(slot_by_id) != {group.slot_id for group in report.groups}:
        raise ValueError("every TableSlot must have exactly one TableGroup")
    if set(view_by_id) != set(admission_by_id):
        raise ValueError("every CandidateView must have exactly one admission result")

    evaluations_by_candidate: dict[str, list[SlotMatchEvaluation]] = {
        identifier: [] for identifier in view_by_id
    }
    for evaluation in report.slot_match_evaluations:
        candidate = view_by_id.get(evaluation.candidate_id)
        slot = slot_by_id.get(evaluation.slot_id)
        if not candidate or not slot:
            raise ValueError("evaluation refers to an unknown candidate or slot")
        if (
            candidate.processing_status != "comparable" or slot.status != "eligible"
            or candidate.page_number != slot.page_number
        ):
            raise ValueError("evaluation must connect comparable same-page objects")
        evaluations_by_candidate[evaluation.candidate_id].append(evaluation)

    for identifier, result in admission_by_id.items():
        candidate = view_by_id[identifier]
        related = evaluations_by_candidate[identifier]
        evaluated_ids = [item.slot_id for item in related]
        accepted_ids = [item.slot_id for item in related if item.decision == "accepted"]
        if result.evaluated_slot_ids != evaluated_ids or result.accepted_slot_ids != accepted_ids:
            raise ValueError("admission result does not match its slot evaluations")
        if candidate.processing_status == "deferred":
            if (
                result.processing_status != "deferred"
                or result.deferred_reason != candidate.deferred_reason
                or result.admission_decision is not None
                or result.matched_slot_id is not None
                or related
            ):
                raise ValueError("deferred candidate admission state is invalid")
        elif result.deferred_reason == "slot_source_page_failed":
            if result.processing_status != "deferred" or result.admission_decision is not None or related or result.matched_slot_id is not None:
                raise ValueError("failed slot page must defer admission without comparisons")
        elif result.processing_status != "completed" or result.deferred_reason is not None:
            raise ValueError("comparable candidate admission state is invalid")

    memberships: dict[str, list[str]] = {identifier: [] for identifier in view_by_id}
    for group in report.groups:
        slot = slot_by_id[group.slot_id]
        if (
            group.docling_table_ref != slot.docling_table_ref
            or group.page_number != slot.page_number
            or group.slot_bbox != slot.bbox
        ):
            raise ValueError("group metadata does not match its TableSlot")
        for identifier in group.member_candidate_ids:
            if identifier not in memberships:
                raise ValueError(f"group contains unknown candidate: {identifier}")
            memberships[identifier].append(group.slot_id)
        if slot.status == "deferred":
            if group.status != "unresolved" or group.unresolved_reason != slot.deferred_reason or group.member_candidate_ids:
                raise ValueError("deferred slot group state is invalid")
        elif group.member_candidate_ids:
            if group.status != "ready_for_scoring" or group.unresolved_reason is not None:
                raise ValueError("ready group state is invalid")
        elif group.status != "unresolved" or group.unresolved_reason != "no_admitted_candidate":
            raise ValueError("empty eligible slot group state is invalid")

    for identifier, result in admission_by_id.items():
        candidate_memberships = memberships[identifier]
        if result.admission_decision == "admitted":
            if result.matched_slot_id is None or candidate_memberships != [result.matched_slot_id]:
                raise ValueError("admitted candidate must belong to its unique matched slot")
            if result.accepted_slot_ids != [result.matched_slot_id]:
                raise ValueError("admitted candidate must have one accepted slot")
        elif candidate_memberships:
            raise ValueError("rejected or deferred candidate cannot belong to a group")
