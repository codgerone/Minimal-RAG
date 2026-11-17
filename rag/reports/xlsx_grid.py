"""Excel sheets drawn as grids, from the reader's facts and the regions saved in parse-native.json
(rag/ingest/parsers/xlsx_native.py). Used by the ingest review page and the excerpt review page.

Each sheet is a grid with row numbers and column letters, as in Excel. Regions are coloured by kind
(table, text) and outlined; hidden rows, columns and sheets are hatched; a formula without a cached
value, a display written as General instead of its format, and merged cells are marked. Hovering a
cell shows its address, number format, formula and cached value.
"""

from __future__ import annotations

from openpyxl.utils import get_column_letter

from rag.reports.html import esc

STATE_LABELS = {"visible": "", "hidden": "隐藏", "veryHidden": "深度隐藏"}
LEGEND = ('<div class="meta legend xg-legend"><span class="rg-t">表格区域</span><span class="rg-x">正文区域</span>'
          '<span class="hid">隐藏的行、列</span><span class="mg">合并单元格</span>'
          '<span class="nc">公式没有缓存值</span><span class="fb">按 General 写出</span>'
          '<span class="bd">有边框的空格</span></div>')


def _extent(sheet: dict, regions: list[dict]) -> tuple[int, int]:
    rows = [c["row"] for c in sheet["cells"]] + [m["last_row"] for m in sheet["merges"]]
    cols = [c["column"] for c in sheet["cells"]] + [m["last_column"] for m in sheet["merges"]]
    rows += [r["last_row"] for r in regions]
    cols += [r["last_column"] for r in regions]
    return (max(rows) if rows else 0), (max(cols) if cols else 0)


def region_of(regions: list[dict]) -> dict[tuple[int, int], int]:
    """Position → region number (from 1, reading order) for every position a region covers: the
    whole rectangle of a table, the value cells (and their merges) of a text region."""
    found: dict[tuple[int, int], int] = {}
    for number, region in enumerate(regions, start=1):
        for row in range(region["first_row"], region["last_row"] + 1):
            for column in range(region["first_column"], region["last_column"] + 1):
                found.setdefault((row, column), number)
    return found


def sheet_grid(sheet: dict, regions: list[dict], highlight: dict[tuple[int, int], str] | None = None,
               only: tuple[int, int, int, int] | None = None) -> str:
    """The sheet as an HTML grid. `highlight`: position → CSS class added to that cell (excerpt
    review). `only`: (first row, first column, last row, last column) to draw instead of the sheet."""
    last_row, last_col = _extent(sheet, regions)
    first_row, first_col = 1, 1
    if only is not None:
        first_row, first_col, last_row, last_col = only
    if not last_row or not last_col:
        return '<div class="meta">（工作表为空）</div>'
    cells = {(c["row"], c["column"]): c for c in sheet["cells"]}
    merges = {(m["first_row"], m["first_column"]): m for m in sheet["merges"]}
    covered = {(r, c) for m in sheet["merges"] for r in range(m["first_row"], m["last_row"] + 1)
               for c in range(m["first_column"], m["last_column"] + 1)} - set(merges)
    hidden_rows, hidden_cols = set(sheet["hidden_rows"]), set(sheet["hidden_columns"])
    bordered = {tuple(p) for p in sheet.get("bordered", ())}
    owner = region_of(regions)
    kinds = {n: r["kind"] for n, r in enumerate(regions, start=1)}
    highlight = highlight or {}
    head = "".join(f'<th class="{"hid" if c in hidden_cols else ""}">{get_column_letter(c)}</th>'
                   for c in range(first_col, last_col + 1))
    rows = [f'<tr><th></th>{head}</tr>']
    for r in range(first_row, last_row + 1):
        out = [f'<th class="{"hid" if r in hidden_rows else ""}">{r}</th>']
        for c in range(first_col, last_col + 1):
            if (r, c) in covered:
                continue
            classes = []
            number = owner.get((r, c))
            if number is not None:
                classes.append("rg-t" if kinds[number] == "table" else "rg-x")
                classes.append(f"rg{number % 2}")
            if r in hidden_rows or c in hidden_cols:
                classes.append("hid")
            span = ""
            if (r, c) in merges:
                m = merges[(r, c)]
                rs = min(m["last_row"], last_row) - r + 1
                cs = min(m["last_column"], last_col) - c + 1
                span = (f' rowspan="{rs}"' if rs > 1 else "") + (f' colspan="{cs}"' if cs > 1 else "")
                classes.append("mg")
            cell = cells.get((r, c))
            title = f"{get_column_letter(c)}{r}"
            text = ""
            if cell is not None:
                text = cell["display"]
                title += f" · 格式 {cell['number_format']}"
                if cell["formula"]:
                    title += f" · 公式 {cell['formula']}"
                if cell["value"] is not None and str(cell["value"]) != text:
                    title += f" · 缓存值 {cell['value']}"
                if cell["no_cached_value"]:
                    classes.append("nc")
                    title += " · 公式没有缓存值"
                if cell["fallback"]:
                    classes.append("fb")
                    title += f" · 按 General 写出（{cell['fallback']}）"
            elif (r, c) in bordered:
                classes.append("bd")
            if (r, c) in highlight:
                classes.append(highlight[(r, c)])
            if number is not None and owner.get((r - 1, c)) != number:
                classes.append("rg-top")
            if number is not None and owner.get((r, c - 1)) != number:
                classes.append("rg-left")
            out.append(f'<td class="{" ".join(classes)}"{span} title="{esc(title)}">{esc(text)}</td>')
        rows.append("<tr>" + "".join(out) + "</tr>")
    return f'<div class="scroll" data-grid><table class="xg">{"".join(rows)}</table></div>'


def sheet_label(sheet: dict) -> str:
    state = STATE_LABELS.get(sheet["state"], sheet["state"])
    return sheet["name"] + (f"（{state}）" if state else "")
