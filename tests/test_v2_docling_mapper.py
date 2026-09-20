from pathlib import Path

from docling_core.types.doc import (
    BoundingBox, CoordOrigin, DocItemLabel, DoclingDocument, GroupLabel,
    ProvenanceItem, Size, TableCell, TableData,
)

from rag.v2.docling_mapper import map_docling_document
from rag.v2.document_models import LayoutList, LayoutTablePlaceholder, LayoutText, SourceDocument


def _prov(page: int, y: float) -> ProvenanceItem:
    return ProvenanceItem(page_no=page, charspan=(0, 1),
                          bbox=BoundingBox(l=10, t=y, r=90, b=y + 10, coord_origin=CoordOrigin.TOPLEFT))


def _source(tmp_path: Path) -> SourceDocument:
    path = (tmp_path / "sample.pdf").resolve()
    return SourceDocument("doc", "sample.pdf", "documents/sample.pdf", path, "hash")


def test_mapper_preserves_body_list_table_and_furniture_order(tmp_path: Path) -> None:
    document = DoclingDocument(name="fixture")
    document.add_page(1, Size(width=100, height=100))
    header = document.add_text(DocItemLabel.PAGE_HEADER, "Header", prov=_prov(1, 0), parent=document.furniture)
    document.add_text(DocItemLabel.TITLE, "Title", prov=_prov(1, 15))
    group = document.add_group(GroupLabel.LIST, "list")
    first = document.add_list_item("First", marker="1.", prov=_prov(1, 30), parent=group)
    nested = document.add_group(GroupLabel.LIST, "list", parent=group)
    document.add_list_item("Nested", marker="-", prov=_prov(1, 40), parent=nested)
    caption = document.add_text(DocItemLabel.CAPTION, "Table caption", prov=_prov(1, 45))
    data = TableData(table_cells=[TableCell(
        text="A", start_row_offset_idx=0, end_row_offset_idx=1,
        start_col_offset_idx=0, end_col_offset_idx=1,
        bbox=BoundingBox(l=10, t=55, r=90, b=70), column_header=True,
    )], num_rows=1, num_cols=1)
    document.add_table(data, caption=caption, prov=_prov(1, 55))
    footer = document.add_text(DocItemLabel.PAGE_FOOTER, "Footer", prov=_prov(1, 90), parent=document.furniture)

    result = map_docling_document(document, _source(tmp_path), {1: (100.0, 100.0)})
    elements = result.layout_document.elements
    assert isinstance(elements[0], LayoutText) and elements[0].docling_ref == header.self_ref
    assert isinstance(elements[2], LayoutList)
    assert elements[2].items[1].parent_item_id == elements[2].items[0].item_id
    assert sum(isinstance(item, LayoutText) and item.text == "Table caption" for item in elements) == 1
    assert sum(isinstance(item, LayoutTablePlaceholder) for item in elements) == 1
    assert isinstance(elements[-1], LayoutText) and elements[-1].docling_ref == footer.self_ref
    assert result.layout_document.table_slots[0].status == "eligible"
    assert result.docling_table_candidates[0].cells[0].roles == ("column_header",)
    assert result.raw_document.table_count == 1


def test_detached_text_without_source_is_retained_with_warning(tmp_path: Path) -> None:
    document = DoclingDocument(name="fixture")
    document.add_page(1, Size(width=100, height=100))
    detached = document.add_text(DocItemLabel.TEXT, "Detached")
    # Remove it from body while keeping it in the authoritative texts container.
    document.body.children = [ref for ref in document.body.children if ref.cref != detached.self_ref]

    result = map_docling_document(document, _source(tmp_path), {1: (100.0, 100.0)})
    assert result.layout_document.elements[-1].text == "Detached"
    assert any(item.code == "source_location_unavailable" for item in result.layout_document.warnings)


def test_adjacent_list_groups_remain_separate(tmp_path: Path) -> None:
    document = DoclingDocument(name="fixture")
    document.add_page(1, Size(width=100, height=100))
    for number in range(2):
        group = document.add_group(GroupLabel.LIST, "list")
        document.add_list_item(f"item {number}", parent=group)

    result = map_docling_document(document, _source(tmp_path), {1: (100.0, 100.0)})
    assert len([item for item in result.layout_document.elements if isinstance(item, LayoutList)]) == 2


def test_formula_falls_back_to_orig_when_normalized_text_is_empty(tmp_path: Path) -> None:
    document = DoclingDocument(name="fixture")
    document.add_page(1, Size(width=100, height=100))
    formula = document.add_text(
        DocItemLabel.FORMULA,
        "",
        orig="Penalty = numerator denominator",
        prov=_prov(1, 20),
    )

    result = map_docling_document(document, _source(tmp_path), {1: (100.0, 100.0)})

    mapped = next(
        item for item in result.layout_document.elements
        if isinstance(item, LayoutText) and item.docling_ref == formula.self_ref
    )
    assert mapped.text == "Penalty = numerator denominator"
    assert any(
        item.code == "formula_orig_fallback" and item.source_refs == (formula.self_ref,)
        for item in result.layout_document.warnings
    )
