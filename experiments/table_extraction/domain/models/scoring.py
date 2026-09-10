"""表格评分参照、指标、相对分和选优结果的数据模型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from experiments.table_extraction.domain.models.selection import TableGroup, TableSlot
from experiments.table_extraction.domain.models.tables import BoundingBox, TableCandidate


MetricName = Literal[
    "text_f1",
    "critical_token_integrity",
    "shape_support",
    "blank_anomaly",
]
MetricStatus = Literal["evaluated", "not_evaluable", "not_applicable"]
BlankReferenceSource = Literal["unique_mode", "minimum"]
PairwiseDecision = Literal["first_wins", "tie", "second_wins"]
PairwiseReason = Literal[
    "higher_value",
    "lower_value",
    "equal_value",
    "both_unavailable",
    "only_first_evaluable",
    "only_second_evaluable",
]
SelectionReason = Literal[
    "highest_total_score",
    "tool_priority_tiebreak",
    "candidate_id_tiebreak",
]


@dataclass(frozen=True)
class TokenCount:
    """记录一个规范化 token 在多重集中的出现次数。"""

    token: str
    count: int


@dataclass(frozen=True)
class ReferenceWord:
    """记录 Table Slot 内一个原生 PDF word 及其来源位置。"""

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
    """保存同组候选共用的原始 PDF 文本参照。"""

    slot_id: str
    source: Literal["pymupdf_page_words_v1"]
    page_number: int
    slot_bbox: BoundingBox
    words: list[ReferenceWord]
    token_counts: list[TokenCount]
    critical_token_counts: list[TokenCount]


@dataclass(frozen=True)
class CandidateToken:
    """记录候选 token 及其所属物理单元格。"""

    cell_id: str | None
    token_index: int
    raw_text: str
    normalized_token: str


@dataclass(frozen=True)
class TextCoverageMetrics:
    """记录候选文本对 Slot 原生文本的双向覆盖率。"""

    status: Literal["evaluated", "not_evaluable"]
    reason_codes: list[str]
    reference_token_count: int
    candidate_token_count: int
    matched_token_count: int
    precision: float | None
    recall: float | None
    f1: float | None
    unmatched_reference_tokens: list[TokenCount]
    unmatched_candidate_tokens: list[TokenCount]


@dataclass(frozen=True)
class CriticalTokenMetrics:
    """记录含数字关键 token 是否保持为完整 token。"""

    status: Literal["evaluated", "not_applicable"]
    reason_codes: list[str]
    reference_token_count: int
    matched_token_count: int
    integrity: float | None
    unmatched_reference_tokens: list[TokenCount]


@dataclass(frozen=True)
class GridShapeMetrics:
    """记录候选行列二元组获得的组内支持。"""

    status: Literal["evaluated"]
    reason_codes: list[str]
    row_count: int | None
    column_count: int | None
    same_shape_candidate_count: int
    group_candidate_count: int
    support: float


@dataclass(frozen=True)
class BlankGridMetrics:
    """记录 span-aware 空白率及相对于组内参照的异常度。"""

    status: Literal["evaluated", "not_evaluable"]
    reason_codes: list[str]
    logical_position_count: int | None
    nonempty_covered_position_count: int | None
    blank_position_count: int | None
    blank_ratio: float | None
    reference_blank_ratio: float | None
    reference_source: BlankReferenceSource | None
    blank_anomaly: float | None
    invalid_nonempty_cell_ids: list[str]


@dataclass(frozen=True)
class CandidateRawMetrics:
    """汇总一个候选的输入 token 和四项原始指标。"""

    tokens: list[CandidateToken]
    text_coverage: TextCoverageMetrics
    critical_tokens: CriticalTokenMetrics
    grid_shape: GridShapeMetrics
    blank_grid: BlankGridMetrics
    warnings: list[str]


@dataclass(frozen=True)
class MetricPairEvaluation:
    """记录同组两个候选在一项指标上的胜负。"""

    metric_name: MetricName
    first_candidate_id: str
    second_candidate_id: str
    first_value: float | None
    second_value: float | None
    decision: PairwiseDecision
    reason: PairwiseReason


@dataclass(frozen=True)
class RelativeMetricScore:
    """汇总一个候选在一项指标上的组内两两战绩。"""

    metric_name: MetricName
    wins: int
    ties: int
    losses: int
    opponent_count: int
    score: float


@dataclass(frozen=True)
class CandidateScoringResult:
    """记录一个候选的原始指标、相对分和总分。"""

    candidate_id: str
    tool: str
    strategy: str | None
    raw_metrics: CandidateRawMetrics
    relative_scores: list[RelativeMetricScore]
    total_score: float


@dataclass(frozen=True)
class GroupScoringResult:
    """记录一个 ready group 的全部评分事实和唯一 winner。"""

    group_id: str
    slot_id: str
    text_reference: SlotTextReference
    candidates: list[CandidateScoringResult]
    pair_evaluations: list[MetricPairEvaluation]
    highest_total_score: float
    tied_top_candidate_ids: list[str]
    selected_candidate_id: str
    selection_reason: SelectionReason


@dataclass(frozen=True)
class ScoringReport:
    """汇总一份 PDF 的全部 ready group 评分结果。"""

    format_version: Literal["table_candidate_scoring_v1"]
    groups: list[GroupScoringResult]
    source_pdf: str
    source_grouping_report: str
    warnings: list[str]




