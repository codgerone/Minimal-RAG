"""PyMuPDF lines, lines_strict, and text table adapters."""

from __future__ import annotations

from pathlib import Path
from statistics import median
from typing import Any

from rag.build_config import TableStrategy
from rag.v2.common import BoundingBox, validate_bbox
from rag.v2.table_extraction import StrategyAdapter
from rag.v2.table_models import GridPosition, TableCandidate, TableCell, TableRegion


BOUNDARY_TOLERANCE_PT = 2.0
SETTINGS: dict[TableStrategy, dict[str, str]] = {
    "lines": {"vertical_strategy": "lines", "horizontal_strategy": "lines"},
    "lines_strict": {"vertical_strategy": "lines_strict", "horizontal_strategy": "lines_strict"},
    "text": {"vertical_strategy": "text", "horizontal_strategy": "text"},
}


def _box(value: Any) -> BoundingBox | None:
    if value is None:
        return None
    try:
        values = tuple(float(item) for item in value)
        return BoundingBox(*values) if len(values) == 4 else None
    except (TypeError, ValueError):
        return None


def _cluster(values: list[float]) -> tuple[float, ...]:
    if not values:
        return ()
    groups: list[list[float]] = [[value] for value in sorted(values)]
    merged = [groups[0]]
    for group in groups[1:]:
        if group[0] - merged[-1][-1] <= BOUNDARY_TOLERANCE_PT:
            merged[-1].extend(group)
        else:
            merged.append(group)
    return tuple(float(median(group)) for group in merged)


def _nearest(boundaries: tuple[float, ...], value: float) -> int | None:
    if not boundaries:
        return None
    index = min(range(len(boundaries)), key=lambda item: abs(boundaries[item] - value))
    return index if abs(boundaries[index] - value) <= BOUNDARY_TOLERANCE_PT else None


def _extract_page(path: Path, page_number: int, strategy: TableStrategy) -> tuple[TableCandidate, ...]:
    import pymupdf

    with pymupdf.open(path) as document:
        if page_number > len(document):
            raise ValueError("page_number 超出 PDF 页数。")
        page = document[page_number - 1]
        finder = page.find_tables(**SETTINGS[strategy])
        width, height = float(page.rect.width), float(page.rect.height)
        unsupported_geometry = page.rotation != 0 or page.cropbox != page.mediabox
        candidates: list[TableCandidate] = []
        for table_index, table in enumerate(finder.tables, start=1):
            candidate_id = f"pymupdf_{strategy}_p{page_number:04d}_t{table_index:04d}"
            source_ref = f"pymupdf:{strategy}:page:{page_number}:table:{table_index}"
            matrix = table.extract()
            raw_cells: list[tuple[BoundingBox, str | None, int, int]] = []
            seen: set[tuple[float, float, float, float]] = set()
            for row_index, row in enumerate(table.rows):
                for column_index, value in enumerate(row.cells):
                    bbox = _box(value)
                    if bbox is None:
                        continue
                    key = (bbox.x0, bbox.y0, bbox.x1, bbox.y1)
                    if key in seen:
                        continue
                    seen.add(key)
                    text = (
                        matrix[row_index][column_index]
                        if row_index < len(matrix) and column_index < len(matrix[row_index])
                        else None
                    )
                    raw_cells.append((bbox, text, row_index, column_index))

            if unsupported_geometry:
                unplaced = "\n".join(
                    str(value) for row in matrix for value in row
                    if value is not None and str(value).strip()
                ) or None
                cells = tuple(
                    TableCell(
                        f"{candidate_id}_raw_{index:04d}", text,
                        None, None, None, None, None, None, bbox, (),
                        "unavailable", (f"{source_ref}:raw-cell:{index}",),
                    )
                    for index, (bbox, text, _row, _column) in enumerate(raw_cells, start=1)
                )
                candidates.append(TableCandidate(
                    candidate_id, "pymupdf", strategy, source_ref,
                    (TableRegion(None, None, None, None, source_ref, None, "coordinate_conversion_failed"),),
                    None, None, None, None, cells, (), unplaced, (),
                ))
                continue

            table_bbox = _box(table.bbox)
            valid_table_bbox = validate_bbox(table_bbox, width, height) if table_bbox else None
            if valid_table_bbox is None:
                raise ValueError(f"{source_ref} 表格 bbox 无效。")
            x_boundaries = _cluster([
                value for bbox, _text, _row, _column in raw_cells for value in (bbox.x0, bbox.x1)
            ] + [valid_table_bbox.x0, valid_table_bbox.x1])
            y_boundaries = _cluster([
                value for bbox, _text, _row, _column in raw_cells for value in (bbox.y0, bbox.y1)
            ] + [valid_table_bbox.y0, valid_table_bbox.y1])
            row_count, column_count = len(y_boundaries) - 1, len(x_boundaries) - 1
            if row_count <= 0 or column_count <= 0:
                raise ValueError(f"{source_ref} 无法建立正维网格。")
            cells_list: list[TableCell] = []
            covered: set[tuple[int, int]] = set()
            for index, (bbox, text, raw_row, raw_column) in enumerate(raw_cells, start=1):
                valid_bbox = validate_bbox(bbox, width, height)
                x0, x1 = _nearest(x_boundaries, bbox.x0), _nearest(x_boundaries, bbox.x1)
                y0, y1 = _nearest(y_boundaries, bbox.y0), _nearest(y_boundaries, bbox.y1)
                valid = valid_bbox is not None and None not in (x0, x1, y0, y1) and x0 < x1 and y0 < y1
                if not valid:
                    raise ValueError(f"{source_ref} cell 无法映射到原子网格。")
                assert x0 is not None and x1 is not None and y0 is not None and y1 is not None
                positions = {(row, col) for row in range(y0, y1) for col in range(x0, x1)}
                if covered & positions:
                    raise ValueError(f"{source_ref} cell 存在正面积网格重叠。")
                covered.update(positions)
                cell_id = f"{candidate_id}_r{y0:04d}c{x0:04d}_r{y1:04d}c{x1:04d}"
                header = getattr(table, "header", None)
                header_bbox = _box(getattr(header, "bbox", None)) if header and not header.external else None
                roles = ("column_header",) if header_bbox and min(valid_bbox.y1, header_bbox.y1) > max(valid_bbox.y0, header_bbox.y0) else ("body",)
                cells_list.append(TableCell(
                    cell_id, text, y0, y1, x0, x1, y1 - y0, x1 - x0,
                    valid_bbox, roles, "geometry_inferred",
                    (f"{source_ref}:row:{raw_row}:column:{raw_column}",),
                ))
            uncovered = tuple(
                GridPosition(row, column)
                for row in range(row_count) for column in range(column_count)
                if (row, column) not in covered
            )
            candidates.append(TableCandidate(
                candidate_id, "pymupdf", strategy, source_ref,
                (TableRegion(page_number, width, height, valid_table_bbox, source_ref, None),),
                row_count, column_count, x_boundaries, y_boundaries,
                tuple(cells_list), uncovered, None, (),
            ))
        return tuple(candidates)


def make_adapters() -> dict[tuple[str, str], StrategyAdapter]:
    return {
        ("pymupdf", strategy): StrategyAdapter(
            "pymupdf", strategy,
            lambda path, page_number, selected=strategy: _extract_page(path, page_number, selected),
        )
        for strategy in SETTINGS
    }
