"""`markdown_rows_v1` table text, its chunk continuation and its regions."""

from rag.ingest.chunkers.structured import chunk_parsed_document
from rag.ingest.regions import chunk_regions
from rag.ingest.tables.header import detect_header
from rag.ingest.tables.markdown_rows import serialize_markdown_rows
from rag.ingest.tables.models import (
    GridPosition, HeaderDecision, HeaderPath, StructuredTable, TableCell,
)
from rag.models import BoundingBox, PageSpan, ParsedDocument, TableNode


def cell(cell_id: str, text: str | None, row: int, col: int, rows: int = 1, cols: int = 1) -> TableCell:
    return TableCell(cell_id, text, row, row + rows, col, col + cols, rows, cols,
                     BoundingBox(col * 50, row * 10, col * 50 + 40, row * 10 + 8),
                     ("body",), "native", ())


def grid(rows: list[list[str | None]], *, merged: tuple[TableCell, ...] = (),
         missing: tuple[tuple[int, int], ...] = (), unplaced: str | None = None) -> StructuredTable:
    occupied = {(r, c) for m in merged
                for r in range(m.start_row_offset_idx or 0, m.end_row_offset_idx or 0)
                for c in range(m.start_col_offset_idx or 0, m.end_col_offset_idx or 0)}
    cells = list(merged)
    for r, values in enumerate(rows):
        for c, value in enumerate(values):
            if (r, c) not in occupied and (r, c) not in missing:
                cells.append(cell(f"c{r}_{c}", value, r, c))
    return StructuredTable("t", "pymupdf", "lines", len(rows), len(rows[0]), tuple(cells),
                           tuple(GridPosition(r, c, "missing_physical_cell") for r, c in missing),
                           unplaced, (), ())


def text_of(table: StructuredTable) -> str:
    return serialize_markdown_rows(table, detect_header(table), table_node_id="tbl").text


def test_identified_header_is_one_line_and_rows_have_no_scaffolding():
    table = grid([["Model", "Qty"], ["HXE12ES", "1,000"], ["HXE13ES", "2,000"]])
    assert detect_header(table).outcome == "identified"
    result = serialize_markdown_rows(table, detect_header(table), table_node_id="tbl")
    assert result.text == "| Model | Qty |\n| HXE12ES | 1,000 |\n| HXE13ES | 2,000 |"
    assert [line.kind for line in result.lines] == ["header", "data", "data"]
    assert result.rule_version == "markdown_rows_v1"


def test_undetermined_header_writes_rows_as_they_are():
    table = grid([["产品", "数量"], ["A", '100"'], ["B", "200"]])
    assert detect_header(table).outcome == "undetermined"
    assert text_of(table) == '| 产品 | 数量 |\n| A | 100" |\n| B | 200 |'


def test_whitespace_blank_missing_pipe_and_unplaced_text():
    table = grid([["a\n b", None, "x|y"], ["  ", "-", "z"]], missing=((1, 2),), unplaced="原文\n残留")
    assert text_of(table) == "| a b |  | x\\|y |\n|  | - | 〔缺失单元格〕 |\n原文\n残留"


def test_merged_cells_keep_value_once_and_mark_covered_positions():
    title = cell("title", "ITEM 1", 1, 0, cols=3)
    company = cell("company", "ENSA", 2, 0, rows=2)
    block = cell("block", "合计 9", 2, 1, rows=2, cols=2)
    table = grid([["k", "a", "b"], ["", "", ""], ["", "", ""], ["", "", ""], ["X", "1", "2"]],
                 merged=(title, company, block))
    lines = text_of(table).split("\n")
    assert lines[1:4] == ["| ITEM 1 | ← | ← |", "| ENSA | 合计 9 | ← |", "| ↑ | ↑ | ↑ |"]


class Counter:
    def count_passage(self, text: str) -> int:
        return len(text)


def first_row_header(table: StructuredTable) -> HeaderDecision:
    paths = tuple(HeaderPath(c.start_col_offset_idx or 0, (c.cell_id,), (c.text or "",))
                  for c in table.cells if c.start_row_offset_idx == 0)
    return HeaderDecision("identified", "unique_supported_candidate", (), (), (), 0, 1,
                          paths, "table_header_v1", 8, 2)


def _document(table: StructuredTable) -> tuple[ParsedDocument, TableNode]:
    header = first_row_header(table)
    node = TableNode("tbl", "s", "#/tables/0", "selected_winner", table, None, header,
                     serialize_markdown_rows(table, header, table_node_id="tbl"),
                     (PageSpan(1, BoundingBox(0, 0, 200, 100), "#/tables/0"),))
    return ParsedDocument("d", "d.pdf", "d.pdf", "a" * 64, (node,), ()), node


def test_chunk_opening_inside_a_vertical_merge_gets_the_value_back_as_repeated_context():
    company = cell("company", "ENSA", 1, 0, rows=3)
    block = cell("block", "B", 1, 2, rows=3, cols=2)
    table = grid([["COMPANY", "MODEL", "QTY", "NOTE"],
                  ["", "HXE12ES", "", ""], ["", "HXE13ES", "", ""], ["", "HXE33K", "", ""],
                  ["ELSE", "HXE34K", "4", "n"]], merged=(company, block))
    document, node = _document(table)
    header = "| COMPANY | MODEL | QTY | NOTE |"
    batch = chunk_parsed_document(document, Counter(), max_input_tokens=len(header) * 3)
    texts = [item.text for item in batch.chunks]
    assert texts[0] == f"{header}\n| ENSA | HXE12ES | B | ← |\n| ↑ | HXE13ES | ↑ | ↑ |"
    assert texts[1] == f"{header}\n| ↑ ENSA | HXE33K | ↑ B | ← |\n| ELSE | HXE34K | 4 | n |"
    second = batch.chunks[1].sources
    assert [s.context_kind for s in second] == ["table_header", "merged_cell", "merged_cell", "none", "none"]
    anchor = node.serialized.text
    assert [anchor[s.source_text_start:s.source_text_end] for s in second[1:3]] == ["ENSA", "B"]
    regions = chunk_regions(batch.chunks[1], document)
    boxes = {r.bbox for r in regions if r.precision == "fine"}
    assert company.bbox in boxes and block.bbox in boxes
    assert all(r.precision == "fine" for r in regions)


def test_labeled_rows_put_column_names_on_single_column_values_only():
    total = cell("total", "Total CIF", 2, 0, cols=2)
    table = grid([["Model", "Qty", "Amount"], ["A", "", "5"], ["", "", "9"]], merged=(total,))
    header = first_row_header(table)
    labeled = serialize_markdown_rows(table, header, table_node_id="tbl", labeled=True)
    assert labeled.text == "| Model: A |  | Amount: 5 |\n| Total CIF | ← | Amount: 9 |"
    assert labeled.rule_version == "labeled_rows_v1"
    first = labeled.lines[0]
    assert [first.text[s.start:s.end] for s in first.cell_spans if s.end > s.start] == ["Model", "A", "Amount", "5"]
    assert "c0_0" in first.source_cell_ids and "c0_2" in first.source_cell_ids


def test_labeled_rows_without_header_equal_markdown_rows():
    table = grid([["产品", "数量"], ["A", '100"'], ["B", "200"]])
    header = detect_header(table)
    assert header.outcome == "undetermined"
    plain = serialize_markdown_rows(table, header, table_node_id="tbl")
    labeled = serialize_markdown_rows(table, header, table_node_id="tbl", labeled=True)
    assert labeled.text == plain.text


def test_labeled_continuation_keeps_the_column_name():
    company = cell("company", "ENSA", 1, 0, rows=3)
    table = grid([["COMPANY", "MODEL"], ["", "HXE12ES"], ["", "HXE13ES"], ["", "HXE33K"]], merged=(company,))
    header = first_row_header(table)
    line = serialize_markdown_rows(table, header, table_node_id="tbl", labeled=True).lines[2]
    assert line.text == "| ↑ | MODEL: HXE33K |"
    assert line.resumed_text() == "| COMPANY: ↑ ENSA | MODEL: HXE33K |"


def test_blank_header_cell_gives_no_column_name():
    table = grid([["", "Qty"], ["A", "1"], ["B", "2"]])
    header = HeaderDecision("identified", "unique_supported_candidate", (), (), (), 0, 1,
                            (HeaderPath(0, ("c0_0",), ()), HeaderPath(1, ("c0_1",), ("Qty",))),
                            "table_header_v1", 8, 2)
    assert serialize_markdown_rows(table, header, labeled=True).text == "| A | Qty: 1 |\n| B | Qty: 2 |"
    assert serialize_markdown_rows(table, header).text.startswith("|  | Qty |")
