"""Prepare the exact adopted table for each primary document slot."""

from __future__ import annotations

from collections.abc import Callable

from rag.ingest.tables.header import detect_header
from rag.ingest.tables.formatter import serialize_table
from rag.models import PageSpan, PrimaryDocument, PrimaryTablePlaceholder
from rag.ingest.tables.models import (
    ContentResolution, HeaderDecision, PreparedTableContent, SerializedTable,
    StructuredTable, TableCandidate,
    TableExtractionReport,
)


class TablePreparationError(ValueError):
    """A selection cannot be reconciled with immutable extraction evidence."""


def _candidate_table(candidate: TableCandidate) -> StructuredTable:
    sources = tuple(
        PageSpan(region.page_number, region.bbox, region.source_ref)
        for region in candidate.regions if region.page_number is not None
    )
    return StructuredTable(
        candidate.candidate_id, candidate.tool, candidate.strategy,
        candidate.row_count, candidate.column_count, candidate.cells,
        candidate.uncovered_grid_positions, candidate.unplaced_text,
        sources, candidate.warnings,
    )


def prepare_tables(
    primary: PrimaryDocument,
    reports: tuple[TableExtractionReport, ...],
    resolutions: tuple[ContentResolution, ...],
    branch_attached: bool,
    *,
    header_detector: Callable[[StructuredTable], HeaderDecision] = detect_header,
    serializer: Callable[..., SerializedTable] = serialize_table,
) -> tuple[PreparedTableContent, ...]:
    """Resolve IDs once; header and text rules operate on the adopted structure."""
    if not branch_attached:
        if reports or resolutions or primary.table_slots:
            raise TablePreparationError("detached table branch has table evidence")
        return ()

    placeholders = tuple(item for item in primary.elements
                         if isinstance(item, PrimaryTablePlaceholder))
    if len(placeholders) != len(primary.table_slots):
        raise TablePreparationError("primary table slot count differs from placeholders")
    by_resolution = {item.slot_id: item for item in resolutions}
    if len(by_resolution) != len(resolutions) or set(by_resolution) != {item.slot_id for item in placeholders}:
        raise TablePreparationError("each table slot requires exactly one resolution")
    by_native = {item.slot_id: item for item in primary.native_tables}
    by_candidate: dict[str, TableCandidate] = {}
    for report in reports:
        if report.file_hash != primary.file_hash:
            raise TablePreparationError("extractor evidence belongs to another source version")
        for candidate in report.candidates:
            if candidate.candidate_id in by_candidate:
                raise TablePreparationError("duplicate candidate ID across extraction reports")
            by_candidate[candidate.candidate_id] = candidate

    prepared: list[PreparedTableContent] = []
    for placeholder in placeholders:
        resolution = by_resolution[placeholder.slot_id]
        if resolution.origin == "selected_winner":
            if not resolution.selected_candidate_id or not resolution.selection_ref:
                raise TablePreparationError("winner resolution lacks candidate or selection reference")
            candidate = by_candidate.get(resolution.selected_candidate_id)
            if candidate is None:
                raise TablePreparationError("selected candidate absent from this execution")
            adopted = _candidate_table(candidate)
        elif resolution.origin == "docling_native_fallback":
            if resolution.selection_ref is not None:
                raise TablePreparationError("native fallback must not claim winner evidence")
            native = by_native.get(placeholder.slot_id)
            if native is None or native.source_ref != placeholder.source_ref:
                raise TablePreparationError("native table missing or from another slot")
            if resolution.selected_candidate_id is not None:
                candidate = by_candidate.get(resolution.selected_candidate_id)
                if (candidate is None or candidate.tool != "docling"
                        or not candidate.source_ref.endswith(placeholder.source_ref)):
                    raise TablePreparationError("native fallback candidate is not this slot")
            adopted = native.table
        else:
            raise TablePreparationError("unknown table resolution origin")
        header = header_detector(adopted)
        serialized = serializer(adopted, header, table_node_id=placeholder.element_id)
        prepared.append(PreparedTableContent(placeholder.slot_id, adopted.table_id,
                                             adopted, header, serialized))
    return tuple(prepared)
