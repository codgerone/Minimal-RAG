"""`markdown_rows_v1`: one Markdown pipe row per table row, header written once.

Merged cells are stated by structure only: the value sits at the cell's top-left position, covered
positions to its right read `←`, covered positions below read `↑`. Lines carry where each cell's
text sits and how a line is rewritten when it opens a chunk in the middle of a vertical merge.
"""

from __future__ import annotations

from rag.ingest.tables.models import (
    CellSpan, Continuation, HeaderDecision, SerializedTable, SerializedTableLine,
    StructuredTable, TableCell,
)

LEFT, UP, MISSING = "←", "↑", "〔缺失单元格〕"


def _normalize(text: str | None) -> str:
    return " ".join((text or "").split()).replace("|", "\\|")


class _Line:
    """Builds `| a | b |` while recording where each slot's text sits."""

    def __init__(self) -> None:
        self.text = "|"
        self.slots: list[tuple[int, int]] = []

    def add(self, value: str) -> None:
        self.text += " "
        start = len(self.text)
        self.text += value
        self.slots.append((start, len(self.text)))
        self.text += " |"


def serialize_markdown_rows(
    table: StructuredTable, header: HeaderDecision, *, table_node_id: str | None = None,
) -> SerializedTable:
    owner = table_node_id or table.table_id
    columns = table.column_count or 0
    identified = header.outcome == "identified"
    header_start = header.header_start_row if identified else None
    header_rows = set(range(header.header_start_row or 0, header.header_end_row or 0)) if identified else set()

    grid: dict[tuple[int, int], TableCell] = {}
    for cell in sorted(table.cells, key=lambda item: item.cell_id):
        if cell.start_row_offset_idx is None or cell.start_col_offset_idx is None:
            continue
        assert cell.end_row_offset_idx is not None and cell.end_col_offset_idx is not None
        for row in range(cell.start_row_offset_idx, cell.end_row_offset_idx):
            for col in range(cell.start_col_offset_idx, cell.end_col_offset_idx):
                grid.setdefault((row, col), cell)

    lines: list[SerializedTableLine] = []
    anchors: dict[str, tuple[str, int, int]] = {}   # cell_id -> line_id and value offsets

    def line_id() -> str:
        return f"{owner}_line_{len(lines) + 1:06d}"

    def emit_header() -> None:
        built, spans, cell_ids = _Line(), [], []
        paths = {item.column_index: item for item in header.paths}
        for col in range(columns):
            path = paths.get(col)
            built.add(_normalize(" / ".join(path.display_parts)) if path else "")
            ids = path.cell_ids if path else ()
            cell_ids.extend(ids)
            spans.append(CellSpan(tuple(ids), *built.slots[-1]))
        lines.append(SerializedTableLine(line_id(), "header", built.text,
                                         tuple(sorted(header_rows)), tuple(dict.fromkeys(cell_ids)),
                                         tuple(spans)))

    def emit_row(row: int) -> None:
        built, spans, cell_ids = _Line(), [], []
        continued: dict[str, list[int]] = {}           # merged cell from a data row above -> slots
        for col in range(columns):
            cell = grid.get((row, col))
            if cell is None:
                built.add(MISSING)
                spans.append(CellSpan((), *built.slots[-1]))
                continue
            if cell.start_row_offset_idx == row and cell.start_col_offset_idx == col:
                built.add(_normalize(cell.text))
                start, end = built.slots[-1]
                spans.append(CellSpan((cell.cell_id,), start, end))
                cell_ids.append(cell.cell_id)
                anchors[cell.cell_id] = (line_id(), start, end)
            elif cell.start_row_offset_idx == row:
                built.add(LEFT)
                spans.append(CellSpan((), *built.slots[-1]))
            else:
                built.add(UP)
                spans.append(CellSpan((), *built.slots[-1]))
                if cell.cell_id in anchors:
                    continued.setdefault(cell.cell_id, []).append(len(built.slots) - 1)
        continuations = []
        for cell_id, slots in continued.items():
            anchor_line, anchor_start, anchor_end = anchors[cell_id]
            anchor_text = next(item.text for item in lines if item.line_id == anchor_line)
            value = anchor_text[anchor_start:anchor_end]
            if not value:
                continue
            start, end = built.slots[slots[0]][0], built.slots[slots[-1]][1]
            text = f"{UP} {value}" + f" | {LEFT}" * (len(slots) - 1)
            continuations.append(Continuation(start, end, text, anchor_line, anchor_start, anchor_end))
        lines.append(SerializedTableLine(line_id(), "data", built.text, (row,), tuple(cell_ids),
                                         tuple(spans), tuple(continuations)))

    for row in range(table.row_count or 0):
        if row == header_start:
            emit_header()
        if row in header_rows:
            continue
        emit_row(row)

    if table.unplaced_text is not None:
        unplaced_ids = tuple(cell.cell_id for cell in table.cells if cell.start_row_offset_idx is None)
        lines.append(SerializedTableLine(line_id(), "unplaced_text", table.unplaced_text, (), unplaced_ids))
    result = tuple(lines)
    return SerializedTable("\n".join(item.text for item in result), result, "markdown_rows_v1")
