import datetime as dt

import openpyxl
import pytest
from openpyxl.worksheet.table import Table

from rag.ingest.xlsx.display import display_value
from rag.ingest.xlsx.reader import read_xlsx

ACCOUNTING = '_-* #,##0.00_-;\\-* #,##0.00_-;_-* "-"??_-;_-@_-'
YUAN = '_ "¥"* #,##0.00_ ;_ "¥"* \\-#,##0.00_ ;_ "¥"* "-"??_ ;_ @_ '


@pytest.mark.parametrize("value, fmt, fid, expected", [
    (733487.9999999999, ACCOUNTING, 164, "733,488.00"),
    (0, ACCOUNTING, 164, "-"),
    (-1234.5, ACCOUNTING, 164, "-1,234.50"),
    (52217290.04608, YUAN, 170, "¥52,217,290.05"),
    (570000, '_-* #,##0\\ _€_-;\\-* #,##0\\ _€_-;_-* "-"??\\ _€_-;_-@_-', 165, "570,000"),
    (6482959.2608, '_-[$$-409]* #,##0.00_ ;_-[$$-409]* \\-#,##0.00\\ ;_-[$$-409]* "-"??_ ;_-@_ ', 175,
     "$6,482,959.26"),
    (-7366999.16, '"US$"#,##0.00_);[Red]\\("US$"#,##0.00\\)', 177, "(US$7,366,999.16)"),
    (1.0, '[$USD]\\ #,##0.00', 166, "USD 1.00"),
    (0.00125, "0.00%", 10, "0.13%"),
    (12345.678, "0.00E+00", 180, "1.23E+04"),
    (1234, "000-0000", 181, "000-1234"),
    (0.5, "#.00", 182, ".50"),
    (2.675, "0.00", 2, "2.68"),            # rounded from 15 significant digits, as Excel does
    (6.112399999999999, "General", 0, "6.1124"),
    (45000, "General", 0, "45000"),
    (1 / 3, "General", 0, "0.33333333333"),      # at most 11 significant digits
    (1.5, "@", 49, "1.5"),
    (-0.4, "0", 1, "-0"),
])
def test_number_formats(value, fmt, fid, expected):
    shown = display_value(value, fmt, fid)
    assert (shown.text, shown.fallback) == (expected, None)


@pytest.mark.parametrize("value, fmt, fid, expected", [
    (dt.datetime(2024, 9, 6), "mm-dd-yy", 14, "2024-09-06"),               # follows the system: fixed
    (dt.datetime(2024, 9, 16, 13, 5), "m/d/yy h:mm", 22, "2024-09-16 13:05"),
    (dt.datetime(2024, 9, 6), "d-mmm-yy", 15, "6-Sep-24"),
    (dt.datetime(2024, 9, 6), '[$-80A]dddd, d "de" mmmm "de" yyyy', 183, "viernes, 6 de septiembre de 2024"),
    (dt.datetime(2024, 9, 6), "[$-804]mmmm ddd", 184, "九月 周五"),
    (dt.datetime(2024, 9, 6), 'yyyy"年"m"月"d"日"', 185, "2024年9月6日"),
    (dt.datetime(2024, 9, 6, 15, 4, 5), "h:mm:ss AM/PM", 186, "3:04:05 PM"),
    (dt.datetime(2024, 9, 6, 15, 4), "mm/dd hh:mm", 187, "09/06 15:04"),
    (45541, "yyyy-mm-dd", 188, "2024-09-06"),                              # a serial number
])
def test_date_formats(value, fmt, fid, expected):
    shown = display_value(value, fmt, fid)
    assert (shown.text, shown.fallback) == (expected, None)


@pytest.mark.parametrize("value, fmt, reason, expected", [
    (5, "[<100]0;0", "condition", "5"),
    (1234567, "#,##0,", "scaling", "1234567"),
    (0.5, "# ?/?", "fraction", "0.5"),
    (dt.timedelta(hours=30), "[h]:mm", "elapsed_time", "1.25"),
])
def test_unsupported_formats_fall_back_to_general(value, fmt, reason, expected):
    shown = display_value(value, fmt, 190)
    assert (shown.text, shown.fallback) == (expected, reason)


def test_text_boolean_error_and_empty():
    assert display_value("abc ", ACCOUNTING, 164).text == "abc "
    assert display_value(True).text == "TRUE"
    assert display_value("#N/A", error=True).text == "#N/A"
    assert display_value(None).text == ""


def _workbook(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Main"
    ws["A1"] = "Title"
    ws.merge_cells("A1:C1")
    ws["A2"], ws["B2"], ws["C2"] = "Item", "Qty", "Price"
    ws["A3"], ws["B3"], ws["C3"] = "Meter", 1200, 5.18
    ws["C3"].number_format = "#,##0.00"
    ws["B4"] = "=B3*2"                        # never computed: no cached value
    ws["D3"] = "=Other!A1"
    ws.row_dimensions[3].hidden = True
    ws.column_dimensions["C"].hidden = True
    other = wb.create_sheet("Other")
    other["A1"], other["B1"], other["A2"], other["B2"] = "k", "v", "x", 1
    other.add_table(Table(displayName="Tbl", ref="A1:B2"))
    secret = wb.create_sheet("Secret")
    secret.sheet_state = "veryHidden"
    secret["A1"] = "hidden text"
    wb.save(path)


def test_reader_facts(tmp_path):
    path = tmp_path / "book.xlsx"
    _workbook(path)
    book = read_xlsx(path)
    main, other, secret = book.sheets
    assert [s.name for s in book.sheets] == ["Main", "Other", "Secret"]
    assert (secret.state, secret.cells[0].display) == ("veryHidden", "hidden text")
    cells = {(c.row, c.column): c for c in main.cells}
    assert cells[(3, 3)].display == "5.18"
    assert cells[(4, 2)].formula == "=B3*2" and cells[(4, 2)].no_cached_value and cells[(4, 2)].display == ""
    assert cells[(3, 4)].formula == "=Other!A1"
    assert [m.ref for m in main.merges] == ["A1:C1"]
    assert (main.hidden_rows, main.hidden_columns) == ((3,), (3,))
    assert [(t.name, t.first_row, t.last_column, t.header_rows) for t in other.tables] == [("Tbl", 1, 2, 1)]


# --- regions and the parser -------------------------------------------------------------------

from openpyxl.styles import Border, Side

from rag.ingest.parsers.xlsx_native import XlsxNativeParser
from rag.ingest.xlsx.regions import sheet_regions
from rag.models import PrimaryTablePlaceholder, PrimaryText, SourceDocument


def _regions(build):
    import tempfile
    from pathlib import Path
    wb = openpyxl.Workbook()
    build(wb.active)
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "r.xlsx"
        wb.save(path)
        sheet = read_xlsx(path).sheets[0]
    return [(r.kind, r.first_row, r.first_column, r.last_row, r.last_column) for r in sheet_regions(sheet)]


def _fill(ws, ref, value="x"):
    for row in ws[ref]:
        for cell in row:
            cell.value = value


def test_connected_cells_are_one_region_and_shape_decides_the_kind():
    def build(ws):
        _fill(ws, "A1:C3")          # table
        ws["A5"], ws["B5"] = "a", "b"   # one row
        ws["E1"], ws["E2"] = "c", "d"   # one column, apart from the table
    assert _regions(build) == [("table", 1, 1, 3, 3), ("column", 1, 5, 2, 5), ("row", 5, 1, 5, 2)]


def test_merge_with_value_occupies_its_cells_and_an_empty_merge_does_not():
    def build(ws):
        ws["A1"] = "title"
        ws.merge_cells("A1:C2")
        ws["D3"] = "x"              # touches the merge only diagonally
        ws.merge_cells("A5:B6")      # empty
        ws["C6"] = "y"
    assert _regions(build) == [("row", 1, 1, 2, 3), ("row", 3, 4, 3, 4), ("row", 6, 3, 6, 3)]


def test_bordered_empty_cells_connect_and_alone_are_dropped():
    side = Side(style="thin")
    def build(ws):
        ws["A1"], ws["C1"], ws["A2"], ws["C2"] = "k", "v", "k2", "v2"
        ws["B1"].border = ws["B2"].border = Border(left=side)
        ws["F9"].border = Border(top=side)
    assert _regions(build) == [("table", 1, 1, 2, 3)]


def test_side_by_side_blocks_join_only_when_top_and_bottom_align():
    def build(ws):
        _fill(ws, "A1:B4")
        _fill(ws, "E1:F4")           # same rows: one table
        _fill(ws, "A7:B10")
        _fill(ws, "E7:F8")           # rows 7–8 only: a table of its own
        ws["A12"], ws["E12"] = "t1", "t2"   # single rows on the same row stay apart
    assert _regions(build) == [("table", 1, 1, 4, 6), ("table", 7, 1, 10, 2), ("table", 7, 5, 8, 6),
                               ("row", 12, 1, 12, 1), ("row", 12, 5, 12, 5)]


def test_overlapping_rectangles_merge():
    def build(ws):
        _fill(ws, "A1:A4")
        _fill(ws, "B1:D1")           # an L shape: connected
        ws["C3"] = "inside"          # not connected, but inside the L's rectangle
    assert _regions(build) == [("table", 1, 1, 4, 4)]


def test_table_object_is_a_table(tmp_path):
    path = tmp_path / "book.xlsx"
    _workbook(path)
    other = read_xlsx(path).sheets[1]
    assert [(r.kind, r.table_object) for r in sheet_regions(other)] == [("table", "Tbl")]


def test_parser_elements_and_addresses(tmp_path):
    path = tmp_path / "book.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Plan"
    ws["A1"], ws["B1"] = " Name ", "Value"     # one row
    ws["A3"], ws["B3"], ws["A4"], ws["B4"] = "No.", "Date", 1, dt.datetime(2024, 9, 6)
    ws["B4"].number_format = "yyyy-mm-dd"
    wb.save(path)
    source = SourceDocument("doc", "book.xlsx", "book.xlsx", path, "hash", file_type="xlsx")
    primary = XlsxNativeParser().parse(source).primary
    title, row, table = primary.elements
    assert (title.kind, title.text, title.sources[0].address) == ("title", "Plan", "sheet1#name")
    assert isinstance(row, PrimaryText) and row.text == "Name | Value"
    assert [(p.start, p.end, p.address, p.offset) for p in row.pieces] == [
        (0, 4, "sheet1/r1c1", 1), (7, 12, "sheet1/r1c2", 0)]
    assert isinstance(table, PrimaryTablePlaceholder) and table.sources[0].label == "Plan!A3:B4"
    cells = {(c.start_row_offset_idx, c.start_col_offset_idx): (c.text, c.address)
             for c in primary.native_tables[0].table.cells}
    assert cells[(1, 1)] == ("2024-09-06", "sheet1/r4c2")
    assert primary.page_count == 1


# --- excerpt location -------------------------------------------------------------------------

from rag.eval.dataset import Excerpt
from rag.eval.xlsx_anchors import file_words, locate_excerpt, _tables


def _excerpt(text, hint=None):
    return Excerpt("x1", "doc", "book.xlsx", (), text, False, hint, None, "missing", ())


def _book(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Plan"
    rows = [("Company", "Qty", "Price"), ("ENSA", 100, 5), ("ENOSA", 100, 7), ("ELUC", 300, 5)]
    for r, row in enumerate(rows, start=1):
        for c, value in enumerate(row, start=1):
            ws.cell(r, c, value)
    ws["A7"] = "Payment: 90 days after conformity"
    other = wb.create_sheet("Other")
    other["A1"], other["B1"], other["A2"], other["B2"] = "ENOSA", 100, "x", "y"
    path = tmp_path / "book.xlsx"
    wb.save(path)
    return read_xlsx(path)


def test_excerpt_pieces_are_cells_and_the_closest_occurrences_are_chosen(tmp_path):
    book = _book(tmp_path)
    anchor = locate_excerpt(_excerpt("ENOSA | 100"), file_words(book), book, _tables(book))
    assert anchor.status == "ambiguous"
    assert [(w.address, w.start, w.end) for w in anchor.words] == [("sheet1/r3c1", 0, 5), ("sheet1/r3c2", 0, 3)] \
        or [(w.address, w.start, w.end) for w in anchor.words] == [("sheet2/r1c1", 0, 5), ("sheet2/r1c2", 0, 3)]
    assert anchor.locations in (("Plan!A1:C4",), ("Other!A1:B2",))


def test_part_of_a_long_text_cell_and_a_hint_choose_the_column(tmp_path):
    book = _book(tmp_path)
    words, tables = file_words(book), _tables(book)
    part = locate_excerpt(_excerpt("90 days"), words, book, tables)
    assert part.status == "located" and [(w.address, w.start, w.end) for w in part.words] == [
        ("sheet1/r7c1", 9, 11), ("sheet1/r7c1", 12, 16)]
    assert part.locations == ("Plan!A7",)
    hinted = locate_excerpt(_excerpt("ELUC | 5", "Price"), words, book, tables)
    assert [w.address for w in hinted.words] == ["sheet1/r4c1", "sheet1/r4c3"]   # not the Qty column
    assert locate_excerpt(_excerpt("no such text"), words, book, tables).status == "missing"
