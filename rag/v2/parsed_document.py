"""Fuse layout placeholders and table decisions into the complete ParsedDocument."""

from __future__ import annotations

from rag.v2.common import PageSpan
from rag.v2.document_models import (
    LayoutDocument, LayoutList, LayoutTablePlaceholder, LayoutText, ListNode,
    ParsedDocument, SourceDocument, StructuredTable, TableNode, TextNode,
)
from rag.v2.table_header import detect_header
from rag.v2.table_models import TableCandidate
from rag.v2.table_scoring import ScoringReport
from rag.v2.table_selection import GroupingReport
from rag.v2.table_serialization import serialize_table


class DocumentFusionError(RuntimeError):
    """Raised when decision references cannot form a complete parsed document."""


def _candidate_sources(candidate: TableCandidate) -> tuple[PageSpan, ...]:
    return tuple(
        PageSpan(region.page_number, region.bbox, region.source_ref)
        for region in candidate.regions if region.page_number is not None
    )


def _structured(candidate: TableCandidate) -> StructuredTable:
    return StructuredTable(
        candidate.candidate_id, candidate.tool, candidate.strategy,
        candidate.row_count, candidate.column_count, candidate.cells,
        candidate.uncovered_grid_positions, candidate.unplaced_text,
        _candidate_sources(candidate), candidate.warnings,
    )


def assemble_parsed_document(
    source: SourceDocument,
    layout: LayoutDocument,
    grouping: GroupingReport,
    scoring: ScoringReport,
    candidates: tuple[TableCandidate, ...],
) -> ParsedDocument:
    if layout.document_id != source.document_id:
        raise DocumentFusionError("LayoutDocument 与 SourceDocument 身份不一致。")
    by_candidate = {item.candidate_id: item for item in candidates}
    if len(by_candidate) != len(candidates):
        raise DocumentFusionError("TableCandidate ID 重复。")
    group_by_slot = {item.slot_id: item for item in grouping.groups}
    scores_by_group = {item.group_id: item for item in scoring.groups}
    docling_by_ref = {
        "#" + candidate.source_ref.split("#", 1)[1]: candidate
        for candidate in candidates if candidate.tool == "docling" and "#" in candidate.source_ref
    }
    nodes = []
    for element in layout.elements:
        if isinstance(element, LayoutText):
            nodes.append(TextNode(element.element_id, element.kind, element.text, element.docling_ref, element.sources))
        elif isinstance(element, LayoutList):
            nodes.append(ListNode(element.element_id, element.items, element.docling_refs, element.sources))
        elif isinstance(element, LayoutTablePlaceholder):
            group = group_by_slot.get(element.slot_id)
            if group is None:
                raise DocumentFusionError(f"placeholder {element.slot_id} 缺少分组结果。")
            if group.status == "ready_for_scoring":
                scored = scores_by_group.get(group.group_id)
                if scored is None:
                    raise DocumentFusionError(f"ready group {group.group_id} 缺少评分结果。")
                candidate = by_candidate.get(scored.selected_candidate_id)
                if candidate is None or candidate.candidate_id not in group.member_candidate_ids:
                    raise DocumentFusionError(f"winner {scored.selected_candidate_id} 不属于对应 group。")
                origin = "selected_winner"
                selection_ref = f"{group.group_id}:{candidate.candidate_id}"
            else:
                candidate = docling_by_ref.get(element.docling_ref)
                if candidate is None:
                    raise DocumentFusionError(f"fallback {element.docling_ref} 缺少 Docling 原生候选。")
                origin = "docling_native_fallback"
                selection_ref = None
            header = detect_header(candidate)
            serialized = serialize_table(candidate, header, table_node_id=element.element_id)
            nodes.append(TableNode(
                element.element_id, element.slot_id, element.docling_ref, origin,
                _structured(candidate), selection_ref, header, serialized, element.sources,
            ))
        else:  # pragma: no cover - closed union defensive check
            raise DocumentFusionError(f"未知 LayoutElement: {type(element).__name__}")
    return ParsedDocument(source.document_id, source.document_name, source.relative_path,
                          source.file_hash, tuple(nodes), layout.warnings + grouping.warnings + scoring.warnings)
