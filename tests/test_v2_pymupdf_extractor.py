from pathlib import Path

import pymupdf

from rag.v2.extractors.pymupdf import make_adapters


def _table_pdf(path: Path) -> None:
    with pymupdf.open() as document:
        page = document.new_page(width=200, height=200)
        for x in (20, 100, 180):
            page.draw_line((x, 20), (x, 100))
        for y in (20, 60, 100):
            page.draw_line((20, y), (180, y))
        page.insert_text((30, 45), "A")
        page.insert_text((110, 45), "B")
        page.insert_text((30, 85), "1")
        page.insert_text((110, 85), "2")
        document.save(path)


def test_pymupdf_lines_adapter_extracts_rag_owned_candidate(tmp_path: Path) -> None:
    path = tmp_path / "table.pdf"
    _table_pdf(path)

    candidate = make_adapters()[("pymupdf", "lines")].extract_page(path, 1)[0]

    assert candidate.candidate_id == "pymupdf_lines_p0001_t0001"
    assert candidate.row_count == 2
    assert candidate.column_count == 2
    assert [cell.text for cell in candidate.cells] == ["A", "B", "1", "2"]
    assert candidate.uncovered_grid_positions == ()
    assert all(cell.span_source == "geometry_inferred" for cell in candidate.cells)

