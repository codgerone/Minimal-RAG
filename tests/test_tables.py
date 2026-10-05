"""Contract-level examples for V3 table content preparation."""

from dataclasses import replace
from pathlib import Path
import hashlib

import fitz
import pytest

from rag.ingest.tables.pdf_words import PyMuPdfEvidenceReader
from rag.ingest.assemble import compose_document
from rag.ingest.tables.prepare import TablePreparationError, prepare_tables
from rag.ingest.tables.header import classify_cell, detect_header
from rag.ingest.tables.formatter import serialize_table
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
    result = serialize_table(source, decision, table_node_id="node")
    assert result.text.startswith("表头未确定。\n第1行：第1列 = \"产品\"")
    assert '第2列 = "100\\\""' in result.text
    assert result.lines[-1].line_id == "node_line_000004"


def test_header_h01_and_fallback_for_invalid_span() -> None:
    source = table([["产品", "数量"], ["A", "100"], ["B", "200"]])
    result = detect_header(source)
    assert (result.outcome, result.header_start_row, result.header_end_row) == ("identified", 0, 1)
    assert serialize_table(source, result).text == (
        '第2行：产品 = "A"；数量 = "100"。\n'
        '第3行：产品 = "B"；数量 = "200"。'
    )
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
    assert serialize_table(source, result).text.startswith(
        '第1行：第1列至第2列为合并单元格，内容 = "采购明细"。'
    )
    gap = replace(source, cells=tuple(cell for cell in source.cells if cell.cell_id != "c1_1"),
                  uncovered_grid_positions=(GridPosition(1, 1, "missing_physical_cell"),))
    assert detect_header(gap).evaluations[0].structural_issues[0].code == "missing_header_position"


def test_classifier_keeps_nonempty_placeholders_as_text() -> None:
    assert classify_cell("USD 96.80") == "number"
    assert classify_cell("25.09.25") == "date"
    assert classify_cell("N/A") == "text_shape"


def test_selected_and_native_table_use_same_content_rules() -> None:
    native = table([["产品", "数量"], ["A", "100"], ["B", "200"]])
    source = SourceDocument("doc", "x.pdf", "x.pdf", Path("x.pdf"), "a" * 64)
    placeholder = PrimaryTablePlaceholder("node", "slot_001", "#/tables/0", ())
    slot = TableSlot("slot_001", "#/tables/0", None, None, None, None,
                     "deferred", "missing_slot_provenance", ())
    primary = PrimaryDocument("doc", source.file_hash, 1, (placeholder,), (slot,),
                              (NativeTableFact("slot_001", "#/tables/0", native),), ())
    fallback = ContentResolution("slot_001", "docling_native_fallback", None, None, "decision")
    prepared = prepare_tables(primary, (), (fallback,), True)
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
    selected = prepare_tables(primary, (report,), (winner,), True)
    selected_parsed = compose_document(source, primary, (winner,), selected, True)
    assert selected_parsed.nodes[0].table.tool == "pymupdf"
    assert selected_parsed.nodes[0].serialized.text == parsed.nodes[0].serialized.text
    with pytest.raises(TablePreparationError):
        prepare_tables(primary, (report,), (replace(winner, selected_candidate_id="other"),), True)


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
