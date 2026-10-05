"""T-02 boundary fixtures for the four table tool adapters."""

from __future__ import annotations

from types import SimpleNamespace as Obj

import pymupdf

from rag.ingest.tables.camelot_tables import normalize_table as camelot_table
from rag.ingest.tables.docling_tables import normalize_table as docling_table
from rag.ingest.tables.pymupdf_tables import _extract_page as pymupdf_page
from rag.ingest.tables.unstructured_tables import normalize_element as unstructured_element


def test_pymupdf_merged_cell_page_scope_and_unsupported_geometry(monkeypatch, tmp_path):
    table = Obj(
        bbox=(0, 0, 100, 100),
        rows=(Obj(cells=((0, 0, 100, 50), None)),
              Obj(cells=((0, 50, 50, 100), (50, 50, 100, 100)))),
        header=None,
        extract=lambda: (("Title", None), ("A", "B")),
    )

    class Page:
        rect = Obj(width=100, height=100)
        cropbox = (0, 0, 100, 100)
        mediabox = (0, 0, 100, 100)

        def __init__(self, rotation):
            self.rotation = rotation

        def find_tables(self, **_settings):
            return Obj(tables=(table,))

    class Document:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def __len__(self):
            return 2

        def __getitem__(self, index):
            return Page(0 if index == 1 else 90)

    monkeypatch.setattr(pymupdf, "open", lambda _path: Document())
    source = tmp_path / "synthetic.pdf"
    normal, = pymupdf_page(source, 2, "lines")
    assert normal.regions[0].page_number == 2
    assert (normal.row_count, normal.column_count) == (2, 2)
    assert any((cell.row_span, cell.col_span) == (1, 2) for cell in normal.cells)
    rotated, = pymupdf_page(source, 1, "lines")
    assert rotated.regions[0].unavailable_reason == "coordinate_conversion_failed"
    assert all(cell.span_source == "unavailable" for cell in rotated.cells)


def test_camelot_merged_edges_page_number_and_missing_bbox():
    def cell(x0, x1, y0, y1, text, **borders):
        return Obj(x1=x0, x2=x1, y1=y0, y2=y1, text=text,
                   left=borders.get("left", True), right=borders.get("right", True),
                   top=borders.get("top", True), bottom=borders.get("bottom", True))

    raw = Obj(cells=(
        (cell(0, 50, 50, 100, "Title", right=False),
         cell(50, 100, 50, 100, "Title", left=False)),
        (cell(0, 50, 0, 50, "A"), cell(50, 100, 0, 50, "B")),
    ))
    result = camelot_table(raw, "stream", 2, 1, 100, 100)
    assert result.regions[0].page_number == 2
    assert result.regions[0].coordinate_transform.source_origin == "bottom_left"
    assert any(cell.col_span == 2 and cell.text == "Title" for cell in result.cells)
    missing = camelot_table(Obj(cells=(), df=None), "stream", 2, 2, 100, 100)
    assert missing.regions[0].unavailable_reason == "missing_bbox"


def test_docling_cross_page_merged_cell_and_missing_coordinates():
    merged = Obj(start_row_offset_idx=0, end_row_offset_idx=1,
                 start_col_offset_idx=0, end_col_offset_idx=2,
                 row_span=1, col_span=2, text="Title", bbox=None,
                 column_header=True, row_header=False, row_section=False)
    raw = Obj(prov=(Obj(page_no=1, bbox=None), Obj(page_no=2, bbox=None)),
              data=Obj(table_cells=(merged,), num_rows=1, num_cols=2))
    result = docling_table(raw, 0, {1: (100, 100), 2: (100, 100)})
    assert [item.page_number for item in result.regions] == [1, 2]
    assert all(item.unavailable_reason == "missing_bbox" for item in result.regions)
    assert result.cells[0].col_span == 2
    assert result.cells[0].bbox is None
    assert result.cells[0].roles == ("column_header",)


def test_unstructured_html_span_and_missing_layout_coordinates_on_second_page():
    metadata = Obj(page_number=2, coordinates=None,
                   text_as_html="<table><tr><th colspan='2'>Title</th></tr>"
                                "<tr><td>A</td><td>B</td></tr></table>")
    result = unstructured_element(Obj(metadata=metadata, text="Title A B"), 0,
                                  {1: (100, 100), 2: (100, 100)}, table_ordinal=1)
    assert result.regions[0].page_number == 2
    assert result.regions[0].unavailable_reason == "coordinate_conversion_failed"
    assert (result.row_count, result.column_count) == (2, 2)
    assert any(cell.col_span == 2 and cell.text == "Title" for cell in result.cells)
