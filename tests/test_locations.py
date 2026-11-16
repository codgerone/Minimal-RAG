"""Word positions: chunk regions from text pieces and cell addresses, coverage of Word excerpt
words, and location labels."""

from rag.eval.dataset import Excerpt, _anchor, text_sha256
from rag.eval.judge import JudgeChunk, judge_case
from rag.ingest.regions import chunk_regions
from rag.ingest.tables.header import detect_header
from rag.ingest.tables.markdown_rows import serialize_markdown_rows
from rag.ingest.tables.models import StructuredTable, TableCell
from rag.ingest.tables.structure import rule_structure
from rag.locations import describe, merge, span_location
from rag.models import (
    ChunkSource, DocumentChunk, DocxRegion, DocxSpan, ListItem, ListNode, PageSpan, ParsedDocument,
    TableNode, TextNode, TextPiece,
)
from tests.test_eval import case


def chunk(sources) -> DocumentChunk:
    return DocumentChunk("c0", "d", 0, "text", "x", None, tuple(sources), None, 0, 1)


def document(*nodes) -> ParsedDocument:
    return ParsedDocument("d", "d.docx", "d.docx", "a" * 64, tuple(nodes), (), "english")


def test_text_pieces_give_the_characters_of_each_address_in_the_chunk_range():
    # "1.2 Scope of supply": label "1.2" from body/3#label, the rest from body/3 after 2 stripped spaces
    text = "1.2 Scope of supply"
    pieces = (TextPiece(0, 3, "body/3#label", 0), TextPiece(4, 19, "body/3", 2))
    node = TextNode("n", "title", text, (DocxSpan("body/3", "第 4 块", "p"),), None, (), pieces)
    whole = chunk_regions(chunk([ChunkSource("n", node.sources, 0, 19, False, "none")]), document(node))
    part = chunk_regions(chunk([ChunkSource("n", node.sources, 10, 19, False, "none")]), document(node))
    assert whole == (DocxRegion("body/3#label", 0, 3), DocxRegion("body/3", 2, 17))
    assert part == (DocxRegion("body/3", 8, 17),)


def test_a_list_item_brings_its_label_only_with_its_start():
    span = (DocxSpan("body/5", "第 6 块", "p"),)
    item = ListItem("i", "Delivery in 30 days", 0, "a)", None, span, None, (TextPiece(0, 19, "body/5", 0),))
    node = ListNode("l", (item,), (), span)
    start = chunk_regions(chunk([ChunkSource("i", span, 0, 8, False, "none")]), document(node))
    rest = chunk_regions(chunk([ChunkSource("i", span, 9, 19, False, "none")]), document(node))
    assert start == (DocxRegion("body/5#label", None, None), DocxRegion("body/5", 0, 8))
    assert rest == (DocxRegion("body/5", 9, 19),)


def test_table_cells_with_addresses_give_whole_address_regions():
    rows = [["Model", "Qty"], ["A", "100"], ["B", "200"]]
    cells = tuple(TableCell(f"c{r}_{c}", v, r, r + 1, c, c + 1, 1, 1, None, ("body",), "native", (),
                            f"body/2/r{r}c{c}") for r, row in enumerate(rows) for c, v in enumerate(row))
    table = StructuredTable("t", "docx_native", "file", 3, 2, cells, (), None, (), ())
    header = detect_header(table)
    serialized = serialize_markdown_rows(table, rule_structure(header), table_node_id="tbl")
    node = TableNode("tbl", "s", "t0", "parser_exact", table, None, header, rule_structure(header),
                     serialized, serialized.text, "rows", (DocxSpan("body/2", "表 1", "t0"),))
    line = serialized.lines[1]
    start = serialized.text.index(line.text)
    regions = chunk_regions(chunk([ChunkSource("tbl", node.sources, start, start + len(line.text),
                                               False, "none")]), document(node))
    assert set(regions) >= {DocxRegion("body/2/r1c0", None, None), DocxRegion("body/2/r1c1", None, None)}


def test_word_excerpt_words_are_covered_by_same_address_ranges_and_cell_addresses():
    anchor, state = _anchor({"text": "x", "anchor": {
        "text_sha256": text_sha256("x"),
        "pieces": [{"text": "Scope of", "words": ["body/3 2 7", "body/3 8 10"]},
                   {"text": "100", "words": ["body/2/r1c1/0 0 3"]}]}})
    assert state == "ok" and anchor[0][1] == (("body/3", 2, 7), ("body/3", 8, 10))
    x = Excerpt("x1", "d1", "d1.docx", (), "Scope of | 100", False, None, anchor, "ok")
    chunks = [JudgeChunk("a", "d1", "", None, (), (), (("body/3", 0, 9), ("body/2/r1c1", None, None))),
              JudgeChunk("b", "d1", "", None, (), (), (("body/3", 9, 20),)),
              JudgeChunk("c", "d1", "", None, (), (), (("body/30", 0, 99), ("body/2/r1c10", None, None)))]
    [group] = judge_case(case([x], ("e1", [["x1"]])), chunks).groups
    # "Scope" (middle 4) and "100" in a; "of" (middle 9) only in b
    assert group.mode == "coordinate" and group.acceptable_sets == (frozenset({"a", "b"}),)


def test_location_labels_keep_pdf_pages_sorted_and_word_labels_in_order():
    assert span_location(PageSpan(3, None, "p")) == 3
    assert span_location(DocxSpan("body/0", "第 1 块", "p")) == "第 1 块"
    assert merge([3, 1, 3]) == (1, 3) and describe((1, 3)) == "第 1,3 页"
    assert merge(["第 9 块", "表 2", "第 9 块"]) == ("第 9 块", "表 2")
    assert describe(("第 9 块", "表 2")) == "第 9 块、表 2"
