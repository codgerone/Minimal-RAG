"""Select one adopted candidate or an explicit native fallback per table slot."""

from __future__ import annotations

from typing import Protocol

from rag.v3.application.table_scoring import build_slot_text_reference, score_group
from rag.v3.application.table_selection import (
    build_slot_groups, candidate_view, decide_candidate_admissions, normalize_slot,
    evaluate_slot_matches,
)
from rag.v3.contracts.documents import PrimaryDocument, SourceDocument
from rag.v3.contracts.tables import (
    ContentResolution, GroupingReport, ScoringReport, TableCandidate,
    TableExtractionReport,
)


class TableSelectionError(ValueError):
    """Table evidence does not belong to this source or cannot form unique decisions."""


class PdfEvidencePort(Protocol):
    def read_document_geometry(self, source: SourceDocument) -> tuple[tuple[float, float], ...]: ...
    def read_page_words(self, source: SourceDocument, page_number: int): ...


def select_tables(
    source: SourceDocument,
    primary: PrimaryDocument,
    reports: tuple[TableExtractionReport, ...],
    evidence: PdfEvidencePort,
) -> tuple[tuple[ContentResolution, ...], GroupingReport, ScoringReport]:
    if primary.document_id != source.document_id or primary.file_hash != source.file_hash:
        raise TableSelectionError("primary and source identity differ")
    candidates: list[TableCandidate] = []
    for report in reports:
        if report.file_hash != source.file_hash or report.source_pdf != source.relative_path:
            raise TableSelectionError("extractor report belongs to another PDF version")
        candidates.extend(report.candidates)
    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    if len(by_id) != len(candidates):
        raise TableSelectionError("duplicate candidate ID across bound extractors")
    geometry = evidence.read_document_geometry(source)
    if len(geometry) != primary.page_count:
        raise TableSelectionError("main parser and PDF page counts differ")
    def actual(page: int | None) -> tuple[float, float] | None:
        return geometry[page - 1] if page is not None and 1 <= page <= len(geometry) else None
    slots = tuple(normalize_slot(slot, actual(slot.page_number))
                  for slot in primary.table_slots)
    views = tuple(candidate_view(candidate,
        actual(candidate.regions[0].page_number) if candidate.regions else None,
        require_actual=True) for candidate in candidates)
    matches = evaluate_slot_matches(views, slots)
    failed_docling_pages = frozenset(
        page.page_number
        for report in reports for execution in report.executions
        if execution.tool == "docling"
        for page in execution.pages if page.status == "failed"
    )
    admissions = decide_candidate_admissions(views, matches, failed_docling_pages)
    groups = build_slot_groups(slots, views, admissions)
    grouping = GroupingReport(slots, views, matches, admissions,
                              groups, (), "table_candidate_selection_v3")

    scored_groups = []
    page_words = {}
    for group in groups:
        if group.status != "ready_for_scoring":
            continue
        if group.page_number is None or group.slot_bbox is None:
            raise TableSelectionError("ready group has no PDF geometry")
        if group.page_number not in page_words:
            page_words[group.page_number] = evidence.read_page_words(source, group.page_number)
        reference = build_slot_text_reference(group.slot_id, group.page_number,
                                              group.slot_bbox, page_words[group.page_number])
        members = tuple(by_id[identifier] for identifier in group.member_candidate_ids)
        scored_groups.append(score_group(group.group_id, group.slot_id, reference, members))
    scoring = ScoringReport(source.relative_path, tuple(scored_groups), (),
                            "table_candidate_scoring_v3")
    score_by_slot = {item.slot_id: item for item in scored_groups}
    group_by_slot = {item.slot_id: item for item in groups}
    resolutions: list[ContentResolution] = []
    for slot in primary.table_slots:
        group = group_by_slot[slot.slot_id]
        scored = score_by_slot.get(slot.slot_id)
        if scored is not None:
            candidate_id = scored.selected_candidate_id
            resolutions.append(ContentResolution(slot.slot_id, "selected_winner",
                                                 candidate_id, f"{group.group_id}:{candidate_id}",
                                                 group.group_id))
        else:
            native_ids = tuple(candidate.candidate_id for candidate in candidates
                               if candidate.tool == "docling"
                               and candidate.source_ref.endswith(slot.docling_table_ref))
            if len(native_ids) > 1:
                raise TableSelectionError("multiple native Docling candidates for one slot")
            resolutions.append(ContentResolution(slot.slot_id, "docling_native_fallback",
                                                 native_ids[0] if native_ids else None,
                                                 None, group.group_id))
    return tuple(resolutions), grouping, scoring
