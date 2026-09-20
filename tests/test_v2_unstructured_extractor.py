from types import SimpleNamespace

from rag.v2.extractors.unstructured import normalize_element, normalize_elements


def _element(html: str | None, text: str = "fallback") -> SimpleNamespace:
    coordinates = SimpleNamespace(
        points=((20, 40), (180, 40), (180, 160), (20, 160)),
        system=SimpleNamespace(width=200, height=200),
    )
    return SimpleNamespace(category="Table", text=text, metadata=SimpleNamespace(
        page_number=1, text_as_html=html, coordinates=coordinates,
    ))


def test_html_rowspan_colspan_restore_one_physical_merged_cell() -> None:
    html = "<table><tr><th rowspan='2'>A</th><th colspan='2'>B</th></tr><tr><td>C</td><td>D</td></tr></table>"
    candidate = normalize_element(_element(html), 3, {1: (100.0, 100.0)}, table_ordinal=1)
    assert (candidate.row_count, candidate.column_count) == (2, 3)
    assert [(cell.row_span, cell.col_span) for cell in candidate.cells] == [(2, 1), (1, 2), (1, 1), (1, 1)]
    assert candidate.cells[0].roles == ("column_header",)
    assert candidate.cells[0].bbox is None
    assert candidate.regions[0].bbox.x0 == 10
    assert candidate.regions[0].bbox.y1 == 80


def test_missing_or_invalid_html_keeps_element_text_without_fake_grid() -> None:
    for html in (None, "<table><tr><td rowspan='bad'>A</td></tr></table>"):
        candidate = normalize_element(_element(html, "A B"), 0, {1: (100.0, 100.0)}, table_ordinal=1)
        assert candidate.row_count is None
        assert candidate.cells == ()
        assert candidate.unplaced_text == "A B"


def test_only_table_elements_are_normalized_in_original_order() -> None:
    elements = [SimpleNamespace(category="Text"), _element("<table><tr><td>A</td></tr></table>"),
                _element("<table><tr><td>B</td></tr></table>")]
    candidates = normalize_elements(elements, {1: (100.0, 100.0)})
    assert [item.candidate_id for item in candidates] == [
        "unstructured_hi_res_p0001_t0001", "unstructured_hi_res_p0001_t0002",
    ]
