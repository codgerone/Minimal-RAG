from rag.v2.table_header import classify_cell, detect_header
from rag.v2.table_models import GridPosition, TableCandidate, TableCell, TableRegion


def _table(rows: list[list[str | None]], *, spans: tuple[TableCell, ...] = ()) -> TableCandidate:
    row_count, column_count = len(rows), len(rows[0])
    covered = {
        (row, col)
        for cell in spans
        for row in range(cell.start_row_offset_idx or 0, cell.end_row_offset_idx or 0)
        for col in range(cell.start_col_offset_idx or 0, cell.end_col_offset_idx or 0)
    }
    cells = list(spans)
    for row, values in enumerate(rows):
        for col, value in enumerate(values):
            if (row, col) not in covered:
                cells.append(TableCell(f"c{row}_{col}", value, row, row + 1, col, col + 1,
                                       1, 1, None, ("body",), "native", (f"/{row}/{col}",)))
    return TableCandidate(
        "table", "pymupdf", "lines", "raw", (TableRegion(1, 100, 100, None, "raw", None, "missing_bbox"),),
        row_count, column_count, tuple(float(i) for i in range(column_count + 1)),
        tuple(float(i) for i in range(row_count + 1)), tuple(cells), (), None, (),
    )


def test_shape_classifier_is_whole_cell_and_preserves_placeholders_as_text() -> None:
    assert classify_cell(" USD 96.80 ") == "number"
    assert classify_cell("25.09.25") == "date"
    assert classify_cell("HXE12ES") == "text_shape"
    assert classify_cell("N/A") == "text_shape"
    assert classify_cell("  \n ") == "empty"


def test_unique_single_level_header_is_identified() -> None:
    decision = detect_header(_table([["产品", "数量"], ["A", "100"], ["B", "200"]]))
    assert decision.outcome == "identified"
    assert decision.header_start_row == 0 and decision.header_end_row == 1
    assert decision.paths[1].display_parts == ("数量",)
    assert decision.evaluations[0].supporting_columns == (1,)


def test_numeric_continuation_does_not_veto_another_supporting_column() -> None:
    decision = detect_header(_table([["地区", "销量", "2025"], ["华东", "100", "200"], ["华南", "300", "400"]]))
    assert decision.outcome == "identified"
    assert decision.evaluations[0].supporting_columns == (1,)


def test_effective_text_cannot_be_skipped_to_find_numbers() -> None:
    decision = detect_header(_table([["数量"], ["台"], ["100"], ["200"]]))
    assert decision.outcome == "identified"
    assert decision.header_end_row == 2
    assert decision.evaluations[0].result_reason == "no_type_transition"


def test_prefix_full_width_row_is_skipped_but_preserved_in_decision() -> None:
    span = TableCell("title", "采购明细", 0, 1, 0, 2, 1, 2, None, ("unknown",), "native", ("/title",))
    decision = detect_header(_table([[None, None], ["产品", "数量"], ["A", "1"], ["B", "2"]], spans=(span,)))
    assert decision.outcome == "identified"
    assert decision.skipped_prefix_rows == (0,)
    assert decision.header_start_row == 1


def test_missing_candidate_position_rejects_that_candidate() -> None:
    table = _table([["产品", "数量"], ["A", "1"], ["B", "2"]])
    cells = tuple(cell for cell in table.cells if cell.cell_id != "c0_1")
    table = TableCandidate(table.candidate_id, table.tool, table.strategy, table.source_ref, table.regions,
                           table.row_count, table.column_count, table.x_boundaries, table.y_boundaries,
                           cells, (GridPosition(0, 1),), None, ())
    decision = detect_header(table)
    assert decision.outcome == "undetermined"
    assert decision.evaluations[0].structural_issues[0].code == "missing_header_position"
