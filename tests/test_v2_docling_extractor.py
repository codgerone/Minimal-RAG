from types import SimpleNamespace

from docling_core.types.doc import BoundingBox as NativeBox
from docling_core.types.doc import CoordOrigin, TableCell as NativeCell, TableData

from rag.v2.extractors.docling import normalize_table


def _cell(text: str, row: int, col: int, *, header: bool = False) -> NativeCell:
    return NativeCell(
        text=text, start_row_offset_idx=row, end_row_offset_idx=row + 1,
        start_col_offset_idx=col, end_col_offset_idx=col + 1,
        bbox=NativeBox(l=10 + col * 40, t=10 + row * 20, r=50 + col * 40,
                       b=30 + row * 20, coord_origin=CoordOrigin.TOPLEFT),
        column_header=header,
    )


def test_docling_native_offsets_roles_and_bottom_left_regions_are_preserved() -> None:
    table = SimpleNamespace(
        prov=[SimpleNamespace(page_no=1, bbox=NativeBox(
            l=10, t=90, r=90, b=10, coord_origin=CoordOrigin.BOTTOMLEFT))],
        data=TableData(table_cells=[_cell("A", 0, 0, header=True), _cell("1", 0, 1)],
                       num_rows=1, num_cols=2),
    )
    result = normalize_table(table, 0, {1: (100.0, 100.0)})
    assert result.candidate_id == "docling_accurate_p0001_t0001"
    assert result.regions[0].bbox.y0 == 10
    assert result.regions[0].bbox.y1 == 90
    assert result.cells[0].roles == ("column_header",)
    assert result.cells[0].span_source == "native"
    assert result.x_boundaries == (10.0, 50.0, 90.0)


def test_cross_page_table_keeps_logical_grid_without_inventing_cell_geometry() -> None:
    table = SimpleNamespace(
        prov=[
            SimpleNamespace(page_no=1, bbox=NativeBox(l=0, t=0, r=100, b=50)),
            SimpleNamespace(page_no=2, bbox=NativeBox(l=0, t=0, r=100, b=50)),
        ],
        data=TableData(table_cells=[_cell("A", 0, 0)], num_rows=1, num_cols=1),
    )
    result = normalize_table(table, 0, {1: (100.0, 100.0), 2: (100.0, 100.0)})
    assert result.row_count == result.column_count == 1
    assert result.x_boundaries is None and result.y_boundaries is None
    assert result.cells[0].bbox is None
    assert result.cells[0].start_row_offset_idx == 0


def test_invalid_native_span_is_unplaced_without_partial_coordinates() -> None:
    invalid = SimpleNamespace(
        text="orphan", start_row_offset_idx=0, end_row_offset_idx=1,
        start_col_offset_idx=0, end_col_offset_idx=1, row_span=2, col_span=1,
        bbox=None, column_header=False, row_header=False, row_section=False,
    )
    table = SimpleNamespace(prov=[], data=SimpleNamespace(table_cells=[invalid], num_rows=1, num_cols=1))
    result = normalize_table(table, 0, {})
    assert result.cells[0].span_source == "unavailable"
    assert result.cells[0].start_row_offset_idx is None
    assert result.unplaced_text == "orphan"
    assert result.uncovered_grid_positions[0].row_index == 0
