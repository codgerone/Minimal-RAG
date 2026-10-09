"""Deterministic table header rule, consuming the public StructuredTable."""

from __future__ import annotations

import datetime
import re
import unicodedata

from rag.ingest.tables.models import (
    CellShape, HeaderCandidateEvaluation, HeaderColumnObservation, HeaderDecision,
    HeaderIssue, HeaderObservation, HeaderPath,
    HeaderSkippedRow, StructuredTable, TableCell, ValueType,
)

RULE_VERSION = "table_header_v3"
MAX_HEADER_ROWS = 5

# Integer part: plain digits, or 1-3 digits then groups of exactly 3 joined by a thousands mark
# (an apostrophe may be followed by one space); an optional decimal part closes the number.
_DIGITS = r"(?:\d{1,3}(?:(?:[,.' ]|' )\d{3})+|\d+)(?:[.,]\d+)?"
_SIGN = r"[+\-−]"
_CURRENCY_CODES = ("USD", "EUR", "GBP", "CNY", "RMB", "JPY", "HKD", "PEN", "BRL", "MXN", "CLP",
                   "COP", "ARS", "CAD", "AUD", "CHF", "SGD", "KRW", "INR")
_SYMBOLS = r"US\$|S/\.?|R\$|[$€£¥]|" + "|".join(_CURRENCY_CODES)
_PLAIN = rf"{_SIGN}?{_DIGITS}(?: ?[%‰])?"
_MONEY = (rf"{_SIGN}?(?:{_SYMBOLS}) ?{_SIGN}?{_DIGITS}"
          rf"|{_SIGN}?{_DIGITS} ?(?:万元|元|{_SYMBOLS})")
_NUMBER = re.compile(rf"{_PLAIN}|\({_PLAIN}\)")
_CURRENCY = re.compile(rf"(?:{_MONEY})|\((?:{_MONEY})\)")
# Cells meaning "none"; they count as empty in every header judgment.
_PLACEHOLDERS = frozenset({"-", "–", "—", "−"})

_NUMERIC_DATE = re.compile(r"(\d{1,4})([/.\-])(\d{1,2})\2(\d{1,4})")
_YEAR_MONTH = re.compile(r"(\d{4})[/.\-](\d{1,2})|(\d{1,2})/(\d{4})")
_DAY_MONTH = re.compile(r"(\d{1,2})/(\d{1,2})")
_COMPACT_DATE = re.compile(r"((?:19|20)\d{2})(\d{2})(\d{2})")
_CHINESE_DATE = re.compile(r"(?:(\d{4})年)?(\d{1,2})月(?:(\d{1,2})[日号])?")
_MONTHS = {
    name: number for number, names in enumerate((
        ("january", "jan", "enero", "ene"),
        ("february", "feb", "febrero"),
        ("march", "mar", "marzo"),
        ("april", "apr", "abril", "abr"),
        ("may", "mayo"),
        ("june", "jun", "junio"),
        ("july", "jul", "julio"),
        ("august", "aug", "agosto", "ago"),
        ("september", "sep", "sept", "septiembre", "setiembre", "set"),
        ("october", "oct", "octubre"),
        ("november", "nov", "noviembre"),
        ("december", "dec", "diciembre", "dic"),
    ), start=1) for name in names
}
_DAY_TOKEN = re.compile(r"(\d{1,2})(?:st|nd|rd|th|º|°)?")
_YEAR_TOKEN = re.compile(r"\d{2}|\d{4}")


# NFKC would split the acute accent into a space plus a combining mark; map apostrophe-like marks first.
_APOSTROPHES = str.maketrans({"´": "'", "‘": "'", "’": "'", "ʼ": "'"})


def _normalized(value: str | None) -> str:
    return " ".join(unicodedata.normalize("NFKC", (value or "").translate(_APOSTROPHES)).strip().split())


def _valid(year: int | None, month: int, day: int | None) -> bool:
    """Calendar check; a missing year allows Feb 29, a two-digit year counts as 20YY."""
    if year is None:
        year = 2000
    elif year < 100:
        year += 2000
    try:
        datetime.date(year, month, day or 1)
    except ValueError:
        return False
    return True


def _numeric_date(text: str) -> bool:
    if match := _NUMERIC_DATE.fullmatch(text):
        first, _, second, third = match.groups()
        a, b, c = int(first), int(second), int(third)
        if len(first) == 4:
            return len(third) <= 2 and _valid(a, b, c)
        if len(first) > 2:
            return False
        if len(third) == 4:
            return _valid(c, b, a) or _valid(c, a, b)
        return len(third) == 2 and (_valid(a, b, c) or _valid(c, b, a) or _valid(c, a, b))
    if match := _YEAR_MONTH.fullmatch(text):
        year, month = (match[1], match[2]) if match[1] else (match[4], match[3])
        return _valid(int(year), int(month), None)
    if match := _DAY_MONTH.fullmatch(text):
        a, b = int(match[1]), int(match[2])
        return _valid(None, b, a) or _valid(None, a, b)
    if match := _COMPACT_DATE.fullmatch(text):
        return _valid(int(match[1]), int(match[2]), int(match[3]))
    if match := _CHINESE_DATE.fullmatch(text.replace(" ", "")):
        year, month, day = (int(item) if item else None for item in match.groups())
        return (year is not None or day is not None) and month is not None and _valid(year, month, day)
    return False


def _worded_date(text: str) -> bool:
    """Day, month name and year in an accepted order; English or Spanish month names."""
    tokens = [token for token in re.split(r"[\s,/.\-]+", text.casefold())
              if token and token not in ("de", "del")]
    months = [index for index, token in enumerate(tokens) if token in _MONTHS]
    if len(tokens) not in (2, 3) or len(months) != 1:
        return False
    position = months[0]
    month = _MONTHS[tokens[position]]
    rest = tokens[:position] + tokens[position + 1:]

    def day(token: str) -> int | None:
        match = _DAY_TOKEN.fullmatch(token)
        return int(match[1]) if match else None

    def year(token: str) -> int | None:
        return int(token) if _YEAR_TOKEN.fullmatch(token) else None

    if len(tokens) == 3:                       # D M Y or M D Y
        if position == 2:
            return False
        d, y = day(rest[0]), year(rest[1])
        return d is not None and y is not None and _valid(y, month, d)
    d, y = day(rest[0]), year(rest[0])
    if position == 1:                          # D M
        return d is not None and _valid(None, month, d)
    return ((d is not None and _valid(None, month, d))   # M D or M Y
            or (y is not None and _valid(y, month, None)))


def value_types(value: str | None) -> tuple[ValueType, ...]:
    """Every type the cell can be read as; empty means text or an empty cell."""
    text = _normalized(value)
    if not text:
        return ()
    types: list[ValueType] = []
    if _NUMBER.fullmatch(text):
        types.append("number")
    if _CURRENCY.fullmatch(text):
        types.append("currency")
    if _numeric_date(text) or _worded_date(text):
        types.append("date")
    return tuple(types)


def cell_shape(value: str | None) -> CellShape:
    text = _normalized(value)
    if not text or text in _PLACEHOLDERS:
        return "empty"
    return "typed" if value_types(value) else "text"


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
    return bool(cells) and all(cell_shape(cell.text) == "empty" for cell in cells)


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
    # Columns whose header cells are all blank are allowed; the candidate needs text somewhere.
    blank_cols = tuple(col for col, path in enumerate(paths) if not any(
        cell is not None and cell_shape(cell.text) != "empty" for cell in path))
    if len(blank_cols) == len(paths):
        issues.append(HeaderIssue("no_nonempty_path", column_indices=blank_cols))
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
        # A row led by a horizontally merged cell is a summary or section row and is skipped whole;
        # elsewhere only the merged cells are left out.
        filled = sorted((cell for cell in cells if cell_shape(cell.text) != "empty"),
                        key=lambda cell: cell.start_col_offset_idx or 0)
        summary = bool(filled) and (filled[0].col_span or 0) > 1
        usable = not summary and any((cell.col_span or 0) == 1 for cell in filled)
        missing = any(pos.row_index == row for pos in table.uncovered_grid_positions)
        reason = (None if usable else "summary_row" if summary
                  else "horizontal_span" if any((cell.col_span or 0) > 1 for cell in cells)
                  else "missing_position" if missing else "blank_row")
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
            if (cell.col_span or 0) == 1 and cell_shape(cell.text) != "empty":
                observations.append(HeaderObservation(sample_row, cell.cell_id, cell.text or "",
                                                      value_types(cell.text)))
        # Stable: enough observations that share a type under at least one reading each.
        shared = (set.intersection(*(set(item.value_types) for item in observations))
                  if len(observations) >= minimum_independent_observations else set())
        stable: tuple[ValueType, ...] = tuple(kind for kind in ("number", "currency", "date") if kind in shared)
        header_text = lowest.text if lowest else None
        header_shape = cell_shape(header_text)
        # Transition: a non-empty header cell that cannot be read as the body's stable type.
        supports = (header_shape != "empty" and bool(stable)
                    and not set(value_types(header_text)) & set(stable))
        if supports:
            supporting.append(col)
        columns.append(HeaderColumnObservation(col, lowest.cell_id if lowest else None, header_shape,
                                               tuple(observations), stable, supports))
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
        return HeaderDecision("undetermined", "invalid_grid", issues, (), (), None, None, (), RULE_VERSION, sample_row_budget, minimum_independent_observations)
    if issues:
        return HeaderDecision("undetermined", "unplaced_content", issues, (), (), None, None, (), RULE_VERSION, sample_row_budget, minimum_independent_observations)
    assert table.row_count is not None and table.column_count is not None
    skipped: list[int] = []
    start = 0
    while start < table.row_count and ((_is_blank_row(table, start) and all((c.row_span or 0) == 1 for c in _unique(_row_cells(table, start)))) or _is_full_width_row(table, start)):
        skipped.append(start)
        start += 1
    if start >= table.row_count - 1:
        return HeaderDecision("undetermined", "no_candidate_region", (), tuple(skipped), (), None, None, (), RULE_VERSION, sample_row_budget, minimum_independent_observations)
    evaluations = tuple(_evaluate(table, start, end, sample_row_budget,
                                  minimum_independent_observations)
                        for end in range(start + 1, min(start + MAX_HEADER_ROWS, table.row_count - 1) + 1))
    supported = tuple(item for item in evaluations if item.result_reason == "supported")
    if len(supported) != 1:
        reason = "no_supported_candidate" if not supported else "ambiguous_candidates"
        return HeaderDecision("undetermined", reason, (), tuple(skipped), evaluations, None, None, (), RULE_VERSION, sample_row_budget, minimum_independent_observations)
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
        # Blank header cells contribute no part; a column with none has an empty path.
        parts = tuple(cell.text or "" for cell in cells if cell_shape(cell.text) != "empty")
        paths.append(HeaderPath(col, tuple(cell.cell_id for cell in cells), parts))
    return HeaderDecision("identified", "unique_supported_candidate", (), tuple(skipped), evaluations,
                          winner.start_row, winner.end_row, tuple(paths), RULE_VERSION, sample_row_budget, minimum_independent_observations)
