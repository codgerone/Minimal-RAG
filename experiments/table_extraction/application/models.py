"""Application requests and page-level execution facts."""
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from experiments.table_extraction.domain.models.tables import TableCandidate

@dataclass
class PageExtractionResult:
    page_number: int
    status: Literal['success', 'failed']
    candidate_ids: list[str] = field(default_factory=list)
    error: str | None = None

@dataclass
class StrategyExecution:
    tool: str
    strategy: str
    requested_page_numbers: list[int]
    startup_error: str | None = None
    page_results: list[PageExtractionResult] = field(default_factory=list)
    run_metadata: dict = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return (self.startup_error is None
                and bool(self.requested_page_numbers)
                and sorted(p.page_number for p in self.page_results) == sorted(self.requested_page_numbers)
                and all(p.status == 'success' for p in self.page_results))

    def to_dict(self) -> dict:
        return asdict(self)

@dataclass(frozen=True)
class ExtractionRequest:
    source_pdf: Path
    output_dir: Path
    tool: str
    strategies: tuple[str, ...] = ()
    pages: str = 'all'

@dataclass
class ExtractionResult:
    candidates: list[TableCandidate]
    executions: list[StrategyExecution]
    manifest_path: Path

    @property
    def succeeded(self) -> bool:
        return bool(self.executions) and all(run.succeeded for run in self.executions)

from experiments.table_extraction.domain.models.selection import TableGroup, TableSlot

@dataclass(frozen=True)
class GroupingManifest:
    """记录一次准入与分组运行的输入、计数和输出位置。"""

    source_pdf: str
    format_version: str
    input_candidate_counts: dict[str, int]
    table_slot_count: int
    eligible_slot_count: int
    deferred_slot_count: int
    candidate_count: int
    comparable_candidate_count: int
    deferred_candidate_count: int
    admitted_candidate_count: int
    rejected_candidate_count: int
    ready_group_count: int
    unresolved_group_count: int
    warnings: list[str]
    elapsed_seconds: float
    groups_file: str
    manual_review_dir: str
    deferred_admission_count: int = 0


@dataclass(frozen=True)
class LoadedScoringInput:
    """保存经过身份校验的分组事实及组内完整候选。"""

    grouping_report_path: str
    groups: list[TableGroup]
    slots_by_id: dict[str, TableSlot]
    candidates_by_id: dict[str, TableCandidate]


@dataclass(frozen=True)
class ScoringManifest:
    """记录一次评分运行的输入、计数和输出位置。"""

    source_pdf: str
    format_version: str
    source_grouping_report: str
    ready_group_count: int
    scored_group_count: int
    scored_candidate_count: int
    selected_by_reason_counts: dict[str, int]
    warnings: list[str]
    elapsed_seconds: float
    scoring_file: str
    manual_review_dir: str

