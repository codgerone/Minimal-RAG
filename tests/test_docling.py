from pathlib import Path
import hashlib

import fitz

from docling_core.types.doc import (
    BoundingBox, CoordOrigin, DocItemLabel, DoclingDocument, GroupLabel,
    ProvenanceItem, Size, TableCell, TableData,
)

from rag.ingest.parsers.docling_mapper import map_docling_document
from rag.ingest.parsers.docling_layout import DoclingLayoutParser
from rag.ingest.tables.extractors import DoclingTableExtractor
from rag.models import (
    PrimaryList, PrimaryTablePlaceholder, PrimaryText, SourceDocument,
)


def provenance(y: float) -> ProvenanceItem:
    return ProvenanceItem(page_no=1, charspan=(0, 1),
                          bbox=BoundingBox(l=10, t=y, r=90, b=y + 10,
                                           coord_origin=CoordOrigin.TOPLEFT))


def test_one_docling_object_maps_reading_order_and_native_table(tmp_path: Path) -> None:
    doc = DoclingDocument(name="fixture")
    doc.add_page(1, Size(width=100, height=100))
    header = doc.add_text(DocItemLabel.PAGE_HEADER, "Header", prov=provenance(0),
                          parent=doc.furniture)
    doc.add_text(DocItemLabel.TITLE, "Title", prov=provenance(15))
    group = doc.add_group(GroupLabel.LIST, "list")
    doc.add_list_item("First", marker="1.", prov=provenance(30), parent=group)
    nested = doc.add_group(GroupLabel.LIST, "list", parent=group)
    doc.add_list_item("Nested", marker="-", prov=provenance(40), parent=nested)
    caption = doc.add_text(DocItemLabel.CAPTION, "Table caption", prov=provenance(45))
    data = TableData(table_cells=[TableCell(
        text="A", start_row_offset_idx=0, end_row_offset_idx=1,
        start_col_offset_idx=0, end_col_offset_idx=1,
        bbox=BoundingBox(l=10, t=55, r=90, b=70), column_header=True,
    )], num_rows=1, num_cols=1)
    doc.add_table(data, caption=caption, prov=provenance(55))
    footer = doc.add_text(DocItemLabel.PAGE_FOOTER, "Footer", prov=provenance(90),
                          parent=doc.furniture)
    source = SourceDocument("doc", "sample.pdf", "sample.pdf", tmp_path / "sample.pdf", "a" * 64)

    mapped = map_docling_document(doc, source, {1: (100.0, 100.0)})
    assert isinstance(mapped.elements[0], PrimaryText)
    assert mapped.elements[0].source_ref == header.self_ref
    assert isinstance(mapped.elements[2], PrimaryList)
    assert mapped.elements[2].items[1].parent_item_id == mapped.elements[2].items[0].item_id
    assert sum(isinstance(item, PrimaryText) and item.text == "Table caption"
               for item in mapped.elements) == 1
    assert sum(isinstance(item, PrimaryTablePlaceholder) for item in mapped.elements) == 1
    assert mapped.elements[-1].source_ref == footer.self_ref
    assert mapped.table_slots[0].status == "eligible"
    assert mapped.native_tables[0].table.cells[0].roles == ("column_header",)


def test_docling_converts_once_and_table_extractor_reuses_it(tmp_path: Path) -> None:
    pdf_path = tmp_path / "source.pdf"
    with fitz.open() as pdf:
        pdf.new_page(width=100, height=100)
        pdf.save(pdf_path)
    doc = DoclingDocument(name="fixture")
    doc.add_page(1, Size(width=100, height=100))
    doc.add_text(DocItemLabel.TEXT, "Body", prov=provenance(10))
    source = SourceDocument("doc", "source.pdf", "source.pdf", pdf_path,
                            hashlib.sha256(pdf_path.read_bytes()).hexdigest())
    calls: list[bytes] = []

    def convert(data: bytes) -> DoclingDocument:
        calls.append(data)
        return doc

    parsed = DoclingLayoutParser(converter=convert).parse(source)
    report = DoclingTableExtractor().extract(source, parsed.shared)
    assert len(calls) == 1
    assert parsed.native == doc.export_to_dict()
    assert parsed.primary.elements[0].text == "Body"
    assert report.executions[0].status == "completed_no_tables"
