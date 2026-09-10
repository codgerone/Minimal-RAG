"""验证 Docling 表格到公共坐标的适配规则。"""

from types import SimpleNamespace

from experiments.table_extraction.infrastructure.pdf.coordinates import PageGeometry
from experiments.table_extraction.infrastructure.extractors.docling.normalizer import normalize_document


def _bbox(left: float, top: float, right: float, bottom: float) -> SimpleNamespace:
    """构造带左下原点标记的最小 Docling bbox。"""
    return SimpleNamespace(l=left, t=top, r=right, b=bottom, coord_origin="BOTTOMLEFT")


def _cell(text: str) -> SimpleNamespace:
    """构造一个覆盖单行单列的最小 Docling 单元格。"""
    return SimpleNamespace(
        bbox=_bbox(10, 80, 50, 20), text=text,
        start_row_offset_idx=0, end_row_offset_idx=1,
        start_col_offset_idx=0, end_col_offset_idx=1,
        row_span=1, col_span=1,
        column_header=False, row_header=False, row_section=False,
    )


def _document(provenances: list[SimpleNamespace]) -> SimpleNamespace:
    """构造仅含一张表的最小 DoclingDocument 替身。"""
    table = SimpleNamespace(prov=provenances, data=SimpleNamespace(table_cells=[_cell("A")]))
    return SimpleNamespace(tables=[table])


def test_single_page_docling_cells_use_the_only_provenance_page(monkeypatch) -> None:
    """单页 provenance 允许将 cell bbox 转换并建立网格边界。"""
    geometries = {1: PageGeometry(1, 100, 100, 0)}
    monkeypatch.setattr("experiments.table_extraction.infrastructure.extractors.docling.normalizer.page_geometries", lambda _: geometries)

    result = normalize_document(_document([SimpleNamespace(page_no=1, bbox=_bbox(10, 80, 50, 20))]), "sample.pdf")

    candidate = result.candidates[0]
    assert candidate.cells[0].bbox is not None
    assert candidate.cells[0].bbox.y0 == 20
    assert candidate.cells[0].bbox.y1 == 80
    assert candidate.x_boundaries == [10.0, 50.0]
    assert candidate.y_boundaries == [20.0, 80.0]


def test_multi_page_docling_table_does_not_guess_cell_page(monkeypatch) -> None:
    """多页 provenance 仅转换 regions，cell bbox 与边界必须置空。"""
    geometries = {1: PageGeometry(1, 100, 100, 0), 2: PageGeometry(2, 100, 100, 0)}
    monkeypatch.setattr("experiments.table_extraction.infrastructure.extractors.docling.normalizer.page_geometries", lambda _: geometries)
    provenances = [
        SimpleNamespace(page_no=1, bbox=_bbox(10, 80, 50, 20)),
        SimpleNamespace(page_no=2, bbox=_bbox(10, 80, 50, 20)),
    ]

    result = normalize_document(_document(provenances), "sample.pdf")

    candidate = result.candidates[0]
    assert all(region.bbox is not None for region in candidate.regions)
    assert candidate.cells[0].bbox is None
    assert candidate.x_boundaries is None
    assert candidate.y_boundaries is None
    assert any("cell-to-page" in warning for warning in candidate.warnings)
