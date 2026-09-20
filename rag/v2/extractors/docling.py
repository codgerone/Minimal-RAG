"""Normalize Docling 2.121 table facts without leaking SDK objects downstream."""

from __future__ import annotations

from statistics import median
from typing import Any

from rag.v2.common import BoundingBox, CoordinateTransform, PageSpan, validate_bbox
from rag.v2.table_models import GridPosition, TableCandidate, TableCell, TableRegion


BOUNDARY_TOLERANCE_PT = 2.0


def _native_bbox(value: Any, page_width: float, page_height: float) -> tuple[BoundingBox | None, CoordinateTransform | None]:
    if value is None:
        return None, None
    try:
        left, top, right, bottom = (float(value.l), float(value.t), float(value.r), float(value.b))
    except (AttributeError, TypeError, ValueError):
        return None, None
    origin = getattr(getattr(value, "coord_origin", None), "value", getattr(value, "coord_origin", None))
    if origin == "TOPLEFT":
        box = BoundingBox(left, top, right, bottom)
        transform = CoordinateTransform("top_left", "pt", 1.0, 1.0, page_height)
    elif origin == "BOTTOMLEFT":
        box = BoundingBox(left, page_height - top, right, page_height - bottom)
        transform = CoordinateTransform("bottom_left", "pt", 1.0, 1.0, page_height)
    else:
        return None, None
    return validate_bbox(box, page_width, page_height), transform


def page_spans(item: Any, page_sizes: dict[int, tuple[float, float]], source_ref: str) -> tuple[PageSpan, ...]:
    spans: list[PageSpan] = []
    seen: set[tuple[int, float | None, float | None, float | None, float | None]] = set()
    for index, provenance in enumerate(getattr(item, "prov", ()) or ()):
        page_number = getattr(provenance, "page_no", None)
        if not isinstance(page_number, int) or page_number not in page_sizes:
            continue
        width, height = page_sizes[page_number]
        box, _ = _native_bbox(getattr(provenance, "bbox", None), width, height)
        key = (page_number,) + ((box.x0, box.y0, box.x1, box.y1) if box else (None, None, None, None))
        if key not in seen:
            spans.append(PageSpan(page_number, box, f"{source_ref}/prov/{index}"))
            seen.add(key)
    return tuple(spans)


def _cluster(values: list[float]) -> tuple[float, ...]:
    clusters: list[list[float]] = []
    for value in sorted(values):
        if clusters and value - clusters[-1][-1] <= BOUNDARY_TOLERANCE_PT:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    return tuple(float(median(group)) for group in clusters)


def _roles(raw: Any) -> tuple[str, ...]:
    roles = tuple(name for name in ("column_header", "row_header", "row_section") if getattr(raw, name, False))
    return roles or ("body",)


def normalize_table(
    table: Any,
    table_index: int,
    page_sizes: dict[int, tuple[float, float]],
    *,
    raw_path: str = "raw-docling-document.json",
) -> TableCandidate:
    source_ref = f"{raw_path}#/tables/{table_index}"
    provenances = tuple(getattr(table, "prov", ()) or ())
    provenance_pages = tuple(getattr(item, "page_no", None) for item in provenances)
    valid_pages = tuple(page for page in provenance_pages if isinstance(page, int) and page in page_sizes)
    unique_pages = tuple(dict.fromkeys(valid_pages))
    page_token = f"{unique_pages[0]:04d}" if len(unique_pages) == 1 else "multi"
    candidate_id = f"docling_accurate_p{page_token}_t{table_index + 1:04d}"

    regions: list[TableRegion] = []
    for index, provenance in enumerate(provenances):
        page_number = getattr(provenance, "page_no", None)
        located = isinstance(page_number, int) and page_number in page_sizes
        width, height = page_sizes[page_number] if located else (None, None)
        raw_bbox = getattr(provenance, "bbox", None)
        box, transform = _native_bbox(raw_bbox, width, height) if located else (None, None)
        unavailable = None
        if box is None:
            unavailable = "invalid_page_geometry" if not located else "missing_bbox" if raw_bbox is None else "coordinate_conversion_failed" if transform is None else "invalid_bbox"
        regions.append(TableRegion(page_number if located else None, width, height, box,
                                   f"{source_ref}/prov/{index}", transform, unavailable))

    data = getattr(table, "data", None)
    raw_cells = tuple(getattr(data, "table_cells", ()) or ())
    declared_rows, declared_cols = getattr(data, "num_rows", 0), getattr(data, "num_cols", 0)
    dimensions_valid = type(declared_rows) is int and type(declared_cols) is int and declared_rows > 0 and declared_cols > 0
    cell_page = unique_pages[0] if len(unique_pages) == 1 else None
    cells: list[TableCell] = []
    unplaced: list[str] = []
    for index, raw in enumerate(raw_cells):
        offsets = (
            getattr(raw, "start_row_offset_idx", None), getattr(raw, "end_row_offset_idx", None),
            getattr(raw, "start_col_offset_idx", None), getattr(raw, "end_col_offset_idx", None),
        )
        spans = (getattr(raw, "row_span", None), getattr(raw, "col_span", None))
        valid = all(type(value) is int for value in offsets) and offsets[0] >= 0 and offsets[2] >= 0 and offsets[1] > offsets[0] and offsets[3] > offsets[2]
        valid = valid and spans == (offsets[1] - offsets[0], offsets[3] - offsets[2])
        valid = valid and dimensions_valid and offsets[1] <= declared_rows and offsets[3] <= declared_cols
        text = getattr(raw, "text", None)
        if valid:
            row0, row1, col0, col1 = offsets
            cell_id = f"{candidate_id}_r{row0:04d}c{col0:04d}_r{row1:04d}c{col1:04d}"
            bbox = None
            if cell_page is not None:
                width, height = page_sizes[cell_page]
                bbox, _ = _native_bbox(getattr(raw, "bbox", None), width, height)
            cells.append(TableCell(cell_id, text, row0, row1, col0, col1, spans[0], spans[1], bbox,
                                   _roles(raw), "native", (f"{source_ref}/data/table_cells/{index}",)))
        else:
            cell_id = f"{candidate_id}_unplaced_{index + 1:04d}"
            cells.append(TableCell(cell_id, text, None, None, None, None, None, None, None,
                                   _roles(raw), "unavailable", (f"{source_ref}/data/table_cells/{index}",)))
            if isinstance(text, str) and text.strip():
                unplaced.append(text)

    if not dimensions_valid:
        return TableCandidate(candidate_id, "docling", "accurate", source_ref, tuple(regions),
                              None, None, None, None, tuple(cells), (), "\n".join(unplaced) or None, ())
    covered: set[tuple[int, int]] = set()
    for cell in cells:
        if cell.start_row_offset_idx is not None:
            covered.update((row, col) for row in range(cell.start_row_offset_idx, cell.end_row_offset_idx)  # type: ignore[arg-type]
                           for col in range(cell.start_col_offset_idx, cell.end_col_offset_idx))  # type: ignore[arg-type]
    uncovered = tuple(GridPosition(row, col) for row in range(declared_rows) for col in range(declared_cols)
                      if (row, col) not in covered)
    located_boxes = tuple(cell.bbox for cell in cells if cell.bbox is not None)
    xs = _cluster([value for box in located_boxes for value in (box.x0, box.x1)])
    ys = _cluster([value for box in located_boxes for value in (box.y0, box.y1)])
    boundaries = (xs, ys) if len(xs) == declared_cols + 1 and len(ys) == declared_rows + 1 else (None, None)
    return TableCandidate(candidate_id, "docling", "accurate", source_ref, tuple(regions),
                          declared_rows, declared_cols, boundaries[0], boundaries[1], tuple(cells), uncovered,
                          "\n".join(unplaced) or None, ())


def normalize_document_tables(document: Any, page_sizes: dict[int, tuple[float, float]]) -> tuple[TableCandidate, ...]:
    return tuple(normalize_table(table, index, page_sizes) for index, table in enumerate(document.tables))
