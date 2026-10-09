"""V3 table facts, decision evidence and content models.

Transcribed from spec/architecture/table-contracts.md; business rules remain in
the requirements and are enforced by the producing adapters/services.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, TypeAlias

from rag.models import BoundingBox, PageSpan, ProcessingWarning

ToolName: TypeAlias = Literal["pymupdf", "camelot", "docling", "unstructured"]
TableStrategy: TypeAlias = Literal["lines", "lines_strict", "text", "lattice", "stream", "network", "hybrid", "accurate", "hi_res"]
StrategyStatus: TypeAlias = Literal["not_started", "completed_no_tables", "completed_with_tables", "completed_with_page_failures", "failed"]
CellRole: TypeAlias = Literal["column_header", "row_header", "row_section", "body", "unknown"]
SpanSource: TypeAlias = Literal["native", "geometry_inferred", "edge_inferred", "unavailable"]
RegionUnavailableReason: TypeAlias = Literal["missing_bbox", "coordinate_conversion_failed", "invalid_bbox", "invalid_page_geometry"]
SlotDeferredReason: TypeAlias = Literal["cross_page_slot", "missing_slot_provenance", "missing_slot_bbox", "slot_coordinate_conversion_failed", "invalid_slot_bbox", "invalid_slot_page_geometry"]
CandidateDeferredReason: TypeAlias = Literal["cross_page_candidate", "missing_candidate_region", "missing_candidate_bbox", "candidate_coordinate_conversion_failed", "invalid_candidate_bbox", "invalid_candidate_page_geometry"]
AdmissionDeferredReason: TypeAlias = Literal["cross_page_candidate", "missing_candidate_region", "missing_candidate_bbox", "candidate_coordinate_conversion_failed", "invalid_candidate_bbox", "invalid_candidate_page_geometry", "slot_source_page_failed"]
GroupUnresolvedReason: TypeAlias = Literal["cross_page_slot", "missing_slot_provenance", "missing_slot_bbox", "slot_coordinate_conversion_failed", "invalid_slot_bbox", "invalid_slot_page_geometry", "no_admitted_candidate"]
MatchReason: TypeAlias = Literal["bidirectional_coverage_passed", "candidate_coverage_below_minimum", "slot_coverage_below_minimum"]
AdmissionReason: TypeAlias = Literal["no_eligible_table_slot_on_page", "no_table_slot_passed_bidirectional_coverage", "matched_single_table_slot", "multiple_table_slots_passed_bidirectional_coverage"]
MetricName: TypeAlias = Literal["text_f1", "critical_token_integrity", "shape_support", "blank_anomaly"]
MetricReason: TypeAlias = Literal["empty_reference", "empty_candidate", "no_critical_reference_tokens", "invalid_or_missing_dimensions", "invalid_cell_interval", "overlapping_cells"]
HeaderInputIssueCode: TypeAlias = Literal["invalid_dimensions", "duplicate_cell_id", "unavailable_cell_position", "invalid_cell_interval", "span_mismatch", "overlapping_cells", "unplaced_text"]
HeaderStructuralIssueCode: TypeAlias = Literal["boundary_crosses_cell", "missing_header_position", "internal_blank_row", "internal_full_width_row", "crossing_column_intervals", "no_nonempty_path"]


@dataclass(frozen=True)
class PdfPageWord:
    page_number: int
    bbox: BoundingBox
    raw_text: str
    block_index: int
    line_index: int
    word_index: int


@dataclass(frozen=True)
class PreparedTableContent:
    slot_id: str
    table_id: str
    adopted_table: StructuredTable
    header_decision: HeaderDecision
    serialized_table: SerializedTable

@dataclass(frozen=True)
class TableExtractionReport:
    source_pdf: str
    file_hash: str
    executions: tuple[StrategyExecution, ...]
    candidates: tuple[TableCandidate, ...]
    warnings: tuple[ProcessingWarning, ...]

    def __post_init__(self) -> None:
        if not self.source_pdf or not self.file_hash:
            raise ValueError("extraction report source identity required")
        all_ids = tuple(candidate.candidate_id for candidate in self.candidates)
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("duplicate candidate ID in extraction report")
        bound = tuple(candidate_id for execution in self.executions
                      for candidate_id in execution.candidate_ids)
        if len(bound) != len(set(bound)) or set(bound) != set(all_ids):
            raise ValueError("report candidates disagree with strategy executions")
        by_id = {candidate.candidate_id: candidate for candidate in self.candidates}
        for execution in self.executions:
            if any((by_id[candidate_id].tool, by_id[candidate_id].strategy)
                   != (execution.tool, execution.strategy)
                   for candidate_id in execution.candidate_ids):
                raise ValueError("candidate belongs to another strategy execution")

@dataclass(frozen=True)
class StrategyExecution:
    tool: ToolName
    strategy: TableStrategy
    status: StrategyStatus
    pages: tuple[PageExecution, ...]
    candidate_ids: tuple[str, ...]
    error_type: str | None
    error_message: str | None

    def __post_init__(self) -> None:
        allowed = {
            "pymupdf": ("lines", "lines_strict", "text"),
            "camelot": ("lattice", "stream", "network", "hybrid"),
            "docling": ("accurate",),
            "unstructured": ("hi_res",),
        }
        if self.strategy not in allowed[self.tool]:
            raise ValueError("invalid table tool-strategy pair")
        page_numbers = tuple(page.page_number for page in self.pages)
        if page_numbers != tuple(sorted(set(page_numbers))):
            raise ValueError("page executions must be strictly ordered")
        page_ids = tuple(candidate_id for page in self.pages if page.status == "completed"
                         for candidate_id in page.candidate_ids)
        if len(self.candidate_ids) != len(set(self.candidate_ids)) or not set(page_ids) <= set(self.candidate_ids):
            raise ValueError("strategy candidate IDs disagree with successful pages")
        if self.tool != "docling" and self.candidate_ids != page_ids:
            raise ValueError("page strategy candidate order differs from pages")
        completed = any(page.status == "completed" for page in self.pages)
        failed = any(page.status == "failed" for page in self.pages)
        error = bool(self.error_type and self.error_message)
        if (self.error_type is None) != (self.error_message is None):
            raise ValueError("strategy diagnostic must be complete")
        states = {
            "not_started": not self.pages and not self.candidate_ids and error,
            "failed": not completed and not self.candidate_ids and error,
            "completed_no_tables": completed and not failed and not self.candidate_ids and not error,
            "completed_with_tables": completed and not failed and bool(self.candidate_ids) and not error,
            "completed_with_page_failures": completed and failed and not error,
        }
        if not states[self.status]:
            raise ValueError("strategy status contradicts execution evidence")

@dataclass(frozen=True)
class PageExecution:
    page_number: int
    status: Literal["completed", "failed"]
    candidate_ids: tuple[str, ...]
    error_type: str | None
    error_message: str | None

    def __post_init__(self) -> None:
        if type(self.page_number) is not int or self.page_number <= 0:
            raise ValueError("page execution needs one-based physical page")
        if self.status == "completed":
            if self.error_type is not None or self.error_message is not None:
                raise ValueError("successful page cannot carry an error")
        elif self.candidate_ids or not self.error_type or not self.error_message:
            raise ValueError("failed page must carry error and no candidates")

@dataclass(frozen=True)
class CoordinateTransform:
    source_origin: Literal["top_left", "bottom_left"]
    source_units: Literal["pt", "px"]
    scale_x: float
    scale_y: float
    page_height_before_scale: float

    def __post_init__(self) -> None:
        if not all(math.isfinite(value) and value > 0 for value in
                   (self.scale_x, self.scale_y, self.page_height_before_scale)):
            raise ValueError("coordinate transform requires finite positive geometry")

@dataclass(frozen=True)
class TableRegion:
    page_number: int | None
    page_width: float | None
    page_height: float | None
    bbox: BoundingBox | None
    source_ref: str
    coordinate_transform: CoordinateTransform | None
    unavailable_reason: RegionUnavailableReason | None

@dataclass(frozen=True)
class TableCandidate:
    candidate_id: str
    tool: ToolName
    strategy: TableStrategy
    source_ref: str
    regions: tuple[TableRegion, ...]
    row_count: int | None
    column_count: int | None
    x_boundaries: tuple[float, ...] | None
    y_boundaries: tuple[float, ...] | None
    cells: tuple[TableCell, ...]
    uncovered_grid_positions: tuple[GridPosition, ...]
    unplaced_text: str | None
    warnings: tuple[ProcessingWarning, ...]

@dataclass(frozen=True)
class TableCell:
    cell_id: str
    text: str | None
    start_row_offset_idx: int | None
    end_row_offset_idx: int | None
    start_col_offset_idx: int | None
    end_col_offset_idx: int | None
    row_span: int | None
    col_span: int | None
    bbox: BoundingBox | None
    roles: tuple[CellRole, ...]
    span_source: SpanSource
    source_refs: tuple[str, ...]

@dataclass(frozen=True)
class GridPosition:
    row_index: int
    column_index: int
    reason: Literal["missing_physical_cell"]

@dataclass(frozen=True)
class TableSlot:
    slot_id: str
    docling_table_ref: str
    page_number: int | None
    page_width: float | None
    page_height: float | None
    bbox: BoundingBox | None
    status: Literal["eligible", "deferred"]
    deferred_reason: SlotDeferredReason | None
    source_refs: tuple[str, ...]

@dataclass(frozen=True)
class ContentResolution:
    slot_id: str
    origin: Literal["selected_winner", "docling_native_fallback"]
    selected_candidate_id: str | None
    selection_ref: str | None
    decision_ref: str

@dataclass(frozen=True)
class CandidateView:
    candidate_id: str
    tool: ToolName
    strategy: TableStrategy
    page_number: int | None
    page_width: float | None
    page_height: float | None
    bbox: BoundingBox | None
    processing_status: Literal["comparable", "deferred"]
    deferred_reason: CandidateDeferredReason | None

@dataclass(frozen=True)
class SlotMatchMetrics:
    intersection_area: float
    union_area: float
    iou: float
    candidate_coverage: float
    slot_coverage: float

@dataclass(frozen=True)
class SlotMatchEvaluation:
    evaluation_id: str
    candidate_id: str
    slot_id: str
    decision: Literal["accepted", "rejected"]
    reason_codes: tuple[MatchReason, ...]
    metrics: SlotMatchMetrics

@dataclass(frozen=True)
class CandidateAdmissionResult:
    candidate_id: str
    processing_status: Literal["completed", "deferred"]
    deferred_reason: AdmissionDeferredReason | None
    admission_decision: Literal["admitted", "rejected"] | None
    matched_slot_id: str | None
    evaluated_slot_ids: tuple[str, ...]
    accepted_slot_ids: tuple[str, ...]
    reason_codes: tuple[AdmissionReason, ...]

@dataclass(frozen=True)
class TableGroup:
    group_id: str
    slot_id: str
    docling_table_ref: str
    page_number: int | None
    slot_bbox: BoundingBox | None
    status: Literal["ready_for_scoring", "unresolved"]
    unresolved_reason: GroupUnresolvedReason | None
    member_candidate_ids: tuple[str, ...]

@dataclass(frozen=True)
class GroupingReport:
    table_slots: tuple[TableSlot, ...]
    candidate_views: tuple[CandidateView, ...]
    slot_match_evaluations: tuple[SlotMatchEvaluation, ...]
    candidate_admission_results: tuple[CandidateAdmissionResult, ...]
    groups: tuple[TableGroup, ...]
    warnings: tuple[ProcessingWarning, ...]
    schema_version: Literal["table_candidate_selection_v3"]

@dataclass(frozen=True)
class TokenCount:
    token: str
    count: int

@dataclass(frozen=True)
class ReferenceWord:
    word_id: str
    page_number: int
    bbox: BoundingBox
    raw_text: str
    normalized_token: str
    block_index: int
    line_index: int
    word_index: int
    is_critical: bool

@dataclass(frozen=True)
class SlotTextReference:
    slot_id: str
    page_number: int
    slot_bbox: BoundingBox
    words: tuple[ReferenceWord, ...]
    token_counts: tuple[TokenCount, ...]
    critical_token_counts: tuple[TokenCount, ...]
    source: Literal["pymupdf_page_words_v1"]

@dataclass(frozen=True)
class CandidateRawMetrics:
    text_coverage: TextCoverageMetrics
    critical_tokens: CriticalTokenMetrics
    grid_shape: GridShapeMetrics
    blank_grid: BlankGridMetrics
    warnings: tuple[ProcessingWarning, ...]

@dataclass(frozen=True)
class CandidateScoringResult:
    candidate_id: str
    tool: ToolName
    strategy: TableStrategy
    raw_metrics: CandidateRawMetrics
    relative_scores: tuple[RelativeMetricScore, ...]
    total_score: float

@dataclass(frozen=True)
class GroupScoringResult:
    group_id: str
    slot_id: str
    text_reference: SlotTextReference
    candidates: tuple[CandidateScoringResult, ...]
    pair_evaluations: tuple[MetricPairEvaluation, ...]
    highest_total_score: float
    tied_top_candidate_ids: tuple[str, ...]
    selected_candidate_id: str
    selection_reason: Literal["highest_total_score", "tool_priority_tiebreak", "candidate_id_tiebreak"]

@dataclass(frozen=True)
class ScoringReport:
    source_pdf: str
    groups: tuple[GroupScoringResult, ...]
    warnings: tuple[ProcessingWarning, ...]
    schema_version: Literal["table_candidate_scoring_v3"]

@dataclass(frozen=True)
class TextCoverageMetrics:
    status: Literal["evaluated", "not_evaluable"]
    reason_codes: tuple[MetricReason, ...]
    reference_token_count: int
    candidate_token_count: int
    matched_token_count: int
    precision: float | None
    recall: float | None
    f1: float | None
    unmatched_reference_tokens: tuple[TokenCount, ...]
    unmatched_candidate_tokens: tuple[TokenCount, ...]

@dataclass(frozen=True)
class CriticalTokenMetrics:
    status: Literal["evaluated", "not_applicable"]
    reason_codes: tuple[MetricReason, ...]
    reference_token_count: int
    matched_token_count: int
    integrity: float | None
    unmatched_reference_tokens: tuple[TokenCount, ...]

@dataclass(frozen=True)
class GridShapeMetrics:
    status: Literal["evaluated"]
    reason_codes: tuple[MetricReason, ...]
    row_count: int | None
    column_count: int | None
    same_shape_candidate_count: int
    group_candidate_count: int
    support: float

@dataclass(frozen=True)
class BlankGridMetrics:
    status: Literal["evaluated", "not_evaluable"]
    reason_codes: tuple[MetricReason, ...]
    logical_position_count: int | None
    nonempty_covered_position_count: int | None
    blank_position_count: int | None
    blank_ratio: float | None
    reference_blank_ratio: float | None
    blank_anomaly: float | None
    reference_source: Literal["unique_mode", "minimum"] | None
    invalid_nonempty_cell_ids: tuple[str, ...]

@dataclass(frozen=True)
class RelativeMetricScore:
    metric_name: MetricName
    wins: int
    ties: int
    losses: int
    opponent_count: int
    score: float

@dataclass(frozen=True)
class MetricPairEvaluation:
    metric_name: MetricName
    first_candidate_id: str
    second_candidate_id: str
    first_value: float | None
    second_value: float | None
    decision: Literal["first_wins", "tie", "second_wins"]
    reason: Literal["higher_value", "lower_value", "equal_value", "both_unavailable", "only_first_evaluable", "only_second_evaluable"]

@dataclass(frozen=True)
class HeaderPath:
    column_index: int
    cell_ids: tuple[str, ...]
    display_parts: tuple[str, ...]

@dataclass(frozen=True)
class HeaderSkippedRow:
    row_index: int
    reason: Literal["missing_position", "horizontal_span", "blank_row"]
    cell_ids: tuple[str, ...]

@dataclass(frozen=True)
class HeaderIssue:
    code: HeaderInputIssueCode | HeaderStructuralIssueCode
    row_indices: tuple[int, ...] = ()
    column_indices: tuple[int, ...] = ()
    cell_ids: tuple[str, ...] = ()

@dataclass(frozen=True)
class HeaderObservation:
    row_index: int
    cell_id: str
    raw_text: str
    value_type: Literal["number", "date", "text_shape"]

@dataclass(frozen=True)
class HeaderColumnObservation:
    column_index: int
    lowest_header_cell_id: str | None
    header_value_type: Literal["number", "date", "text_shape", "empty"]
    observations: tuple[HeaderObservation, ...]
    stable_body_type: Literal["number", "date"] | None
    supports_transition: bool

@dataclass(frozen=True)
class HeaderCandidateEvaluation:
    start_row: int
    end_row: int
    result_reason: Literal["structural_rejection", "no_type_transition", "supported"]
    structural_issues: tuple[HeaderIssue, ...]
    sampled_row_indices: tuple[int, ...]
    skipped_rows: tuple[HeaderSkippedRow, ...]
    column_observations: tuple[HeaderColumnObservation, ...]
    supporting_columns: tuple[int, ...]
    tool_header_cell_ids_inside: tuple[str, ...]
    tool_header_cell_ids_outside: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.result_reason == "structural_rejection":
            if any((self.sampled_row_indices, self.skipped_rows, self.column_observations,
                    self.supporting_columns)):
                raise ValueError("structural rejection cannot carry sampling evidence")
        elif self.structural_issues:
            raise ValueError("sampling decision cannot carry structural issues")
        supported = tuple(item.column_index for item in self.column_observations
                          if item.supports_transition)
        if supported != self.supporting_columns:
            raise ValueError("supporting columns disagree with observations")

@dataclass(frozen=True)
class HeaderDecision:
    outcome: Literal["identified", "undetermined"]
    reason: Literal["invalid_grid", "unplaced_content", "no_candidate_region", "no_supported_candidate", "ambiguous_candidates", "unique_supported_candidate"]
    input_issues: tuple[HeaderIssue, ...]
    skipped_prefix_rows: tuple[int, ...]
    evaluations: tuple[HeaderCandidateEvaluation, ...]
    header_start_row: int | None
    header_end_row: int | None
    paths: tuple[HeaderPath, ...]
    rule_version: Literal["table_header_v1"]
    sample_row_budget: Literal[8]
    minimum_independent_observations: Literal[2]

    def __post_init__(self) -> None:
        if (self.rule_version, self.sample_row_budget,
                self.minimum_independent_observations) != ("table_header_v1", 8, 2):
            raise ValueError("unsupported header rule")
        identified = self.outcome == "identified"
        has_range = self.header_start_row is not None and self.header_end_row is not None
        if identified != (self.reason == "unique_supported_candidate" and has_range and bool(self.paths)):
            raise ValueError("header outcome and evidence disagree")
        if not identified and (self.header_start_row is not None or self.header_end_row is not None or self.paths):
            raise ValueError("undetermined header cannot publish paths")

@dataclass(frozen=True)
class CellSpan:
    """Where cell text sits inside a line (local character offsets)."""
    cell_ids: tuple[str, ...]
    start: int
    end: int

@dataclass(frozen=True)
class Continuation:
    """How a line is rewritten when it opens a chunk while a vertical merged cell runs through it.

    `start`/`end` cover the line's own markers for that cell; `text` replaces them. The value written
    into `text` is the anchor cell's text, found at `anchor_start`/`anchor_end` of `anchor_line_id`.
    """
    start: int
    end: int
    text: str
    anchor_line_id: str
    anchor_start: int
    anchor_end: int

@dataclass(frozen=True)
class SerializedTableLine:
    line_id: str
    kind: Literal["header", "data", "unplaced_text"]
    text: str
    source_rows: tuple[int, ...]
    source_cell_ids: tuple[str, ...]
    cell_spans: tuple[CellSpan, ...] = ()
    continuations: tuple[Continuation, ...] = ()

    def resumed_text(self) -> str:
        text = self.text
        for item in sorted(self.continuations, key=lambda c: c.start, reverse=True):
            text = text[:item.start] + item.text + text[item.end:]
        return text

TableTextRule: TypeAlias = Literal["markdown_rows_v1", "labeled_rows_v1"]

@dataclass(frozen=True)
class SerializedTable:
    text: str
    lines: tuple[SerializedTableLine, ...]
    rule_version: TableTextRule

    def __post_init__(self) -> None:
        if (self.rule_version not in ("markdown_rows_v1", "labeled_rows_v1")
                or self.text != "\n".join(line.text for line in self.lines)):
            raise ValueError("serialized table body or rule invalid")
        ids = tuple(line.line_id for line in self.lines)
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate serialized line ID")

@dataclass(frozen=True)
class StructuredTable:
    table_id: str
    tool: ToolName
    strategy: TableStrategy
    row_count: int | None
    column_count: int | None
    cells: tuple[TableCell, ...]
    uncovered_grid_positions: tuple[GridPosition, ...]
    unplaced_text: str | None
    sources: tuple[PageSpan, ...]
    warnings: tuple[ProcessingWarning, ...]
