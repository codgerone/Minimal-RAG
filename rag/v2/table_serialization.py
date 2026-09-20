"""Deterministic structured-table serialization defined by table_text_v1."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from rag.v2.table_header import HeaderDecision, HeaderPath
from rag.v2.table_models import TableCandidate, TableCell


@dataclass(frozen=True)
class SerializedTableLine:
    line_id: str
    kind: Literal["header", "data", "merged", "unplaced_text"]
    text: str
    source_rows: tuple[int, ...]
    source_cell_ids: tuple[str, ...]


@dataclass(frozen=True)
class SerializedTable:
    text: str
    lines: tuple[SerializedTableLine, ...]
    rule_version: Literal["table_text_v1"] = "table_text_v1"

    def __post_init__(self) -> None:
        if self.text != "\n".join(item.text for item in self.lines):
            raise ValueError("SerializedTable.text 必须由 lines 原样连接。")
        ids = tuple(item.line_id for item in self.lines)
        if len(ids) != len(set(ids)):
            raise ValueError("SerializedTableLine.line_id 必须唯一。")


def _quoted(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _value(cell: TableCell) -> str:
    if cell.text is None or not cell.text.strip():
        return "〔空白〕"
    return _quoted(cell.text)


def _path(paths: dict[int, HeaderPath], col: int) -> str:
    path = paths.get(col)
    return " / ".join(path.display_parts) if path else f"第{col + 1}列"


def _range(first: int, end: int, unit: str) -> str:
    return f"第{first + 1}{unit}" if end == first + 1 else f"第{first + 1}{unit}至第{end}{unit}"


def _merged_text(cell: TableCell, paths: dict[int, HeaderPath], identified: bool) -> str:
    assert cell.start_row_offset_idx is not None and cell.end_row_offset_idx is not None
    assert cell.start_col_offset_idx is not None and cell.end_col_offset_idx is not None
    rows = _range(cell.start_row_offset_idx, cell.end_row_offset_idx, "行")
    cols = _range(cell.start_col_offset_idx, cell.end_col_offset_idx, "列")
    if cell.row_span and cell.row_span > 1 and cell.col_span == 1:
        field = f"，字段 = {_path(paths, cell.start_col_offset_idx)}" if identified else ""
        return f"{rows}共享{cols}{field}，内容 = {_value(cell)}。"
    if cell.row_span == 1:
        return f"第{cell.start_row_offset_idx + 1}行：{cols}为合并单元格，内容 = {_value(cell)}。"
    return f"{rows}、{cols}为合并单元格，内容 = {_value(cell)}。"


def serialize_table(
    table: TableCandidate, header: HeaderDecision, *, table_node_id: str | None = None,
) -> SerializedTable:
    """Serialize every positioned fact once, retaining unplaced original text."""
    paths = {item.column_index: item for item in header.paths}
    identified = header.outcome == "identified"
    header_rows = set(range(header.header_start_row or 0, header.header_end_row or 0)) if identified else set()
    lines: list[SerializedTableLine] = []
    line_number = 0

    def append(kind: Literal["header", "data", "merged", "unplaced_text"], text: str,
               rows: tuple[int, ...], cells: tuple[str, ...]) -> None:
        nonlocal line_number
        line_number += 1
        owner = table_node_id or table.candidate_id
        lines.append(SerializedTableLine(f"{owner}_line_{line_number:06d}", kind, text, rows, cells))

    if not identified:
        append("header", "表头未确定。", (), ())

    emitted_merged: set[str] = set()
    for row in range(table.row_count or 0):
        if row in header_rows:
            continue
        row_cells = sorted(
            (cell for cell in table.cells if cell.start_row_offset_idx is not None
             and cell.start_row_offset_idx <= row < cell.end_row_offset_idx),
            key=lambda cell: (cell.start_col_offset_idx or 0, cell.cell_id),
        )
        merged = tuple(dict.fromkeys(cell.cell_id for cell in row_cells if (cell.row_span or 0) > 1 or (cell.col_span or 0) > 1))
        for cell_id in merged:
            if cell_id in emitted_merged:
                continue
            emitted_merged.add(cell_id)
            cell = next(item for item in row_cells if item.cell_id == cell_id)
            assert cell.start_row_offset_idx is not None and cell.end_row_offset_idx is not None
            append("merged", _merged_text(cell, paths, identified and row > (header.header_end_row or 0) - 1),
                   tuple(range(cell.start_row_offset_idx, cell.end_row_offset_idx)), (cell.cell_id,))

        parts: list[str] = []
        source_ids: list[str] = []
        occupied: set[int] = set()
        for cell in row_cells:
            assert cell.start_col_offset_idx is not None and cell.end_col_offset_idx is not None
            occupied.update(range(cell.start_col_offset_idx, cell.end_col_offset_idx))
            if cell.cell_id in merged:
                continue
            label = _path(paths, cell.start_col_offset_idx) if identified and row >= (header.header_end_row or 0) else f"第{cell.start_col_offset_idx + 1}列"
            parts.append(f"{label} = {_value(cell)}")
            source_ids.append(cell.cell_id)
        missing = sorted(pos.column_index for pos in table.uncovered_grid_positions if pos.row_index == row and pos.column_index not in occupied)
        for col in missing:
            label = _path(paths, col) if identified and row >= (header.header_end_row or 0) else f"第{col + 1}列"
            parts.append(f"{label} = 〔缺失单元格〕")
        if parts:
            append("data", f"第{row + 1}行：" + "；".join(parts) + "。", (row,), tuple(source_ids))

    if table.unplaced_text is not None:
        unplaced_ids = tuple(cell.cell_id for cell in table.cells if cell.start_row_offset_idx is None)
        append("unplaced_text", f"结构无法定位：{_quoted(table.unplaced_text)}。", (), unplaced_ids)
    result = tuple(lines)
    return SerializedTable("\n".join(item.text for item in result), result)
