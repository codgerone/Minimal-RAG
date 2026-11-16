"""Read an .xlsx file into facts: sheets, cells with display values, merges, hidden rows and columns,
Table objects (docs/rules/xlsx.md). The parser (xlsx_native) and the excerpt locator (anchors) both
read through here, so a cell's text is the same for both.

openpyxl reads the file twice: with cached values (what Excel computed when it saved the file) and
with formulas. Not in read-only mode, which does not see merged cells. Only cells stored in the
file are visited, never max_row × max_column (formatting stretches the used range). Every sheet is
read, hidden and very hidden ones included; chart sheets are skipped.

Addresses: a sheet is `sheet<N>` (its position in the workbook, from 1), a cell `sheet<N>/r<row>c<col>`
(from 1). They hold no spaces, so they can be written in the ground truth as they are.
"""

from __future__ import annotations

import datetime as dt
import warnings
from dataclasses import dataclass
from pathlib import Path

from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import range_boundaries

from rag.ingest.xlsx.display import display_value


@dataclass(frozen=True)
class Cell:
    row: int
    column: int
    display: str                  # what Excel shows; "" when empty
    value: str | int | float | bool | None   # cached value (dates as ISO text); None when empty
    formula: str | None           # the formula text, cross-sheet references as written
    number_format: str
    format_id: int | None
    fallback: str | None          # why the display is General instead of the cell's format
    no_cached_value: bool         # a formula whose cached value is missing


@dataclass(frozen=True)
class Merge:
    first_row: int
    first_column: int
    last_row: int
    last_column: int

    @property
    def ref(self) -> str:
        return (f"{get_column_letter(self.first_column)}{self.first_row}:"
                f"{get_column_letter(self.last_column)}{self.last_row}")


@dataclass(frozen=True)
class TableObject:
    name: str
    first_row: int
    first_column: int
    last_row: int
    last_column: int
    header_rows: int


@dataclass(frozen=True)
class Sheet:
    index: int                    # position in the workbook, from 1
    name: str
    state: str                    # "visible", "hidden", "veryHidden"
    cells: tuple[Cell, ...]       # stored cells with a display value or a formula, by row then column
    merges: tuple[Merge, ...]
    hidden_rows: tuple[int, ...]
    hidden_columns: tuple[int, ...]
    tables: tuple[TableObject, ...]
    images: int
    charts: int

    @property
    def address(self) -> str:
        return f"sheet{self.index}"


@dataclass(frozen=True)
class XlsxFile:
    sheets: tuple[Sheet, ...]
    chart_sheets: tuple[str, ...]     # names of chart sheets, not read


def cell_address(sheet: int, row: int, column: int) -> str:
    return f"sheet{sheet}/r{row}c{column}"


def cell_ref(row: int, column: int) -> str:
    """A1-style reference, for people."""
    return f"{get_column_letter(column)}{row}"


def _stored(value):
    if isinstance(value, dt.datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, (dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return str(value)
    return value


def _formula(value) -> str | None:
    if isinstance(value, str) and value.startswith("="):
        return value
    text = getattr(value, "text", None)       # ArrayFormula, DataTableFormula
    return text if isinstance(text, str) else None


def _hidden_columns(ws) -> tuple[int, ...]:
    hidden: set[int] = set()
    for dimension in ws.column_dimensions.values():
        if dimension.hidden and dimension.min is not None:
            hidden.update(range(dimension.min, (dimension.max or dimension.min) + 1))
    return tuple(sorted(hidden))


def read_xlsx(path: Path) -> XlsxFile:
    import openpyxl
    from openpyxl.chartsheet import Chartsheet
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")      # unsupported extensions (data validation) are not read
        values = openpyxl.load_workbook(path, data_only=True)
        formulas = openpyxl.load_workbook(path, data_only=False)
    sheets: list[Sheet] = []
    charts: list[str] = []
    for index, name in enumerate(values.sheetnames, start=1):
        ws = values[name]
        if isinstance(ws, Chartsheet):
            charts.append(name)
            continue
        wf = formulas[name]
        cells: list[Cell] = []
        for (row, column), cell in sorted(ws._cells.items()):
            formula = _formula(wf._cells[(row, column)].value) if (row, column) in wf._cells else None
            value = cell.value
            if value is None and formula is None:
                continue
            format_id = getattr(cell._style, "numFmtId", None)
            shown = display_value(value, cell.number_format, format_id, error=cell.data_type == "e")
            cells.append(Cell(row, column, shown.text, _stored(value), formula, cell.number_format,
                              format_id, shown.fallback, formula is not None and value is None))
        merges = tuple(sorted((Merge(r.min_row, r.min_col, r.max_row, r.max_col) for r in ws.merged_cells.ranges),
                              key=lambda m: (m.first_row, m.first_column)))
        hidden_rows = tuple(sorted(r for r, d in ws.row_dimensions.items() if d.hidden))
        tables = []
        for table in ws.tables.values():
            first_column, first_row, last_column, last_row = range_boundaries(table.ref)
            tables.append(TableObject(table.name, first_row, first_column, last_row, last_column,
                                      table.headerRowCount if table.headerRowCount is not None else 1))
        sheets.append(Sheet(index, name, ws.sheet_state, tuple(cells), merges, hidden_rows,
                            _hidden_columns(ws), tuple(sorted(tables, key=lambda t: (t.first_row, t.first_column))),
                            len(ws._images), len(ws._charts)))
    return XlsxFile(tuple(sheets), tuple(charts))
