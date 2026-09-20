from types import SimpleNamespace

from rag.v2.extractors.camelot import normalize_table


def _cell(x1, y1, x2, y2, text, *, left=True, right=True, top=True, bottom=True):
    return SimpleNamespace(
        x1=x1, y1=y1, x2=x2, y2=y2, text=text,
        left=left, right=right, top=top, bottom=bottom,
    )


def test_camelot_rectangular_component_merges_and_flips_coordinates() -> None:
    table = SimpleNamespace(cells=[
        [
            _cell(0, 50, 50, 100, "same", right=False),
            _cell(50, 50, 100, 100, "same", left=False),
        ],
        [_cell(0, 0, 50, 50, "1"), _cell(50, 0, 100, 50, "2")],
    ])

    candidate = normalize_table(table, "lattice", 1, 1, 100, 100)

    assert candidate.candidate_id == "camelot_lattice_p0001_t0001"
    assert candidate.cells[0].text == "same"
    assert candidate.cells[0].col_span == 2
    assert candidate.cells[0].bbox.y0 == 0
    assert candidate.cells[0].bbox.y1 == 50
    assert candidate.regions[0].coordinate_transform.source_origin == "bottom_left"


def test_camelot_nonrectangular_missing_edge_component_is_not_hard_merged() -> None:
    table = SimpleNamespace(cells=[
        [
            _cell(0, 50, 50, 100, "a", right=False, bottom=False),
            _cell(50, 50, 100, 100, "b", left=False),
        ],
        [_cell(0, 0, 50, 50, "c", top=False), _cell(50, 0, 100, 50, "d")],
    ])

    candidate = normalize_table(table, "lattice", 1, 1, 100, 100)

    assert len(candidate.cells) == 4
    assert all(cell.row_span == cell.col_span == 1 for cell in candidate.cells)

