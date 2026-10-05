# 表格候选、选优与内容准备契约

状态：**架构已审核，通过规格级编码就绪复审**。业务规则、公式与阈值唯一见[表格提取选优](../requirements/table-selection.md)、[表头](../requirements/table-header.md)、[文本化](../requirements/table-text.md)；本文只定义技术数据与插件边界。

## 1. 内部接口与依赖

`TableExtractor` 是 DocumentProcessor 的无条件选接 multi 插槽，每个插件读取同一 SourceDocument，输出统一 `TableExtractionReport`。`TableSelector` 在至少一个提取器接入时条件必接 one，输入 PrimaryDocument 中的 slot/原生结构、按绑定顺序收集的报告及 PdfEvidenceReader 的原文/几何事实，输出按 slot 唯一的 `ContentResolution` 与完整决策证据。它内部依次执行准入、分组、评分、选优，不把其中每个纯规则函数强行插件化。PdfEvidenceReader 是 Selector 的支撑依赖，不是入库主图中的独立处理阶段。表头判定和文本化属于 DocumentAssembler 的条件内容准备子链，各必接 one，顺序为 HeaderDetector → TableSerializer；该子链按 slot 交付 `PreparedTableContent`，再由必接的 DocumentComposer 与主文档合成 ParsedDocument。交接模型字段与不变量唯一见[文档处理契约](document-processing.md#4-组装和表格内容准备)。

四个首批提取插件以工具为边界：PyMuPDF 的 `lines|lines_strict|text`，Camelot 的 `lattice|stream|network|hybrid`，Docling 的 `accurate`，Unstructured 的 `hi_res`。策略是对应插件的内部执行配置，合计九策略；不能因为工具先返回候选而提前停止其余策略。所有候选都转换为同一公共结构，工具原生对象和 SDK 枚举不流向 Selector。Docling 插件与主解析插件共享同次转换资源。

`PdfEvidenceReader.read_page_words(source: SourceDocument, page_number: int) -> tuple[PdfPageWord, ...]` 是 Selector 唯一的 PDF 原文支撑端口；一基页号越界、PDF 不可读或来源 hash 不符时失败，不返回伪造空页。真实空页返回空元组。PyMuPDF `get_text("words", sort=False)` 的每项至少含八个值；少于八项跳过并记录处理诊断，超出八项的尾部 SDK 字段不进入公共事实。该接口只转换原始词、坐标和顺序索引；按 slot bbox 中心点筛选、词法归一化、关键数字判断和 `SlotTextReference` 构造均由 Selector 负责，不能由 Reader 偷做评分判断。其返回不是主链的另一份处理阶段成果，也不产生可审计快照。

```python
@dataclass(frozen=True)
class PdfPageWord:
    page_number: int
    bbox: BoundingBox
    raw_text: str
    block_index: int
    line_index: int
    word_index: int
```

页码从 1 开始；三个索引非负，bbox 坐标有限且位于该物理页坐标系；`raw_text` 可为空或仅空白，以保留原始事实。Reader 按 SDK 返回次序交付；Selector 严格按历史 `build_slot_text_reference` 的中心点包含、NFKC/数字撇号/casefold/strip、空 token 跳过、block/line/word/bbox 稳定排序规则构造下文 `ReferenceWord`，不把新排序或文本清洗隐含在 Reader 中。

## 2. 提取事实模型

本节只记录四工具适配后的原始候选；不存在 winner、评分或表头结论。先看 `TableExtractionReport`，再下钻策略执行、区域、单元格。

每个已接入的 TableExtractor 各创建一份报告；其中的候选是适配器转换后的事实，不含准入/评分结论。字段全集按历史文档的逐模型代码块展示：

```python
@dataclass(frozen=True)
class TableExtractionReport:
    source_pdf: str
    file_hash: str
    executions: tuple[StrategyExecution, ...]
    candidates: tuple[TableCandidate, ...]
    warnings: tuple[ProcessingWarning, ...]

@dataclass(frozen=True)
class StrategyExecution:
    tool: ToolName
    strategy: TableStrategy
    status: StrategyStatus
    pages: tuple[PageExecution, ...]
    candidate_ids: tuple[str, ...]
    error_type: str | None
    error_message: str | None

@dataclass(frozen=True)
class PageExecution:
    page_number: int
    status: Literal["completed", "failed"]
    candidate_ids: tuple[str, ...]
    error_type: str | None
    error_message: str | None

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
```

报告与源身份相同，executions 按固定工具/策略顺序，候选 ID 唯一。已定位到唯一成功页的候选 ID 必须出现在该页及对应策略的 `candidate_ids` 中；原工具确实产出但无法唯一归页的候选仍保留在报告 `candidates` 和策略 `candidate_ids`，标记几何 deferred，不伪造页号，也不塞入某个 PageExecution。未装配插件不产生报告。StrategyStatus 为 `not_started|completed_no_tables|completed_with_tables|completed_with_page_failures|failed`；页码递增、一基，完成态无整体错误，失败态不伪称零表。失败页无候选且有诊断，完成页诊断为空。CoordinateTransform 仅记录原工具坐标转公共坐标所用的原点、单位、两轴比例与转换前页高；三项数值必须是有限正数，不得由缺失页几何推造。TableRegion 的页码和几何同时有或同时无；缺 bbox 要有封闭原因。行列维度、两轴边界分别成对有或无，边界严格递增；有网格时 cell 与 uncovered 对位置互斥且覆盖完整。TableCell 的六个网格索引同时有或无，区间半开、跨度一致；未知定位标 `unavailable` 而不填零。null text 与空字符串不同；GridPosition 索引非负且在候选网格内。

`StrategyExecution` 的五态组合逐项保留已验证事实。逐页策略的 `candidate_ids` 恰等于所有 completed 页的 ID 顺序拼接；Docling 文档级策略按原生表顺序列出全部候选 ID，其中无法唯一归页者不进入 PageExecution。两类顺序都不得按完成时刻重排或悄悄去重；`failed` 页绝无候选。下表中的“整体诊断”指 `error_type` 与 `error_message` 同时非空，其余状态两者同时为空。

| status | pages / candidate_ids | 整体诊断 |
| --- | --- | --- |
| `not_started` | pages 与 candidate_ids 均空 | 必有；表示策略没有进入页面执行 |
| `failed` | 不存在 completed 页，candidate_ids 为空；可保留已失败页 | 必有；不等同正常零表 |
| `completed_no_tables` | 至少一页 completed、无 failed 页、candidate_ids 为空 | 必空 |
| `completed_with_tables` | 至少一页 completed、无 failed 页、candidate_ids 非空 | 必空 |
| `completed_with_page_failures` | 至少一页 completed 且至少一页 failed；candidate_ids 可空，只含已成功执行取得的候选 | 必空；失败细节在 PageExecution |

`error_type/error_message` 只保存适配器诊断：成功态两者均空，page/strategy 失败态两者均为非空字符串；任何控制流只能读取封闭 status 和 warning/error code，不得匹配这两个字符串。候选正文为空、策略成功但没有候选均不是异常，分别由候选字段和 `completed_no_tables` 表达。

`ToolName`、`TableStrategy`、`CellRole`、`SpanSource`、`RegionUnavailableReason` 均为封闭枚举；合法工具—策略对以上述九项为全集，不能以开放字符串接受新值。

取值全集：`ToolName=pymupdf|camelot|docling|unstructured`；`CellRole=column_header|row_header|row_section|body|unknown`；`SpanSource=native|geometry_inferred|edge_inferred|unavailable`；`RegionUnavailableReason=missing_bbox|coordinate_conversion_failed|invalid_bbox|invalid_page_geometry`。`SlotDeferredReason=cross_page_slot|missing_slot_provenance|missing_slot_bbox|slot_coordinate_conversion_failed|invalid_slot_bbox|invalid_slot_page_geometry`；`CandidateDeferredReason=cross_page_candidate|missing_candidate_region|missing_candidate_bbox|candidate_coordinate_conversion_failed|invalid_candidate_bbox|invalid_candidate_page_geometry`；AdmissionDeferredReason 再允许 `slot_source_page_failed`；GroupUnresolvedReason 在 SlotDeferredReason 外只允许 `no_admitted_candidate`。这些原因分别归属 slot、candidate、admission、group，不能跨模型复用近义字符串。

`MatchReason` 封闭为 `bidirectional_coverage_passed|candidate_coverage_below_minimum|slot_coverage_below_minimum`；accepted 恰有 passed，rejected 至少有一个 below 且不得有 passed。`AdmissionReason` 封闭为 `no_eligible_table_slot_on_page|no_table_slot_passed_bidirectional_coverage|matched_single_table_slot|multiple_table_slots_passed_bidirectional_coverage`；deferred 无 AdmissionReason，completed 恰有一个并与 admitted/rejected 对应。四评分指标名封闭为 `text_f1|critical_token_integrity|shape_support|blank_anomaly`，前 3 个值越大越好，blank anomaly 越小越好；相对同分使用需求的 `1e-12` 容差与工具优先序。`MetricPairEvaluation.reason` 封闭为 `higher_value|lower_value|equal_value|both_unavailable|only_first_evaluable|only_second_evaluable`，其 decision 封闭为 `first_wins|tie|second_wins`。

## 3. slot、决策与最终结构

本节依次展示主解析 slot → Selector 的定位/匹配/准入/评分证据 → `ContentResolution` → 被采用的结构、表头与文本化。字段代码块是唯一模型定义，公式仍只在需求中。

主解析适配器从真实表格占位创建 TableSlot；Selector 后续只读这些事实并生成独立判断：

```python
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
```

eligible 时四个定位字段齐全且 deferred_reason 为空；deferred 时必有原因，缺失事实保留 null，不猜值。CandidateView 从 TableCandidate 派生、不修改候选；SlotMatchEvaluation 保存匹配证据，CandidateAdmissionResult 保存准入结论，TableGroup 按 slot 汇集。最终每个 ready 组恰一个 winner；判断、公式与阈值执行已批准需求，不让适配器暗中准入。

```python
@dataclass(frozen=True)
class ContentResolution:
    slot_id: str
    origin: Literal["selected_winner", "docling_native_fallback"]
    selected_candidate_id: str | None
    selection_ref: str | None
    decision_ref: str
```

Selector 创建，每 slot 唯一；winner 必有 selected_candidate_id/selection_ref，且候选属于该组。fallback 的 selection_ref 为空，selected_candidate_id 仅在原生 Docling 提取候选确实存在时填写，否则为空；最终结构从 PrimaryDocument 同 slot 的 NativeTableFact 读取。decision_ref 指向不可变决策证据，不等于候选 ID。Assembler 校验每占位恰一 resolution；其内容准备阶段按选中 ID 在同次 TableExtractionReport 中查找候选并生成统一 StructuredTable，重复、缺失或跨 slot 引用均失败，不重新评分。未接提取分支时不产生 resolution，直接采用 NativeTableFact，不因可选提取器失败丢失原生表。结构与表头/文本化的同一性由[PreparedTableContent](document-processing.md#4-组装和表格内容准备)约束。

决策证据按 **候选定位 → slot 匹配 → 准入/分组 → 评分 → winner** 创建；所有引用按 ID 校验，不复制或修改提取事实。

```python
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
```

comparable 候选四个定位字段齐全且 deferred_reason 空；deferred 必有原因。交并面积非负、union 正，比例在 `[0,1]`；每候选/slot 至多一个匹配判断。deferred 准入无决策、匹配、已评估 ID 与原因，completed 则有明确决策；admitted 恰一匹配 slot。ready 组有成员无原因，unresolved 无成员有原因；每个 admitted 候选仅进入唯一 matched slot 的组。GroupingReport 中每 candidate 恰一准入结果、每 slot 恰一 group，引用不得跨文档。

```python
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
```

TokenCount.count 与页码正，ReferenceWord 三种 index 非负；参照原文只来自指定页 PyMuPDF，不从候选正文反推。分数有限，relative_scores 恰覆盖四指标各一次；每个 ready 组候选非空、唯一，winner 与 tied 集合在 candidates 内。unresolved 组不伪造零分 winner，具体公式仍以[选优需求](../requirements/table-selection.md)为唯一权威。

评分模型完整字段与空值如下；指标的具体计算公式、比较 epsilon、零分母规则只由已批准[选优需求](../requirements/table-selection.md)定义，不在此复制第二套公式。`MetricReason` 封闭为 `empty_reference|empty_candidate|no_critical_reference_tokens|invalid_or_missing_dimensions|invalid_cell_interval|overlapping_cells`，所属指标见表；正常可计算且无特殊事实时 reason_codes 为空。

```python
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
```

所有计数非负、有限比例在 `[0,1]`。TextCoverage 只有空 reference 为 not_evaluable，三个比率同时空；空 candidate 仍可 evaluated。无关键参照 token 时 CriticalToken 为 not_applicable、integrity 空。GridShape 缺维度时两维均空、同形数 0、原因 `invalid_or_missing_dimensions`、support 0。BlankGrid not_evaluable 时数量、比例与参照源全空；evaluated 时逻辑数量和 blank_ratio 必有，应用组参照后其余比率/来源必有。RelativeMetricScore 战绩和等于 opponent_count；MetricPairEvaluation 两候选不同，reason 与两值的可空性和决胜一致。

`MetricName` 封闭为 `text_f1|critical_token_integrity|shape_support|blank_anomaly`。`reason_codes` 只能取相应指标的子集：文本为 `empty_reference|empty_candidate`；关键 token 为 `no_critical_reference_tokens`；形状为 `invalid_or_missing_dimensions`；空白为 `invalid_or_missing_dimensions|invalid_cell_interval|overlapping_cells`。不能把 SDK 错误文本或任意字符串写入原因码。

最终 `StructuredTable` 保留被采用的结构事实，不复用 SDK 对象。HeaderDecision 记录判定及完整证据；SerializedTable 只做文本交付。表头和文本化不修改候选事实，也不重新选 winner。

表头证据模型字段全集如下，索引均为零基非负整数，引用 cell ID 必须属于同一 StructuredTable；空序列表示已检查但无相应证据，不代替未执行。`HeaderInputIssueCode` 封闭为 `invalid_dimensions|duplicate_cell_id|unavailable_cell_position|invalid_cell_interval|span_mismatch|overlapping_cells|unplaced_text`；`HeaderStructuralIssueCode` 封闭为 `boundary_crosses_cell|missing_header_position|internal_blank_row|internal_full_width_row|crossing_column_intervals|no_nonempty_path`。

```python
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
    row_indices: tuple[int, ...]
    column_indices: tuple[int, ...]
    cell_ids: tuple[str, ...]

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

@dataclass(frozen=True)
class SerializedTableLine:
    line_id: str
    kind: Literal["header", "data", "merged", "unplaced_text"]
    text: str
    source_rows: tuple[int, ...]
    source_cell_ids: tuple[str, ...]

@dataclass(frozen=True)
class SerializedTable:
    text: str
    lines: tuple[SerializedTableLine, ...]
    rule_version: Literal["table_text_v1"]

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
```

所有行列索引非负，cell ID 引用同一 StructuredTable。HeaderPath 两数组非空、等长且自上而下；无最低表头 cell 时 lowest_header_cell_id 空，无稳定正文类型时 stable_body_type 空且 supports_transition=false。结构拒绝时不产生采样证据，非结构拒绝时 structural_issues 为空。identified 当且仅当唯一受支持候选、范围和路径齐全；undetermined 的两范围及 paths 均空。SerializedTable.text 是各 line.text 以换行符原样连接，line_id 唯一，来源行/cell 可回到结构表。StructuredTable 行列同时有或无，cell 与 uncovered 对网格位置互斥覆盖，来源来自采用候选或原生表事实。

## 4. 单位、转换和失败

公共 bbox 使用左上原点 pt。PyMuPDF 直接映射页面 pt；Camelot 的 PDF 底部原点须用真实页高翻转；Docling 的坐标依据其导出 provenance 与页面尺寸转换；Unstructured 仅在已知 PixelSpace/layout 宽高时分别按 x/y 比例缩放。任何缺单位、页高或转换依据时记录缺失原因，不假设固定 DPI；cell 坐标不得从整表框无依据地平分。PyMuPDF 退化区间、Camelot 非矩形分量、Docling 缺 cell-to-page、Unstructured HTML/span 不合法的处理，均按表格需求保留原始文字及失败证据，不伪造结构。

单策略页失败保留该页失败和其余页成功事实；整文主解析失败仍阻断。Selector 的正常 deferred/rejected/unresolved 不是异常，Assembler 按原生回退。候选决策证据缺失、winner 不在组内、必要原生结构缺失或同一 slot 多结果则阻断该文档，不交付部分 ParsedDocument。

## 5. 实现一致性门

字段和封闭机器值须在实现时按本契约严格映射，不允许从旧类的开放 `str` 推断新枚举。编码前按[技术验证](../technical-validation.md)核对九策略实际参数、四工具单位/坐标/schema 与公共映射，并证明同一 Docling 转换在主解析和提取器间共享且不改变候选 ID、raw、来源、正文；不通过时先修订适配设计。
