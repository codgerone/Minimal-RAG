"""加载四工具规范化候选并建立准入使用的轻量视图。"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from experiments.table_extraction.infrastructure.pdf.coordinates import PageGeometry
from experiments.table_extraction.domain.admission_config import PAGE_SIZE_TOLERANCE_PT, TARGET_COORDINATE_SYSTEM
from experiments.table_extraction.domain.geometry import bbox_from_dict, enclosing_bbox, validate_bbox
from experiments.table_extraction.domain.models.selection import CandidateView, TableSlot
from experiments.table_extraction.domain.models.tables import BoundingBox


TOOLS = ("pymupdf", "camelot", "unstructured", "docling")


def _base(candidate: dict[str, Any]) -> dict[str, Any]:
    """提取所有 CandidateView 状态共用的候选身份和审核字段。"""
    artifacts = candidate.get("artifacts")
    html_file = artifacts.get("html_file") if isinstance(artifacts, dict) else None
    return {
        "candidate_id": str(candidate["candidate_id"]),
        "tool": str(candidate["tool"]),
        "strategy": candidate.get("strategy"),
        "source_ref": str(candidate.get("source_ref", "")),
        "html_file": str(html_file) if html_file else None,
    }


def _deferred(
    base: dict[str, Any],
    reason: str,
    page: PageGeometry | None = None,
    bbox: BoundingBox | None = None,
) -> CandidateView:
    """构造带明确原因的 deferred 候选视图。"""
    return CandidateView(
        **base,
        page_number=page.page_number if page else None,
        page_width=page.width if page else None,
        page_height=page.height if page else None,
        bbox=bbox,
        processing_status="deferred",
        deferred_reason=reason,
    )


def candidate_view(candidate: dict[str, Any], pages: dict[int, PageGeometry]) -> CandidateView:
    """校验候选 regions，并将同页区域归并为一个公共 bbox。"""
    base = _base(candidate)
    regions = candidate.get("regions")
    if not isinstance(regions, list) or not regions or any(not isinstance(item, dict) for item in regions):
        return _deferred(base, "missing_candidate_region")

    page_numbers = [item.get("page_number") for item in regions]
    if any(isinstance(number, bool) or not isinstance(number, int) or number not in pages for number in page_numbers):
        return _deferred(base, "invalid_candidate_page_geometry")
    unique_pages = set(page_numbers)
    if len(unique_pages) != 1:
        return _deferred(base, "cross_page_candidate")
    page = pages[page_numbers[0]]
    if page.rotation != 0 or page.has_crop_offset:
        return _deferred(base, "candidate_coordinate_conversion_failed", page)

    if any(item.get("bbox") is None for item in regions):
        return _deferred(base, "missing_candidate_bbox", page)
    for item in regions:
        transform = item.get("coordinate_transform")
        if not isinstance(transform, dict) or transform.get("target_system") != TARGET_COORDINATE_SYSTEM:
            return _deferred(base, "candidate_coordinate_conversion_failed", page)

    boxes: list[BoundingBox] = []
    for item in regions:
        try:
            width, height = float(item.get("page_width")), float(item.get("page_height"))
        except (TypeError, ValueError):
            return _deferred(base, "invalid_candidate_page_geometry", page)
        if (
            not math.isfinite(width) or not math.isfinite(height)
            or width <= 0 or height <= 0
            or abs(width - page.width) > PAGE_SIZE_TOLERANCE_PT
            or abs(height - page.height) > PAGE_SIZE_TOLERANCE_PT
        ):
            return _deferred(base, "invalid_candidate_page_geometry", page)
        raw_box = bbox_from_dict(item.get("bbox"))
        if raw_box is None:
            return _deferred(base, "invalid_candidate_bbox", page)
        box = validate_bbox(raw_box, page.width, page.height)
        if box is None:
            return _deferred(base, "invalid_candidate_bbox", page)
        boxes.append(box)

    merged = enclosing_bbox(boxes)
    assert merged is not None
    return CandidateView(
        **base,
        page_number=page.page_number,
        page_width=page.width,
        page_height=page.height,
        bbox=merged,
        processing_status="comparable",
        deferred_reason=None,
    )


def _read_strategy(path: Path, tool: str, pages: dict[int, PageGeometry]) -> list[CandidateView]:
    """读取并验证一个工具策略的完整 tables.json。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("tables"), list):
        raise ValueError("tables must be a list")
    strategy = path.parent.name
    if payload.get("tool") != tool or payload.get("strategy") != strategy:
        raise ValueError(f"top-level tool/strategy must be {tool}/{strategy}")
    views: list[CandidateView] = []
    for index, candidate in enumerate(payload["tables"]):
        if not isinstance(candidate, dict):
            raise ValueError(f"tables[{index}] must be an object")
        identifier = candidate.get("candidate_id")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError(f"tables[{index}].candidate_id is invalid")
        if candidate.get("tool") != tool or candidate.get("strategy") != strategy:
            raise ValueError(f"candidate {identifier} tool/strategy must be {tool}/{strategy}")
        views.append(candidate_view(candidate, pages))
    return views


def load_candidate_views(
    output_root: Path,
    pdf_stem: str,
    pages: dict[int, PageGeometry],
    *, source_pdf: Path | None = None,
) -> tuple[list[CandidateView], list[str], dict[str, int]]:
    """加载全部可用策略；Docling 缺失时报错，其他工具缺失时告警。"""
    views: list[CandidateView] = []
    warnings: list[str] = []
    counts = {tool: 0 for tool in TOOLS}
    identifiers: set[str] = set()

    for tool in TOOLS:
        normalized = output_root / tool / pdf_stem / "normalized"
        paths = sorted(normalized.glob("*/tables.json")) if normalized.is_dir() else []
        if not paths:
            message = f"missing normalized input: {normalized}"
            if tool == "docling":
                raise ValueError(message)
            warnings.append(message)
            continue
        if source_pdf is not None:
            from .identity import verify_extraction_source
            try:
                warnings.extend(verify_extraction_source(normalized.parent, source_pdf))
            except (OSError, ValueError) as error:
                if tool == 'docling': raise
                warnings.append(f'{tool}: invalid extraction source: {error}')
                continue
        expected = {'pymupdf': {'lines','lines_strict','text'}, 'camelot': {'lattice','stream','network','hybrid'},
                    'docling': {'default'}, 'unstructured': {'hi_res'}}[tool]
        for missing in sorted(expected - {path.parent.name for path in paths}):
            warnings.append(f'{tool}/{missing}: missing normalized strategy')
        for path in paths:
            try:
                loaded = _read_strategy(path, tool, pages)
            except (OSError, ValueError, json.JSONDecodeError) as error:
                message = f"failed to load {path}: {error}"
                if tool == "docling":
                    raise ValueError(message) from error
                warnings.append(message)
                continue
            for view in loaded:
                if view.candidate_id in identifiers:
                    raise ValueError(f"duplicate candidate_id: {view.candidate_id}")
                identifiers.add(view.candidate_id)
            views.extend(loaded)
            counts[tool] += len(loaded)

    tool_order = {tool: index for index, tool in enumerate(TOOLS)}
    views.sort(key=lambda item: (tool_order[item.tool], item.strategy or "", item.candidate_id))
    return views, warnings, counts


def validate_docling_baselines(slots: list[TableSlot], candidates: list[CandidateView], *, unavailable_refs: set[str] | None = None) -> None:
    """确保每个原生 Table Slot 恰有一个 Docling 规范化候选基线。"""
    refs = [item.source_ref for item in candidates if item.tool == "docling"]
    for slot in slots:
        if slot.docling_table_ref in (unavailable_refs or set()):
            continue
        expected = f"raw/document.json{slot.docling_table_ref}"
        if refs.count(expected) != 1:
            raise ValueError(f"Docling slot must map to one normalized candidate: {slot.slot_id}")
