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
