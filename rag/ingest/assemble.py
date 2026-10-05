"""Merge the primary reading order with the prepared tables into one ParsedDocument."""

from __future__ import annotations

from rag.models import (
    ListNode, ParsedDocument, PrimaryDocument, PrimaryList,
    PrimaryTablePlaceholder, PrimaryText, SourceDocument, TableNode, TextNode,
    ProcessingWarning,
)
from rag.ingest.tables.models import ContentResolution, PreparedTableContent


class DocumentCompositionError(ValueError):
    """The resolved document cannot be assembled without losing source facts."""


def compose_document(
    source: SourceDocument,
    primary: PrimaryDocument,
    resolutions: tuple[ContentResolution, ...],
    prepared_tables: tuple[PreparedTableContent, ...],
    branch_attached: bool,
    additional_warnings: tuple[ProcessingWarning, ...] = (),
) -> ParsedDocument:
    if primary.document_id != source.document_id or primary.file_hash != source.file_hash:
        raise DocumentCompositionError("primary source identity differs from requested PDF")
    placeholders = tuple(item for item in primary.elements
                         if isinstance(item, PrimaryTablePlaceholder))
    if not branch_attached and (placeholders or resolutions or prepared_tables):
        raise DocumentCompositionError("plain assembly received table branch facts")
    resolution_by_slot = {item.slot_id: item for item in resolutions}
    prepared_by_slot = {item.slot_id: item for item in prepared_tables}
    expected_slots = {item.slot_id for item in placeholders}
    if (len(resolution_by_slot) != len(resolutions)
            or len(prepared_by_slot) != len(prepared_tables)
            or set(resolution_by_slot) != expected_slots
            or set(prepared_by_slot) != expected_slots):
        raise DocumentCompositionError("table slot handoff is incomplete or duplicated")

    nodes = []
    for item in primary.elements:
        if isinstance(item, PrimaryText):
            nodes.append(TextNode(item.element_id, item.kind, item.text,
                                  item.sources, item.source_ref))
        elif isinstance(item, PrimaryList):
            nodes.append(ListNode(item.element_id, item.items, item.source_refs, item.sources))
        elif isinstance(item, PrimaryTablePlaceholder):
            resolution = resolution_by_slot[item.slot_id]
            prepared = prepared_by_slot[item.slot_id]
            if prepared.table_id != prepared.adopted_table.table_id:
                raise DocumentCompositionError("prepared table ID differs from adopted table")
            if (resolution.origin == "selected_winner"
                    and prepared.table_id != resolution.selected_candidate_id):
                raise DocumentCompositionError("prepared table is not the selected winner")
            nodes.append(TableNode(item.element_id, item.slot_id, item.source_ref,
                                   resolution.origin, prepared.adopted_table,
                                   resolution.selection_ref, prepared.header_decision,
                                   prepared.serialized_table, item.sources))
        else:
            raise DocumentCompositionError("unknown primary element")
    return ParsedDocument(source.document_id, source.document_name, source.relative_path,
                          source.file_hash, tuple(nodes), primary.warnings + additional_warnings)
