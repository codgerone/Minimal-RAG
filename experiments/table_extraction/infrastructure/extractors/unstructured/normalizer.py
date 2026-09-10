"""将 Unstructured Table Element 的 HTML 转换为公共表格候选。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bs4 import BeautifulSoup

from experiments.table_extraction.infrastructure.pdf.coordinates import from_pixel_space, page_geometries
from experiments.table_extraction.domain.models.tables import GridPosition, TableArtifacts, TableCandidate, TableCell, TableRegion


@dataclass
class UnstructuredNormalizationResult:
    """保存规范化候选及其二维文本视图。"""

    candidates: list[TableCandidate]
    rows_by_candidate: dict[str, list[list[str | None]]]


def _span(tag: Any, name: str) -> int | None:
    """读取正整数 HTML span 属性，缺失时使用 1。"""
    value = tag.get(name, 1)
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _rows(candidate: TableCandidate) -> list[list[str | None]]:
    """生成 CSV 所需的二维锚点文本视图。"""
    rows = [[None for _ in range(candidate.column_count or 0)] for _ in range(candidate.row_count or 0)]
    for cell in candidate.cells:
        if cell.start_row_offset_idx is not None and cell.start_col_offset_idx is not None:
            rows[cell.start_row_offset_idx][cell.start_col_offset_idx] = cell.text
    return rows


def normalize_elements(elements: list[dict[str, Any]], pdf_path: str, *, element_indices: list[int] | None = None, table_numbers: dict[int, int] | None = None) -> UnstructuredNormalizationResult:
    """将序列化 Element 列表中的 Table 适配为公共候选。"""
    geometries = page_geometries(pdf_path)
    candidates: list[TableCandidate] = []
    rows_by_candidate: dict[str, list[list[str | None]]] = {}
    table_number = 0
    for element_index, element in enumerate(elements):
        element_index = element_indices[element_index] if element_indices is not None else element_index
        if element.get("type") != "Table":
            continue
        table_number = table_numbers[element_index] if table_numbers is not None else table_number + 1
        metadata = element.get("metadata") or {}
        page = metadata.get("page_number") if isinstance(metadata.get("page_number"), int) else 0
        candidate_id = f"unstructured_hi_res_p{page:02d}_t{table_number:02d}"
        warnings: list[str] = []
        html = metadata.get("text_as_html")
        cells: list[TableCell] = []
        covered: set[tuple[int, int]] = set()
        if isinstance(html, str) and html.strip():
            soup = BeautifulSoup(html, "html.parser")
            for row_index, row in enumerate(soup.find_all("tr")):
                for tag in row.find_all(["td", "th"], recursive=False):
                    column = 0
                    while (row_index, column) in covered:
                        column += 1
                    row_span, col_span = _span(tag, "rowspan"), _span(tag, "colspan")
                    if row_span is None or col_span is None:
                        warnings.append(f"第 {row_index} 行存在非法 span。")
                        cells.append(TableCell(f"{candidate_id}_c{len(cells)+1:03d}", None, None, None, None, None, None, None, tag.get_text(" ", strip=True), ["column_header" if tag.name == "th" else "body"], "unavailable", f"raw/elements.json#/{element_index}/metadata/text_as_html"))
                        continue
                    positions = [(r, c) for r in range(row_index, row_index + row_span) for c in range(column, column + col_span)]
                    if any(position in covered for position in positions):
                        warnings.append(f"第 {row_index} 行存在重叠 span。")
                        cells.append(TableCell(f"{candidate_id}_c{len(cells)+1:03d}", None, None, None,
                            None, None, None, None, tag.get_text(" ", strip=True),
                            ["column_header" if tag.name == "th" else "body"], "unavailable",
                            f"raw/elements.json#/{element_index}/metadata/text_as_html"))
                        continue
                    covered.update(positions)
                    cells.append(TableCell(f"{candidate_id}_c{len(cells)+1:03d}", None, row_span, col_span, row_index, row_index + row_span, column, column + col_span, tag.get_text(" ", strip=True), ["column_header" if tag.name == "th" else "body"], "native", f"raw/elements.json#/{element_index}/metadata/text_as_html"))
        else:
            warnings.append("Table Element 未提供 metadata.text_as_html。")
        row_count = max((cell.end_row_offset_idx or 0 for cell in cells), default=0) or None
        column_count = max((cell.end_col_offset_idx or 0 for cell in cells), default=0) or None
        uncovered = [GridPosition(r, c, "未检测到物理单元格，且未被已知跨度覆盖。") for r in range(row_count or 0) for c in range(column_count or 0) if (r, c) not in covered]
        geometry = geometries.get(page)
        coordinates = metadata.get("coordinates") or {}
        bbox = transform = None
        if geometry and coordinates.get("system") == "PixelSpace" and coordinates.get("points") and coordinates.get("layout_width") and coordinates.get("layout_height"):
            bbox, transform = from_pixel_space(coordinates["points"], coordinates["layout_width"], coordinates["layout_height"], geometry)
            if bbox is None: warnings.append("PixelSpace 坐标转换后越出有效页面。")
        else: warnings.append("Table Element 坐标缺失或坐标系统不受支持。")
        region = TableRegion(page, geometry.width if geometry else None, geometry.height if geometry else None, bbox, f"raw/elements.json#/{element_index}", transform)
        candidate = TableCandidate(candidate_id, "unstructured", "hi_res", f"raw/elements.json#/{element_index}", [region], row_count, column_count, None, None, cells, uncovered, TableArtifacts(f"tables/page_{page:02d}_table_{table_number:02d}.csv", f"tables/page_{page:02d}_table_{table_number:02d}.html"), warnings)
        candidates.append(candidate)
        if not isinstance(html, str) or not html.strip():
            candidate.unplaced_text = element.get('text') if isinstance(element.get('text'), str) else None
        rows_by_candidate[candidate_id] = _rows(candidate)
    return UnstructuredNormalizationResult(candidates, rows_by_candidate)
