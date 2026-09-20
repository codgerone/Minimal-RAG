from pathlib import Path

from rag.v2.common import BoundingBox, PageSpan
from rag.v2.document_models import LayoutDocument, LayoutTablePlaceholder, SourceDocument
from rag.v2.parsed_document import assemble_parsed_document
from rag.v2.table_models import TableCandidate, TableCell, TableRegion
from rag.v2.table_scoring import ScoringReport, SlotTextReference, score_group
from rag.v2.table_selection import (
    CandidateAdmissionResult, CandidateView, GroupingReport, TableGroup, TableSlot,
)


def _source() -> SourceDocument:
    return SourceDocument("doc", "a.pdf", "documents/a.pdf", Path("C:/a.pdf"), "hash")


def _candidate(identifier: str, tool: str, source_ref: str, value: str = "1") -> TableCandidate:
    strategy = "accurate" if tool == "docling" else "lines"
    cells = (
        TableCell(identifier + "h", "数量", 0, 1, 0, 1, 1, 1, None, ("column_header",), "native", ("h",)),
        TableCell(identifier + "a", value, 1, 2, 0, 1, 1, 1, None, ("body",), "native", ("a",)),
        TableCell(identifier + "b", "2", 2, 3, 0, 1, 1, 1, None, ("body",), "native", ("b",)),
    )
    return TableCandidate(identifier, tool, strategy, source_ref,
                          (TableRegion(1, 100, 100, BoundingBox(0, 0, 10, 10), source_ref, None),),
                          3, 1, (0.0, 10.0), (0.0, 3.0, 6.0, 10.0), cells, (), None, ())


def _layout(slot: TableSlot) -> LayoutDocument:
    placeholder = LayoutTablePlaceholder("node_table", "#/tables/0", "slot_001",
                                         (PageSpan(1, BoundingBox(0, 0, 10, 10), "#/tables/0/prov/0"),))
    return LayoutDocument("doc", 1, (placeholder,), (slot,), ())


def test_ready_group_uses_selected_winner_and_same_serialized_text() -> None:
    slot = TableSlot("slot_001", "#/tables/0", 1, 100, 100, BoundingBox(0, 0, 10, 10), "eligible", None, ())
    docling = _candidate("docling", "docling", "raw/document.json#/tables/0")
    winner = _candidate("winner", "pymupdf", "pymupdf")
    group = TableGroup("group_001", "slot_001", "#/tables/0", 1, slot.bbox,
                       "ready_for_scoring", None, (winner.candidate_id,))
    view = CandidateView(winner.candidate_id, "pymupdf", "lines", 1, 100, 100, slot.bbox, "comparable", None)
    admission = CandidateAdmissionResult(winner.candidate_id, "completed", None, "admitted", "slot_001",
                                         ("slot_001",), ("slot_001",), ("matched_single_table_slot",))
    grouping = GroupingReport((slot,), (view,), (), (admission,), (group,), ())
    reference = SlotTextReference("slot_001", 1, slot.bbox, (), (), ())
    score = score_group("group_001", "slot_001", reference, (winner,))
    parsed = assemble_parsed_document(_source(), _layout(slot), grouping,
                                      ScoringReport("a.pdf", (score,), ()), (docling, winner))
    node = parsed.nodes[0]
    assert node.origin == "selected_winner"
    assert node.selection_ref == "group_001:winner"
    assert node.table.table_id == "winner"
    assert node.serialized.lines[0].line_id.startswith("node_table_line_")


def test_unresolved_group_uses_docling_fallback_without_winner_claim() -> None:
    slot = TableSlot("slot_001", "#/tables/0", None, None, None, None,
                     "deferred", "missing_slot_provenance", ())
    docling = _candidate("docling", "docling", "raw/document.json#/tables/0")
    view = CandidateView(docling.candidate_id, "docling", "accurate", None, None, None, None,
                         "deferred", "missing_candidate_region")
    admission = CandidateAdmissionResult(docling.candidate_id, "deferred", "missing_candidate_region",
                                         None, None, (), (), ("missing_candidate_region",))
    group = TableGroup("group_001", "slot_001", "#/tables/0", None, None,
                       "unresolved", "missing_slot_provenance", ())
    grouping = GroupingReport((slot,), (view,), (), (admission,), (group,), ())
    parsed = assemble_parsed_document(_source(), _layout(slot), grouping,
                                      ScoringReport("a.pdf", (), ()), (docling,))
    node = parsed.nodes[0]
    assert node.origin == "docling_native_fallback"
    assert node.selection_ref is None
    assert node.table.table_id == "docling"
