# V2.0 表格领域模型契约

本文是[当前架构第 3.2 节](../../architecture.md)的表格字段子文档，只定义字段和不变量；计算规则以[表格提取与选优需求](../../requirements/v2.0/table-extraction-selection.md)为准。所有索引均为零基，表格行列区间均为半开区间。

## 1. 提取事实

```python
ToolName = Literal["pymupdf", "camelot", "docling", "unstructured"]
TableStrategy = Literal[
    "lines", "lines_strict", "text",
    "lattice", "stream", "network", "hybrid",
    "accurate", "hi_res",
]
StrategyStatus = Literal[
    "not_started", "completed_no_tables", "completed_with_tables",
    "completed_with_page_failures", "failed",
]
PageExecutionStatus = Literal["completed", "failed"]
SpanSource = Literal["native", "geometry_inferred", "edge_inferred", "unavailable"]
CellRole = Literal["column_header", "row_header", "row_section", "body", "unknown"]

合法工具—策略组合固定为：pymupdf→lines/lines_strict/text，camelot→lattice/stream/network/hybrid，docling→accurate，unstructured→hi_res。模型构造时拒绝其他组合。

class PageExecution:
    page_number: int
    status: PageExecutionStatus
    candidate_ids: tuple[str, ...]
    error_type: str | None
    error_message: str | None

class StrategyExecution:
    tool: ToolName
    strategy: TableStrategy
    status: StrategyStatus
    pages: tuple[PageExecution, ...]
    candidate_ids: tuple[str, ...]
    error_type: str | None
    error_message: str | None

class TableRegion:
    page_number: int | None
    page_width: float | None
    page_height: float | None
    bbox: BoundingBox | None
    source_ref: str
    coordinate_transform: CoordinateTransform | None
    unavailable_reason: Literal[
        "missing_bbox", "coordinate_conversion_failed",
        "invalid_bbox", "invalid_page_geometry",
    ] | None

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

class GridPosition:
    row_index: int
    column_index: int
    reason: Literal["missing_physical_cell"]

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

class ExtractionReport:
    schema_version: Literal["table_extraction_v1"]
    source_pdf: str
    file_hash: str
    executions: tuple[StrategyExecution, ...]
    candidates: tuple[TableCandidate, ...]
    warnings: tuple[ProcessingWarning, ...]
```

PageExecution completed 时 error 字段均为空；failed 时 candidate_ids 为空且 error_type/message 非空。每个已启动策略的 pages 按 PDF 页码严格递增且每页恰有一项；初始化失败的策略没有 pages。

StrategyExecution 的组合不变量为：

| status | pages | candidate_ids | strategy error |
| --- | --- | --- | --- |
| not_started | 空 | 空 | 非空；工具或策略初始化未执行成功 |
| failed | 可为空；若非空则全部 failed | 空 | 非空；没有成功执行页 |
| completed_no_tables | 全部 completed 且均零候选 | 空 | 空 |
| completed_with_tables | 全部 completed 且至少一个候选 | 成功页候选连接 | 空 |
| completed_with_page_failures | completed/failed 均至少一页；至少一个 completed 页 | 成功页候选连接，可为空 | 空；页错只在 PageExecution |

StrategyExecution.candidate_ids 必须等于 completed 页面候选按页和工具原序连接；ExtractionReport.candidates 的 ID 集合必须与全部 execution candidate_ids 的并集相等且无重复。

TableRegion 至少有非空 source_ref。能唯一定位到一页时 page_number/page_width/page_height 必须同时非空且为正；bbox 非空时这三者也必须非空且 unavailable_reason 为空。coordinate_transform 仅在源坐标经过转换时非空。来源区域无法形成公共 bbox 时 unavailable_reason 保存最先发生的原始事实：源未提供框、坐标转换依据不足、转换后框非法或页几何非法；准入层据此映射 CandidateView 原因，不根据工具名猜测。一个候选跨页由多个各自可定位的 region 表达，不抹掉各页事实；单个 region 自身无法确定页面时 page_number/page_width/page_height/bbox 全为空并记录 invalid_page_geometry。

TableCell 的四个 offset、row_span、col_span 是一组：可定位时全部非空，两个区间均为合法非空半开区间，span 分别等于区间长度；无法定位时六项全部为空且 span_source=unavailable。bbox 可独立为空，但非空时必须落在某个已定位 region。text=null 只表示工具未提供文字；空字符串表示工具明确提供空白格。

TableCandidate 的逻辑网格与可选几何边界分开表达。row_count/column_count 必须同时为正或同时为空；有逻辑网格时 cells、uncovered_grid_positions 的坐标不得越界或相互矛盾。x/y boundaries 必须同时存在或同时为空；存在时分别有 column_count+1/row_count+1 个有限、严格递增值。Docling 跨页表等仍有可靠逻辑 offsets、但无法把 cell 唯一归页的输入保留逻辑网格并令两组 boundaries 为空，不得用逻辑序号冒充页面坐标。无合法逻辑网格时四项全部为空，所有 cell 的位置组也必须为空。unplaced_text 仅在存在无法映射到物理格的非空原文时非空；否则为 null。已定位 cell 的区间、span 和 bbox 必须满足需求；无法定位时不得部分伪造。

## 2. 准入与分组

```python
SlotStatus = Literal["eligible", "deferred"]
CandidateStatus = Literal["comparable", "deferred"]
EvaluationDecision = Literal["accepted", "rejected"]
AdmissionStatus = Literal["completed", "deferred"]
AdmissionDecision = Literal["admitted", "rejected"]
GroupStatus = Literal["ready_for_scoring", "unresolved"]
SlotDeferredReason = Literal[
    "cross_page_slot", "missing_slot_provenance", "missing_slot_bbox",
    "slot_coordinate_conversion_failed", "invalid_slot_bbox",
    "invalid_slot_page_geometry",
]
CandidateDeferredReason = Literal[
    "cross_page_candidate", "missing_candidate_region", "missing_candidate_bbox",
    "candidate_coordinate_conversion_failed", "invalid_candidate_bbox",
    "invalid_candidate_page_geometry",
]
AdmissionDeferredReason = CandidateDeferredReason | Literal["slot_source_page_failed"]
SlotMatchReasonCode = Literal[
    "bidirectional_coverage_passed", "candidate_coverage_below_minimum",
    "slot_coverage_below_minimum",
]
CandidateAdmissionReasonCode = Literal[
    "cross_page_candidate", "missing_candidate_region", "missing_candidate_bbox",
    "candidate_coordinate_conversion_failed", "invalid_candidate_bbox",
    "invalid_candidate_page_geometry", "slot_source_page_failed",
    "no_eligible_table_slot_on_page",
    "no_table_slot_passed_bidirectional_coverage",
    "matched_single_table_slot",
    "multiple_table_slots_passed_bidirectional_coverage",
]
GroupUnresolvedReason = SlotDeferredReason | Literal["no_admitted_candidate"]

class TableSlot:
    slot_id: str
    docling_table_ref: str
    page_number: int | None
    page_width: float | None
    page_height: float | None
    bbox: BoundingBox | None
    status: SlotStatus
    deferred_reason: SlotDeferredReason | None
    source_refs: tuple[str, ...]

class CandidateView:
    candidate_id: str
    tool: ToolName
    strategy: TableStrategy
    page_number: int | None
    page_width: float | None
    page_height: float | None
    bbox: BoundingBox | None
    processing_status: CandidateStatus
    deferred_reason: CandidateDeferredReason | None

class SlotMatchMetrics:
    intersection_area: float
    union_area: float
    iou: float
    candidate_coverage: float
    slot_coverage: float

class SlotMatchEvaluation:
    evaluation_id: str
    candidate_id: str
    slot_id: str
    decision: EvaluationDecision
    reason_codes: tuple[SlotMatchReasonCode, ...]
    metrics: SlotMatchMetrics

class CandidateAdmissionResult:
    candidate_id: str
    processing_status: AdmissionStatus
    deferred_reason: AdmissionDeferredReason | None
    admission_decision: AdmissionDecision | None
    matched_slot_id: str | None
    evaluated_slot_ids: tuple[str, ...]
    accepted_slot_ids: tuple[str, ...]
    reason_codes: tuple[CandidateAdmissionReasonCode, ...]

class TableGroup:
    group_id: str
    slot_id: str
    docling_table_ref: str
    page_number: int | None
    slot_bbox: BoundingBox | None
    status: GroupStatus
    unresolved_reason: GroupUnresolvedReason | None
    member_candidate_ids: tuple[str, ...]

class GroupingReport:
    schema_version: Literal["table_candidate_selection_v2"]
    table_slots: tuple[TableSlot, ...]
    candidate_views: tuple[CandidateView, ...]
    slot_match_evaluations: tuple[SlotMatchEvaluation, ...]
    candidate_admission_results: tuple[CandidateAdmissionResult, ...]
    groups: tuple[TableGroup, ...]
    warnings: tuple[ProcessingWarning, ...]
```

deferred slot/candidate 必须有 deferred_reason；非 deferred 时为 None。eligible slot 与 comparable candidate 的 page_number/page_width/page_height/bbox 必须全部非空，deferred 对象只保留可直接取得的事实，不补齐缺项。SlotMatchEvaluation 只为同页 eligible/comparable 对生成；五个面积值有限且非负，union_area>0，三种比例位于 [0,1]。accepted 当且仅当两个 coverage 均达到阈值，此时 reason_codes 仅为 bidirectional_coverage_passed；rejected 保存一个或两个未达阈值原因。

AdmissionStatus=deferred 时 decision/matched_slot_id 均为 None，evaluated_slot_ids/accepted_slot_ids 均为空。completed+rejected 时 matched_slot_id=None；accepted_slot_ids 可以为零或多个，分别对应无匹配或多匹配，reason_codes 必须表达该结论。completed+admitted 时 matched_slot_id 非空、accepted_slot_ids 恰好一个且与之相等。evaluated_slot_ids 按 slot 顺序且包含 accepted_slot_ids。每个 candidate 恰有一个 admission result，每个 slot 恰有一组；ready_for_scoring 组至少一个 member，unresolved 组成员为空且必须有 unresolved_reason。

原因码全集直接采用表格需求第 3—4 节，禁止适配器自创同义值。

## 3. 评分证据

```python
MetricName = Literal["text_f1", "critical_token_integrity", "shape_support", "blank_anomaly"]
MetricStatus = Literal["evaluated", "not_evaluable", "not_applicable"]
PairDecision = Literal["first_wins", "tie", "second_wins"]
PairReason = Literal[
    "higher_value", "lower_value", "equal_value", "both_unavailable",
    "only_first_evaluable", "only_second_evaluable",
]
SelectionReason = Literal["highest_total_score", "tool_priority_tiebreak", "candidate_id_tiebreak"]
TextCoverageReason = Literal["empty_reference", "empty_candidate", "unreadable_candidate"]
CriticalTokenReason = Literal["no_critical_reference_tokens"]
GridShapeReason = Literal["invalid_or_missing_dimensions"]
BlankGridReason = Literal[
    "invalid_or_missing_dimensions", "invalid_cell_interval",
    "overlapping_cells", "grid_not_evaluable",
]

class TokenCount:
    token: str
    count: int

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

class SlotTextReference:
    slot_id: str
    source: Literal["pymupdf_page_words_v1"]
    page_number: int
    slot_bbox: BoundingBox
    words: tuple[ReferenceWord, ...]
    token_counts: tuple[TokenCount, ...]
    critical_token_counts: tuple[TokenCount, ...]

class CandidateRawMetrics:
    text_coverage: TextCoverageMetrics
    critical_tokens: CriticalTokenMetrics
    grid_shape: GridShapeMetrics
    blank_grid: BlankGridMetrics
    warnings: tuple[ProcessingWarning, ...]

class TextCoverageMetrics:
    status: Literal["evaluated", "not_evaluable"]
    reason_codes: tuple[TextCoverageReason, ...]
    reference_token_count: int
    candidate_token_count: int
    matched_token_count: int
    precision: float | None
    recall: float | None
    f1: float | None
    unmatched_reference_tokens: tuple[TokenCount, ...]
    unmatched_candidate_tokens: tuple[TokenCount, ...]

class CriticalTokenMetrics:
    status: Literal["evaluated", "not_applicable"]
    reason_codes: tuple[CriticalTokenReason, ...]
    reference_token_count: int
    matched_token_count: int
    integrity: float | None
    unmatched_reference_tokens: tuple[TokenCount, ...]

class GridShapeMetrics:
    status: Literal["evaluated"]
    reason_codes: tuple[GridShapeReason, ...]
    row_count: int | None
    column_count: int | None
    same_shape_candidate_count: int
    group_candidate_count: int
    support: float

class BlankGridMetrics:
    status: Literal["evaluated", "not_evaluable"]
    reason_codes: tuple[BlankGridReason, ...]
    logical_position_count: int | None
    nonempty_covered_position_count: int | None
    blank_position_count: int | None
    blank_ratio: float | None
    reference_blank_ratio: float | None
    reference_source: Literal["unique_mode", "minimum"] | None
    blank_anomaly: float | None
    invalid_nonempty_cell_ids: tuple[str, ...]

class RelativeMetricScore:
    metric_name: MetricName
    wins: int
    ties: int
    losses: int
    opponent_count: int
    score: float

class CandidateScoringResult:
    candidate_id: str
    tool: ToolName
    strategy: TableStrategy
    raw_metrics: CandidateRawMetrics
    relative_scores: tuple[RelativeMetricScore, ...]
    total_score: float

class MetricPairEvaluation:
    metric_name: MetricName
    first_candidate_id: str
    second_candidate_id: str
    first_value: float | None
    second_value: float | None
    decision: PairDecision
    reason: PairReason

class GroupScoringResult:
    group_id: str
    slot_id: str
    text_reference: SlotTextReference
    candidates: tuple[CandidateScoringResult, ...]
    pair_evaluations: tuple[MetricPairEvaluation, ...]
    highest_total_score: float
    tied_top_candidate_ids: tuple[str, ...]
    selected_candidate_id: str
    selection_reason: SelectionReason

class ScoringReport:
    schema_version: Literal["table_candidate_scoring_v1"]
    source_pdf: str
    groups: tuple[GroupScoringResult, ...]
    warnings: tuple[ProcessingWarning, ...]
```

上述指标必须保存公式的分子、分母、最终值和未匹配/异常证据，不能只保存结果小数，并满足：

- TokenCount.count 为正，同一数组 token 唯一且按规范化 token 排序；matched 不超过对应 reference/candidate count。
- TextCoverageMetrics 在 reference_token_count>0 时为 evaluated，precision/recall/f1 均为 [0,1] 有限值；候选为空或不可读时保存对应原因且三个值按需求为 0。仅 reference_token_count=0 时为 not_evaluable，原因仅为 empty_reference，三个结果全为空；计数字段仍保存实际可得值。
- CriticalTokenMetrics evaluated 时 reason_codes 为空、reference_token_count>0、integrity 为 [0,1]；not_applicable 时原因仅为 no_critical_reference_tokens、reference/matched 均为 0、integrity=null。
- GridShapeMetrics 始终 evaluated；合法正整数维度时 reason_codes 为空，same_shape_candidate_count 按相同维度统计，support 等于 same_shape_candidate_count/group_candidate_count；缺失、非整数或非正维度时原因仅为 invalid_or_missing_dimensions、same_shape_candidate_count=0、support=0，并保留工具原始可表示的 row/column 值。group_candidate_count 为正，same_shape_candidate_count 位于 [0,group_candidate_count]。
- BlankGridMetrics evaluated 时所有计数、两个 ratio 和 anomaly 非空且范围合法，reason_codes 为空；not_evaluable 时 blank_ratio/reference_blank_ratio/reference_source/anomaly 全为空且至少一个原因。reference_source 仅在 evaluated 时非空。
- RelativeMetricScore 的 wins+ties+losses=opponent_count，opponent_count=组成员数-1；score 使用需求公式且位于 [0,1]。每个 candidate 恰有四个不同 metric_name。
- MetricPairEvaluation 对每个无序候选对、每个指标恰有一项，first/second 按 candidate_id 升序。value 可空条件由相应指标 status 唯一决定；decision/reason 必须与需求中的比较规则一致。
- SlotTextReference 即使 words 为空也必须存在；token_counts 和 critical_token_counts 从 words 确定性聚合。空参照通过 metric status/reason 表达，不删除整个 GroupScoringResult。

每个 ready group 恰有一个 GroupScoringResult；其 candidates 与 group members 集合相同，selected_candidate_id 必须属于 candidates，tied_top_candidate_ids 非空且包含 selected，highest_total_score 等于 top 值。unresolved group 不出现在 ScoringReport.groups。所有浮点数必须有限；comparison_epsilon 只用于比较，不改写保存的原始值。

## 4. 端口边界

Extractor 只生产 ExtractionReport；AdmissionAndGrouping 只消费 LayoutDocument.table_slots 与 ExtractionReport；Scorer 只消费 GroupingReport、候选和 PDF words；Assembler 通过 ID 查找，不读取诊断 HTML 或重新计算决策。

所有报告冻结后不可原位修改。application 需要派生新结果时构造新模型，确保审核产物与实际计算引用同一对象。
