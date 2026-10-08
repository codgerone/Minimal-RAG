"""Where on the PDF each chunk's content comes from, derived from the chunk's sources.

Chunkers record, for every piece of a chunk, which element (text node, list item, table) and which
character range it came from. Parsers record where elements sit on the page. Joining the two gives
the chunk's regions, so every chunker gets them without code of its own:

- text with word positions: the words overlapping the chunk's range (fine);
- text, list item without word positions: the element's box; fine when the chunk holds the whole
  element, coarse when it holds only part of it; the whole page (coarse) when there is no box;
- table: the cells of the table lines in the chunk's range (fine; coarse when the chunk holds only
  part of a line, or the table's box when a cell has no position). When the formatter records where
  each cell sits in a line, part of a line gives the cells in that part. Under `row_text_v1` an
  identified header is written into every line as field names rather than as lines of its own; its
  cells count for the table's first chunk only;
- locator text the chunker adds (`fallback_locator`) is not content and has no region.
"""

from __future__ import annotations

from dataclasses import replace

from rag.ingest.chunkers.structured import normalize_node_text
from rag.models import (
    BoundingBox, ChunkBatch, ChunkRegion, ChunkSource, DocumentChunk, ListItem, ListNode,
    PageSpan, ParsedDocument, TableNode, TextNode,
)


def _span_regions(spans: tuple[PageSpan, ...], whole: bool) -> list[ChunkRegion]:
    return [ChunkRegion(s.page_number, s.bbox, "fine" if whole and s.bbox is not None else "coarse")
            for s in spans]


def _text_regions(node: TextNode, source: ChunkSource) -> list[ChunkRegion]:
    start = 0 if source.source_text_start is None else source.source_text_start
    end = len(node.text) if source.source_text_end is None else source.source_text_end
    if node.word_boxes:
        return [ChunkRegion(w.page_number, w.bbox, "fine") for w in node.word_boxes
                if w.start < end and start < w.end]
    whole = start == 0 and end >= len(normalize_node_text(node.text))
    return _span_regions(node.sources, whole)


def _item_regions(item: ListItem, source: ChunkSource) -> list[ChunkRegion]:
    whole = (source.source_text_start in (None, 0)
             and (source.source_text_end is None
                  or source.source_text_end >= len(normalize_node_text(item.text))))
    return _span_regions(item.sources, whole)


def _table_regions(node: TableNode, source: ChunkSource, first_fragment: bool) -> list[ChunkRegion]:
    table_box: BoundingBox | None = next((s.bbox for s in node.sources if s.bbox is not None), None)
    page = node.sources[0].page_number
    cells = {cell.cell_id: cell for cell in node.table.cells}
    start = source.source_text_start or 0
    end = len(node.serialized.text) if source.source_text_end is None else source.source_text_end
    regions: list[ChunkRegion] = []

    def add(cell_ids, precision: str) -> None:
        for cell_id in cell_ids:
            cell = cells.get(cell_id)
            if cell is not None and cell.bbox is not None:
                regions.append(ChunkRegion(page, cell.bbox, precision))   # type: ignore[arg-type]
            else:
                regions.append(ChunkRegion(page, table_box, "coarse"))

    offset = 0
    for line in node.serialized.lines:
        line_start, line_end = offset, offset + len(line.text)
        offset = line_end + 1
        if line_start < end and start < line_end:
            whole = start <= line_start and line_end <= end
            if whole or not line.cell_spans:
                add(line.source_cell_ids, "fine" if whole else "coarse")
                continue
            for span in line.cell_spans:
                if line_start + span.start < end and start < line_start + span.end:
                    inside = start <= line_start + span.start and line_start + span.end <= end
                    add(span.cell_ids, "fine" if inside else "coarse")
    header = node.header
    if first_fragment and header.outcome == "identified" and header.header_end_row:
        rows = range(header.header_start_row or 0, header.header_end_row)
        add([c.cell_id for c in node.table.cells
             if c.start_row_offset_idx is not None and c.start_row_offset_idx in rows], "fine")
    return regions


def chunk_regions(chunk: DocumentChunk, document: ParsedDocument) -> tuple[ChunkRegion, ...]:
    nodes = {node.node_id: node for node in document.nodes}
    items = {item.item_id: item for node in document.nodes if isinstance(node, ListNode)
             for item in node.items}
    regions: list[ChunkRegion] = []
    for source in chunk.sources:
        if source.context_kind == "fallback_locator":
            continue
        node = nodes.get(source.node_id)
        if isinstance(node, TextNode):
            regions += _text_regions(node, source)
        elif isinstance(node, TableNode):
            regions += _table_regions(node, source, chunk.fragment_index == 0)
        elif source.node_id in items:
            regions += _item_regions(items[source.node_id], source)
        else:
            regions += _span_regions(source.page_spans, False)
    return tuple(dict.fromkeys(regions))


def attach_regions(batch: ChunkBatch, document: ParsedDocument) -> ChunkBatch:
    return replace(batch, chunks=tuple(replace(chunk, regions=chunk_regions(chunk, document))
                                       for chunk in batch.chunks))
