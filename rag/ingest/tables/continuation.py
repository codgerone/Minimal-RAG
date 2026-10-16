"""Cross-page table continuation: judge which tables continue the one before, and merge them.

Rules: docs/rules/table-continuation.md. Inputs are upstream deliverables only — the primary
document's reading order and node kinds, the table slots (Docling's table positions) and the
adopted tables; nothing is re-read from the PDF.
"""

from __future__ import annotations

import unicodedata
from dataclasses import replace

from rag.ingest.tables.header import column_observations, sample_rows
from rag.ingest.tables.models import (
    ColumnGeometry, ContinuationEvaluation, ContinuationFailure, ContinuationReport, GridPosition,
    HeaderDecision, StructuredTable, TableCell, TableSegment, TableSlot, ValueType,
)
from rag.models import PrimaryDocument, PrimaryList, PrimaryTablePlaceholder, PrimaryText

RULE_VERSION = "table_continuation_v1"
POSITION_BAND = 0.2            # bottom / top fraction of the page
TEXT_WIDTH_LIMIT = 120         # per side; full-width characters count 2
EXTENT_OVERLAP_MIN = 0.8
SEPARATOR_SHIFT_MAX = 0.35
TYPE_SAMPLE_ROWS = 8
MIN_TYPE_OBSERVATIONS = 2
_PAGE_FURNITURE = frozenset({"header", "footer"})


def text_width(text: str) -> int:
    """Width in half-width units: full-width (East Asian F/W) characters 2, others 1, whitespace 0."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1
               for ch in text if not ch.isspace())


def _between_widths(elements, earlier_page: int, later_page: int) -> tuple[int, int]:
    before = after = 0
    for element in elements:
        if isinstance(element, PrimaryText):
            if element.kind in _PAGE_FURNITURE:
                continue
            parts = ((element.text, element.sources),)
        elif isinstance(element, PrimaryList):
            parts = tuple((item.text, item.sources) for item in element.items)
        else:
            continue
        for text, sources in parts:
            page = sources[0].page_number if sources else None
            if page == earlier_page:
                before += text_width(text)
            elif page == later_page:
                after += text_width(text)
    return before, after


def _column_intervals(table: StructuredTable) -> dict[int, tuple[float, float]]:
    """Per column: from the leftmost to the rightmost edge of its single-column cells with text."""
    out: dict[int, tuple[float, float]] = {}
    for cell in table.cells:
        if (cell.bbox is None or cell.start_col_offset_idx is None or cell.end_col_offset_idx is None
                or cell.end_col_offset_idx - cell.start_col_offset_idx != 1 or not (cell.text or "").strip()):
            continue
        col = cell.start_col_offset_idx
        low, high = out.get(col, (cell.bbox.x0, cell.bbox.x1))
        out[col] = (min(low, cell.bbox.x0), max(high, cell.bbox.x1))
    return out


def _separators(intervals: dict[int, tuple[float, float]], columns: int):
    """Between columns k and k+1: the range from one interval's edge to the next (a line or a gap)."""
    out = {}
    for k in range(columns - 1):
        if k in intervals and k + 1 in intervals:
            (l0, r0), (l1, r1) = intervals[k], intervals[k + 1]
            out[k] = (min(r0, l1), max(r0, l1), r0 - l0, r1 - l1)
    return out


def column_geometry(earlier: StructuredTable, later: StructuredTable,
                    ) -> tuple[ColumnGeometry, float | None, float | None, int, int]:
    """(verdict, extent overlap, max separator shift, comparable separators, all separators)."""
    columns = earlier.column_count or 0
    a, b = _column_intervals(earlier), _column_intervals(later)
    total = max(columns - 1, 0)
    if not a or not b:
        return "unavailable", None, None, 0, total
    a0, a1 = min(x for x, _ in a.values()), max(x for _, x in a.values())
    b0, b1 = min(x for x, _ in b.values()), max(x for _, x in b.values())
    union = max(a1, b1) - min(a0, b0)
    extent = max(0.0, min(a1, b1) - max(a0, b0)) / union if union > 0 else 0.0
    sa, sb = _separators(a, columns), _separators(b, columns)
    shifts = []
    for k in sorted(sa.keys() & sb.keys()):
        lo_a, hi_a, *widths_a = sa[k]
        lo_b, hi_b, *widths_b = sb[k]
        distance = max(0.0, max(lo_a, lo_b) - min(hi_a, hi_b))   # 0 when the ranges meet
        narrowest = min(*widths_a, *widths_b)
        shifts.append(distance / narrowest if narrowest > 0 else float("inf"))
    comparable = len(shifts)
    worst = max(shifts) if shifts else None
    aligned = (extent >= EXTENT_OVERLAP_MIN and 2 * comparable >= total
               and all(shift <= SEPARATOR_SHIFT_MAX for shift in shifts))
    return ("aligned" if aligned else "misaligned"), extent, worst, comparable, total


def _stable_types(table: StructuredTable, rows) -> tuple[tuple[ValueType, ...], ...]:
    sampled, _skipped = sample_rows(table, rows, TYPE_SAMPLE_ROWS)
    return tuple(column_observations(table, sampled, col, MIN_TYPE_OBSERVATIONS)[1]
                 for col in range(table.column_count or 0))


def _body_rows_from_bottom(table: StructuredTable, header: HeaderDecision) -> range:
    first = header.header_end_row if header.outcome == "identified" and header.header_end_row else 0
    return range((table.row_count or 0) - 1, first - 1, -1)


def evaluate_pair(earlier_slot: TableSlot, later_slot: TableSlot,
                  earlier: StructuredTable, later: StructuredTable,
                  earlier_header: HeaderDecision, later_header: HeaderDecision,
                  between) -> ContinuationEvaluation:
    assert earlier_slot.page_number is not None and later_slot.page_number is not None
    failures: list[ContinuationFailure] = []

    def ratio(slot: TableSlot, edge: str) -> float | None:
        if slot.bbox is None or not slot.page_height:
            return None
        return getattr(slot.bbox, edge) / slot.page_height

    bottom, top = ratio(earlier_slot, "y1"), ratio(later_slot, "y0")
    if bottom is None or top is None:
        failures.append("position_unavailable")
    else:
        if bottom < 1 - POSITION_BAND:
            failures.append("earlier_not_at_page_end")
        if top > POSITION_BAND:
            failures.append("later_not_at_page_start")
    before, after = _between_widths(between, earlier_slot.page_number, later_slot.page_number)
    if before > TEXT_WIDTH_LIMIT or after > TEXT_WIDTH_LIMIT:
        failures.append("text_between_too_long")

    geometry: ColumnGeometry | None = None
    extent = shift = None
    comparable = total = 0
    earlier_types: tuple[tuple[ValueType, ...], ...] = ()
    later_types: tuple[tuple[ValueType, ...], ...] = ()
    conflicts: tuple[int, ...] = ()
    if earlier_header.reason == "invalid_grid" or later_header.reason == "invalid_grid":
        failures.append("invalid_grid")
    elif earlier.column_count != later.column_count:
        failures.append("column_count_differs")
    else:
        geometry, extent, shift, comparable, total = column_geometry(earlier, later)
        if geometry == "unavailable":
            failures.append("geometry_unavailable")
        elif geometry == "misaligned":
            failures.append("columns_misaligned")
        earlier_types = _stable_types(earlier, _body_rows_from_bottom(earlier, earlier_header))
        later_types = _stable_types(later, range(later.row_count or 0))
        # Only a contradiction counts: both columns stable, with no type in common.
        conflicts = tuple(col for col, (x, y) in enumerate(zip(earlier_types, later_types))
                          if x and y and not set(x) & set(y))
        if conflicts:
            failures.append("column_types_conflict")
    if later_header.outcome == "identified":
        failures.append("later_has_header")
    return ContinuationEvaluation(
        earlier_slot.slot_id, later_slot.slot_id, earlier_slot.page_number, later_slot.page_number,
        bottom, top, before, after, earlier.column_count, later.column_count, geometry, extent, shift,
        comparable, total, earlier_types, later_types, conflicts, later_header.outcome, tuple(failures))


def detect_continuations(primary: PrimaryDocument, tables: dict[str, StructuredTable],
                         headers: dict[str, HeaderDecision]) -> ContinuationReport:
    """Evaluate every pair of tables adjacent in reading order on consecutive pages; chain the hits."""
    slots = {slot.slot_id: slot for slot in primary.table_slots}
    positions = [(index, element.slot_id) for index, element in enumerate(primary.elements)
                 if isinstance(element, PrimaryTablePlaceholder)]
    evaluations: list[ContinuationEvaluation] = []
    for (i, earlier_id), (j, later_id) in zip(positions, positions[1:]):
        earlier_slot, later_slot = slots[earlier_id], slots[later_id]
        # Only single-page tables on consecutive pages are candidates; Docling leaves the page
        # number empty for a table position spanning pages.
        if (earlier_slot.page_number is None or later_slot.page_number is None
                or later_slot.page_number != earlier_slot.page_number + 1):
            continue
        evaluations.append(evaluate_pair(earlier_slot, later_slot, tables[earlier_id], tables[later_id],
                                         headers[earlier_id], headers[later_id],
                                         primary.elements[i + 1:j]))
    chains: list[list[str]] = []
    for item in evaluations:
        if not item.is_continuation:
            continue
        if chains and chains[-1][-1] == item.earlier_slot_id:
            chains[-1].append(item.later_slot_id)
        else:
            chains.append([item.earlier_slot_id, item.later_slot_id])
    return ContinuationReport(RULE_VERSION, tuple(evaluations), tuple(tuple(chain) for chain in chains))


def merge_tables(parts: tuple[tuple[TableSlot, StructuredTable], ...],
                 ) -> tuple[StructuredTable, tuple[TableSegment, ...]]:
    """Stack the parts' rows in order; cells keep their text and coordinates, rows are offset."""
    cells: list[TableCell] = []
    uncovered: list[GridPosition] = []
    segments: list[TableSegment] = []
    offset = 0
    for slot, table in parts:
        rows = table.row_count or 0
        for cell in table.cells:
            cells.append(cell if cell.start_row_offset_idx is None else replace(
                cell, start_row_offset_idx=cell.start_row_offset_idx + offset,
                end_row_offset_idx=(cell.end_row_offset_idx or 0) + offset))
        uncovered += [replace(pos, row_index=pos.row_index + offset) for pos in table.uncovered_grid_positions]
        segments.append(TableSegment(slot.slot_id, table.table_id, slot.page_number, offset, offset + rows))
        offset += rows
    first = parts[0][1]
    unplaced = "\n".join(t.unplaced_text for _, t in parts if t.unplaced_text) or None
    merged = StructuredTable(
        "+".join(t.table_id for _, t in parts), first.tool, first.strategy, offset, first.column_count,
        tuple(cells), tuple(uncovered), unplaced, tuple(s for _, t in parts for s in t.sources),
        tuple(w for _, t in parts for w in t.warnings))
    return merged, tuple(segments)
