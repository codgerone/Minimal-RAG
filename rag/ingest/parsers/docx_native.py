"""Word parser: reads a .docx file directly (rag/ingest/docx/reader.py) into the primary document.
Rules: docs/rules/docx.md.

Reading order: headers, body, footnotes, endnotes, footers. Body paragraphs become headings (by
style or outline level only), list items (automatic numbering or bullets) or paragraphs; table-of-
contents paragraphs are skipped. Tables of two or more grid columns are tables, read exactly from
the file (`parser_exact`); a one-column table is a layout frame whose cells are read as body text.
Header, footer and note paragraphs are header, footer and footnote text. Pictures, charts and OLE
objects are skipped with a warning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rag.ingest.docx.reader import Block, Paragraph, Part, Table, cell_text, read_docx
from rag.ingest.parsers import ParseResult
from rag.ingest.parsers.docling_mapper import node_id
from rag.ingest.tables.models import GridPosition, StructuredTable, TableCell, TableSlot
from rag.models import (
    DocxSpan, ListItem, NativeTableFact, PrimaryDocument, PrimaryElement, PrimaryList,
    PrimaryTablePlaceholder, PrimaryText, ProcessingWarning, SourceDocument, TextKind, TextPiece,
)

# Rules version of the Word parser (docx_native.py and rag/ingest/docx/). Bump it, to the release
# that ships the change, whenever this code changes its output: Word documents are then rebuilt.
RULES_VERSION = "v4.2"

PART_LABELS = {"header": "页眉", "footer": "页脚", "footnotes": "脚注", "endnotes": "尾注"}
PART_KINDS: dict[str, TextKind] = {"header": "header", "footer": "footer", "footnotes": "footnote",
                                   "endnotes": "footnote"}
OBJECT_LABELS = {"picture": "图片", "chart": "图表", "ole": "嵌入对象"}


@dataclass
class _Builder:
    document_id: str
    elements: list[PrimaryElement] = field(default_factory=list)
    slots: list[TableSlot] = field(default_factory=list)
    tables: list[NativeTableFact] = field(default_factory=list)
    warnings: list[ProcessingWarning] = field(default_factory=list)
    items: list[ListItem] = field(default_factory=list)      # the list being collected
    skipped_toc: list[str] = field(default_factory=list)
    objects: list[dict[str, str]] = field(default_factory=list)
    frames: list[str] = field(default_factory=list)

    # --- lists ---------------------------------------------------------------------------
    def flush(self) -> None:
        if not self.items:
            return
        items = tuple(self.items)
        list_id = node_id(self.document_id, "list|" + items[0].item_id)
        renamed: list[ListItem] = []
        ids: dict[str, str] = {}
        for index, item in enumerate(items, start=1):
            ids[item.item_id] = f"{list_id}_item_{index:06d}"
        for item in items:
            renamed.append(ListItem(ids[item.item_id], item.text, item.level, item.ordinal,
                                    ids.get(item.parent_item_id or ""), item.sources, item.source_ref,
                                    item.pieces))
        self.elements.append(PrimaryList(list_id, tuple(renamed), tuple(i.source_ref or "" for i in items),
                                         tuple(span for item in items for span in item.sources)))
        self.items = []

    def add_item(self, paragraph: Paragraph, location: str, text: str, lead: int) -> None:
        parent = next((item.item_id for item in reversed(self.items) if item.level < paragraph.level), None)
        span = DocxSpan(paragraph.address, location, paragraph.address)
        pieces = (TextPiece(0, len(text), paragraph.address, lead),) if text else ()
        # item_id is the address until the list is complete; flush() gives the final IDs
        self.items.append(ListItem(paragraph.address, text, paragraph.level, paragraph.label, parent,
                                   (span,), paragraph.address, pieces))

    # --- elements ------------------------------------------------------------------------
    def text(self, paragraph: Paragraph, kind: TextKind, location: str) -> None:
        raw = paragraph.text
        body = raw.strip()
        lead = len(raw) - len(raw.lstrip())
        label = paragraph.label
        if not body and not label:
            return
        if label:
            text = f"{label} {body}".rstrip()
            pieces = (TextPiece(0, len(label), paragraph.address + "#label", 0),)
            if body:
                pieces += (TextPiece(len(label) + 1, len(text), paragraph.address, lead),)
        else:
            text, pieces = body, (TextPiece(0, len(body), paragraph.address, lead),)
        self.flush()
        self.elements.append(PrimaryText(node_id(self.document_id, paragraph.address), kind, text,
                                         (DocxSpan(paragraph.address, location, paragraph.address),),
                                         paragraph.address, (), pieces))

    def paragraph(self, paragraph: Paragraph, part: Part, location: str) -> None:
        for kind in paragraph.objects:
            self.objects.append({"address": paragraph.address, "kind": kind})
            self.warnings.append(ProcessingWarning(
                "unsupported_nontext_item", "docx_parsing",
                f"{location}的{OBJECT_LABELS.get(kind, kind)}未入库", (paragraph.address,)))
        if paragraph.toc:
            if paragraph.text.strip():
                self.skipped_toc.append(paragraph.address)
        elif part.kind != "body":
            self.text(paragraph, PART_KINDS[part.kind], location)
        elif paragraph.heading is not None:
            self.text(paragraph, "title", location)
        elif paragraph.numbered:
            raw = paragraph.text
            self.add_item(paragraph, location, raw.strip(), len(raw) - len(raw.lstrip()))
        else:
            self.text(paragraph, "paragraph", location)
        for inner in paragraph.textbox:
            self.block(inner, part, location)

    def table(self, table: Table, location_of: Any) -> None:
        if table.rows == 0 or table.columns == 0:
            return
        self.flush()
        number = len(self.slots) + 1
        slot_id, table_id = f"slot_{number:03d}", f"docx_table_{number:03d}"
        location = f"表 {number}"
        span = DocxSpan(table.address, location, table.address)
        cells = tuple(TableCell(f"{table_id}_r{c.row}c{c.column}", cell_text(c), c.row, c.row + c.row_span,
                                c.column, c.column + c.col_span, c.row_span, c.col_span, None,
                                ("column_header",) if c.header else ("body",), "native", (c.address,),
                                c.address)
                      for c in table.cells)
        uncovered = tuple(GridPosition(r, c, "missing_physical_cell") for r, c in table.uncovered)
        self.slots.append(TableSlot(slot_id, table.address, None, None, None, None, "eligible", None,
                                    (table.address,)))
        self.tables.append(NativeTableFact(slot_id, table.address, StructuredTable(
            table_id, "docx_native", "file", table.rows, table.columns, cells, uncovered, None, (span,), ())))
        self.elements.append(PrimaryTablePlaceholder(node_id(self.document_id, table.address), slot_id,
                                                     table.address, (span,)))

    def block(self, block: Block, part: Part, location: str) -> None:
        if isinstance(block, Paragraph):
            self.paragraph(block, part, location)
        elif block.columns == 1:
            # A one-column table is a layout frame: its cells are read in order as body text.
            self.frames.append(block.address)
            for cell in sorted(block.cells, key=lambda c: c.row):
                for inner in cell.blocks:
                    self.block(inner, part, location)
        else:
            self.table(block, location)


def _location(part: Part, index: int) -> str:
    return f"第 {index + 1} 块" if part.kind == "body" else PART_LABELS[part.kind]


class DocxNativeParser:
    provides_table_slots = True

    def identity(self) -> dict[str, Any]:
        from rag.ingest.libraries import versions
        return {"rules": RULES_VERSION, "libraries": versions("python-docx", "lxml")}

    def parse(self, source: SourceDocument, cache: Any = None) -> ParseResult:
        """The parse takes well under a second per document, so it is not cached."""
        file = read_docx(source.absolute_path)
        builder = _Builder(source.document_id)
        for part in file.parts:
            for index, block in enumerate(part.blocks):
                builder.block(block, part, _location(part, index))
            builder.flush()
        warnings = list(builder.warnings)
        for fmt in file.unknown_formats:
            warnings.append(ProcessingWarning("unknown_numbering_format", "docx_parsing",
                                              f"编号格式 {fmt} 不支持，按阿拉伯数字写出", ()))
        primary = PrimaryDocument(source.document_id, source.file_hash, None, tuple(builder.elements),
                                  tuple(builder.slots), tuple(builder.tables), tuple(warnings))
        native = {"format": "docx_native_v1",
                  "parts": [{"name": part.name, "kind": part.kind, "blocks": len(part.blocks)} for part in file.parts],
                  "skipped_toc": builder.skipped_toc, "objects": builder.objects,
                  "layout_frames": builder.frames}
        return ParseResult(primary, native)
