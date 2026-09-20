"""Normalize Unstructured hi_res Table elements and HTML spans."""

from __future__ import annotations

import math
import shutil
from pathlib import Path
from html.parser import HTMLParser
from typing import Any

from rag.v2.common import BoundingBox, CoordinateTransform, validate_bbox
from rag.v2.table_models import GridPosition, TableCandidate, TableCell, TableRegion
from rag.v2.table_extraction import StrategyAdapter


class _TableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[tuple[str, bool, str | None, str | None]]] = []
        self._row: list[tuple[str, bool, str | None, str | None]] | None = None
        self._cell: list[str] | None = None
        self._header = False
        self._rowspan: str | None = None
        self._colspan: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            values = dict(attrs)
            self._cell, self._header = [], tag == "th"
            self._rowspan, self._colspan = values.get("rowspan"), values.get("colspan")
        elif tag == "br" and self._cell is not None:
            self._cell.append("\n")

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(("".join(self._cell), self._header, self._rowspan, self._colspan))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None


def _positive_span(value: str | None) -> int | None:
    if value is None or value == "":
        return 1
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _html_grid(html: str) -> tuple[list[tuple[str, bool, int, int, int, int]], int, int] | None:
    parser = _TableHTMLParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return None
    occupied: set[tuple[int, int]] = set()
    cells: list[tuple[str, bool, int, int, int, int]] = []
    max_col = 0
    for row_index, row in enumerate(parser.rows):
        col = 0
        for text, header, raw_rowspan, raw_colspan in row:
            while (row_index, col) in occupied:
                col += 1
            rowspan, colspan = _positive_span(raw_rowspan), _positive_span(raw_colspan)
            if rowspan is None or colspan is None:
                return None
            positions = {(row, column) for row in range(row_index, row_index + rowspan)
                         for column in range(col, col + colspan)}
            if occupied & positions:
                return None
            occupied.update(positions)
            cells.append((text, header, row_index, row_index + rowspan, col, col + colspan))
            col += colspan
            max_col = max(max_col, col)
    row_count = max((row1 for *_prefix, row1, _c0, _c1 in cells), default=0)
    return (cells, row_count, max_col) if cells and row_count and max_col else None


def _region(element: Any, page_sizes: dict[int, tuple[float, float]], source_ref: str) -> TableRegion:
    metadata = getattr(element, "metadata", None)
    page = getattr(metadata, "page_number", None)
    coordinates = getattr(metadata, "coordinates", None)
    system = getattr(coordinates, "system", None)
    points = getattr(coordinates, "points", None)
    if not isinstance(page, int) or page not in page_sizes:
        return TableRegion(None, None, None, None, source_ref, None, "invalid_page_geometry")
    page_width, page_height = page_sizes[page]
    layout_width, layout_height = getattr(system, "width", None), getattr(system, "height", None)
    if not all(isinstance(value, (int, float)) and math.isfinite(value) and value > 0 for value in (layout_width, layout_height)):
        return TableRegion(page, page_width, page_height, None, source_ref, None, "coordinate_conversion_failed")
    try:
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
        scale_x, scale_y = page_width / layout_width, page_height / layout_height
        box = validate_bbox(BoundingBox(min(xs) * scale_x, min(ys) * scale_y,
                                         max(xs) * scale_x, max(ys) * scale_y), page_width, page_height)
    except (TypeError, ValueError, IndexError):
        box, scale_x, scale_y = None, 1.0, 1.0
    transform = CoordinateTransform("top_left", "px", scale_x, scale_y, layout_height)
    return TableRegion(page, page_width, page_height, box, source_ref, transform,
                       None if box is not None else "invalid_bbox")


def normalize_element(
    element: Any, element_index: int, page_sizes: dict[int, tuple[float, float]],
    *, table_ordinal: int,
) -> TableCandidate:
    metadata = getattr(element, "metadata", None)
    page = getattr(metadata, "page_number", None)
    page_token = f"{page:04d}" if isinstance(page, int) else "multi"
    candidate_id = f"unstructured_hi_res_p{page_token}_t{table_ordinal:04d}"
    source_ref = f"unstructured/elements.json#/{element_index}"
    region = _region(element, page_sizes, source_ref)
    html = getattr(metadata, "text_as_html", None)
    parsed = _html_grid(html) if isinstance(html, str) and html.strip() else None
    raw_text = getattr(element, "text", None)
    if parsed is None:
        return TableCandidate(candidate_id, "unstructured", "hi_res", source_ref, (region,),
                              None, None, None, None, (), (),
                              raw_text if isinstance(raw_text, str) and raw_text.strip() else None, ())
    raw_cells, row_count, column_count = parsed
    cells = tuple(TableCell(
        f"{candidate_id}_r{row0:04d}c{col0:04d}_r{row1:04d}c{col1:04d}", text,
        row0, row1, col0, col1, row1 - row0, col1 - col0, None,
        ("column_header",) if header else ("body",), "native", (f"{source_ref}/html/cell/{index}",),
    ) for index, (text, header, row0, row1, col0, col1) in enumerate(raw_cells))
    covered = {(row, col) for cell in cells for row in range(cell.start_row_offset_idx, cell.end_row_offset_idx)  # type: ignore[arg-type]
               for col in range(cell.start_col_offset_idx, cell.end_col_offset_idx)}  # type: ignore[arg-type]
    uncovered = tuple(GridPosition(row, col) for row in range(row_count) for col in range(column_count)
                      if (row, col) not in covered)
    return TableCandidate(candidate_id, "unstructured", "hi_res", source_ref, (region,),
                          row_count, column_count, None, None, cells, uncovered, None, ())


def normalize_elements(elements: list[Any], page_sizes: dict[int, tuple[float, float]]) -> tuple[TableCandidate, ...]:
    candidates = []
    for index, element in enumerate(elements):
        category = getattr(element, "category", type(element).__name__)
        if str(category).casefold() == "table":
            candidates.append(normalize_element(element, index, page_sizes, table_ordinal=len(candidates) + 1))
    return tuple(candidates)


def make_adapter(work_root: Path) -> StrategyAdapter:
    def extract_page(source_pdf: Path, page_number: int) -> tuple[TableCandidate, ...]:
        import pymupdf
        from unstructured.partition.pdf import partition_pdf

        page_dir = work_root / f"unstructured-page-{page_number:04d}"
        if page_dir.exists():
            shutil.rmtree(page_dir)
        page_dir.mkdir(parents=True)
        page_pdf = page_dir / "page.pdf"
        try:
            with pymupdf.open(source_pdf) as source:
                if page_number <= 0 or page_number > len(source):
                    raise ValueError("page_number 超出 PDF 页数。")
                width, height = float(source[page_number - 1].rect.width), float(source[page_number - 1].rect.height)
                single = pymupdf.open()
                single.insert_pdf(source, from_page=page_number - 1, to_page=page_number - 1)
                single.save(page_pdf)
                single.close()
            elements = list(partition_pdf(filename=str(page_pdf), strategy="hi_res", infer_table_structure=True))
            for element in elements:
                metadata = getattr(element, "metadata", None)
                if metadata is not None:
                    metadata.page_number = page_number
            return normalize_elements(elements, {page_number: (width, height)})
        finally:
            if page_dir.exists():
                shutil.rmtree(page_dir)

    return StrategyAdapter("unstructured", "hi_res", extract_page)
