from __future__ import annotations
import html
from experiments.table_extraction.domain.models.tables import TableCandidate, TableCell

def _mapped_cells(candidate: TableCandidate) -> dict[tuple[int, int], TableCell]:
    """按单元格起始网格位置索引可用于 HTML 重建的单元格。"""
    mapped: dict[tuple[int, int], TableCell] = {}
    for cell in candidate.cells:
        if cell.span_source != "unavailable" and None not in (cell.start_row_offset_idx, cell.start_col_offset_idx, cell.end_row_offset_idx, cell.end_col_offset_idx):
            mapped[(cell.start_row_offset_idx, cell.start_col_offset_idx)] = cell
    return mapped

def render_table(candidate: TableCandidate) -> str:
    """用推断的行列跨度渲染表格，并显式标记未覆盖网格。"""
    row_count, column_count = candidate.row_count or 0, candidate.column_count or 0
    starts = _mapped_cells(candidate)
    covered: set[tuple[int, int]] = set()
    body: list[str] = []
    for row in range(row_count):
        parts = ["<tr>"]
        for column in range(column_count):
            if (row, column) in covered:
                continue
            cell = starts.get((row, column))
            if cell:
                for covered_row in range(cell.start_row_offset_idx or row, cell.end_row_offset_idx or row + 1):
                    for covered_column in range(cell.start_col_offset_idx or column, cell.end_col_offset_idx or column + 1):
                        covered.add((covered_row, covered_column))
                text = html.escape(cell.text or "").replace("\n", "<br>")
                parts.append(f'<td rowspan="{cell.row_span}" colspan="{cell.col_span}">{text}</td>')
            else:
                parts.append('<td class="uncovered" title="未覆盖的逻辑网格"></td>')
        parts.append("</tr>")
        body.append("".join(parts))
    document = """<!doctype html><html><head><meta charset="utf-8"><style>
table { border-collapse: collapse; } td { border: 1px solid #777; padding: 4px; vertical-align: top; }
.uncovered { background: #fff3cd; min-width: 18px; }
</style></head><body><table>""" + "".join(body) + "</table></body></html>"
    unplaced = [cell.text for cell in candidate.cells
                if cell.span_source == 'unavailable' and cell.text]
    if candidate.unplaced_text:
        unplaced.append(candidate.unplaced_text)
    if unplaced:
        detail = '<h2>无法定位到网格的原始文本</h2><pre>' + html.escape('\n'.join(unplaced)) + '</pre>'
        document = document.replace('</body>', detail + '</body>')
    return document

