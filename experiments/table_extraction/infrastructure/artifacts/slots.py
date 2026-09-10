"""校验 Docling 权威输入并从原生 TableItem 建立 Table Slot。"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

from experiments.table_extraction.infrastructure.pdf.coordinates import PageGeometry
from experiments.table_extraction.domain.geometry import enclosing_bbox, validate_bbox
from experiments.table_extraction.domain.models.selection import TableSlot
from experiments.table_extraction.domain.models.tables import BoundingBox


def _same_path(first: Path, second: Path) -> bool:
    """按当前操作系统的路径大小写规则比较两个绝对路径。"""
    return os.path.normcase(str(first.resolve())) == os.path.normcase(str(second.resolve()))


def _deferred_slot(
    index: int,
    reason: str,
    page: PageGeometry | None = None,
) -> TableSlot:
    """构造保留原生引用的 deferred Table Slot。"""
    return TableSlot(
        slot_id=f"slot_{index + 1:03d}",
        docling_table_ref=f"#/tables/{index}",
        page_number=page.page_number if page else None,
        page_width=page.width if page else None,
        page_height=page.height if page else None,
        bbox=None,
        status="deferred",
        deferred_reason=reason,
    )


def _docling_bbox(value: Any, page: PageGeometry) -> tuple[BoundingBox | None, str | None]:
    """把 Docling provenance bbox 转换并校验为公共坐标。"""
    if page.rotation != 0 or page.has_crop_offset:
        return None, "slot_coordinate_conversion_failed"
    if not isinstance(value, dict):
        return None, "slot_coordinate_conversion_failed"
    try:
        left, top, right, bottom = (float(value[key]) for key in ("l", "t", "r", "b"))
    except (KeyError, TypeError, ValueError):
        return None, "slot_coordinate_conversion_failed"
    if not all(math.isfinite(item) for item in (left, top, right, bottom)):
        return None, "invalid_slot_bbox"
    origin = value.get("coord_origin")
    if origin == "TOPLEFT":
        box = BoundingBox(left, top, right, bottom)
    elif origin == "BOTTOMLEFT":
        box = BoundingBox(left, page.height - top, right, page.height - bottom)
    else:
        return None, "slot_coordinate_conversion_failed"
    valid = validate_bbox(box, page.width, page.height)
    return (valid, None) if valid else (None, "invalid_slot_bbox")


def _build_slot(index: int, table: Any, pages: dict[int, PageGeometry]) -> tuple[TableSlot, str | None]:
    """从一个原生 TableItem 建立 eligible 或 deferred slot。"""
    if not isinstance(table, dict):
        return _deferred_slot(index, "missing_slot_provenance"), None
    provenance = table.get("prov")
    if not isinstance(provenance, list) or not provenance:
        return _deferred_slot(index, "missing_slot_provenance"), None

    page_numbers = [item.get("page_no") if isinstance(item, dict) else None for item in provenance]
    if any(isinstance(number, bool) or not isinstance(number, int) or number not in pages for number in page_numbers):
        return _deferred_slot(index, "invalid_slot_page_geometry"), None
    unique_pages = set(page_numbers)
    if len(unique_pages) != 1:
        return _deferred_slot(index, "cross_page_slot"), None
    page = pages[page_numbers[0]]

    if any(not isinstance(item, dict) or item.get("bbox") is None for item in provenance):
        return _deferred_slot(index, "missing_slot_bbox", page), None
    boxes: list[BoundingBox] = []
    for item in provenance:
        box, reason = _docling_bbox(item["bbox"], page)
        if reason:
            return _deferred_slot(index, reason, page), None
        assert box is not None
        boxes.append(box)
    merged = enclosing_bbox(boxes)
    assert merged is not None
    slot = TableSlot(
        slot_id=f"slot_{index + 1:03d}",
        docling_table_ref=f"#/tables/{index}",
        page_number=page.page_number,
        page_width=page.width,
        page_height=page.height,
        bbox=merged,
        status="eligible",
        deferred_reason=None,
    )
    self_ref = table.get("self_ref")
    warning = None if self_ref in (None, slot.docling_table_ref) else (
        f"Docling table self_ref mismatch: expected {slot.docling_table_ref}, got {self_ref}"
    )
    return slot, warning


def load_table_slots(
    source_pdf: Path,
    tools_output_root: Path,
    pages: dict[int, PageGeometry],
) -> tuple[list[TableSlot], list[str]]:
    """验证 Docling manifest/document，并按原生顺序建立全部 Table Slot。"""
    docling_dir = tools_output_root / "docling" / source_pdf.stem
    manifest_path = docling_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"Docling manifest not found: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid Docling manifest: {error}") from error
    if not isinstance(manifest, dict) or manifest.get("status") != "success":
        raise ValueError("Docling manifest status must be success")
    manifest_source = manifest.get("source_pdf")
    if not isinstance(manifest_source, str) or not _same_path(Path(manifest_source), source_pdf):
        raise ValueError("Docling manifest source_pdf does not match the requested PDF")

    from .identity import verify_extraction_source
    provenance_warnings = verify_extraction_source(docling_dir, source_pdf)
    raw_relative = manifest.get("raw_document_path")
    if not isinstance(raw_relative, str):
        raise ValueError("Docling manifest raw_document_path is missing")
    raw_path = (docling_dir / raw_relative).resolve()
    if docling_dir.resolve() not in raw_path.parents or not raw_path.is_file():
        raise ValueError(f"Docling raw document not found: {raw_path}")
    try:
        document = json.loads(raw_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid Docling raw document: {error}") from error
    tables = document.get("tables") if isinstance(document, dict) else None
    if not isinstance(tables, list):
        raise ValueError("Docling raw document tables must be a list")

    slots: list[TableSlot] = []
    warnings: list[str] = list(provenance_warnings)
    for index, table in enumerate(tables):
        slot, warning = _build_slot(index, table, pages)
        slots.append(slot)
        if warning:
            warnings.append(warning)
    return slots, warnings

