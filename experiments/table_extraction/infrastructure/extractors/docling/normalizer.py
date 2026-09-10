"""将 Docling 原生表格适配为跨工具公共候选模型。"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any

from experiments.table_extraction.infrastructure.extractors.pymupdf.config import SPAN_BOUNDARY_TOLERANCE
from experiments.table_extraction.infrastructure.pdf.coordinates import PageGeometry, from_bottom_left, from_top_left, page_geometries
from experiments.table_extraction.domain.models.tables import BoundingBox, GridPosition, TableArtifacts, TableCandidate, TableCell, TableRegion


@dataclass
class DoclingNormalizationResult:
    """保存规范化候选及其 CSV 所需的二维文本视图。"""

    candidates: list[TableCandidate]
    rows_by_candidate: dict[str, list[list[str | None]]]


def _canonical_bbox(value: Any, geometry: PageGeometry | None, warnings: list[str]) -> tuple[BoundingBox | None, Any | None]:
    """按 Docling bbox 的原点转换为公共左上坐标。"""
    if value is None:
        return None, None
    if geometry is None:
        warnings.append("bbox 缺少对应 PyMuPDF 页面几何，无法转换。")
        return None, None
    try:
        left, top, right, bottom = (float(value.l), float(value.t), float(value.r), float(value.b))
    except (AttributeError, TypeError, ValueError):
        warnings.append("bbox 字段不可读取。")
        return None, None

    origin = getattr(value, "coord_origin", None)
    origin_name = getattr(origin, "value", origin)
    if origin_name == "BOTTOMLEFT":
        return from_bottom_left(left, bottom, right, top, geometry, "docling_BOTTOMLEFT")
    if origin_name == "TOPLEFT":
        return from_top_left(BoundingBox(left, top, right, bottom), geometry, "docling_TOPLEFT")
    warnings.append(f"不支持的 Docling bbox 坐标原点：{origin_name!r}。")
    return None, None


def _roles(cell: Any) -> list[str]:
    """将 Docling 单元格角色标记转换为公共角色列表。"""
    roles = [name for name in ("column_header", "row_header", "row_section") if getattr(cell, name, False)]
    return roles or ["body"]


def _cluster(values: list[float]) -> list[float]:
    """按公共容差聚类边界，并以簇中位数作为边界值。"""
    if not values:
        return []
    merged: list[list[float]] = [[value] for value in sorted(values)]
    index = 1
    while index < len(merged):
        if merged[index][0] - merged[index - 1][-1] <= SPAN_BOUNDARY_TOLERANCE:
            merged[index - 1].extend(merged.pop(index))
        else:
            index += 1
    return [float(median(cluster)) for cluster in merged]


def _rows(candidate: TableCandidate) -> list[list[str | None]]:
    """将左上锚点单元格展开为 CSV 所需的二维文本视图。"""
    rows = [[None for _ in range(candidate.column_count or 0)] for _ in range(candidate.row_count or 0)]
    for cell in candidate.cells:
        if cell.span_source != "unavailable" and cell.start_row_offset_idx is not None and cell.start_col_offset_idx is not None:
            rows[cell.start_row_offset_idx][cell.start_col_offset_idx] = cell.text
    return rows


def _cell_span(raw_cell: Any, cell_index: int, warnings: list[str]) -> tuple[int | None, int | None, int | None, int | None, int | None, int | None, str]:
    """校验原生逻辑位置与跨度，并返回公共单元格字段。"""
    start_row = getattr(raw_cell, "start_row_offset_idx", None)
    end_row = getattr(raw_cell, "end_row_offset_idx", None)
    start_col = getattr(raw_cell, "start_col_offset_idx", None)
    end_col = getattr(raw_cell, "end_col_offset_idx", None)
    row_span = getattr(raw_cell, "row_span", None)
    col_span = getattr(raw_cell, "col_span", None)
    valid_offsets = all(type(value) is int for value in (start_row, end_row, start_col, end_col))
    valid_offsets = valid_offsets and 0 <= start_row < end_row and 0 <= start_col < end_col
    expected_span = (end_row - start_row, end_col - start_col) if valid_offsets else None
    if valid_offsets and (row_span, col_span) in ((None, None), expected_span):
        return expected_span[0], expected_span[1], start_row, end_row, start_col, end_col, "native"
    warnings.append(f"单元格 {cell_index} 的跨度与 offset 不一致。")
    return row_span, col_span, start_row, end_row, start_col, end_col, "unavailable"


def normalize_document(document: Any, pdf_path: str, *, table_indices: list[int] | None = None) -> DoclingNormalizationResult:
    """将 DoclingDocument 中的原生表格转换为公共候选。"""
    geometries = page_geometries(pdf_path)
    candidates: list[TableCandidate] = []
    rows_by_candidate: dict[str, list[list[str | None]]] = {}

    for table_index, table in enumerate(document.tables):
        table_index = table_indices[table_index] if table_indices is not None else table_index
        warnings: list[str] = []
        provenances = list(getattr(table, "prov", []) or [])
        page_numbers = [item.page_no for item in provenances if isinstance(getattr(item, "page_no", None), int)]
        unique_pages = set(page_numbers)
        first_page = page_numbers[0] if page_numbers else 0
        candidate_id = f"docling_default_p{first_page:02d}_t{table_index + 1:02d}"

        regions: list[TableRegion] = []
        for region_index, provenance in enumerate(provenances):
            page_number = getattr(provenance, "page_no", None)
            geometry = geometries.get(page_number)
            native_page = getattr(document, 'pages', {}).get(page_number)
            size = getattr(native_page, 'size', None)
            if geometry and size and (abs(size.width - geometry.width) > 1 or abs(size.height - geometry.height) > 1):
                warnings.append('docling_page_size_mismatch')
                geometry = None
            bbox, transform = _canonical_bbox(getattr(provenance, "bbox", None), geometry, warnings)
            regions.append(TableRegion(
                page_number=page_number if isinstance(page_number, int) else 0,
                page_width=geometry.width if geometry else None,
                page_height=geometry.height if geometry else None,
                bbox=bbox,
                source_ref=f"raw/document.json#/tables/{table_index}/prov/{region_index}",
                coordinate_transform=transform,
            ))

        cell_geometry = geometries.get(next(iter(unique_pages))) if len(unique_pages) == 1 else None
        if 'docling_page_size_mismatch' in warnings:
            cell_geometry = None
        if len(unique_pages) > 1:
            warnings.append("表格 provenance 涉及多个页面；Docling 未提供 cell-to-page 关联，cell bbox 已设为 null。")
        elif len(unique_pages) == 0:
            warnings.append("表格缺少有效 provenance page_no，cell bbox 已设为 null。")

        cells: list[TableCell] = []
        raw_cells = list(getattr(getattr(table, "data", None), "table_cells", []) or [])
        for cell_index, raw_cell in enumerate(raw_cells):
            row_span, col_span, start_row, end_row, start_col, end_col, source = _cell_span(raw_cell, cell_index, warnings)
            bbox = _canonical_bbox(getattr(raw_cell, "bbox", None), cell_geometry, warnings)[0] if cell_geometry else None
            cells.append(TableCell(
                cell_id=f"{candidate_id}_c{cell_index + 1:03d}", bbox=bbox,
                row_span=row_span, col_span=col_span,
                start_row_offset_idx=start_row, end_row_offset_idx=end_row,
                start_col_offset_idx=start_col, end_col_offset_idx=end_col,
                text=getattr(raw_cell, "text", None), roles=_roles(raw_cell), span_source=source,
                source_ref=f"raw/document.json#/tables/{table_index}/data/table_cells/{cell_index}",
            ))

        row_count = max((cell.end_row_offset_idx or 0 for cell in cells if cell.span_source != "unavailable"), default=0) or None
        column_count = max((cell.end_col_offset_idx or 0 for cell in cells if cell.span_source != "unavailable"), default=0) or None
        if len(unique_pages) == 1 and cell_geometry is not None:
            bboxes = [cell.bbox for cell in cells if cell.bbox is not None] + [region.bbox for region in regions if region.bbox is not None]
            x_boundaries = _cluster([value for box in bboxes for value in (box.x0, box.x1)])
            y_boundaries = _cluster([value for box in bboxes for value in (box.y0, box.y1)])
        else:
            x_boundaries = y_boundaries = None

        if row_count is None or column_count is None:
            warnings.append("unknown_grid_dimensions")
        covered = {
            (row, column)
            for cell in cells
            if cell.span_source != "unavailable" and cell.start_row_offset_idx is not None
            for row in range(cell.start_row_offset_idx, cell.end_row_offset_idx)
            for column in range(cell.start_col_offset_idx, cell.end_col_offset_idx)
        }
        uncovered = [
            GridPosition(row, column, "未检测到物理单元格，且未被已知跨度覆盖。")
            for row in range(row_count or 0)
            for column in range(column_count or 0)
            if (row, column) not in covered
        ]
        candidate = TableCandidate(
            candidate_id=candidate_id, tool="docling", strategy="default",
            source_ref=f"raw/document.json#/tables/{table_index}", regions=regions,
            row_count=row_count, column_count=column_count,
            x_boundaries=x_boundaries, y_boundaries=y_boundaries, cells=cells,
            uncovered_grid_positions=uncovered,
            artifacts=TableArtifacts(
                f"tables/page_{first_page:02d}_table_{table_index + 1:02d}.csv",
                f"tables/page_{first_page:02d}_table_{table_index + 1:02d}.html",
            ),
            warnings=warnings,
        )
        candidates.append(candidate)
        rows_by_candidate[candidate_id] = _rows(candidate)
    return DoclingNormalizationResult(candidates, rows_by_candidate)
