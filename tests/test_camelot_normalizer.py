"""验证 Camelot 表格到公共候选的几何适配。"""

from types import SimpleNamespace

from experiments.table_extraction.infrastructure.extractors.camelot.normalizer import normalize_flavor
from experiments.table_extraction.infrastructure.pdf.coordinates import PageGeometry


def _cell(x1: float, y1: float, x2: float, y2: float, text: str) -> SimpleNamespace:
    """构造一个最小 Camelot Cell 替身。"""
    return SimpleNamespace(x1=x1, y1=y1, x2=x2, y2=y2, text=text, left=True, right=True, top=True, bottom=True)


def _table(cells: list[list[SimpleNamespace]]) -> SimpleNamespace:
    """构造一个最小 Camelot Table 替身。"""
    return SimpleNamespace(page="1", cells=cells)


def test_camelot_cells_use_common_top_left_coordinates(monkeypatch) -> None:
    """Camelot 左下原点 cell bbox 必须翻转为公共左上坐标。"""
    monkeypatch.setattr(
        "experiments.table_extraction.infrastructure.extractors.camelot.normalizer.page_geometries",
        lambda _: {1: PageGeometry(1, 100, 100, 0)},
    )
    table = _table([[_cell(10, 80, 50, 100, "A")]])

    result = normalize_flavor([table], "lattice", "sample.pdf")

    candidate = result.candidates[0]
    assert candidate.cells[0].bbox is not None
    assert candidate.cells[0].bbox.y0 == 0
    assert candidate.cells[0].bbox.y1 == 20
    assert candidate.regions[0].coordinate_transform.y_axis_flipped is True


def test_camelot_missing_shared_edge_recovers_merged_cell_span(monkeypatch) -> None:
    """相邻原子 cell 的共享竖线缺失时应恢复为 col_span=2。"""
    monkeypatch.setattr(
        "experiments.table_extraction.infrastructure.extractors.camelot.normalizer.page_geometries",
        lambda _: {1: PageGeometry(1, 100, 100, 0)},
    )
    left, right = _cell(0, 50, 50, 100, "header"), _cell(50, 50, 100, 100, "")
    left.right, right.left = False, False
    table = _table([[left, right], [_cell(0, 0, 50, 50, "left"), _cell(50, 0, 100, 50, "right")]])

    result = normalize_flavor([table], "lattice", "sample.pdf")

    candidate = result.candidates[0]
    header = candidate.cells[0]
    assert header.row_span == 1
    assert header.col_span == 2
    assert header.span_source == "edge_inferred"
    assert candidate.uncovered_grid_positions == []


def test_camelot_merges_atomic_cells_without_bbox_based_span(monkeypatch) -> None:
    """原子 bbox 各占一列时，仍应根据缺失共享边界生成一个合并 cell。"""
    monkeypatch.setattr(
        "experiments.table_extraction.infrastructure.extractors.camelot.normalizer.page_geometries",
        lambda _: {1: PageGeometry(1, 100, 100, 0)},
    )
    left = _cell(0, 0, 50, 50, "merged")
    right = _cell(50, 0, 100, 50, "")
    left.left, left.right, left.top, left.bottom = True, False, True, True
    right.left, right.right, right.top, right.bottom = False, True, True, True

    result = normalize_flavor([_table([[left, right]])], "lattice", "sample.pdf")

    candidate = result.candidates[0]
    assert len(candidate.cells) == 1
    assert candidate.cells[0].col_span == 2
    assert candidate.cells[0].bbox.x0 == 0
    assert candidate.cells[0].bbox.x1 == 100
    assert candidate.warnings == []


def test_camelot_missing_shared_horizontal_edge_recovers_row_span(monkeypatch) -> None:
    """相邻原子 cell 的共享横线缺失时应恢复为 row_span=2。"""
    monkeypatch.setattr(
        "experiments.table_extraction.infrastructure.extractors.camelot.normalizer.page_geometries",
        lambda _: {1: PageGeometry(1, 100, 100, 0)},
    )
    top, bottom = _cell(0, 50, 50, 100, "merged"), _cell(0, 0, 50, 50, "")
    top.bottom, bottom.top = False, False

    result = normalize_flavor([_table([[top], [bottom]])], "lattice", "sample.pdf")

    candidate = result.candidates[0]
    assert len(candidate.cells) == 1
    assert candidate.cells[0].row_span == 2
    assert candidate.cells[0].span_source == "edge_inferred"
