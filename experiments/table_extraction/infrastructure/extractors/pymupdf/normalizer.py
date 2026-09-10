"""将 PyMuPDF 原始快照转换为统一候选模型，并推断隐含跨度。"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any

from experiments.table_extraction.infrastructure.extractors.pymupdf.config import SPAN_BOUNDARY_TOLERANCE
from experiments.table_extraction.infrastructure.pdf.coordinates import PageGeometry, from_top_left
from experiments.table_extraction.domain.models.tables import BoundingBox, GridPosition, TableArtifacts, TableCandidate, TableCell, TableRegion


@dataclass
class NormalizedStrategyResult:
    """保存一个策略的规范化候选及其 CSV 所需的原始行。"""

    candidates: list[TableCandidate]
    rows_by_candidate: dict[str, list[list[str | None]]]


def _bbox(values: list[float] | None) -> BoundingBox | None:
    """将四元坐标数组转换为统一边界对象。"""
    if values is None:
        return None
    return BoundingBox(*[float(value) for value in values])


def _cluster_boundaries(values: list[float]) -> list[float]:
    """按容差聚类边界坐标，并以每组中位数作为原子网格边界。"""
    if not values:
        return []
    clusters: list[list[float]] = [[value] for value in sorted(values)]
    merged: list[list[float]] = [clusters[0]]
    for cluster in clusters[1:]:
        if cluster[0] - merged[-1][-1] <= SPAN_BOUNDARY_TOLERANCE:
            merged[-1].extend(cluster)
        else:
            merged.append(cluster)
    return [float(median(cluster)) for cluster in merged]


def _nearest_index(boundaries: list[float], value: float) -> int | None:
    """返回容差内最近边界的索引；没有匹配时返回空。"""
    if not boundaries:
        return None
    index = min(range(len(boundaries)), key=lambda item: abs(boundaries[item] - value))
    return index if abs(boundaries[index] - value) <= SPAN_BOUNDARY_TOLERANCE else None


def _positive_overlap(left: BoundingBox, right: BoundingBox) -> bool:
    """判断两个物理单元格是否存在正面积重叠。"""
    return min(left.x1, right.x1) > max(left.x0, right.x0) and min(left.y1, right.y1) > max(left.y0, right.y0)


def _cell_roles(cell_bbox: BoundingBox, header: dict[str, Any]) -> list[str]:
    """根据非外置表头区域为单元格标记列标题或正文角色。"""
    header_bbox = _bbox(header.get("bbox"))
    if not header.get("external") and header_bbox and _positive_overlap(cell_bbox, header_bbox):
        return ["column_header"]
    return ["body"]


def _cell_coordinates(raw_table: dict[str, Any]) -> list[tuple[int, int, BoundingBox, str | None]]:
    """从原始行单元格中收集去重后的物理单元格和其首次出现位置。"""
    texts = raw_table.get("extract", [])
    seen: set[tuple[float, float, float, float]] = set()
    cells: list[tuple[int, int, BoundingBox, str | None]] = []
    for row_index, row in enumerate(raw_table.get("rows", [])):
        for column_index, raw_bbox in enumerate(row.get("cells", [])):
            bbox = _bbox(raw_bbox)
            if bbox is None:
                continue
            key = (bbox.x0, bbox.y0, bbox.x1, bbox.y1)
            if key in seen:
                continue
            seen.add(key)
            text = texts[row_index][column_index] if row_index < len(texts) and column_index < len(texts[row_index]) else None
            cells.append((row_index, column_index, bbox, text))
    return cells


def _boundaries(raw_table: dict[str, Any], cells: list[tuple[int, int, BoundingBox, str | None]]) -> tuple[list[float], list[float]]:
    """基于表格和物理单元格边缘建立原子行、列边界。"""
    table_bbox = _bbox(raw_table.get("bbox"))
    x_values = [value for _, _, bbox, _ in cells for value in (bbox.x0, bbox.x1)]
    y_values = [value for _, _, bbox, _ in cells for value in (bbox.y0, bbox.y1)]
    if table_bbox:
        x_values.extend([table_bbox.x0, table_bbox.x1])
        y_values.extend([table_bbox.y0, table_bbox.y1])
    return _cluster_boundaries(x_values), _cluster_boundaries(y_values)


def _infer_cells(raw_table: dict[str, Any], candidate_id: str, raw_ref: str = "") -> tuple[list[TableCell], list[float], list[float], list[str]]:
    """通过单元格 bbox 映射原子网格，恢复行列跨度并记录异常。"""
    raw_cells = _cell_coordinates(raw_table)
    x_boundaries, y_boundaries = _boundaries(raw_table, raw_cells)
    warnings: list[str] = []
    overlapping_indexes = {
        index
        for index, (_, _, bbox, _) in enumerate(raw_cells)
        for other_index, (_, _, other_bbox, _) in enumerate(raw_cells)
        if index != other_index and _positive_overlap(bbox, other_bbox)
    }
    if overlapping_indexes:
        warnings.append("存在正面积重叠的物理单元格，相关单元格未推断跨度。")

    cells: list[TableCell] = []
    for index, (raw_row, raw_column, bbox, text) in enumerate(raw_cells):
        source_ref = f"{raw_ref}/rows/{raw_row}/cells/{raw_column}"
        x0, x1 = _nearest_index(x_boundaries, bbox.x0), _nearest_index(x_boundaries, bbox.x1)
        y0, y1 = _nearest_index(y_boundaries, bbox.y0), _nearest_index(y_boundaries, bbox.y1)
        valid = index not in overlapping_indexes and None not in (x0, x1, y0, y1) and x0 < x1 and y0 < y1
        if valid:
            cells.append(TableCell(
                cell_id=f"{candidate_id}_c{index + 1:03d}", bbox=bbox,
                row_span=y1 - y0, col_span=x1 - x0,
                start_row_offset_idx=y0, end_row_offset_idx=y1,
                start_col_offset_idx=x0, end_col_offset_idx=x1,
                text=text, roles=_cell_roles(bbox, raw_table.get("header", {})),
                span_source="geometry_inferred", source_ref=source_ref,
            ))
        else:
            warnings.append(f"单元格 ({raw_row}, {raw_column}) 无法映射到原子网格。")
            cells.append(TableCell(
                cell_id=f"{candidate_id}_c{index + 1:03d}", bbox=bbox,
                row_span=None, col_span=None,
                start_row_offset_idx=None, end_row_offset_idx=None,
                start_col_offset_idx=None, end_col_offset_idx=None,
                text=text, roles=_cell_roles(bbox, raw_table.get("header", {})),
                span_source="unavailable", source_ref=source_ref,
            ))
    return cells, x_boundaries, y_boundaries, warnings


def _uncovered_positions(row_count: int | None, column_count: int | None, cells: list[TableCell]) -> list[GridPosition]:
    """列出逻辑网格中既无物理单元格也未被跨度覆盖的位置。"""
    if row_count is None or column_count is None:
        return []
    covered: set[tuple[int, int]] = set()
    for cell in cells:
        if None in (cell.start_row_offset_idx, cell.end_row_offset_idx, cell.start_col_offset_idx, cell.end_col_offset_idx):
            continue
        for row in range(cell.start_row_offset_idx, min(cell.end_row_offset_idx, row_count)):
            for column in range(cell.start_col_offset_idx, min(cell.end_col_offset_idx, column_count)):
                covered.add((row, column))
    return [
        GridPosition(row, column, "未检测到物理单元格，且未被已推断跨度覆盖。")
        for row in range(row_count) for column in range(column_count)
        if (row, column) not in covered
    ]


def normalize_strategy(raw_strategy: dict[str, Any]) -> NormalizedStrategyResult:
    """将一个策略的原始快照转换为统一候选与阅读视图。"""
    strategy = raw_strategy["strategy"]
    candidates: list[TableCandidate] = []
    rows_by_candidate: dict[str, list[list[str | None]]] = {}
    for page_index, page in enumerate(raw_strategy["pages"]):
        page_index = page.get("_source_page_index", page_index)
        page_number = page["page_number"]
        for table_index, raw_table in enumerate(page["tables"]):
            candidate_id = f"pymupdf_{strategy}_p{page_number:02d}_t{table_index + 1:02d}"
            cells, x_boundaries, y_boundaries, warnings = _infer_cells(raw_table, candidate_id, f"raw/{strategy}.json#/pages/{page_index}/tables/{table_index}")
            row_count = raw_table.get("row_count")
            column_count = raw_table.get("column_count")
            geometry = PageGeometry(page_number, page["bbox"][2], page["bbox"][3], page.get("rotation", 0), page.get("has_crop_offset", False))
            canonical_bbox, transform = from_top_left(_bbox(raw_table.get("bbox")) or BoundingBox(0, 0, 0, 0), geometry, "pymupdf_page_top_left_pt_v1")
            if canonical_bbox is None:
                warnings.append("表格 bbox 未通过公共坐标校验。")
            for cell in cells:
                if cell.bbox is not None:
                    cell.bbox = from_top_left(cell.bbox, geometry)[0]
            if (len(raw_table.get('rows', [])) != row_count
                    or len(raw_table.get('extract', [])) != row_count
                    or any(len(row.get('cells', [])) != column_count for row in raw_table.get('rows', []))
                    or any(len(row) != column_count for row in raw_table.get('extract', []))):
                warnings.append('raw_matrix_shape_mismatch')
            candidate = TableCandidate(
                candidate_id=candidate_id, tool="pymupdf", strategy=strategy,
                source_ref=f"raw/{strategy}.json#/pages/{page_index}/tables/{table_index}",
                regions=[TableRegion(page_number, geometry.width, geometry.height, canonical_bbox, f"raw/{strategy}.json#/pages/{page_index}", transform)],
                row_count=row_count, column_count=column_count,
                x_boundaries=x_boundaries if canonical_bbox is not None else None,
                y_boundaries=y_boundaries if canonical_bbox is not None else None, cells=cells,
                uncovered_grid_positions=_uncovered_positions(row_count, column_count, cells),
                artifacts=TableArtifacts(
                    csv_file=f"tables/page_{page_number:02d}_table_{table_index + 1:02d}.csv",
                    html_file=f"tables/page_{page_number:02d}_table_{table_index + 1:02d}.html",
                ),
                warnings=warnings,
            )
            candidates.append(candidate)
            rows_by_candidate[candidate_id] = raw_table.get("extract", [])
    return NormalizedStrategyResult(candidates, rows_by_candidate)
