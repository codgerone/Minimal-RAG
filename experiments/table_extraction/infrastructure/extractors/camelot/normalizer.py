"""将 Camelot 原子 cell 网格适配为公共表格候选。"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any

from experiments.table_extraction.infrastructure.extractors.pymupdf.config import SPAN_BOUNDARY_TOLERANCE
from experiments.table_extraction.infrastructure.pdf.coordinates import PageGeometry, from_bottom_left, page_geometries
from experiments.table_extraction.domain.models.tables import BoundingBox, GridPosition, TableArtifacts, TableCandidate, TableCell, TableRegion


GridIndex = tuple[int, int]


@dataclass
class CamelotNormalizationResult:
    """保存一个 flavor 的候选及其 CSV 二维文本。"""

    candidates: list[TableCandidate]
    rows_by_candidate: dict[str, list[list[str | None]]]


class DisjointSet:
    """维护由缺失共享边界连接的原子 cell 分量。"""

    def __init__(self, items: list[GridIndex]) -> None:
        """为每个原子 cell 初始化独立集合。"""
        self.parents = {item: item for item in items}

    def find(self, item: GridIndex) -> GridIndex:
        """返回集合根，并压缩查询路径。"""
        parent = self.parents[item]
        if parent != item:
            self.parents[item] = self.find(parent)
        return self.parents[item]

    def union(self, first: GridIndex, second: GridIndex) -> None:
        """合并两个相邻原子 cell 所在集合。"""
        first_root, second_root = self.find(first), self.find(second)
        if first_root != second_root:
            self.parents[second_root] = first_root


def _page_number(table: Any) -> int | None:
    """读取 Camelot 表格页码并转换为整数。"""
    try:
        return int(getattr(table, "page", None))
    except (TypeError, ValueError):
        return None


def _cluster(values: list[float]) -> list[float]:
    """按容差聚类原子 cell 边界，并返回簇中位数。"""
    if not values:
        return []
    clusters: list[list[float]] = [[value] for value in sorted(values)]
    index = 1
    while index < len(clusters):
        if clusters[index][0] - clusters[index - 1][-1] <= SPAN_BOUNDARY_TOLERANCE:
            clusters[index - 1].extend(clusters.pop(index))
        else:
            index += 1
    return [float(median(cluster)) for cluster in clusters]


def _cell_bbox(cell: Any, geometry: PageGeometry | None) -> tuple[BoundingBox | None, Any | None]:
    """将 Camelot 左下原点的原子 cell bbox 转换为公共坐标。"""
    if geometry is None:
        return None, None
    try:
        return from_bottom_left(
            float(cell.x1), float(cell.y1), float(cell.x2), float(cell.y2),
            geometry, "camelot_bottom_left_pt",
        )
    except (AttributeError, TypeError, ValueError):
        return None, None


def _components(raw_cells: dict[GridIndex, Any], warnings: list[str]) -> list[set[GridIndex]]:
    """由相邻原子 cell 同时缺失的共享边界构造连通分量。"""
    disjoint_set = DisjointSet(list(raw_cells))
    for (row, column), cell in raw_cells.items():
        right = (row, column + 1)
        if right in raw_cells:
            missing_here = getattr(cell, "right", None) is False
            missing_there = getattr(raw_cells[right], "left", None) is False
            if missing_here and missing_there:
                disjoint_set.union((row, column), right)
            elif missing_here != missing_there:
                warnings.append(f"原子 cell ({row}, {column}) 与 ({row}, {column + 1}) 的共享竖线状态矛盾。")
        below = (row + 1, column)
        if below in raw_cells:
            missing_here = getattr(cell, "bottom", None) is False
            missing_there = getattr(raw_cells[below], "top", None) is False
            if missing_here and missing_there:
                disjoint_set.union((row, column), below)
            elif missing_here != missing_there:
                warnings.append(f"原子 cell ({row}, {column}) 与 ({row + 1}, {column}) 的共享横线状态矛盾。")
    grouped: dict[GridIndex, set[GridIndex]] = {}
    for index in raw_cells:
        grouped.setdefault(disjoint_set.find(index), set()).add(index)
    return list(grouped.values())


def _rectangle(component: set[GridIndex]) -> tuple[int, int, int, int] | None:
    """验证连通分量是否完整覆盖一个矩形原子网格区域。"""
    rows = [item[0] for item in component]
    columns = [item[1] for item in component]
    start_row, end_row = min(rows), max(rows) + 1
    start_column, end_column = min(columns), max(columns) + 1
    expected = {(row, column) for row in range(start_row, end_row) for column in range(start_column, end_column)}
    if component == expected:
        return start_row, end_row, start_column, end_column
    return None


def _union_bbox(boxes: list[BoundingBox]) -> BoundingBox:
    """计算多个已转换原子 bbox 的最小外接矩形。"""
    return BoundingBox(
        min(box.x0 for box in boxes), min(box.y0 for box in boxes),
        max(box.x1 for box in boxes), max(box.y1 for box in boxes),
    )


def _component_text(component: set[GridIndex], raw_cells: dict[GridIndex, Any], anchor: GridIndex, warnings: list[str], *, confirmed: bool = True) -> str | None:
    """保留锚点文本，并在延续位置含额外文本时合并且提示核验。"""
    ordered = sorted(component)
    anchor_text = getattr(raw_cells[anchor], "text", None)
    extra = [getattr(raw_cells[index], "text", None) for index in ordered if index != anchor and str(getattr(raw_cells[index], "text", "")).strip()]
    if not extra:
        return anchor_text
    values = [value for value in [anchor_text, *extra] if value is not None and str(value).strip()]
    warnings.append(f"合并区域锚点 {anchor} 的延续位置包含额外文本，已按阅读顺序合并。")
    return "\n".join(dict.fromkeys(values) if confirmed else values)


def _rows(candidate: TableCandidate) -> list[list[str | None]]:
    """将物理 cell 的左上锚点展开为 CSV 文本矩阵。"""
    rows = [[None for _ in range(candidate.column_count or 0)] for _ in range(candidate.row_count or 0)]
    for cell in candidate.cells:
        if cell.start_row_offset_idx is not None and cell.start_col_offset_idx is not None:
            rows[cell.start_row_offset_idx][cell.start_col_offset_idx] = cell.text
    return rows


def normalize_flavor(tables: list[Any], flavor: str, pdf_path: str, *, table_indices: list[int] | None = None) -> CamelotNormalizationResult:
    """将一个 Camelot flavor 的原子 cell 网格转换为公共候选。"""
    geometries = page_geometries(pdf_path)
    candidates: list[TableCandidate] = []
    rows_by_candidate: dict[str, list[list[str | None]]] = {}
    for table_index, table in enumerate(tables, start=1):
        table_index = table_indices[table_index - 1] + 1 if table_indices is not None else table_index
        warnings: list[str] = []
        page_number = _page_number(table)
        geometry = geometries.get(page_number)
        candidate_id = f"camelot_{flavor}_p{page_number or 0:02d}_t{table_index:02d}"
        raw_cells = {
            (row, column): cell
            for row, row_cells in enumerate(getattr(table, "cells", []))
            for column, cell in enumerate(row_cells)
        }
        converted = {index: _cell_bbox(cell, geometry) for index, cell in raw_cells.items()}
        atomic_boxes = [bbox for bbox, _ in converted.values() if bbox is not None]
        if geometry is None:
            warnings.append("表格缺少对应 PyMuPDF 页面几何，所有 bbox 均为 null。")
        elif not atomic_boxes:
            warnings.append("未取得可用 Camelot 原子 cell bbox。")
        x_boundaries = _cluster([value for box in atomic_boxes for value in (box.x0, box.x1)]) if atomic_boxes else None
        y_boundaries = _cluster([value for box in atomic_boxes for value in (box.y0, box.y1)]) if atomic_boxes else None

        cells: list[TableCell] = []
        covered: set[GridIndex] = set()
        for component in sorted(_components(raw_cells, warnings), key=lambda item: min(item)):
            rectangle = _rectangle(component)
            anchor = min(component)
            boxes = [converted[index][0] for index in component]
            valid = rectangle is not None and all(box is not None for box in boxes)
            if not valid:
                warnings.append(f"原子 cell 分量 {sorted(component)} 无法恢复为带几何的矩形合并单元格。")
                start_row = end_row = start_column = end_column = row_span = col_span = None
                bbox = None
                source = "unavailable"
            else:
                start_row, end_row, start_column, end_column = rectangle
                row_span, col_span = end_row - start_row, end_column - start_column
                bbox = _union_bbox([box for box in boxes if box is not None])
                source = "edge_inferred"
                covered.update(component)
            cells.append(TableCell(
                cell_id=f"{candidate_id}_c{anchor[0] + 1:02d}_{anchor[1] + 1:02d}", bbox=bbox,
                row_span=row_span, col_span=col_span,
                start_row_offset_idx=start_row, end_row_offset_idx=end_row,
                start_col_offset_idx=start_column, end_col_offset_idx=end_column,
                text=_component_text(component, raw_cells, anchor, warnings, confirmed=rectangle is not None), roles=["unknown"], span_source=source,
                source_ref=f"raw/{flavor}.json#/tables/{table_index - 1}/cells/{anchor[0]}/{anchor[1]}",
                source_refs=[f"raw/{flavor}.json#/tables/{table_index - 1}/cells/{r}/{c}" for r, c in sorted(component)],
            ))
        row_count = len(getattr(table, "cells", [])) or None
        column_count = max((len(row) for row in getattr(table, "cells", [])), default=0) or None
        region_bbox = _union_bbox(atomic_boxes) if atomic_boxes else None
        transform = next((transform for _, transform in converted.values() if transform is not None), None)
        uncovered = [
            GridPosition(row, column, "未检测到有效物理 cell，且未被已知跨度覆盖。")
            for row in range(row_count or 0)
            for column in range(column_count or 0)
            if (row, column) not in covered
        ]
        candidate = TableCandidate(
            candidate_id=candidate_id, tool="camelot", strategy=flavor,
            source_ref=f"raw/{flavor}.json#/tables/{table_index - 1}",
            regions=[TableRegion(page_number or 0, geometry.width if geometry else None, geometry.height if geometry else None, region_bbox, f"raw/{flavor}.json#/tables/{table_index - 1}", transform)],
            row_count=row_count, column_count=column_count,
            x_boundaries=x_boundaries, y_boundaries=y_boundaries, cells=cells,
            uncovered_grid_positions=uncovered,
            artifacts=TableArtifacts(f"tables/page_{page_number or 0:02d}_table_{table_index:02d}.csv", f"tables/page_{page_number or 0:02d}_table_{table_index:02d}.html"),
            warnings=warnings,
        )
        candidates.append(candidate)
        rows_by_candidate[candidate_id] = _rows(candidate)
    return CamelotNormalizationResult(candidates, rows_by_candidate)
