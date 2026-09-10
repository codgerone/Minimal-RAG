"""校验分组产物并加载 ready group 的完整表格候选。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from experiments.table_extraction.domain.scoring.config import SUPPORTED_GROUPING_FORMAT_VERSION, TOOL_TIEBREAK_ORDER
from experiments.table_extraction.application.models import LoadedScoringInput
from experiments.table_extraction.domain.models.selection import TableGroup, TableSlot
from experiments.table_extraction.domain.models.tables import (
    BoundingBox,
    CoordinateTransform,
    GridPosition,
    TableArtifacts,
    TableCandidate,
    TableCell,
    TableRegion,
)


def _read_json(path: Path) -> dict[str, Any]:
    """读取顶层必须为对象的 UTF-8 JSON。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def _same_path(first: str | Path, second: str | Path) -> bool:
    """按 Windows 规范化规则比较两个绝对路径身份。"""
    left = os.path.normcase(str(Path(first).resolve()))
    right = os.path.normcase(str(Path(second).resolve()))
    return left == right


def _bbox(value: Any) -> BoundingBox | None:
    """把可用 bbox 对象转换为公共模型。"""
    if not isinstance(value, dict):
        return None
    try:
        return BoundingBox(*(float(value[key]) for key in ("x0", "y0", "x1", "y1")))
    except (KeyError, TypeError, ValueError):
        return None


def _slot(value: dict[str, Any]) -> TableSlot:
    """反序列化评分所需的 TableSlot 字段。"""
    return TableSlot(
        slot_id=str(value["slot_id"]),
        docling_table_ref=str(value["docling_table_ref"]),
        page_number=value.get("page_number"),
        page_width=value.get("page_width"),
        page_height=value.get("page_height"),
        bbox=_bbox(value.get("bbox")),
        status=value["status"],
        deferred_reason=value.get("deferred_reason"),
    )


def _group(value: dict[str, Any]) -> TableGroup:
    """反序列化评分所需的 TableGroup 字段。"""
    members = value.get("member_candidate_ids")
    if not isinstance(members, list) or any(not isinstance(item, str) for item in members):
        raise ValueError(f"invalid group members: {value.get('group_id')}")
    return TableGroup(
        group_id=str(value["group_id"]),
        slot_id=str(value["slot_id"]),
        docling_table_ref=str(value["docling_table_ref"]),
        page_number=value.get("page_number"),
        slot_bbox=_bbox(value.get("slot_bbox")),
        status=value["status"],
        unresolved_reason=value.get("unresolved_reason"),
        member_candidate_ids=list(members),
    )


def _transform(value: Any) -> CoordinateTransform | None:
    """反序列化候选 region 的坐标转换证据。"""
    if not isinstance(value, dict):
        return None
    return CoordinateTransform(
        source_system=str(value.get("source_system", "")),
        target_system=str(value.get("target_system", "pymupdf_page_top_left_pt_v1")),
        source_page_width=value.get("source_page_width"),
        source_page_height=value.get("source_page_height"),
        scale_x=value.get("scale_x"),
        scale_y=value.get("scale_y"),
        y_axis_flipped=bool(value.get("y_axis_flipped", False)),
        page_rotation=value.get("page_rotation"),
    )


def _region(value: dict[str, Any]) -> TableRegion:
    """反序列化一个候选表格来源区域。"""
    return TableRegion(
        page_number=value.get("page_number"),
        page_width=value.get("page_width"),
        page_height=value.get("page_height"),
        bbox=_bbox(value.get("bbox")),
        source_ref=str(value.get("source_ref", "")),
        coordinate_transform=_transform(value.get("coordinate_transform")),
    )


def _cell(value: dict[str, Any], index: int) -> TableCell:
    """反序列化一个物理单元格，保留可能由指标处理的异常值。"""
    roles = value.get("roles")
    return TableCell(
        cell_id=str(value.get("cell_id") or f"__invalid_cell_{index:04d}"),
        bbox=_bbox(value.get("bbox")),
        row_span=value.get("row_span"),
        col_span=value.get("col_span"),
        start_row_offset_idx=value.get("start_row_offset_idx"),
        end_row_offset_idx=value.get("end_row_offset_idx"),
        start_col_offset_idx=value.get("start_col_offset_idx"),
        end_col_offset_idx=value.get("end_col_offset_idx"),
        text=value.get("text"),
        roles=list(roles) if isinstance(roles, list) else [],
        span_source=value.get("span_source", "unavailable"),
        source_ref=str(value.get("source_ref", "")),
        source_refs=list(value.get("source_refs", [])),
    )


def _candidate(value: dict[str, Any]) -> TableCandidate:
    """反序列化公共候选，并把 cell 容器异常留给评分指标处理。"""
    warnings = list(value.get("warnings")) if isinstance(value.get("warnings"), list) else []
    raw_cells = value.get("cells")
    cells: list[TableCell] = []
    if not isinstance(raw_cells, list) or any(not isinstance(item, dict) for item in raw_cells):
        warnings.append("candidate_text_unreadable")
    else:
        cells = [_cell(item, index) for index, item in enumerate(raw_cells)]

    raw_regions = value.get("regions")
    regions = [_region(item) for item in raw_regions if isinstance(item, dict)] if isinstance(raw_regions, list) else []
    raw_uncovered = value.get("uncovered_grid_positions")
    uncovered = []
    if isinstance(raw_uncovered, list):
        for item in raw_uncovered:
            if isinstance(item, dict):
                uncovered.append(GridPosition(
                    row_index=item.get("row_index"),
                    column_index=item.get("column_index"),
                    reason=str(item.get("reason", "")),
                ))
    raw_artifacts = value.get("artifacts")
    artifacts = TableArtifacts(
        csv_file=raw_artifacts.get("csv_file") if isinstance(raw_artifacts, dict) else None,
        html_file=raw_artifacts.get("html_file") if isinstance(raw_artifacts, dict) else None,
    )
    return TableCandidate(
        candidate_id=str(value["candidate_id"]),
        tool=str(value["tool"]),
        strategy=value.get("strategy"),
        source_ref=str(value.get("source_ref", "")),
        regions=regions,
        row_count=value.get("row_count"),
        column_count=value.get("column_count"),
        x_boundaries=value.get("x_boundaries"),
        y_boundaries=value.get("y_boundaries"),
        cells=cells,
        uncovered_grid_positions=uncovered,
        artifacts=artifacts,
        warnings=warnings,
        unplaced_text=value.get('unplaced_text'),
    )


def _validated_group_facts(
    payload: dict[str, Any],
) -> tuple[list[TableGroup], dict[str, TableSlot], dict[str, dict[str, Any]]]:
    """校验 ready group、slot、view 和 admission 的闭合关系。"""
    raw_groups = payload.get("groups")
    raw_slots = payload.get("table_slots")
    raw_views = payload.get("candidate_views")
    raw_admissions = payload.get("candidate_admission_results")
    if not all(isinstance(value, list) for value in (raw_groups, raw_slots, raw_views, raw_admissions)):
        raise ValueError("grouping report collections are invalid")
    if any(not isinstance(value, dict) for values in (raw_groups, raw_slots, raw_views, raw_admissions) for value in values):
        raise ValueError("grouping report collection item must be an object")

    groups = [_group(value) for value in raw_groups]
    slots = [_slot(value) for value in raw_slots]
    slots_by_id = {item.slot_id: item for item in slots}
    views_by_id = {str(item["candidate_id"]): item for item in raw_views}
    admissions_by_id = {str(item["candidate_id"]): item for item in raw_admissions}
    if len(slots_by_id) != len(slots) or len(views_by_id) != len(raw_views) or len(admissions_by_id) != len(raw_admissions):
        raise ValueError("duplicate slot or candidate identity in grouping report")
    if len({group.group_id for group in groups}) != len(groups):
        raise ValueError("duplicate group_id in grouping report")
    if len({group.slot_id for group in groups}) != len(groups):
        raise ValueError("one slot appears in multiple groups")
    if {group.slot_id for group in groups} != set(slots_by_id):
        raise ValueError("grouping report must contain exactly one group per slot")

    ready = []
    seen_members: set[str] = set()
    for group in groups:
        slot = slots_by_id[group.slot_id]
        if group.docling_table_ref != slot.docling_table_ref:
            raise ValueError(f"group and slot source identity differ: {group.group_id}")
        if group.status == "unresolved":
            if group.member_candidate_ids:
                raise ValueError(f"unresolved group cannot contain members: {group.group_id}")
            continue
        if group.status != "ready_for_scoring":
            raise ValueError(f"unsupported group status: {group.group_id}")
        if not group.member_candidate_ids or len(set(group.member_candidate_ids)) != len(group.member_candidate_ids):
            raise ValueError(f"ready group must have unique members: {group.group_id}")
        if (
            slot.status != "eligible" or slot.bbox is None
            or group.slot_bbox != slot.bbox or group.page_number != slot.page_number
        ):
            raise ValueError(f"ready group does not match one eligible slot: {group.group_id}")
        for identifier in group.member_candidate_ids:
            if identifier in seen_members:
                raise ValueError(f"candidate belongs to multiple ready groups: {identifier}")
            seen_members.add(identifier)
            view = views_by_id.get(identifier)
            admission = admissions_by_id.get(identifier)
            if view is None or admission is None:
                raise ValueError(f"group member facts are missing: {identifier}")
            if view.get("processing_status") != "comparable":
                raise ValueError(f"deferred candidate appears in ready group: {identifier}")
            if admission.get("admission_decision") != "admitted" or admission.get("matched_slot_id") != group.slot_id:
                raise ValueError(f"non-admitted candidate appears in ready group: {identifier}")
        ready.append(group)
    return ready, slots_by_id, views_by_id


def load_scoring_input(source_pdf: Path, tools_output_root: Path) -> LoadedScoringInput:
    """加载并验证分组报告及其中所有 ready member 的完整候选。"""
    grouping_dir = tools_output_root.parent / "grouping" / source_pdf.stem
    manifest_path = grouping_dir / "manifest.json"
    report_path = grouping_dir / "groups.json"
    manifest = _read_json(manifest_path)
    payload = _read_json(report_path)
    if not _same_path(manifest.get("source_pdf", ""), source_pdf):
        raise ValueError("grouping manifest source_pdf does not match input PDF")
    if manifest.get("format_version") != SUPPORTED_GROUPING_FORMAT_VERSION:
        raise ValueError("unsupported grouping manifest format_version")
    if payload.get("format_version") != SUPPORTED_GROUPING_FORMAT_VERSION:
        raise ValueError("unsupported grouping report format_version")

    from .identity import digest, extraction_fingerprints
    if manifest.get('source_sha256') != digest(source_pdf):
        raise ValueError('grouping PDF content identity changed or is missing')
    if manifest.get('report_sha256') != digest(report_path):
        raise ValueError('grouping report content changed or identity is missing')
    if manifest.get('extraction_sha256') != extraction_fingerprints(tools_output_root, source_pdf.stem):
        raise ValueError('extraction inputs changed after grouping')
    groups, slots_by_id, views_by_id = _validated_group_facts(payload)
    if manifest.get("ready_group_count") != len(groups):
        raise ValueError("grouping manifest ready_group_count is inconsistent")
    file_cache: dict[Path, dict[str, dict[str, Any]]] = {}
    candidates: dict[str, TableCandidate] = {}
    for group in groups:
        for identifier in group.member_candidate_ids:
            view = views_by_id[identifier]
            tool, strategy = view.get("tool"), view.get("strategy")
            if tool not in TOOL_TIEBREAK_ORDER or not isinstance(strategy, str) or not strategy:
                raise ValueError(f"invalid ready member tool/strategy: {identifier}")
            path = tools_output_root / tool / source_pdf.stem / "normalized" / strategy / "tables.json"
            if path not in file_cache:
                table_payload = _read_json(path)
                raw_tables = table_payload.get("tables")
                if not isinstance(raw_tables, list) or any(not isinstance(item, dict) for item in raw_tables):
                    raise ValueError(f"invalid normalized tables: {path}")
                indexed = {str(item.get("candidate_id")): item for item in raw_tables}
                if len(indexed) != len(raw_tables):
                    raise ValueError(f"duplicate candidate_id in normalized tables: {path}")
                file_cache[path] = indexed
            raw_candidate = file_cache[path].get(identifier)
            if raw_candidate is None:
                raise ValueError(f"ready member not found in normalized tables: {identifier}")
            candidate = _candidate(raw_candidate)
            if (
                candidate.tool != tool or candidate.strategy != strategy
                or candidate.source_ref != str(view.get("source_ref", ""))
            ):
                raise ValueError(f"ready member identity drifted since grouping: {identifier}")
            candidates[identifier] = candidate

    return LoadedScoringInput(
        grouping_report_path=str(report_path.resolve()),
        groups=groups,
        slots_by_id=slots_by_id,
        candidates_by_id=candidates,
    )
