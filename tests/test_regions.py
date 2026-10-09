"""Chunk regions are derived from chunk sources and the parser's positions."""

from dataclasses import replace

from rag.ingest.regions import chunk_regions
from rag.ingest.tables.markdown_rows import serialize_markdown_rows
from rag.ingest.tables.header import detect_header
from rag.ingest.tables.models import StructuredTable, TableCell
from rag.models import (
    BoundingBox, ChunkSource, DocumentChunk, PageSpan, ParsedDocument, TableNode, TextNode, WordBox,
)


def box(x: float, y: float) -> BoundingBox:
    return BoundingBox(x, y, x + 10, y + 5)


def chunk(sources, fragment_index=0, fragment_count=1, parent=None) -> DocumentChunk:
    return DocumentChunk("c0", "d", 0, "text", "x", None, tuple(sources), parent, fragment_index, fragment_count)


def document(*nodes) -> ParsedDocument:
    return ParsedDocument("d", "d.pdf", "d.pdf", "a" * 64, tuple(nodes), ())


def test_text_with_word_positions_gives_the_words_in_the_chunk_range():
    text = "alpha beta gamma"
    words = (WordBox(0, 5, 1, box(0, 0)), WordBox(6, 10, 1, box(20, 0)), WordBox(11, 16, 1, box(40, 0)))
    node = TextNode("n", "paragraph", text, (PageSpan(1, None, "p"),), None, words)
    regions = chunk_regions(chunk([ChunkSource("n", node.sources, 6, 16, False, "none")]), document(node))
    assert [(r.bbox, r.precision) for r in regions] == [(box(20, 0), "fine"), (box(40, 0), "fine")]


def test_part_of_a_paragraph_is_coarse_and_the_whole_paragraph_is_fine():
    node = TextNode("n", "paragraph", "one two", (PageSpan(2, box(5, 5), "p"),), None)
    whole = chunk_regions(chunk([ChunkSource("n", node.sources, 0, 7, False, "none")]), document(node))
    part = chunk_regions(chunk([ChunkSource("n", node.sources, 0, 3, False, "none")]), document(node))
    assert [r.precision for r in whole] == ["fine"] and [r.precision for r in part] == ["coarse"]


def test_no_box_means_the_whole_page_and_locator_text_has_no_region():
    node = TextNode("n", "paragraph", "one", (PageSpan(3, None, "p"),), None)
    regions = chunk_regions(chunk([ChunkSource("n", node.sources, None, None, True, "fallback_locator"),
                                   ChunkSource("n", node.sources, 0, 3, False, "none")]), document(node))
    assert [(r.page_number, r.bbox, r.precision) for r in regions] == [(3, None, "coarse")]


def _table_node() -> TableNode:
    rows = [["Model", "Qty"], ["A", "100"], ["B", "200"]]
    cells = tuple(TableCell(f"c{r}_{c}", v, r, r + 1, c, c + 1, 1, 1, box(c * 50, r * 10), ("body",),
                            "native", ()) for r, row in enumerate(rows) for c, v in enumerate(row))
    table = StructuredTable("t", "pymupdf", "lines", 3, 2, cells, (), None, (), ())
    header = detect_header(table)
    assert header.outcome == "identified"
    return TableNode("tbl", "s", "#/tables/0", "selected_winner", table, None, header,
                     serialize_markdown_rows(table, header, table_node_id="tbl", labeled=True),
                     (PageSpan(1, BoundingBox(0, 0, 100, 30), "#/tables/0"),))


def test_table_chunk_gives_its_rows_cells_and_the_header_cells_its_column_names_come_from():
    node = _table_node()
    first_line = node.serialized.lines[0].text      # row 2: A / 100
    second = len(first_line) + 1
    first = chunk([ChunkSource("tbl", node.sources, 0, len(first_line), False, "none")], 0, 2, "tbl")
    later = chunk([ChunkSource("tbl", node.sources, second, len(node.serialized.text), False, "none")], 1, 2, "tbl")
    doc = document(node)
    assert {r.bbox for r in chunk_regions(first, doc)} == {box(0, 10), box(50, 10), box(0, 0), box(50, 0)}
    assert {r.bbox for r in chunk_regions(later, doc)} == {box(0, 20), box(50, 20), box(0, 0), box(50, 0)}


def test_table_cell_without_position_falls_back_to_the_table_box():
    node = _table_node()
    cells = tuple(replace(c, bbox=None) if c.cell_id == "c1_1" else c for c in node.table.cells)
    node = replace(node, table=replace(node.table, cells=cells))
    first_line = node.serialized.lines[0].text
    regions = chunk_regions(chunk([ChunkSource("tbl", node.sources, 0, len(first_line), False, "none")], 1, 2, "tbl"),
                            document(node))
    assert ("coarse", BoundingBox(0, 0, 100, 30)) in {(r.precision, r.bbox) for r in regions}
