"""Camelot lattice, stream, network, and hybrid adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rag.build_config import TableStrategy
from rag.v2.common import BoundingBox, CoordinateTransform, validate_bbox
from rag.v2.table_extraction import StrategyAdapter
from rag.v2.table_models import GridPosition, TableCandidate, TableCell, TableRegion


FLAVORS: tuple[TableStrategy, ...] = ("lattice", "stream", "network", "hybrid")
GridIndex = tuple[int, int]


class _DisjointSet:
    def __init__(self, items: tuple[GridIndex, ...]) -> None:
        self.parents = {item: item for item in items}

    def find(self, item: GridIndex) -> GridIndex:
        if self.parents[item] != item:
            self.parents[item] = self.find(self.parents[item])
        return self.parents[item]

    def union(self, first: GridIndex, second: GridIndex) -> None:
        left, right = self.find(first), self.find(second)
        if left != right:
            self.parents[right] = left


def _components(raw_cells: dict[GridIndex, Any]) -> tuple[frozenset[GridIndex], ...]:
    dsu = _DisjointSet(tuple(raw_cells))
    for (row, column), cell in raw_cells.items():
        right = (row, column + 1)
        if right in raw_cells and getattr(cell, "right", None) is False and getattr(raw_cells[right], "left", None) is False:
            dsu.union((row, column), right)
        below = (row + 1, column)
        if below in raw_cells and getattr(cell, "bottom", None) is False and getattr(raw_cells[below], "top", None) is False:
            dsu.union((row, column), below)
    groups: dict[GridIndex, set[GridIndex]] = {}
    for item in raw_cells:
        groups.setdefault(dsu.find(item), set()).add(item)
    return tuple(frozenset(value) for value in groups.values())


def _rectangle(component: frozenset[GridIndex]) -> tuple[int, int, int, int] | None:
    rows, columns = [item[0] for item in component], [item[1] for item in component]
    row0, row1, col0, col1 = min(rows), max(rows) + 1, min(columns), max(columns) + 1
    expected = {(row, column) for row in range(row0, row1) for column in range(col0, col1)}
    return (row0, row1, col0, col1) if set(component) == expected else None


def _to_top_left(cell: Any, page_width: float, page_height: float) -> BoundingBox:
    source = BoundingBox(float(cell.x1), float(cell.y1), float(cell.x2), float(cell.y2))
    converted = BoundingBox(source.x0, page_height - source.y1, source.x1, page_height - source.y0)
    valid = validate_bbox(converted, page_width, page_height)
    if valid is None:
        raise ValueError("Camelot cell bbox 无法转换到公共坐标。")
    return valid


def _component_text(component: frozenset[GridIndex], raw_cells: dict[GridIndex, Any]) -> str | None:
    values = [
        str(getattr(raw_cells[index], "text", ""))
        for index in sorted(component)
        if str(getattr(raw_cells[index], "text", "")).strip()
    ]
    unique = tuple(dict.fromkeys(values))
    return "\n".join(unique) if unique else getattr(raw_cells[min(component)], "text", None)


def normalize_table(
    table: Any, flavor: TableStrategy, page_number: int, table_index: int,
    page_width: float, page_height: float,
) -> TableCandidate:
    candidate_id = f"camelot_{flavor}_p{page_number:04d}_t{table_index:04d}"
    source_ref = f"camelot:{flavor}:page:{page_number}:table:{table_index}"
    rows = tuple(tuple(row) for row in getattr(table, "cells", ()))
    if not rows or not max((len(row) for row in rows), default=0):
        text = str(getattr(getattr(table, "df", None), "to_string", lambda **_: "")()).strip() or None
        return TableCandidate(
            candidate_id, "camelot", flavor, source_ref,
            (TableRegion(None, None, None, None, source_ref, None, "missing_bbox"),),
            None, None, None, None, (), (), text, (),
        )
    column_count = max(len(row) for row in rows)
    if any(len(row) != column_count for row in rows):
        raise ValueError("Camelot 原子 cell 矩阵不是矩形。")
    row_count = len(rows)
    raw_cells = {(row, column): rows[row][column] for row in range(row_count) for column in range(column_count)}
    boxes = {index: _to_top_left(cell, page_width, page_height) for index, cell in raw_cells.items()}
    transform = CoordinateTransform("bottom_left", "pt", 1.0, 1.0, page_height)
    components: list[frozenset[GridIndex]] = []
    for component in _components(raw_cells):
        if _rectangle(component) is None:
            components.extend(frozenset((index,)) for index in sorted(component))
        else:
            components.append(component)
    cells: list[TableCell] = []
    covered: set[GridIndex] = set()
    for component in sorted(components, key=min):
        rectangle = _rectangle(component)
        assert rectangle is not None
        row0, row1, col0, col1 = rectangle
        covered.update(component)
        component_boxes = tuple(boxes[index] for index in component)
        bbox = BoundingBox(
            min(item.x0 for item in component_boxes), min(item.y0 for item in component_boxes),
            max(item.x1 for item in component_boxes), max(item.y1 for item in component_boxes),
        )
        cell_id = f"{candidate_id}_r{row0:04d}c{col0:04d}_r{row1:04d}c{col1:04d}"
        cells.append(TableCell(
            cell_id, _component_text(component, raw_cells), row0, row1, col0, col1,
            row1 - row0, col1 - col0, bbox, ("unknown",), "edge_inferred",
            tuple(f"{source_ref}:cell:{row}:{column}" for row, column in sorted(component)),
        ))
    all_boxes = tuple(boxes.values())
    region = BoundingBox(
        min(item.x0 for item in all_boxes), min(item.y0 for item in all_boxes),
        max(item.x1 for item in all_boxes), max(item.y1 for item in all_boxes),
    )
    x_boundaries = tuple(sorted({value for item in all_boxes for value in (item.x0, item.x1)}))
    y_boundaries = tuple(sorted({value for item in all_boxes for value in (item.y0, item.y1)}))
    if len(x_boundaries) != column_count + 1 or len(y_boundaries) != row_count + 1:
        raise ValueError("Camelot 原子 bbox 边界与矩阵维度不一致。")
    uncovered = tuple(
        GridPosition(row, column)
        for row in range(row_count) for column in range(column_count)
        if (row, column) not in covered
    )
    return TableCandidate(
        candidate_id, "camelot", flavor, source_ref,
        (TableRegion(page_number, page_width, page_height, region, source_ref, transform),),
        row_count, column_count, x_boundaries, y_boundaries,
        tuple(cells), uncovered, None, (),
    )


def _extract_page(path: Path, page_number: int, flavor: TableStrategy) -> tuple[TableCandidate, ...]:
    import camelot
    import pymupdf

    with pymupdf.open(path) as document:
        page = document[page_number - 1]
        if page.rotation != 0 or page.cropbox != page.mediabox:
            raise ValueError("Camelot 当前不支持旋转页或 CropBox 偏移页的可靠转换。")
        width, height = float(page.rect.width), float(page.rect.height)
    arguments = {"copy_text": None, "shift_text": ["l", "t"]} if flavor in {"lattice", "hybrid"} else {}
    tables = camelot.read_pdf(str(path), pages=str(page_number), flavor=flavor, **arguments)
    return tuple(
        normalize_table(table, flavor, page_number, index, width, height)
        for index, table in enumerate(tables, start=1)
    )


def make_adapters() -> dict[tuple[str, str], StrategyAdapter]:
    return {
        ("camelot", flavor): StrategyAdapter(
            "camelot", flavor,
            lambda path, page_number, selected=flavor: _extract_page(path, page_number, selected),
        )
        for flavor in FLAVORS
    }
