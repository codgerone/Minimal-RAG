"""Contract-level examples for V3 table content preparation."""

from dataclasses import replace
from pathlib import Path
import hashlib

import fitz
import pytest

from rag.ingest.tables.pdf_words import PyMuPdfEvidenceReader
from rag.ingest.assemble import compose_document
from rag.ingest.tables.prepare import TablePreparationError, prepare_tables
from rag.ingest.tables.header import cell_shape, detect_header, value_types
from rag.ingest.tables.markdown_rows import serialize_markdown_rows
from rag.ingest.tables.select import select_tables
from rag.models import (
    BoundingBox, NativeTableFact, PrimaryDocument, PrimaryTablePlaceholder, SourceDocument, TableNode,
)
from rag.ingest.tables.models import (
    ContentResolution, GridPosition, StructuredTable, TableCandidate, TableCell,
    PageExecution, StrategyExecution, TableExtractionReport, TableRegion, TableSlot,
)


def table(rows: list[list[str | None]], *, spans: tuple[TableCell, ...] = ()) -> StructuredTable:
    occupied = {(row, col) for cell in spans
                for row in range(cell.start_row_offset_idx or 0, cell.end_row_offset_idx or 0)
                for col in range(cell.start_col_offset_idx or 0, cell.end_col_offset_idx or 0)}
    cells = list(spans)
    for row, values in enumerate(rows):
        for col, value in enumerate(values):
            if (row, col) not in occupied:
                cells.append(TableCell(f"c{row}_{col}", value, row, row + 1, col, col + 1,
                                       1, 1, None, ("body",), "native", (f"/{row}/{col}",)))
    return StructuredTable("t", "pymupdf", "lines", len(rows), len(rows[0]),
                           tuple(cells), (), None, (), ())


def test_header_and_serialization_preserve_exact_cell_text() -> None:
    source = table([["产品", "数量"], ["A", '100"'], ["B", "200"]])
    decision = detect_header(source)
    assert decision.outcome == "undetermined"  # quote makes the first quantity text
    result = serialize_markdown_rows(source, decision, table_node_id="node", labeled=True)
    assert result.text == '| 产品 | 数量 |\n| A | 100" |\n| B | 200 |'
    assert result.lines[-1].line_id == "node_line_000003"


def test_header_h01_and_fallback_for_invalid_span() -> None:
    source = table([["产品", "数量"], ["A", "100"], ["B", "200"]])
    result = detect_header(source)
    assert (result.outcome, result.header_start_row, result.header_end_row) == ("identified", 0, 1)
    assert serialize_markdown_rows(source, result, labeled=True).text == (
        "| 产品: A | 数量: 100 |\n| 产品: B | 数量: 200 |"
    )
    assert serialize_markdown_rows(source, result).text == "| 产品 | 数量 |\n| A | 100 |\n| B | 200 |"
    bad_cell = replace(source.cells[0], row_span=2)
    bad = replace(source, cells=(bad_cell,) + source.cells[1:])
    rejected = detect_header(bad)
    assert rejected.reason == "invalid_grid"
    assert "span_mismatch" in {issue.code for issue in rejected.input_issues}


def test_missing_header_position_and_merged_fact() -> None:
    merged = TableCell("title", "采购明细", 0, 1, 0, 2, 1, 2, None,
                       ("unknown",), "native", ("/title",))
    source = table([[None, None], ["产品", "数量"], ["A", "1"], ["B", "2"]], spans=(merged,))
    result = detect_header(source)
    assert result.outcome == "identified"
    assert result.skipped_prefix_rows == (0,)
    assert serialize_markdown_rows(source, result).text.startswith("| 采购明细 | ← |\n| 产品 | 数量 |")
    gap = replace(source, cells=tuple(cell for cell in source.cells if cell.cell_id != "c1_1"),
                  uncovered_grid_positions=(GridPosition(1, 1, "missing_physical_cell"),))
    assert detect_header(gap).evaluations[0].structural_issues[0].code == "missing_header_position"


@pytest.mark.parametrize("value", [
    "28/02/2026", "02/28/2026", "09/04/2026", "2026/02/28", "2026-02-28", "2026.02.28",
    "26/02/28", "26.02.28", "28.02.26", "25.09.25", "28-02-2026", "2026-02", "02/2026",
    "28/02", "02/28", "20260228", "29/02", "2026年2月28日", "2026年2月", "2月28日",
    "28 Feb 2026", "28-Feb-26", "Feb 28, 2026", "February 28th, 2026", "August 2nd",
    "28 de febrero de 2026", "28 febrero", "Feb 2026", "febrero de 2026", "28 setiembre 2026",
])
def test_dates_in_common_formats(value: str) -> None:
    assert "date" in value_types(value)


@pytest.mark.parametrize("value", [
    "1´131,056.00", "1´ 131,056.00", "5’157,615.36", "1,234.56", "1.234,56", "1 000 000", "10,000",
    "20000.00", "4.56", "(1,200.00)", "12%", "12 %", "3‰", "−15", "+15",
])
def test_plain_numbers_in_common_formats(value: str) -> None:
    assert value_types(value)[0] == "number" and "currency" not in value_types(value)


@pytest.mark.parametrize("value", [
    "USD 96.80", "USD96.80", "96.80 USD", "$ 280,875.00", "$280,875.00", "US$ 2,713.41",
    "S/ 1,200.50", "S/. 1,200.50", "€1.234,56", "1.234,56 €", "-$5.00", "$-5.00",
    "(USD 1,200.00)", "100元", "12.5万元", "¥100", "￥100", "USD 5’157,615.36",
])
def test_currency_amounts_in_common_formats(value: str) -> None:
    assert value_types(value) == ("currency",)


@pytest.mark.parametrize("value", ["10.12.25", "25.09.25", "1,00,000", "12,34.5", "1.2345,6"])
def test_thousands_groups_must_have_three_digits(value: str) -> None:
    assert "number" not in value_types(value)


@pytest.mark.parametrize("value", [
    "N/A", "14:31:15", "HXE34K-S1", "HXE12", "224-4609", "2373310-1-37", "62058-31", "4G",
    "2 wires", "1,000 PCS", "220V", "5(60)A", "1ST DELIVERY", "12-05", "30/02/2026",
    "13/13/2026", "1, 3 y 4", "Item 1", "Mar", "DDP DATE", "+51 1 2345678 ext", "UD 218,400.00",
])
def test_other_writings_stay_text(value: str) -> None:
    assert value_types(value) == ()


def test_ambiguous_writings_keep_every_reading() -> None:
    assert value_types("20260228") == ("number", "date")
    assert value_types("2026.02") == ("number", "date")
    assert value_types("28.02") == ("number",)
    assert value_types("") == ()
    assert [cell_shape(value) for value in ("-", "–", "—", "−", "", "x", "5")] == [
        "empty", "empty", "empty", "empty", "empty", "text", "typed"]


def test_shared_type_makes_column_stable_and_blank_header_column_is_allowed() -> None:
    source = table([[None, "DELIVERIES", "DDP DATE"],
                    ["1", "1ST DELIVERY", "28/02/2026"],
                    ["2", "2ND DELIVERY", "09/04/2026"],
                    ["3", "3RD DELIVERY", "07/08/2026"]])
    result = detect_header(source)
    assert (result.outcome, result.header_start_row, result.header_end_row) == ("identified", 0, 1)
    assert [path.display_parts for path in result.paths] == [(), ("DELIVERIES",), ("DDP DATE",)]
    assert result.evaluations[0].column_observations[2].stable_body_types == ("date",)

    numbers = table([["Price"], ["4.56"], ["2026.02"]])
    assert detect_header(numbers).evaluations[0].column_observations[0].stable_body_types == ("number",)


def test_selected_and_native_table_use_same_content_rules() -> None:
    native = table([["产品", "数量"], ["A", "100"], ["B", "200"]])
    source = SourceDocument("doc", "x.pdf", "x.pdf", Path("x.pdf"), "a" * 64)
    placeholder = PrimaryTablePlaceholder("node", "slot_001", "#/tables/0", ())
    slot = TableSlot("slot_001", "#/tables/0", None, None, None, None,
                     "deferred", "missing_slot_provenance", ())
    primary = PrimaryDocument("doc", source.file_hash, 1, (placeholder,), (slot,),
                              (NativeTableFact("slot_001", "#/tables/0", native),), ())
    fallback = ContentResolution("slot_001", "docling_native_fallback", None, None, "decision")
    prepared = prepare_tables(primary, (), (fallback,), True, serializer=serialize_markdown_rows)
    parsed = compose_document(source, primary, (fallback,), prepared, True)
    assert isinstance(parsed.nodes[0], TableNode)
    assert parsed.nodes[0].origin == "docling_native_fallback"
    assert parsed.nodes[0].serialized.text == prepared[0].serialized_table.text

    winner_candidate = TableCandidate("winner", "pymupdf", "lines", "ref", (),
                                      native.row_count, native.column_count, None, None,
                                      native.cells, (), None, ())
    execution = StrategyExecution("pymupdf", "lines", "completed_with_tables",
                                  (PageExecution(1, "completed", ("winner",), None, None),),
                                  ("winner",), None, None)
    report = TableExtractionReport("x.pdf", source.file_hash, (execution,), (winner_candidate,), ())
    winner = ContentResolution("slot_001", "selected_winner", "winner", "group:winner", "decision")
    selected = prepare_tables(primary, (report,), (winner,), True, serializer=serialize_markdown_rows)
    selected_parsed = compose_document(source, primary, (winner,), selected, True)
    assert selected_parsed.nodes[0].table.tool == "pymupdf"
    assert selected_parsed.nodes[0].serialized.text == parsed.nodes[0].serialized.text
    with pytest.raises(TablePreparationError):
        prepare_tables(primary, (report,), (replace(winner, selected_candidate_id="other"),), True,
                       serializer=serialize_markdown_rows)


def test_real_pdf_words_drive_group_scoring_and_explicit_fallback(tmp_path: Path) -> None:
    pdf_path = tmp_path / "table.pdf"
    with fitz.open() as pdf:
        page = pdf.new_page(width=100, height=100)
        page.insert_text((20, 30), "Product 100")
        pdf.save(pdf_path)
    source = SourceDocument("doc", "table.pdf", "table.pdf", pdf_path,
                            hashlib.sha256(pdf_path.read_bytes()).hexdigest())
    native = table([["Product", "100"]])
    box = BoundingBox(10, 10, 95, 80)
    slot = TableSlot("slot_001", "#/tables/0", 1, 100, 100, box, "eligible", None, ())
    primary = PrimaryDocument("doc", source.file_hash, 1,
                              (PrimaryTablePlaceholder("node", "slot_001", "#/tables/0", ()),),
                              (slot,), (NativeTableFact("slot_001", "#/tables/0", native),), ())
    candidate = TableCandidate("winner", "pymupdf", "lines", "raw", 
                               (TableRegion(1, 100, 100, box, "raw", None, None),),
                               native.row_count, native.column_count, None, None,
                               native.cells, (), None, ())
    execution = StrategyExecution("pymupdf", "lines", "completed_with_tables",
                                  (PageExecution(1, "completed", ("winner",), None, None),),
                                  ("winner",), None, None)
    report = TableExtractionReport(source.relative_path, source.file_hash, (execution,), (candidate,), ())
    decisions, grouping, scoring = select_tables(source, primary, (report,),
                                                  PyMuPdfEvidenceReader())
    assert decisions[0].origin == "selected_winner"
    assert scoring.groups[0].selected_candidate_id == "winner"
    assert scoring.groups[0].text_reference.token_counts
    assert grouping.groups[0].member_candidate_ids == ("winner",)

    fallback, empty_grouping, empty_scoring = select_tables(source, primary, (),
                                                             PyMuPdfEvidenceReader())
    assert fallback[0].origin == "docling_native_fallback"
    assert empty_grouping.groups[0].status == "unresolved"
    assert empty_scoring.groups == ()


def test_summary_row_is_skipped_and_split_cells_are_left_out() -> None:
    item = TableCell("item", "Item", 0, 2, 0, 1, 2, 1, None, ("body",), "native", ("/item",))
    qty = TableCell("qty", "Qty", 0, 2, 1, 2, 2, 1, None, ("body",), "native", ("/qty",))
    total = TableCell("total", "Total CIF", 3, 4, 0, 2, 1, 2, None, ("body",), "native", ("/total",))
    single = table([[None, None, "Total Amount"], [None, None, "(USD)"],
                    ["1", "10,000", "968,000.00"], [None, None, "USD 968,000.00"]], spans=(item, qty, total))
    result = detect_header(single)
    assert result.outcome == "undetermined"  # one data row: no column can be checked
    skipped = next(item for item in result.evaluations if item.end_row == 2).skipped_rows
    assert [(row.row_index, row.reason) for row in skipped] == [(3, "summary_row")]

    blank_led = TableCell("label", "Total", 3, 4, 1, 3, 1, 2, None, ("body",), "native", ("/label",))
    led = table([["Item", "Desc", "Qty", "Amount"], ["1", "a", "5", "USD 1"], ["2", "b", "6", "USD 2"],
                 [None, None, None, "USD 3"]], spans=(blank_led,))
    winner = next(item for item in detect_header(led).evaluations if item.end_row == 1)
    assert [(row.row_index, row.reason) for row in winner.skipped_rows] == [(3, "summary_row")]

    price = [TableCell(f"p{row}", value, row, row + 1, 1, 3, 1, 2, None, ("body",), "native", (f"/p{row}",))
             for row, value in ((0, "Unit price"), (1, "USD 5.28"), (2, "USD 5.50"))]
    split = table([["Item", None, None], ["1", None, None], ["2", None, None]], spans=tuple(price))
    result = detect_header(split)
    assert (result.outcome, result.header_end_row) == ("identified", 1)
    assert result.evaluations[0].sampled_row_indices == (1, 2)


def test_date_header_over_numbers_and_placeholders() -> None:
    source = table([["MODEL", "1st Delivery", "2nd Delivery"],
                    [None, "25.09.25", "10.10.25"],
                    ["HXE12ESX", "-", "20,000.00"],
                    ["HXE13ESX", "5,000.00", "2,500.00"],
                    ["HXE33K-S1", "900.00", "-"]])
    merged = replace(source, cells=tuple(
        replace(cell, end_row_offset_idx=2, row_span=2) if cell.cell_id == "c0_0" else cell
        for cell in source.cells if cell.cell_id != "c1_0"))
    result = detect_header(merged)
    assert (result.outcome, result.header_start_row, result.header_end_row) == ("identified", 0, 2)
    assert [path.display_parts for path in result.paths] == [
        ("MODEL",), ("1st Delivery", "25.09.25"), ("2nd Delivery", "10.10.25")]


def test_number_over_currency_is_a_transition_and_mixed_column_is_unstable() -> None:
    assert detect_header(table([["2025"], ["USD 5.00"], ["USD 6.00"]])).header_end_row == 1
    mixed = detect_header(table([["Price"], ["USD 5.00"], ["6.00"]]))
    assert mixed.outcome == "undetermined"
    assert mixed.evaluations[0].column_observations[0].stable_body_types == ()


def test_header_candidates_have_at_most_five_rows() -> None:
    source = table([[f"h{row}", f"g{row}"] for row in range(7)] + [["1", "2"], ["3", "4"]])
    result = detect_header(source)
    assert max(item.end_row for item in result.evaluations) == 5
    assert result.outcome == "undetermined"
