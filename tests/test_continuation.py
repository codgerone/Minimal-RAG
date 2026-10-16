from dataclasses import replace
from pathlib import Path

from rag.ingest.assemble import compose_document
from rag.ingest.regions import chunk_regions
from rag.ingest.tables.continuation import column_geometry, text_width
from rag.ingest.tables.markdown_rows import serialize_markdown_rows
from rag.ingest.tables.models import (
    ContentResolution, StructuredTable, TableCell, TableSlot,
)
from rag.ingest.tables.prepare import prepare_tables
from rag.models import (
    BoundingBox, ChunkSource, DocumentChunk, ListItem, NativeTableFact, PageSpan, PrimaryDocument, PrimaryList,
    PrimaryTablePlaceholder, PrimaryText, SourceDocument, TableNode,
)

PAGE_W, PAGE_H, ROW_H = 595.0, 842.0, 20.0
EDGES = (70.0, 200.0, 330.0, 520.0)
HEAD = [["Company", "Qty", "Amount"]]
BODY = [["ELUC", "2,000", "USD 4.56"], ["HDNA", "1,500", "USD 4.10"], ["SEAL", "900", "USD 3.95"]]
MORE = [["ENSA", "10,804", "USD 5.00"], ["ELSE", "2,600", "USD 4.80"], ["ELPU", "1,200", "USD 4.75"]]


def grid(table_id: str, rows, *, top: float, edges=EDGES, boxes: bool = True) -> StructuredTable:
    cells = []
    for r, values in enumerate(rows):
        for c, value in enumerate(values):
            box = BoundingBox(edges[c], top + r * ROW_H, edges[c + 1], top + (r + 1) * ROW_H) if boxes else None
            cells.append(TableCell(f"{table_id}_r{r}c{c}", value, r, r + 1, c, c + 1, 1, 1, box,
                                   ("body",), "native", ()))
    return StructuredTable(table_id, "pymupdf", "lines_strict", len(rows), len(rows[0]),
                           tuple(cells), (), None, (), ())


def box_of(table: StructuredTable) -> BoundingBox:
    boxes = [cell.bbox for cell in table.cells if cell.bbox]
    if not boxes:
        return BoundingBox(EDGES[0], 100, EDGES[-1], 200)
    return BoundingBox(min(b.x0 for b in boxes), min(b.y0 for b in boxes),
                       max(b.x1 for b in boxes), max(b.y1 for b in boxes))


def document(*items):
    """items: ("table", page, StructuredTable) or ("text", page, kind, text) in reading order."""
    elements, slots, natives = [], [], []
    for index, item in enumerate(items):
        if item[0] == "table":
            _, page, table = item
            slot_id, ref = f"slot_{index:03d}", f"#/tables/{index}"
            span = PageSpan(page, box_of(table), ref)
            elements.append(PrimaryTablePlaceholder(f"node_{index}", slot_id, ref, (span,)))
            slots.append(TableSlot(slot_id, ref, page, PAGE_W, PAGE_H, box_of(table), "eligible", None, (ref,)))
            natives.append(NativeTableFact(slot_id, ref, replace(table, sources=(span,))))
        elif item[0] == "list":
            _, page, text = item
            span = PageSpan(page, BoundingBox(70, 700, 500, 712), "#/l")
            elements.append(PrimaryList(f"node_{index}", (ListItem(f"i{index}", text, 0, "-", None, (span,), None),),
                                        ("#/l",), (span,)))
        else:
            _, page, kind, text = item
            elements.append(PrimaryText(f"node_{index}", kind, text,
                                        (PageSpan(page, BoundingBox(70, 60, 500, 72), "#/t"),), "#/t"))
    primary = PrimaryDocument("doc", "a" * 64, 9, tuple(elements), tuple(slots), tuple(natives), ())
    resolutions = tuple(ContentResolution(slot.slot_id, "docling_native_fallback", None, None, "d")
                        for slot in slots)
    return primary, resolutions


def run(*items):
    primary, resolutions = document(*items)
    prepared, report = prepare_tables(primary, (), resolutions, True,
                                      serializer=lambda t, h, table_node_id: serialize_markdown_rows(
                                          t, h, table_node_id=table_node_id, labeled=True))
    source = SourceDocument("doc", "x.pdf", "x.pdf", Path("x.pdf"), "a" * 64)
    return report, compose_document(source, primary, resolutions, prepared, True)


def at_end(table_id="a", rows=HEAD + BODY, **kw):
    return grid(table_id, rows, top=PAGE_H - 100 - ROW_H * len(rows), **kw)


def at_start(table_id="b", rows=MORE, **kw):
    return grid(table_id, rows, top=80, **kw)


def failures(*items):
    report, _ = run(*items)
    assert len(report.evaluations) == 1
    return report.evaluations[0].failures


def test_continuation_is_merged_into_one_table_with_borrowed_column_names() -> None:
    report, parsed = run(("table", 1, at_end()), ("text", 1, "footer", "Page 1 of 2"),
                         ("text", 2, "header", "ACME Ltd. Purchase order"), ("table", 2, at_start()))
    assert report.chains == (("slot_000", "slot_003"),)
    tables = [node for node in parsed.nodes if isinstance(node, TableNode)]
    assert len(tables) == 1
    node = tables[0]
    assert node.header.outcome == "identified"
    assert [(s.page_number, s.start_row, s.end_row) for s in node.segments] == [(1, 0, 4), (2, 4, 7)]
    assert "| Company: ENSA | Qty: 10,804 | Amount: USD 5.00 |" in node.serialized.text
    assert [s.page_number for s in node.sources] == [1, 2]
    # Text between the parts stays in reading order, after the merged table.
    assert [type(n).__name__ for n in parsed.nodes] == ["TableNode", "TextNode", "TextNode"]


def test_regions_follow_the_page_of_each_row() -> None:
    _, parsed = run(("table", 1, at_end()), ("table", 2, at_start()))
    node = next(n for n in parsed.nodes if isinstance(n, TableNode))
    last = node.serialized.lines[-1]                     # an ELPU row from page 2, labeled from page 1
    start = len(node.serialized.text) - len(last.text)
    chunk = DocumentChunk("doc-c1", "doc", 0, "table", last.text, None,
                          (ChunkSource(node.node_id, node.spans_for_rows(last.source_rows),
                                       start, len(node.serialized.text), False, "none"),), None, 0, 1)
    regions = chunk_regions(chunk, parsed)
    assert {r.page_number for r in regions} == {1, 2}
    assert all(r.precision == "fine" for r in regions)
    assert chunk.sources[0].page_spans[0].page_number == 2


def test_each_condition_alone_rejects() -> None:
    assert failures(("table", 1, grid("a", HEAD + BODY, top=400)), ("table", 2, at_start())) \
        == ("earlier_not_at_page_end",)
    assert failures(("table", 1, at_end()), ("table", 2, grid("b", MORE, top=300))) \
        == ("later_not_at_page_start",)
    assert failures(("table", 1, at_end()), ("text", 2, "paragraph", "说" * 61), ("table", 2, at_start())) \
        == ("text_between_too_long",)
    assert failures(("table", 1, at_end()), ("table", 2, at_start(rows=[r[:2] for r in MORE], edges=EDGES[:3]))) \
        == ("column_count_differs",)
    assert failures(("table", 1, at_end()), ("table", 2, at_start(boxes=False))) == ("geometry_unavailable",)
    dates = [[name, date, money] for (name, _, money), date in zip(MORE, ("28/02/2026", "09/04/2026", "07/08/2026"))]
    assert failures(("table", 1, at_end()), ("table", 2, at_start(rows=dates))) == ("column_types_conflict",)
    assert failures(("table", 1, at_end()), ("table", 2, at_start(rows=HEAD + MORE))) == ("later_has_header",)


def test_text_between_ignores_page_furniture_and_counts_full_width_double() -> None:
    long_header = ("text", 2, "header", "x" * 500)
    assert failures(("table", 1, at_end()), long_header, ("table", 2, at_start())) == ()
    assert failures(("table", 1, at_end()), ("text", 2, "paragraph", "续" * 60), ("table", 2, at_start())) == ()
    assert failures(("table", 1, at_end()), ("list", 1, "y" * 121), ("table", 2, at_start())) \
        == ("text_between_too_long",)
    assert text_width("续表 (cont.)") == 4 + 7


def test_moved_inner_separator_and_narrow_table_inside_wide_are_misaligned() -> None:
    a = grid("a", [["x", "1"], ["y", "2"]], top=0, edges=(0.0, 100.0, 300.0))
    moved = grid("b", [["x", "1"], ["y", "2"]], top=0, edges=(0.0, 150.0, 300.0))
    narrow = grid("c", [["x", "1"], ["y", "2"]], top=0, edges=(80.0, 100.0, 140.0))
    assert column_geometry(a, moved)[0] == "misaligned"
    assert column_geometry(a, narrow)[0] == "misaligned"
    # Text extents inside the cell frames (Docling-style) still align with ruled frames.
    inner = grid("d", [["x", "1"], ["y", "2"]], top=0, edges=(2.0, 98.0, 102.0))
    inner = replace(inner, cells=tuple(replace(c, bbox=replace(c.bbox, x1=40.0 if c.start_col_offset_idx == 0 else 290.0))
                                       for c in inner.cells))
    assert column_geometry(a, inner)[0] == "aligned"


def test_chain_of_three_pages_merges_once() -> None:
    middle = grid("m", [MORE[r % 3] for r in range(33)], top=80)     # fills page 2 top to bottom
    report, parsed = run(("table", 1, at_end()), ("table", 2, middle), ("table", 3, at_start("c")))
    assert report.chains == (("slot_000", "slot_001", "slot_002"),)
    node = next(n for n in parsed.nodes if isinstance(n, TableNode))
    assert [s.page_number for s in node.segments] == [1, 2, 3]
