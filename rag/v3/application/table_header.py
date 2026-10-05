"""Deterministic V3 table header rule, consuming the public StructuredTable."""

from __future__ import annotations

import re
import unicodedata
from typing import Literal, TypeAlias

from rag.v3.contracts.tables import (
    HeaderCandidateEvaluation, HeaderColumnObservation, HeaderDecision,
    HeaderIssue, HeaderObservation, HeaderPath,
    HeaderSkippedRow, StructuredTable, TableCell,
)

ValueType: TypeAlias = Literal["number", "date", "text_shape", "empty"]

_DATE_PATTERNS = (
    re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$"),
    re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$"),
    re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})$"),
)
_NUMERIC_CORE = r"[+-]?[\d]+(?:[.,'‘’ʼ´ ](?=[\d])|[\d])*"
_NUMBER = re.compile(
    rf"^(?:(?:[$€£¥]){_NUMERIC_CORE}|{_NUMERIC_CORE}(?:%)?|[A-Z]{{3}} {_NUMERIC_CORE}|{_NUMERIC_CORE} [A-Z]{{3}}|\({_NUMERIC_CORE}\))$"
)


def _normalized(value: str | None) -> str:
    return " ".join(unicodedata.normalize("NFKC", value or "").strip().split())


def classify_cell(value: str | None) -> ValueType:
    text = _normalized(value)
    if not text:
        return "empty"
    for index, pattern in enumerate(_DATE_PATTERNS):
        match = pattern.fullmatch(text)
        if match:
            parts = tuple(int(item) for item in match.groups())
            if index == 0:
                month, day = parts[1], parts[2]
            elif index == 1:
                month, day = parts[0], parts[1]
            else:
                month, day = parts[1], parts[0]
            if 1 <= month <= 12 and 1 <= day <= 31:
                return "date"
    if _NUMBER.fullmatch(text):
        return "number"
    return "text_shape"


def _cell_at(table: StructuredTable, row: int, col: int) -> TableCell | None:
    return next((cell for cell in table.cells if cell.start_row_offset_idx is not None
                 and cell.start_row_offset_idx <= row < cell.end_row_offset_idx  # type: ignore[operator]
                 and cell.start_col_offset_idx <= col < cell.end_col_offset_idx), None)  # type: ignore[operator]


def _row_cells(table: StructuredTable, row: int) -> tuple[TableCell, ...]:
    return tuple(cell for cell in table.cells if cell.start_row_offset_idx is not None
                 and cell.start_row_offset_idx <= row < cell.end_row_offset_idx)  # type: ignore[operator]


def _unique(cells: tuple[TableCell, ...]) -> tuple[TableCell, ...]:
    return tuple({cell.cell_id: cell for cell in cells}.values())


def _input_issues(table: StructuredTable) -> tuple[HeaderIssue, ...]:
    issues: list[HeaderIssue] = []
    valid_dimensions = (type(table.row_count) is int and table.row_count > 0
                        and type(table.column_count) is int and table.column_count > 0)
    if not valid_dimensions:
        issues.append(HeaderIssue("invalid_dimensions"))
    seen: set[str] = set()
    occupied: dict[tuple[int, int], str] = {}
    overlapping: set[str] = set()
    for cell in table.cells:
        if cell.cell_id in seen:
            issues.append(HeaderIssue("duplicate_cell_id", cell_ids=(cell.cell_id,)))
        seen.add(cell.cell_id)
        coordinates = (cell.start_row_offset_idx, cell.end_row_offset_idx,
                       cell.start_col_offset_idx, cell.end_col_offset_idx)
        if any(type(value) is not int for value in coordinates):
            issues.append(HeaderIssue("unavailable_cell_position", cell_ids=(cell.cell_id,)))
            continue
        row0, row1, col0, col1 = coordinates
        assert isinstance(row0, int) and isinstance(row1, int)
        assert isinstance(col0, int) and isinstance(col1, int)
        if (not valid_dimensions or not (0 <= row0 < row1 <= table.row_count)
                or not (0 <= col0 < col1 <= table.column_count)):
            issues.append(HeaderIssue("invalid_cell_interval", cell_ids=(cell.cell_id,)))
            continue
        if cell.row_span != row1 - row0 or cell.col_span != col1 - col0:
            issues.append(HeaderIssue("span_mismatch", cell_ids=(cell.cell_id,)))
        for row in range(row0, row1):
            for col in range(col0, col1):
                prior = occupied.setdefault((row, col), cell.cell_id)
                if prior != cell.cell_id:
                    overlapping.update((prior, cell.cell_id))
    if overlapping:
        issues.append(HeaderIssue("overlapping_cells", cell_ids=tuple(sorted(overlapping))))
    if table.unplaced_text is not None and table.unplaced_text.strip():
        issues.append(HeaderIssue("unplaced_text"))
    return tuple(issues)


def _is_blank_row(table: StructuredTable, row: int) -> bool:
    if any(pos.row_index == row for pos in table.uncovered_grid_positions):
        return False
    cells = _unique(_row_cells(table, row))
    return bool(cells) and all(classify_cell(cell.text) == "empty" for cell in cells)


def _is_full_width_row(table: StructuredTable, row: int) -> bool:
    cells = _unique(_row_cells(table, row))
    return bool(table.column_count and table.column_count > 1 and len(cells) == 1 and cells[0].start_row_offset_idx == row
                and cells[0].end_row_offset_idx == row + 1
                and cells[0].start_col_offset_idx == 0
                and cells[0].end_col_offset_idx == table.column_count)


def _structural_issues(table: StructuredTable, start: int, end: int) -> tuple[HeaderIssue, ...]:
    issues: list[HeaderIssue] = []
    boundary = tuple(cell for cell in table.cells if cell.start_row_offset_idx is not None and
                     ((cell.start_row_offset_idx < start < cell.end_row_offset_idx) or
                      (cell.start_row_offset_idx < end < cell.end_row_offset_idx)))  # type: ignore[operator]
    if boundary:
        issues.append(HeaderIssue("boundary_crosses_cell", cell_ids=tuple(cell.cell_id for cell in boundary)))
    missing = tuple(pos for pos in table.uncovered_grid_positions if start <= pos.row_index < end)
    if missing:
        issues.append(HeaderIssue("missing_header_position",
                                  tuple(dict.fromkeys(pos.row_index for pos in missing)),
                                  tuple(dict.fromkeys(pos.column_index for pos in missing))))
    blank = tuple(row for row in range(start, end) if _is_blank_row(table, row))
    if blank:
        issues.append(HeaderIssue("internal_blank_row", blank))
    full = tuple(row for row in range(start, end) if _is_full_width_row(table, row))
    if full:
        issues.append(HeaderIssue("internal_full_width_row", full,
                                  cell_ids=tuple(_row_cells(table, row)[0].cell_id for row in full)))
    region = tuple(cell for cell in table.cells if cell.start_row_offset_idx is not None
                   and start <= cell.start_row_offset_idx and cell.end_row_offset_idx <= end)  # type: ignore[operator]
    crossing: list[str] = []
    for upper in region:
        for lower in region:
            if upper.start_row_offset_idx >= lower.start_row_offset_idx:  # type: ignore[operator]
                continue
            overlaps = upper.start_col_offset_idx < lower.end_col_offset_idx and lower.start_col_offset_idx < upper.end_col_offset_idx  # type: ignore[operator]
            contains = upper.start_col_offset_idx <= lower.start_col_offset_idx and lower.end_col_offset_idx <= upper.end_col_offset_idx  # type: ignore[operator]
            if overlaps and not contains:
                crossing.extend((upper.cell_id, lower.cell_id))
    if crossing:
        issues.append(HeaderIssue("crossing_column_intervals", cell_ids=tuple(dict.fromkeys(crossing))))
    paths = [[_cell_at(table, row, col) for row in range(start, end)] for col in range(table.column_count or 0)]
    bad_cols = tuple(col for col, path in enumerate(paths) if not any(
        cell is not None and classify_cell(cell.text) != "empty" for cell in path))
    if bad_cols:
        issues.append(HeaderIssue("no_nonempty_path", column_indices=bad_cols))
    return tuple(issues)


def _tool_ids(table: StructuredTable, start: int, end: int) -> tuple[tuple[str, ...], tuple[str, ...]]:
    header_ids = tuple(cell.cell_id for cell in table.cells if "column_header" in cell.roles)
    inside = tuple(cell.cell_id for cell in table.cells if cell.cell_id in header_ids
                   and cell.start_row_offset_idx is not None and start <= cell.start_row_offset_idx
                   and cell.end_row_offset_idx <= end)  # type: ignore[operator]
    return inside, tuple(item for item in header_ids if item not in inside)


def _evaluate(table: StructuredTable, start: int, end: int,
              sample_row_budget: int, minimum_independent_observations: int) -> HeaderCandidateEvaluation:
    inside, outside = _tool_ids(table, start, end)
    issues = _structural_issues(table, start, end)
    if issues:
        return HeaderCandidateEvaluation(start, end, "structural_rejection", issues, (), (), (), (), inside, outside)
    sampled: list[int] = []
    skipped: list[HeaderSkippedRow] = []
    row = end
    while row < table.row_count and len(sampled) < sample_row_budget:  # type: ignore[operator]
        cells = _unique(_row_cells(table, row))
        missing = any(pos.row_index == row for pos in table.uncovered_grid_positions)
        reason = "missing_position" if missing else "horizontal_span" if any((cell.col_span or 0) > 1 for cell in cells) else "blank_row" if _is_blank_row(table, row) else None
        if reason:
            skipped.append(HeaderSkippedRow(row, reason, tuple(cell.cell_id for cell in cells)))  # type: ignore[arg-type]
        else:
            sampled.append(row)
        row += 1
    columns: list[HeaderColumnObservation] = []
    supporting: list[int] = []
    for col in range(table.column_count or 0):
        lowest = _cell_at(table, end - 1, col)
        observations: list[HeaderObservation] = []
        seen: set[str] = set()
        for sample_row in sampled:
            cell = _cell_at(table, sample_row, col)
            if cell is None or cell.cell_id in seen:
                continue
            seen.add(cell.cell_id)
            value_type = classify_cell(cell.text)
            if value_type != "empty":
                observations.append(HeaderObservation(sample_row, cell.cell_id, cell.text or "", value_type))  # type: ignore[arg-type]
        types = {item.value_type for item in observations}
        stable = (next(iter(types)) if len(observations) >= minimum_independent_observations
                  and len(types) == 1 and next(iter(types)) in ("number", "date") else None)
        header_type = classify_cell(lowest.text if lowest else None)
        supports = header_type == "text_shape" and stable in ("number", "date")
        if supports:
            supporting.append(col)
        columns.append(HeaderColumnObservation(col, lowest.cell_id if lowest else None, header_type,
                                               tuple(observations), stable, supports))  # type: ignore[arg-type]
    reason = "supported" if supporting else "no_type_transition"
    return HeaderCandidateEvaluation(start, end, reason, (), tuple(sampled), tuple(skipped),
                                     tuple(columns), tuple(supporting), inside, outside)


def detect_header(table: StructuredTable, *, sample_row_budget: int = 8,
                  minimum_independent_observations: int = 2) -> HeaderDecision:
    if sample_row_budget <= 0 or minimum_independent_observations <= 0:
        raise ValueError("header observation parameters must be positive")
    issues = _input_issues(table)
    blocking = tuple(item for item in issues if item.code != "unplaced_text")
    if blocking:
        return HeaderDecision("undetermined", "invalid_grid", issues, (), (), None, None, (), "table_header_v1", sample_row_budget, minimum_independent_observations)
    if issues:
        return HeaderDecision("undetermined", "unplaced_content", issues, (), (), None, None, (), "table_header_v1", sample_row_budget, minimum_independent_observations)
    assert table.row_count is not None and table.column_count is not None
    skipped: list[int] = []
    start = 0
    while start < table.row_count and ((_is_blank_row(table, start) and all((c.row_span or 0) == 1 for c in _unique(_row_cells(table, start)))) or _is_full_width_row(table, start)):
        skipped.append(start)
        start += 1
    if start >= table.row_count - 1:
        return HeaderDecision("undetermined", "no_candidate_region", (), tuple(skipped), (), None, None, (), "table_header_v1", sample_row_budget, minimum_independent_observations)
    evaluations = tuple(_evaluate(table, start, end, sample_row_budget,
                                  minimum_independent_observations)
                        for end in range(start + 1, table.row_count))
    supported = tuple(item for item in evaluations if item.result_reason == "supported")
    if len(supported) != 1:
        reason = "no_supported_candidate" if not supported else "ambiguous_candidates"
        return HeaderDecision("undetermined", reason, (), tuple(skipped), evaluations, None, None, (), "table_header_v1", sample_row_budget, minimum_independent_observations)
    winner = supported[0]
    paths: list[HeaderPath] = []
    for col in range(table.column_count):
        cells: list[TableCell] = []
        seen: set[str] = set()
        for row in range(winner.start_row, winner.end_row):
            cell = _cell_at(table, row, col)
            if cell and cell.cell_id not in seen:
                seen.add(cell.cell_id)
                cells.append(cell)
        parts = tuple((cell.text or "") if classify_cell(cell.text) != "empty" else f"第{col + 1}列" for cell in cells)
        if not any(classify_cell(cell.text) != "empty" for cell in cells):
            parts = (f"第{col + 1}列",)
        paths.append(HeaderPath(col, tuple(cell.cell_id for cell in cells), parts))
    return HeaderDecision("identified", "unique_supported_candidate", (), tuple(skipped), evaluations,
                          winner.start_row, winner.end_row, tuple(paths), "table_header_v1", sample_row_budget, minimum_independent_observations)
