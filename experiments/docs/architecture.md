# 多工具表格恢复与选优管道架构设计

本文将[需求](requirements.md)落实为数据模型、算法、运行与持久化契约。工具输出事实的证据见[工具调研](tool-findings.md)。实现状态与保留限制见[当前限制](limitations.md)，实测证据见[重构验收记录](history/refactor-validation.md)。

阅读导航：[总体架构](#1-总体架构)、[全部数据模型](#2-全链路数据模型)、[四工具适配](#3-表格提取与结构恢复设计)、[准入](#4-候选准入设计)、[分组](#5-候选分组设计)、[评分](#6-评分与选优设计)、[编排接口](#7-全链路编排与运行接口)、[完整产物](#8-持久化与完整产物结构)、[模块职责](#9-代码组织与模块职责)、[验证约束](#10-验证与实施约束)。

## 1. 总体架构

```text
CLI 参数/补齐确认
  → 工具 runner（第三方调用与运行事实）
  → raw 快照 / normalizer（统一 TableCandidate）
  → 工具 exporter（raw、normalized、manifest）
  → slot loader + candidate loader
  → 几何可处理性 → 双向覆盖准入 → 按 slot 分组
  → 分组不变量 → GroupingReport / manifest / group HTML
  → scoring loader（只装入 ready member）
  → 原 PDF word 参照 → 四项原始指标
  → 两两胜平负 → 等权相对分 → winner
  → 评分不变量 → ScoringReport / manifest / score HTML
```

原始事实、规范化结构、几何准入、质量排名、持久化展示各有独立职责。candidate-slot 准入评价回答“是否属于该卡位”；评分 pair 回答“同组两个候选哪个指标更好”。两者不共用状态或模型。

阶段契约为：runner 提供原始事实和执行结果，normalizer 提供完整 TableCandidate；准入使用几何视图，不消费工具私有分；分组只消费 slot 与准入结论；评分重新装入完整候选，不改变成员；exporter 只序列化与显示。Docling 一次正常完整转换同时提供文档布局与自身表格候选，避免另建互不一致的文档来源。

## 2. 全链路数据模型

### 2.1 几何与数据层次

| 层 | 内容 | 不能据此推导 |
| --- | --- | --- |
| raw | 工具返回的公开事实、原始 bbox/text/span/metadata | 不能把工具私有 confidence 当跨工具质量真值 |
| normalized | 公共 table/cell、转换后的区域与可追踪引用 | 不能伪造 cell bbox、header 或源文件没有给出的内容 |
| 派生视图 | CSV 二维文本、HTML 跨度展示、可选布局预览 | 不能反向改变 normalized 或评分结果 |

工具原生 span、根据原子边推断的 span、根据几何推断的 span 只是不同证据来源，不直接加分。Camelot 的 cell 连通恢复仍需要并查集；被废止的是不同候选之间的连通分量分组，两者不可混淆。

`PageGeometry` 包含 page_number、width、height、rotation、has_crop_offset；由原 PDF 读取，最后一项表示 CropBox 与 MediaBox 不一致。当前仅确认无旋转且裁切框与媒体框一致页面的映射；其余页面保留原始事实，规范化 bbox 为空，候选与 slot 均延后，不能宣称已支持通用仿射变换。公共 bbox、区域和转换字段与候选共同列在 §2.3，不能用私有工具坐标直接代替。


### 2.2 按页执行事实与策略汇总

用户确认的行为是：页失败后继续该策略的下一页；成功页的候选继续准入和评分。以下模型落实这一行为，不能用旧版整策略 success/failed 门槛过滤全部候选。

```text
PageExtractionResult
  page_number: int                 # 原 PDF 一基页码
  status: success | failed
  candidate_ids: list[str]
  error: str | None

StrategyExecution
  tool: pymupdf | camelot | docling | unstructured
  strategy: str
  requested_page_numbers: list[int]
  startup_error: str | None
  page_results: list[PageExtractionResult]
  run_metadata: dict              # 实际版本、有效配置、耗时
```

| 所属字段 | 值/条件 | 下游影响 |
| --- | --- | --- |
| PageExtractionResult.status | success：该页提取与规范化正常完成；error=null；候选可以为零 | 成功页候选正常使用；零表是已执行的结果 |
| PageExtractionResult.status | failed：该页未完成；error 必填；candidate_ids=[] | 保存 raw/错误诊断，继续下一页；不发布该页的半成品候选 |
| StrategyExecution.startup_error | 非空：依赖初始化或打开文档失败，尚未进入逐页执行 | page_results=[]；请求页属于未执行，不能伪造逐页失败 |
| StrategyExecution.startup_error | null：初始化成功 | 每个请求页必须恰有一个 page result，某页失败不终止遍历 |

汇总直接从页结果计算成功页数、失败页数、候选数；不另存会与页结果矛盾的“部分成功”布尔字段。界面可以显示部分成功，但必须同时显示计数。`extract-all` 有任何失败则最终非零退出，同时保留可供下游加载的成功页产物；退出码不替代逐页可用性。

一页的 candidate_ids 内不得重复；原生跨页候选可以由多个成功页引用同一 ID，策略候选总数按 ID 去重，完整候选只存一份。跨页候选仍按既有准入规则 deferred，不因页级统计改变其逻辑结构。

整文档 API 的返回状态不直接等于所有页面状态。runner 应读取可信的页级诊断；无法确认完成的页面记失败并继续尝试剩余页，必要时按页重试。禁止为了方便重试而把正常完整转换中的跨页 TableItem 静默拆成多个单页成功表；重试产生的局部结果必须带原 PDF 页码与来源，并避免与已成功页重复。

当前执行方式：PyMuPDF/Camelot 逐页捕获工具异常；Unstructured 将原 PDF 每页写为临时单页输入，返回后恢复 Element 的原页码及来源文件。Docling 正常完整转换直接保留完整文档；部分返回只有在错误均有页码时才信任其余页，保留连续成功页段，并对失败/无法确认页逐页重试。使用原生 concatenate 重建引用后恢复原 PDF 页码；部分原始文档另存 raw/attempt_document_N.json，不丢弃跨失败页的原始事实。规范化异常以来源页为隔离单位：保存原始记录与错误，剔除触及失败页的半成品候选，继续下一来源页。异常无法归属页面则停止发布并保留旧产物。

Docling 成功页的原生文档事实继续作为卡位依据；失败页不是“已检测且没有表格”。在有可加载的 Docling raw/normalized 和页级事实时，仅失败页候选延后：CandidateAdmissionResult 增加 deferred_reason=`slot_source_page_failed`，decision=null、匹配列表为空，不创建虚假 rejected 评价。CandidateView 仍只说明几何是否可比较；该原因归属准入结果，不归属几何视图。若整个 Docling 权威输入缺失、身份错误或文件不可解析，仍是加载失败，不能由其他工具替代卡位来源。

新提取使用 table_extraction_v2，规范化容器使用 table_candidates_v2，分组使用 table_candidate_selection_v2。历史提取可以显式 legacy warning 加载，但不能据此声称页级成功；历史 v1 分组须重新构建后评分。


### 2.3 TableCandidate、TableCell 与公共值对象

```text
BoundingBox
  x0, y0, x1, y1: float

CoordinateTransform
  source_system: str
  target_system: str = "pymupdf_page_top_left_pt_v1"
  source_page_width, source_page_height: float | None
  scale_x, scale_y: float | None
  y_axis_flipped: bool
  page_rotation: int | None

TableRegion
  page_number: int
  page_width, page_height: float | None
  bbox: BoundingBox | None
  source_ref: str
  coordinate_transform: CoordinateTransform | None

TableArtifacts
  csv_file, html_file: str | None

GridPosition
  row_index, column_index: int
  reason: str

TableCandidate
  candidate_id: str
  tool: str
  strategy: str | None
  source_ref: str
  regions: list[TableRegion]
  row_count, column_count: int | None
  x_boundaries, y_boundaries: list[float] | None
  cells: list[TableCell]
  uncovered_grid_positions: list[GridPosition]
  artifacts: TableArtifacts
  warnings: list[str]
  unplaced_text: str | None        # 无结构但存在原生整表文本

TableCell
  cell_id: str
  bbox: BoundingBox | None
  row_span, col_span: int | None
  start_row_offset_idx, end_row_offset_idx: int | None
  start_col_offset_idx, end_col_offset_idx: int | None
  text: str | None
  roles: list[CellRole]
  span_source: SpanSource
  source_ref: str
  source_refs: list[str]           # 合并格的全部组成格引用，旧数据默认空
```

#### 字段职责与不变量

- candidate ID 在单份文档的候选集合内唯一；同一输入顺序重复运行稳定，不声称跨内容版本不变。tool 的范围是 pymupdf/camelot/unstructured/docling，策略为需求列出的九种组合。不能把来源 ID 当质量分。
- `regions` 保存全部 provenance 页面区域，可以包含多页。候选的页码集合由 regions 推导；准入时跨页 deferred，不在适配时扔掉跨页事实。
- `TableRegion.page_number` 的可用值为 PDF 的一基有效页码。无效或缺失来源在当前实现可能以 0 表达，不能当有效页；由 loader 标记 deferred。保留 0 仅用于表达缺失原生页码，不能进入有效页级统计。
- `row_count/column_count` 是逻辑网格维度，未知为 null；x/y boundaries 是几何证据，不等于逻辑行列真值。没有可靠单页边界时为 null。
- 一个 TableCell 对应一个物理格或合并格的唯一锚点。起点为零基、终点排他；有效范围必须满足 `0 <= start < end <= dimension`，且 `span = end-start > 0`。布尔值不算整数位置。
- 已识别空 cell 的 text 是空字符串；工具未提供文本是 null；span 覆盖的其他格位没有独立 cell；未被有效 cell 覆盖的位置进入 `uncovered_grid_positions`。不得把这四种事实混为“空白”。
- `GridPosition.reason` 和 candidate `warnings` 是诊断文本，不是准入原因码枚举。structured decision reason 由下游所属模型定义，不能从自由文本猜状态。
- TableArtifacts 路径相对于候选的 `normalized/<strategy>/`；通常为 `tables/...csv/html`。source_ref 指向 raw 文件与原生记录；不可靠引用必须记录缺口，不能伪造 JSON Pointer。
- CoordinateTransform 是坐标转换审计，不是质量特征。source_system 在本阶段为 PyMuPDF 左上 pt、Camelot 左下 pt、Docling TOPLEFT/BOTTOMLEFT 或 Unstructured PixelSpace；未知系统不视为可转换。

#### CellRole 与 SpanSource

| 所属字段 | 取值 | 触发与下游意义 |
| --- | --- | --- |
| TableCell.roles | column_header | 工具明确为列头，或 PyMuPDF 内置表头区域映射；不增加评分 |
| TableCell.roles | row_header | Docling 原生行头标记 |
| TableCell.roles | row_section | Docling 原生行分节标记 |
| TableCell.roles | body | Docling 无原生 header/section 标记、Unstructured td 或 PyMuPDF 非表头 cell |
| TableCell.roles | unknown | 无可靠角色，例如当前 Camelot cell；不得猜为表头 |
| TableCell.span_source | native | Docling 有效原生 span/offset，或 HTML 明确/缺省为 1 的合法 span |
| TableCell.span_source | geometry_inferred | PyMuPDF bbox 成功映射到其策略网格，含 1×1 |
| TableCell.span_source | edge_inferred | Camelot 原子 cell 缺失共享边界形成有效矩形分量 |
| TableCell.span_source | unavailable | 无法确定合法覆盖范围；保留可读取原始事实、warning，不伪装合法结构 |

roles 允许多个原生角色；没有可靠角色不能自行加业务含义。异常原生 span/offset 必须可追溯，不能静默修成一致；详细异常保留和当前偏差见 Docling 章节。

核心模型使用 dataclass；对外枚举使用 `Literal`，并在字段所属模型处定义其含义。`BoundingBox` 复用 `table_models.py` 的公共模型。

### 2.4 TableSlot

```python
TableSlot
    slot_id: str
    docling_table_ref: str
    page_number: int | None
    page_width: float | None
    page_height: float | None
    bbox: BoundingBox | None
    status: "eligible" | "deferred"
    deferred_reason: str | None
```

不变量：

- `slot_id` 由 `document.json.tables` 的原生下标生成 `slot_{index + 1:03d}`；
- `docling_table_ref` 使用确定的 JSON Pointer `#/tables/{index}`，可直接回到原始 TableItem；
- `eligible` 时页码、页面宽高和 bbox 必须有效，`deferred_reason=None`；
- `deferred` 时 bbox 可以为空，原因只能是需求定义的 `cross_page_slot`、`missing_slot_provenance`、`missing_slot_bbox`、`slot_coordinate_conversion_failed`、`invalid_slot_bbox`、`invalid_slot_page_geometry`。

### 2.5 CandidateView

```python
CandidateView
    candidate_id: str
    tool: str
    strategy: str | None
    source_ref: str
    html_file: str | None
    page_number: int | None
    page_width: float | None
    page_height: float | None
    bbox: BoundingBox | None
    processing_status: "comparable" | "deferred"
    deferred_reason: str | None
```

`CandidateView` 只保存准入和审核视图需要的数据，不复制 cells。完整 `TableCandidate` 仍保存在各工具的 `normalized/<strategy>/tables.json`，后续 scoring 按 `candidate_id/source_ref` 重新定位。

`comparable` 时 `deferred_reason=None`；`deferred` 时原因只能是 `cross_page_candidate`、`missing_candidate_region`、`missing_candidate_bbox`、`candidate_coordinate_conversion_failed`、`invalid_candidate_bbox`、`invalid_candidate_page_geometry`。

### 2.6 SlotMatchEvaluation

```python
SlotMatchMetrics
    intersection_area: float
    union_area: float
    iou: float
    candidate_coverage: float
    slot_coverage: float

SlotMatchEvaluation
    evaluation_id: str
    candidate_id: str
    slot_id: str
    decision: "accepted" | "rejected"
    reason_codes: list[str]
    metrics: SlotMatchMetrics
```

只为 `comparable candidate + eligible same-page slot` 创建评价。accepted 的原因只能为 `bidirectional_coverage_passed`；rejected 的原因按实际失败项列出 `candidate_coverage_below_minimum` 和/或 `slot_coverage_below_minimum`。

### 2.7 CandidateAdmissionResult

```python
CandidateAdmissionResult
    candidate_id: str
    processing_status: "completed" | "deferred"
    deferred_reason: str | None
    admission_decision: "admitted" | "rejected" | None
    matched_slot_id: str | None
    evaluated_slot_ids: list[str]
    accepted_slot_ids: list[str]
    reason_codes: list[str]
```

该模型的 deferred_reason 继承 CandidateView 的六种几何原因；新增页级设计另允许 `slot_source_page_failed`（几何可比较，但 Docling 权威页执行失败）。后一原因不加入 CandidateView；它使准入延后，其他成功页继续。

字段组合必须满足：

- deferred candidate：`processing_status="deferred"`，`admission_decision=None`，不产生评价；
- completed candidate：`deferred_reason=None`，`admission_decision` 必为 admitted 或 rejected；
- admitted candidate：恰有一个 `accepted_slot_id`，并等于 `matched_slot_id`；
- rejected candidate：`matched_slot_id=None`，不得进入任何组。

### 2.8 TableGroup

```python
TableGroup
    group_id: str
    slot_id: str
    docling_table_ref: str
    page_number: int | None
    slot_bbox: BoundingBox | None
    status: "ready_for_scoring" | "unresolved"
    unresolved_reason: str | None
    member_candidate_ids: list[str]
```

每个 slot 恰好建立一个 group。`ready_for_scoring` 表示 eligible slot 至少有一个 admitted candidate；`unresolved` 的原因是 slot 自身的 deferred reason，或 `no_admitted_candidate`。单成员组是正常结果。

### 2.9 GroupingReport 与 manifest

```python
GroupingReport
    format_version: str
    groups: list[TableGroup]
    table_slots: list[TableSlot]
    candidate_views: list[CandidateView]
    candidate_admission_results: list[CandidateAdmissionResult]
    slot_match_evaluations: list[SlotMatchEvaluation]
    warnings: list[str]
```

`groups.json` 是 `GroupingReport` 的唯一规范化 JSON。序列化字段顺序固定为上面顺序，使 `groups` 紧跟 `format_version`，同时保留完整审计链。

`manifest.json` 是运行摘要，不属于 `GroupingReport`。实现内部使用 `GroupingManifest` dataclass 固定字段：

```python
GroupingManifest
    source_pdf: str
    format_version: str
    input_candidate_counts: dict[str, int]
    table_slot_count: int
    eligible_slot_count: int
    deferred_slot_count: int
    candidate_count: int
    comparable_candidate_count: int
    deferred_candidate_count: int
    deferred_admission_count: int
    admitted_candidate_count: int
    rejected_candidate_count: int
    ready_group_count: int
    unresolved_group_count: int
    warnings: list[str]
    elapsed_seconds: float
    groups_file: str
    manual_review_dir: str
```

### 2.10 评分公共值对象

```python
MetricName = Literal[
    "text_f1",
    "critical_token_integrity",
    "shape_support",
    "blank_anomaly",
]
MetricStatus = Literal["evaluated", "not_evaluable", "not_applicable"]
BlankReferenceSource = Literal["unique_mode", "minimum"]

TokenCount
    token: str
    count: int
```

比例、异常度、相对分和总分直接保存为 `[0,1]` 范围内的 `float`。原始计数字段是指标的可审计事实，不能用浮点结果反向替代这些计数。

`MetricStatus` 的含义：

- `evaluated`：存在足够输入并得到原始比较值；
- `not_evaluable`：本应评价，但参考事实或 candidate 结构不足，原始比较值为 `null`；
- `not_applicable`：该 group 客观没有该指标的评价对象；第一版只用于 Slot 内不存在关键值 token 的情况。

### 2.11 Slot 文本参照与 candidate token

```python
ReferenceWord
    word_id: str
    page_number: int
    bbox: BoundingBox
    raw_text: str
    normalized_token: str
    block_index: int
    line_index: int
    word_index: int
    is_critical: bool

SlotTextReference
    slot_id: str
    source: "pymupdf_page_words_v1"
    page_number: int
    slot_bbox: BoundingBox
    words: list[ReferenceWord]
    token_counts: list[TokenCount]
    critical_token_counts: list[TokenCount]

CandidateToken
    cell_id: str | None             # unplaced_text token 无物理格归属
    token_index: int
    raw_text: str
    normalized_token: str
```

`ReferenceWord` 保留 PyMuPDF page word 的原始块、行、词序号和 bbox。`CandidateToken` 保留 token 所属物理 cell，使人工可以确认 `500` 是否被输出为同 cell 的 `5 00` 或跨 cell 的 `5 | 00`。匹配仍按多重集完成，不使用 token 顺序或 cell 位置增加分数。

### 2.12 四项原始指标模型

```python
TextCoverageMetrics
    status: "evaluated" | "not_evaluable"
    reason_codes: list[str]
    reference_token_count: int
    candidate_token_count: int
    matched_token_count: int
    precision: float | None
    recall: float | None
    f1: float | None
    unmatched_reference_tokens: list[TokenCount]
    unmatched_candidate_tokens: list[TokenCount]

CriticalTokenMetrics
    status: "evaluated" | "not_applicable"
    reason_codes: list[str]
    reference_token_count: int
    matched_token_count: int
    integrity: float | None
    unmatched_reference_tokens: list[TokenCount]

GridShapeMetrics
    status: "evaluated"
    reason_codes: list[str]
    row_count: int | None
    column_count: int | None
    same_shape_candidate_count: int
    group_candidate_count: int
    support: float

BlankGridMetrics
    status: "evaluated" | "not_evaluable"
    reason_codes: list[str]
    logical_position_count: int | None
    nonempty_covered_position_count: int | None
    blank_position_count: int | None
    blank_ratio: float | None
    reference_blank_ratio: float | None
    reference_source: "unique_mode" | "minimum" | None
    blank_anomaly: float | None
    invalid_nonempty_cell_ids: list[str]

CandidateRawMetrics
    tokens: list[CandidateToken]
    text_coverage: TextCoverageMetrics
    critical_tokens: CriticalTokenMetrics
    grid_shape: GridShapeMetrics
    blank_grid: BlankGridMetrics
    warnings: list[str]
```

原因码及触发条件固定如下：

- `empty_reference_tokens`：Slot 内没有规范化后非空的原生 word；`TextCoverageMetrics.status="not_evaluable"`；
- `candidate_text_unreadable`：candidate 的 `cells` 不是列表，cell 不是对象，或者任一 cell text 既不是字符串也不是 `null`；文本覆盖率和适用时的关键值完整度都按空 candidate 文本得到 `0`，同时记录该原因；
- `no_critical_reference_tokens`：Slot 原生 token 中没有包含 Unicode 十进制数字的 token；`CriticalTokenMetrics.status="not_applicable"`；
- `invalid_grid_shape`：row/column count 缺失、为 bool、不是整数或不为正；shape support 记为 `0.0`，blank metric 为 `not_evaluable`；
- `invalid_nonempty_cell_placement`：至少一个非空物理 cell 的起止 offset/span 缺失、越界或互相矛盾；blank metric 为 `not_evaluable`，并列出 cell ID；
- `overlapping_nonempty_cells`：多个非空物理 cell 覆盖同一逻辑位置；按位置并集继续计算 blank ratio，同时记录 warning，不重复计数。
- `invalid_empty_cell_placement`：空文本 cell 的 offset/span 无效；不影响非空覆盖计算，但记录 candidate warning。

原因码不得由 exporter 根据 `null` 猜测，必须在产生状态的指标函数中写入。

### 2.13 两两比较与相对分模型

```python
PairwiseDecision = Literal["first_wins", "tie", "second_wins"]
PairwiseReason = Literal[
    "higher_value",
    "lower_value",
    "equal_value",
    "both_unavailable",
    "only_first_evaluable",
    "only_second_evaluable",
]

MetricPairEvaluation
    metric_name: MetricName
    first_candidate_id: str
    second_candidate_id: str
    first_value: float | None
    second_value: float | None
    decision: PairwiseDecision
    reason: PairwiseReason

RelativeMetricScore
    metric_name: MetricName
    wins: int
    ties: int
    losses: int
    opponent_count: int
    score: float
```

每个无序 candidate pair 在每项指标上恰好产生一条 `MetricPairEvaluation`，pair 顺序取组内稳定 candidate 顺序。`higher_value` 只用于 `higher_is_better` 指标的正常数值胜负，`lower_value` 只用于 `lower_is_better` 指标的正常数值胜负；`equal_value` 表示两个分数之差不超过 `SCORE_EPSILON`；`both_unavailable` 表示双方均不可评价或不适用；`only_first_evaluable/only_second_evaluable` 表示仅一方有值，可评价方胜出。

### 2.14 Candidate 和 group 评分结果

```python
SelectionReason = Literal[
    "highest_total_score",
    "tool_priority_tiebreak",
    "candidate_id_tiebreak",
]

CandidateScoringResult
    candidate_id: str
    tool: str
    strategy: str | None
    raw_metrics: CandidateRawMetrics
    relative_scores: list[RelativeMetricScore]
    total_score: float

GroupScoringResult
    group_id: str
    slot_id: str
    text_reference: SlotTextReference
    candidates: list[CandidateScoringResult]
    pair_evaluations: list[MetricPairEvaluation]
    highest_total_score: float
    tied_top_candidate_ids: list[str]
    selected_candidate_id: str
    selection_reason: SelectionReason
```

`tied_top_candidate_ids` 保存应用任何工具或 ID 消歧之前，与最高总分之差不超过 `SCORE_EPSILON` 的完整集合。`selection_reason` 的触发条件：

- `highest_total_score`：最高总分 candidate 只有一个；
- `tool_priority_tiebreak`：最高总分有多个工具，按配置的工具顺序后得到唯一 candidate；
- `candidate_id_tiebreak`：最高优先级工具内仍有多个最高总分 candidate，按 candidate ID 升序选择。

### 2.15 ScoringReport 与 manifest

```python
ScoringReport
    format_version: "table_candidate_scoring_v1"
    groups: list[GroupScoringResult]
    source_pdf: str
    source_grouping_report: str
    warnings: list[str]

ScoringManifest
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
```

`ScoringReport.groups` 只包含输入报告中 `ready_for_scoring` 的 group，并保持原 `GroupingReport.groups` 顺序。unresolved group 已完整保存在源分组报告中，不复制为虚假的评分结果。`source_pdf` 和 `source_grouping_report` 均写规范化绝对路径。`ready_group_count` 必须等于 `scored_group_count`，否则评分报告无效。

### 2.16 提取 manifest 与模型兼容

四工具 manifest 保留各自原生摘要：PyMuPDF 的 strategies 计数、Camelot 的 runs、Docling/Unstructured 的 status/error/run_metadata 及文件位置。这些旧摘要便于诊断，**新版页级事实以 executions 为准**，不使用旧 status 丢弃成功页。

| 公共字段 | 职责与校验 |
| --- | --- |
| format_version | table_extraction_v2 |
| tool | 四工具之一 |
| source_pdf / source_file | 原 PDF 规范化绝对路径；source_file 为 PyMuPDF 历史读者保留 |
| source_sha256 | 原 PDF 内容身份，变化则拒绝复用 |
| configuration_identity | tool、version、adapter_revision、options；CLI 比较当前工具版本和固定配置，变化则提示重建 |
| executions | §2.2 的逐策略执行事实；成功零表仍有完整请求页记录 |
| artifact_sha256 | 相对路径 → SHA-256；覆盖本次全部计算用 JSON，排除 manifest 自身，HTML/CSV 为派生展示 |

manifest 是身份索引与运行摘要，raw 是来源事实，normalized 是计算输入。成功零表仍保存空 normalized 列表及有效 raw 路径；缺文件不是零表。历史文件缺少上述身份/页级字段时显式记录 legacy warning，不补造成功页，也不替历史工具参数背书。

分组 manifest 另存 source_sha256、extraction_sha256（四工具全部 JSON 路径与哈希）、report_sha256。评分在读取 ready 成员之前核对三者，提取增删改、PDF 更新或分组报告变动均拒绝复用旧分组。CLI 对旧分组格式提示重建。分组新格式为 table_candidate_selection_v2，评分领域结果未改格式，仍为 table_candidate_scoring_v1。

## 3. 表格提取与结构恢复设计

### 3.1 适配接口与工具能力

此处的接口指 application 定义的提取能力契约，四工具实现属于 infrastructure/extractors；第三方库自身不是本项目端口的实现。接口输入输出及分层约束见 §9.3。以下 runner/normalizer/exporter 是职责划分，迁移时按 §9.4 拆分文件输出与展示，不能将现有 exporter 整体视为单一层。

每个 runner 只负责第三方调用及 raw；normalizer 接收 raw 与权威 PageGeometry，返回公共候选与诊断；exporter 写 raw、normalized、视图和 manifest。四工具共享公共模型，不强迫其 raw 变为同一种伪原生格式。

| 工具 | 逻辑跨度 | cell bbox 语义 | 适配依据 |
| --- | --- | --- | --- |
| PyMuPDF | 由 cell 边缘推算 | 识别或推断的网格区域，不是文本包围盒 | rows/cells/extract + 2 pt 边界聚类 |
| Camelot | 由原子格双侧缺边推算 | 原子网格区域；合并取联合 | left/right/top/bottom；hspan/vspan 不是整数跨度 |
| Docling | 原生 span/offset | 可选预测/匹配区域；当前匹配后处理可能对齐文本，并非可靠真实格边界 | 原生 table_cells 逻辑信息优先 |
| Unstructured | HTML 原生 span 或缺省 1 | 现有实验 raw 无可用 cell bbox；使用 Element 区域 | text_as_html + 占位遍历 |

“有 bbox”不等于“准确的物理线框”。模型推断格区域、PDF 文本区域和缺失几何应分别说明；不要用真假 bbox 一个布尔值覆盖。证据及版本边界见工具调研。

### 3.2 公共坐标变换

公共坐标系为 `pymupdf_page_top_left_pt_v1`：以原 PDF 同一有效页面为基准，左上为 (0,0)，x 向右、y 向下、单位 pt。PageGeometry 从原 PDF 读取页码、宽、高、rotation。所有 normalized table/cell bbox 与几何边界都应位于该空间；raw 不改写来源坐标。

| 来源 | 换算 |
| --- | --- |
| PyMuPDF 左上 pt | 已处于同一有效页面时 identity |
| Camelot `(x1,y1,x2,y2)` 左下 pt | `(x1,H-y2,x2,H-y1)` |
| Docling TOPLEFT `(l,t,r,b)` | `(l,t,r,b)` |
| Docling BOTTOMLEFT `(l,t,r,b)` | `(l,H-t,r,H-b)` |
| Unstructured PixelSpace points | x 乘 `W_pt/W_px`，y 乘 `H_pt/H_px`，取外接矩形；不假设等比缩放 |

换算公式只在来源与目标指向同一有效页面时成立。不能把记录了 rotation 当成执行了旋转变换；CropBox/MediaBox 偏移和旋转需要实际映射及样本验证。无法确认时公共 bbox 置空并告警，raw、text 与可用逻辑结构继续保留。Docling 原生 page.size 与公共页面尺寸不一致需记录告警，不能无说明套用高度。

table 和 cell 必须分别判断来源原点。cell 无可靠页码时不凭序号或相同坐标猜页码。现有 Unstructured 实验 raw 没有可用 cell bbox，不能从整表 bbox 反推；新版本可选字段的边界见工具调研。

有效可比较 bbox 必须坐标有限、正面积、位于有效页范围；页面宽高有限且为正；候选尺寸与 PDF 每个方向相差最多 1.0 pt。覆盖比例 epsilon 是 1e-12，仅防浮点尾差。页面越界裁剪容差统一为 0.000001 pt（1e-6 pt）：仅容差内可裁剪到边界，超过则公共 bbox 不可用，保留 raw 与告警。此容差不替代 2 pt 的跨度边界聚类容差或 1 pt 的页面尺寸一致性容差。

### 3.3 PyMuPDF 适配

#### raw 与身份

三策略分别保存 `raw/<strategy>.json`：tool、strategy、source_file、page_count、pages。每页保存 page_number、page bbox、words、tables；每表保存 table_index、bbox、row_count、column_count、cells、rows、extract、header（bbox/cells/names/external）。快照不是官方文档 serializer，但应忠实保留字段，不能以派生 CSV 替代 raw。

candidate ID：`pymupdf_<strategy>_p<页码两位>_t<页内表序号两位>`。表 ref 为 `raw/<strategy>.json#/pages/<page_index>/tables/<table_index>`。cell ref 为对应表 ref 追加 `/rows/<r>/cells/<c>`，可定位原始 bbox；文本可在同表 `/extract/<r>/<c>` 查回。

#### 几何跨度恢复

1. 按 rows 和 extract 对应收集物理 cell；同一 bbox 的合并延续位置不重复创建 cell。无 bbox 的位置不能直接认定为合并。
2. 收集物理 cell 和 table bbox 边缘，使用 `SPAN_BOUNDARY_TOLERANCE=2.0 pt` 聚类，以中位数形成有序 x/y 边界；相邻边界定义原子行列。
3. 相距不大于 span 容差的边界合并为同一簇，消除退化区间；簇中位数不再额外按三位小数舍入。核验 rows/extract 行数和每行列数与原始维度，矛盾写 raw_matrix_shape_mismatch；不修改原始矩阵。
4. 每条 cell 边映射到最近边界，误差不得超过 2 pt；正面积重叠的物理 cells 不写推断 span，记录冲突；映射不得生成零跨度或越界位置。
5. 成功时以起终点差得到 row/col span，标 geometry_inferred；失败时未知 offset/span 为空，但 text、来源 bbox、warning 保留。
6. 根据有效 span 覆盖位置，区分真实空 cell、延续位置与 uncovered。row/column count 的来源与几何网格不一致需告警，不能悄悄改 raw。

例：x 边界 100/200/300/400，y 边界 50/80/110，bbox `[100,50,400,80]` 对应 row `[0,1)`、column `[0,3)`、row_span=1、col_span=3。这只描述该策略隐含结构，不证明真实 PDF 有此合并格。

非 external 的 header bbox 与 cell 正面积重叠可映射 column_header；external header 不伪装成内部 cell。CSV 可以保留原 extract 二维视图，HTML 必须依 normalized span 渲染。

### 3.4 Camelot 适配

#### raw 与身份

每 flavor 保存 `raw/<flavor>.json` 的状态、错误、运行 metadata 与 Table 快照。Table 包括 page/order/shape、accuracy/whitespace/parsing_report、rows/cols、filename/rotation、可序列化 parse_details/textlines、df 和 cells；缺字段为 null。Cell 保存 x1/y1/x2/y2、text、left/right/top/bottom、hspan/vspan。df 不是完整 raw。

candidate ID：`camelot_<flavor>_p<页码两位>_t<该flavor表序号两位>`；表 ref 指 `raw/<flavor>.json#/tables/<index>`。行列来自原子 cell 网格；表 bbox 为已转换原子格的联合，几何边界只审计，不用于推断 span。

#### 缺边连通恢复

1. 原子 cell 的行列位置是图节点。同一行相邻两格只有左格 right=false 且右格 left=false 才连接；同列相邻两格只有上格 bottom=false 且下格 top=false 才连接。单侧矛盾不连接，告警。
2. 用连通分量寻找合并区域。分量必须恰好填满其最小外接行列矩形；非矩形、缺格或无法可靠转换 bbox 时不伪造带几何的有效 span。
3. 有效矩形左上原子格是唯一锚点，end 为终点加一，span 为区间差；合并 bbox 为所有原子格公共 bbox 联合；来源标 edge_inferred。
4. 被覆盖的非锚点不再创建独立 TableCell；未被有效分量覆盖的位置进入 uncovered。hspan/vspan 是边界状态派生布尔值，不是数值 span。
5. 锚点外出现非空文本要保留可追踪来源并告警。已确认属于同一合并格的原子格若重复记录相同完整文本，只输出一次，例如 xyz + xyz → xyz；不同物理格的相同值分别保留。同一原子格文本自身的重复词不删除。比较使用原始完整文本，不能先做评分 token 规范化再去重；不同非空片段按原子行列顺序合并并保留各来源。无法确认合并关系时不因文本相同而合并。

所有 flavor 使用同一恢复规则；accuracy、whitespace 不参与统一总分。raw 必须支持核验全部组成原子格，不能只保留合并后的 df。

### 3.5 Docling 适配

#### 文档与表格

固定配置见需求“表格提取与结构恢复”。保留 `raw/document.json` 和 `raw/document.html`；JSON 是完整原生文档，HTML 是整体阅读视图。表格只生成公共 normalized 的 JSON/CSV/HTML，不再常态保存额外原生单表 CSV/HTML、Markdown、layout/furniture 清单或 overlay。

每个 document.tables 的原生 TableItem 对应一候选；candidate ID：`docling_default_p<首个provenance页码两位>_t<原生表序号两位>`；source_ref 为 `raw/document.json#/tables/<index>`。无有效页码不能构造虚假有效位置。

每条 provenance 独立转换为 region。全部 provenance 唯一指向一页时 cell 可使用该页；跨多个页面时仅保留 region、cell text/offset/roles/span，cell bbox 与 x/y boundaries 为 null 并说明上游未给 cell-to-page 关联。本实验不自行关闭原生跨页识别，也不把它拼成单页候选。

#### 原生 cell 映射

- 原生 table_cells 一对一成为公共 cell；`cell_id` 结合 candidate ID 与 cell 序号，ref 指 `/data/table_cells/<index>`。
- 原生有效 span 与 offset 优先保留；只有可靠合法 offset 时可用差值补全缺失 span，不从 bbox 重新猜原生逻辑覆盖。
- span 与 offset 矛盾时标 unavailable、保留可读取原值并 warning，消费者不得把异常覆盖当有效网格。规范化保留可读取的原生 offset/span；结构消费者验证其一致性后才展开。
- roles 按原生 column_header/row_header/row_section 转换；无标记为 body。text 包括空字符串，不能丢掉空格事实。
- 行列数来自有效 cell 的排他结束 offset 最大值；无有效值为 null 并告警。逻辑覆盖之外的位置为 uncovered。
- 单页 x/y boundaries 从转换后的 table/cell bbox 边缘聚类，容差 2 pt；只表达几何，不驱动 span。缺可靠边界时不能用空数组冒充完整几何证据。

零表成功时仍要保存 raw 文档和 `normalized/default/tables.json` 的空 tables，manifest 路径不能由表数量决定。非 ASCII 输入使用临时 ASCII 路径是平台适配细节；manifest 和业务身份仍指原 PDF，不能泄露临时副本为源文件。

### 3.6 Unstructured 适配

#### Element 与身份

固定 hi_res + infer_table_structure=True。官方序列化的完整 Element 列表保留为 `raw/elements.json`。每个 type=Table 的 Element 对应一候选，不能因 HTML 多行拆成多候选。

candidate ID：`unstructured_hi_res_p<页码两位>_t<文档内Table序号两位>`；source_ref 指 `raw/elements.json#/<element_index>`。原始文本、HTML、页码和 coordinates 都来自 Element/metadata。

#### HTML 到逻辑网格

1. 按 tr 顺序逐行读取直接 td/th；维护已被有效 span 占用的逻辑位置集合。每个 cell 从当前行最左未占位置开始。
2. rowspan/colspan 缺失默认 1；只有正整数合法。结束 offset=起点+span，为排他边界。
3. 有效 span 标记其全部覆盖位置，只创建左上锚点；th→column_header，td→body，span_source=native。
4. 非法 span、重叠或无法确认位置时，保留可用文本与 warning；offset/span 无法确认部分为空，不静默丢掉该文本，也不猜表格结构。缺 HTML 时原 Element.text 保留在 raw，并通过 TableCandidate.unplaced_text 供文本指标读取；cells=[]、维度未知，不伪造一列真实表。
5. 行列数取有效结束 offset 最大值；计算 uncovered，与已检测空 cell 区分。每 cell source_ref 指对应 text_as_html 字符串；JSON Pointer 无法指字符串内部 HTML 节点，cell_id 负责区分。

仅支持已验证的 PixelSpace：要求有效页码、points、正的布局宽高与公共几何，分别缩放 x/y。缺字段、异常值或未知系统不得导致伪造坐标；公共表 bbox 为空并 warning。cell bbox 和 x/y boundaries 保持 null，不能由 HTML 行列数或整表 bbox 反推。

### 3.7 错误隔离与规范化输出

按 §2.2 记录页结果。只有该页提取/规范化失败才导致该页没有可发布候选；单个可保留的几何异常应形成带 warning 的候选，不能把缺 bbox 当作工具运行失败。成功页候选不受其他页失败影响。整文档调用需显式处理部分返回与异常，不能只检查有没有 document 对象。

每策略 `tables.json` 保存 tool、strategy、table_count、tables；表数必须与列表长度一致。成功零表是空列表，缺文件不能当零表。

normalized HTML 按 offset/span 输出锚点，覆盖位置不重复 td；uncovered 加诊断标记，不能伪装成源文件真实空值。结构不完整仍可输出诊断视图，但必须明确警告；不宣称该视图无损恢复。CSV 不用于恢复完整 span，也不作为评分输入。

从 PDF → raw → normalized JSON/HTML 的对照用于区分工具识别错误与适配错误。涉及坐标、重复文本、跨度规则的修复可能改变准入与 winner，需要单独回归，不归类为纯文件移动；当前实测范围见重构验收记录。

## 4. 候选准入设计

### 4.1 权威输入与候选加载

#### 权威页面几何

`application.grouping` 通过页面几何端口读取原始 PDF，建立 `page_number → width/height/rotation/has_crop_offset` 映射；具体读取由 infrastructure/pdf 完成。后续 slot 和 candidate 校验以此为权威，不以工具报告的尺寸覆盖它。

#### Docling 输入是硬前置条件

路径固定为：

```text
experiments/output/extracting/docling/<PDF stem>/manifest.json
experiments/output/extracting/docling/<PDF stem>/raw/document.json
```

`selection_slot_loader` 必须验证：

1. manifest 可解析，工具与格式受支持，页级执行事实可判定；新版按成功页装入，不要求整个策略无失败；历史输入保留 legacy warning，不推测未知的页级状态；
2. manifest 的 `source_pdf` 规范化绝对路径与命令输入一致；
3. manifest 指向的 raw document 存在；
4. raw document 是对象且 `tables` 为列表。

任一条件失败则整次命令失败，不生成新的分组结果。不得回退到 Unstructured 卡位，也不得把所有 candidate 直接送入 grouping。

#### 四工具 candidate 加载

每个工具从下列模式读取：

```text
experiments/output/extracting/<tool>/<PDF stem>/normalized/*/tables.json
```

加载器按 `tool → strategy path → candidate_id` 稳定排序，并执行：

- 验证顶层 `tables` 为列表；
- 验证 candidate 的 `tool/strategy` 与所在目录一致；
- 全文档范围内禁止重复 `candidate_id`，重复时整次命令失败；
- Docling `normalized/default/tables.json` 缺失或无法解析时整次命令失败，确保原生恢复候选产物可加载；这不保证每个 slot 都有可准入候选；
- 某个非 Docling 工具没有输出时记录 warning，其他工具继续；
- 某个策略文件无法解析时记录 warning 并跳过整个文件，不静默加载残缺数据；
- 已成功读取但几何不完整的 candidate 必须形成 deferred `CandidateView`，不能从报告消失。

Docling 的 raw document 用来建立 slot；Docling 的 normalized table 与其他工具一样作为候选参与准入，不享受无条件通过。

上述文件级验证与页级可用性是两层检查。载入成功后先应用 §2.2 页记录：Docling 失败页不得进入“无 slot 则 rejected”；该页准入延后，其他成功页继续。

### 4.2 卡位与候选几何判定

#### TableSlot 坐标转换

Docling `TableItem.prov[*].bbox` 按其 `coord_origin` 转到公共坐标系 `pymupdf_page_top_left_pt_v1`：

```text
TOPLEFT:
  (x0, y0, x1, y1) = (l, t, r, b)

BOTTOMLEFT，页面高度为 H:
  (x0, y0, x1, y1) = (l, H - t, r, H - b)
```

每条 provenance 必须有有效 `page_no`、bbox 和受支持的原点。同页多条 provenance 分别转换后，取覆盖全部 bbox 的最小外接矩形。provenance 涉及多个页面时 slot 为 `cross_page_slot`，不尝试猜测一个主页面。

#### CandidateView 几何归并

规范化 candidate 的 `TableRegion.bbox` 应已处于公共坐标系。加载时仍需验证 `coordinate_transform.target_system="pymupdf_page_top_left_pt_v1"`；缺失、来源未成功转换或目标坐标系不符时 deferred 为 `candidate_coordinate_conversion_failed`。

regions 的处理规则：

- 空列表：`missing_candidate_region`；
- 涉及多个页面：`cross_page_candidate`；
- 同页一个或多个 region：校验每个 bbox，再取最小外接矩形；
- 任一需参与归并的 region 缺少 bbox：`missing_candidate_bbox`；
- region 页面尺寸与权威 PDF 页面尺寸任一方向相差超过 `1.0 pt`：`invalid_candidate_page_geometry`。

#### bbox 有效性

slot 和 candidate 的公共 bbox 都必须：

- 四个坐标为有限数；
- `x1 > x0` 且 `y1 > y0`；
- 位于 `[0, page_width] × [0, page_height]` 内。

仅当越界不超过 `COORD_EPSILON_PT=1e-6` 时裁剪到页面边界；更大越界不得以容差修正。slot 与 candidate 分别写入对应的 invalid reason。

### 4.3 双向覆盖与候选结论

#### 创建 candidate-slot 评价

按 candidate 稳定顺序遍历，只与同页 eligible slot 比较。对 bbox `C` 和 `S` 计算：

```text
intersection_area = area(C ∩ S)
union_area        = area(C) + area(S) - intersection_area
iou               = intersection_area / union_area
candidate_coverage = intersection_area / area(C)
slot_coverage      = intersection_area / area(S)
```

接受条件只有一条：

```text
candidate_coverage + 1e-12 >= 0.65
and slot_coverage + 1e-12 >= 0.77
```

IoU 只写入审计指标，不参与 decision。不同页、deferred slot 或 deferred candidate 不产生虚假的 rejected evaluation。

`evaluation_id` 按稳定遍历顺序生成 `evaluation_{index:04d}`。`evaluated_slot_ids` 和 `accepted_slot_ids` 按 slot 稳定顺序保存，而不是按覆盖率排序。

#### 推导候选级结论

`selection_admission` 对每个 candidate 严格按以下优先顺序返回一条结果：

```text
CandidateView.deferred
  → deferred / admission_decision=None

无同页 eligible slot
  → completed + rejected / no_eligible_table_slot_on_page

有评价但 accepted 数量为 0
  → completed + rejected / no_table_slot_passed_bidirectional_coverage

accepted 数量为 1
  → completed + admitted / matched_single_table_slot

accepted 数量 >= 2
  → completed + rejected / multiple_table_slots_passed_bidirectional_coverage
```

多 slot 同时通过时必须拒绝，不能选择 IoU 最大者。这样保证一个 candidate 最多属于一个 group。

新增页级前置分支插入在 CandidateView.deferred 之后、无同页 eligible slot 之前：若 Docling 页失败，返回 deferred / slot_source_page_failed，决策为空、两类 slot 列表为空。不为这类候选创建 pair 评价。

## 5. 候选分组设计

`selection_grouping` 不计算新指标，只连接已经完成的决定：

1. 按 slot 的稳定顺序逐个创建 group；
2. 查找 `admission_decision="admitted" and matched_slot_id=slot.slot_id` 的 candidate；
3. 成员按 `tool、strategy、candidate_id` 排序；
4. eligible slot 有成员时为 `ready_for_scoring`；
5. eligible slot 无成员时为 `unresolved/no_admitted_candidate`；
6. deferred slot 为 `unresolved/<slot.deferred_reason>`，成员必须为空。

slot 与 group 的稳定排序键为：

```text
eligible first,
page_number,
bbox.y0,
bbox.x0,
document.json.tables 原生下标
```

`group_id` 在排序后连续生成 `group_001`、`group_002`。每个 slot 恰好出现一次；candidate 只能出现在零个或一个 group 中。

分组阶段的输出包括全部 slot 和全部准入审计事实；评分只使用 ready 组，因此 unresolved 不得从 grouping 报告移除。每个候选唯一性、每个 slot 对应一组、成员与准入结果一致等不变量在持久化前统一验证。

## 6. 评分与选优设计

### 6.1 装入成员与构建参照

#### scoring 对分组产物的身份校验

`scoring_loader` 固定读取：

```text
experiments/output/grouping/<PDF stem>/manifest.json
experiments/output/grouping/<PDF stem>/groups.json
```

开始评分前必须验证：

1. grouping manifest 与 groups 文件都存在且可解析；
2. manifest 的 `source_pdf` 规范化绝对路径与命令输入一致；
3. manifest 和 report 的格式版本均为当前支持的 `table_candidate_selection_v2`；
4. 每个 `ready_for_scoring` group 至少有一个 member，且 member ID 不重复；
5. 每个 member 都对应 `admitted + matched_slot_id=group.slot_id` 的准入结果和唯一 CandidateView；
6. rejected、deferred candidate 不出现在 ready group；
7. 每个 ready group 的 slot ID 可映射到唯一 eligible TableSlot，页码和 bbox 与 group 一致。

任一条件失败都属于过期、残缺或被手工破坏的上游事实，整次 scoring 失败，不输出半成品，也不重新运行 grouping 猜测修复。

#### 完整 TableCandidate 加载

`scoring_loader` 根据 group member 的 CandidateView 精确定位：

```text
experiments/output/extracting/<tool>/<PDF stem>/normalized/<strategy>/tables.json
```

ready member 的 `tool` 必须属于四个已接入工具，`strategy` 必须为非空字符串。同一个策略文件在一次运行中最多读取一次。加载后按 `candidate_id` 建立索引，并验证完整 candidate 的 `candidate_id/tool/strategy/source_ref` 与 CandidateView 一致。ready group 的任一 member 无法定位或身份不一致时整次 scoring 失败；不得从组中临时删除该 member，因为删除会改变共识、两两比较和 winner。

除身份与容器结构外，单个 candidate 的 cell 文本、网格或 span 异常不导致整次失败，而是由对应指标产生 `0` 或 `not_evaluable` 及原因码。这样既不改变已经确认的组成员集合，又能让低质量公共模型事实反映在评分中。

#### Slot 原生 word 参照加载

`scoring_reference` 在一次命令中只打开原始 PDF 一次。对每个 ready group：

1. 使用 group 对应 eligible TableSlot 的 `page_number` 和公共 `slot_bbox`；
2. 从 PyMuPDF `page.get_text("words", sort=False)` 读取整页原生 word，不调用 `find_tables()`；
3. 将 word bbox 中心点落在 slot bbox 闭区间内的 word 归入该 Slot，不扩大或收缩 bbox；
4. 按 `block_no、line_no、word_no、y0、x0` 形成稳定顺序；
5. 对 raw word 应用统一 token 规范化，规范化后为空的 word 不进入多重集，但原始读取异常必须记录 warning；
6. 按稳定顺序生成 `{slot_id}_word_{index:04d}`；
7. 用 `any(character.isdecimal() for character in normalized_token)` 标记关键值 token。

中心点归属只决定一个原生 word 是否属于 Slot，不改变 word 文本，也不把跨边界 word 切成部分 token。PyMuPDF 在这里是 PDF 数字文本层读取器；任何 PyMuPDF table candidate 仍只从 normalized 输出加载，不参与参照构建。

### 6.2 token 规范化与多重集

`scoring_reference.canonicalize_text()` 是 reference word 与 candidate cell 共用的预处理函数；`normalize_token()` 在其结果上完成大小写和首尾空白处理。固定执行：

1. 输入必须为字符串；
2. `unicodedata.normalize("NFKC", value)`；
3. 仅在两个 `str.isdecimal()` 数字之间，把 ASCII apostrophe、left/right single quotation mark、modifier letter apostrophe，以及 `´` 经 NFKC 产生的 combining acute accent 统一为 ASCII `'`；
4. 同时移除该数字分隔符两侧除 `\r`、`\n` 外的 Unicode 空白，使 `1´ 131` 规范化为 `1'131`，但绝不跨行修复；
5. `casefold()`；
6. 去除 token 首尾 Unicode 空白；
7. 规范化后为空则丢弃。

数字撇号规则的实现模式固定等价于：

```text
(?<=\d)[^\S\r\n]*['\u2018\u2019\u02bc\u0301][^\S\r\n]*(?=\d)
→ '
```

该模式在 NFKC 后执行，因此源字符 `´` 已表现为可选空白加 `U+0301`。规则只处理已经存在的明确数字分隔符，不连接普通数字片段，不跨 cell，也不把 `,` 与 `.` 相互转换。非数字上下文的弯引号、撇号保持原字符。

candidate 按 `TableCandidate.cells` 的原始稳定顺序遍历，每个物理 cell 恰好读取一次；cell text 必须先整体调用 `canonicalize_text()`，再用一个或多个 Unicode 空白切分，随后每个分片调用 `normalize_token()`。这样可在切分前消除分隔符旁的解析器噪声，同时保证 `5 00` 仍产生两个 token。`CandidateToken.token_index` 从 0 开始表示该 cell 内的最终分片顺序。不同分片或不同 cell 永不拼接。

reference 和 candidate 分别构造 `Counter[str]`。所有匹配数统一使用：

```text
match_count(R, C) = Σ_t min(R[t], C[t])
```

未匹配 token 频次分别是 `R - C` 和 `C - R` 的 Counter 结果，并按 token 升序序列化。

### 6.3 文本双向覆盖率

`compute_text_coverage(reference, candidate_tokens)` 使用整数计数：

```text
R = reference token count
C = candidate token count
M = match_count(reference_counter, candidate_counter)

precision = M / C
recall    = M / R
f1        = 2M / (R + C)
```

内部使用原始整数完成计数，再执行浮点除法。边界处理：

- `R=0`：status=`not_evaluable`、reason=`empty_reference_tokens`，三个比例均为 `null`；
- `R>0, C=0`：三个比例均为 `0.0`；
- candidate cells/text 容器不可读：视为 `C=0` 并追加 `candidate_text_unreadable`，不删除 candidate。

组内排序只读取 `f1`；precision 和 recall 只用于审计。

### 6.4 关键值词完整度

reference 中 `normalized_token` 至少含一个 `str.isdecimal()` 字符时进入关键值 Counter。candidate 不另做关键类型识别，直接使用全部 candidate token Counter 与关键值 Counter 做多重集匹配：

```text
K = reference critical token count
MK = match_count(critical_reference_counter, candidate_counter)
integrity = MK / K
```

`K=0` 时 status=`not_applicable`、reason=`no_critical_reference_tokens`、integrity=`null`。只匹配完整 token；不得对子串、字符或相邻 token 执行拼接匹配。

### 6.5 网格形状共识度

`compute_shape_support(group_candidates)` 先为每个 candidate 验证 shape：

```text
valid shape = row_count 和 column_count 均为非 bool 的正整数
shape       = (row_count, column_count)
support     = 组内具有同一 shape 的 candidate 数 / 组内全部 candidate 数
```

每个 candidate 独立计一次，同工具不同策略不合并。无效 shape 不与其他无效 shape 形成共识，其 support 固定为 `0.0` 并记录 `invalid_grid_shape`。第一版不读取 `x_boundaries/y_boundaries/cell bbox` 评价边界位置。

### 6.6 span-aware 空白率

`compute_blank_ratio(candidate)` 仅在 shape 有效时执行。非空 cell 定义为 `text` 是字符串且 `text.strip()` 非空。每个非空物理 cell 必须同时满足：

```text
start_row、end_row、start_col、end_col 均为非 bool 的整数
0 <= start_row < end_row <= row_count
0 <= start_col < end_col <= column_count
row_span == end_row - start_row
col_span == end_col - start_col
row_span > 0 and col_span > 0
```

任一非空 cell 不满足时，整个 candidate 的 blank metric 为 `not_evaluable/invalid_nonempty_cell_placement`；不得忽略该 cell 后计算一个虚假的高空白率。空文本 cell 的无效 placement 不影响非空覆盖，但写入 candidate warning。

对全部有效非空 cell 展开其半开区间 `[start_row,end_row) × [start_col,end_col)`，取逻辑位置并集：

```text
total_positions    = row_count × column_count
nonempty_positions = size(union(all nonempty cell covered positions))
blank_positions    = total_positions - nonempty_positions
blank_ratio        = blank_positions / total_positions
```

非空 cell 覆盖重叠时按并集继续计算，并记录 `overlapping_nonempty_cells`；合并 cell 的整个跨度均视为非空，不使用 `uncovered_grid_positions` 直接替代该计算。

### 6.7 空白率参照与异常度

`choose_blank_reference()` 只接收组内 status=`evaluated` 的空白计数和逻辑位置总数：

1. 将 `(blank_position_count, logical_position_count)` 用最大公约数约简为内部比例键，等价于交叉相乘判断比例相等；
2. 找出最高出现次数及其全部取值；
3. 最高次数至少为 2 且只有一个取值时，reference 为该唯一众数，source=`unique_mode`；
4. 所有值唯一或多个众数并列时，reference 为最小值，source=`minimum`。

每个可评价 candidate：

```text
blank_anomaly = max(0, blank_ratio - reference_blank_ratio)
```

内部比例键只用于可靠统计频次，不进入 JSON。不可评价 candidate 的 reference 和 anomaly 均为 `null`。如果全组都不可评价，则不存在 reference，所有成员在 pairwise blank metric 上按 `both_unavailable` 打平。低于 reference 的候选 anomaly 同为 `0.0`，不因空白更少继续获益。

### 6.8 两两比较

`build_metric_pair_evaluations()` 按 group member 的稳定顺序，对四项指标分别遍历所有 `i < j` 的 candidate pair。方向表固定来自 `scoring_config.py`：

```text
text_f1                   higher_is_better
critical_token_integrity  higher_is_better
shape_support             higher_is_better
blank_anomaly             lower_is_better
```

比较规则固定为：

- 双方有值且 `abs(first-second) <= SCORE_EPSILON`：tie/equal_value；
- 双方有值且差值超过容差：按方向产生 first_wins 或 second_wins，reason 为 higher_value 或 lower_value；
- 双方都没有值：tie/both_unavailable；
- 只有一方有值：有值方胜，reason 为 only_first_evaluable 或 only_second_evaluable。

容差只吸收浮点尾差。`1.0` 与 `0.99` 的差值远大于容差，前者必须胜出。

### 6.9 相对分与总分

对 candidate 在某项指标上的 pair 结果计数：

```text
wins + ties + losses = opponent_count = N - 1
relative_score = (2 × wins + ties) / (2 × opponent_count)
```

实现仍可用两倍积分把一次胜出表示为 2、平局表示为 1，再在最后执行一次浮点除法。`N=1` 时四项 relative score 按约定均为 `1.0`，但原始指标照常计算。

四项权重均为 `1/4`：

```text
total_score = (
    relative_text_f1
    + relative_critical_token_integrity
    + relative_shape_support
    + relative_blank_anomaly
) / 4
```

total score 使用 `float`。不同 group 的 total score 不进入任何共同排序。

### 6.10 winner 决策

`select_group_winner()` 按固定因果链执行：

```text
最高 total score 只有一个
  → 选中该 candidate / highest_total_score

最高 total score 有多个
  → 保留完整 tied_top_candidate_ids
  → 按 pymupdf、camelot、docling、unstructured 选择最高优先级工具
  → 该工具只剩一个 candidate
       → tool_priority_tiebreak
  → 该工具仍有多个 candidate
       → candidate_id 升序第一个 / candidate_id_tiebreak
```

不得使用随机数、文件系统枚举顺序或 Python set 顺序。工具优先级不回写任何 relative score 或 total score。

## 7. 全链路编排与运行接口

### 7.1 CLI 与路径参数

从项目根目录运行 `uv run python -m experiments.table_extraction <command>`。这是实验 CLI，算法函数不读取终端输入。

| command | 参数 | 行为 |
| --- | --- | --- |
| pymupdf | `--pdf` 必填，`--output-dir` 可选 | 三策略提取和规范化 |
| camelot | `--pdf` 必填，`--flavors` 默认 lattice,stream,network,hybrid；`--pages` 默认 all；`--output-dir` 可选 | 独立运行指定 flavor；空 flavor 或未知名称拒绝 |
| unstructured | `--pdf` 必填，`--strategy` 仅 hi_res，`--output-dir` 可选 | 本地 Element 分区与 Table 适配 |
| docling | `--pdf` 必填，`--output-dir` 可选 | default 文档转换与原生表格适配 |
| extract-all | `--pdf` 必填 | 顺序尝试四工具全部策略，汇总失败 |
| grouping | `--pdf` 必填，`--output-dir` 可选 | 校验上游，必要时交互补齐，再准入与分组 |
| scoring | `--pdf` 必填，`--output-dir` 可选 | 校验上游，必要时交互补齐，再评分与选优 |
| admission-calibration | `--labels`、`--chart` 可选 | 复算已有人工标签的覆盖率、更新派生列并生成图；不修改执行阈值 |

目标路径契约：提取默认写入 `experiments/output/extracting/<tool>/<PDF stem>/`；grouping/scoring 分别使用所属阶段目录。单工具与 grouping/scoring 的 `--output-dir` 统一表示该命令输出的父目录，最终追加 `<PDF stem>`；不再保留 Camelot 将参数当最终目录的例外。`extract-all` 保持现有参数范围。

grouping 默认从 `output/extracting/` 读取候选，scoring 默认读取 `output/grouping/` 并从 `output/extracting/` 定位完整成员。`--output-dir` 只改变当前阶段输出，不隐式改变后续输入；自定义输入通过应用层路径参数显式传入，CLI 本轮不新增未定义的输入选项。默认目录是完整串联入口。

以上路径及参数语义已实现。路径计算集中在 infrastructure/artifacts，CLI 帮助、补齐检查、loader、exporter、测试及审核链接随迁移一起更新；不得在多个模块分别拼接默认根目录。

### 7.2 依赖补齐

1. 提取就绪检查读取 manifest，验证工具、来源路径、所请求策略及页级执行事实；成功页可用，失败页列出诊断，未执行策略不能伪装成功零表。CLI 就绪检查不能替代 loader 对实际文件和内容的验证。
2. grouping 上游不完整时交互询问；拒绝补齐但 Docling 完整时，可以使用已有可选候选继续。缺可选输入必须持久记录 warning。
3. scoring 上游不完整时一次确认覆盖补齐链。重新提取后同步重建分组；补齐仅可选工具失败仍可继续，硬前置失败停止。
4. 内部 run_selection/run_scoring 只处理给定产物，不擅自调用重型工具或发起确认。缺 ready member 时 scoring 整体失败，不能偷偷删掉成员改变共识。
5. 缺输入、执行失败和用户取消以明确终端信息及非零退出表达；成功为 0。Y/Yes 接受，N/No/空输入/EOF 拒绝，其他回答重问。

CLI 就绪检查按新版页级可用性读取，并验证输入身份；完整执行是否成功由 ExtractionResult.succeeded 计算。有成功页的策略不能仅因失败页存在而整体判为不可用。补齐与退出行为遵守 §2.2。

### 7.3 分组与评分编排、错误边界

#### 准入与分组编排

`application.grouping.run_selection()` 的调用顺序固定为：

```text
解析并验证 source PDF
  → 验证 Docling manifest/raw document
  → build_table_slots()
  → load_candidate_views()
  → evaluate_slot_matches()
  → decide_candidate_admissions()
  → build_slot_groups()
  → validate_report_invariants()
  → export_selection()
```

错误分三层处理：

- **整次失败**：输入 PDF、Docling 权威输入或报告不变量无效；不输出半成品；
- **deferred**：单个已加载 slot/candidate 的几何证据不足；保留模型和原因，不参与比较；
- **warning**：非权威工具输出缺失或整个策略文件无法读取；记录后继续。

export 前必须执行不变量校验，包括 ID 唯一、每个 slot 恰有一个 group、admitted candidate 恰在一个对应组、rejected/deferred candidate 不在组内、evaluation 只连接同页 eligible/comparable 对象。

#### 评分与选优编排

`application.scoring.run_scoring()` 的调用顺序固定为：

```text
解析并验证 source PDF
  → load_and_validate_grouping_report()
  → load_group_table_candidates()
  → build_slot_text_references()
  → tokenize_candidate_cells()
  → compute_text_metrics()
  → compute_shape_support()
  → compute_blank_ratios()
  → choose_blank_reference()
  → compute_blank_anomalies()
  → build_metric_pair_evaluations()
  → aggregate_relative_scores()
  → select_group_winner()
  → validate_scoring_report_invariants()
  → export_scoring()
```

scoring 的错误边界：

- **整次失败**：source PDF、grouping 身份/不变量、ready member 身份或 winner 唯一性无效；不覆盖上一版有效评分结果；
- **指标不可评价**：某项参考或 candidate 结构不足；保留 candidate，按需求中的可用性比较规则处理；
- **warning**：不影响指标确定性的异常事实，例如非空 cell 逻辑覆盖重叠；写入 candidate 和顶层报告；
- **禁止降级**：不得因为新评分失败而调用旧 `scoring.py`，也不得自动选择 Docling candidate 作为隐藏 fallback。

export 前必须验证：每个 ready group 恰有一个结果；输入 member 与结果 candidate 集合完全相同；每项指标对每个无序 pair 恰有一条评价；wins+ties+losses 等于 opponent count；四项 relative score 和 total score 可由 pair 结果复算且误差不超过 `SCORE_EPSILON`；winner 属于组内且符合固定消歧规则。

## 8. 持久化与完整产物结构

### 8.1 全部产物目录

当前产物布局如下：

```text
experiments/
  output/
    extracting/
      <tool>/<PDF stem>/
        manifest.json
        raw/
          <strategy>.json           PyMuPDF / Camelot
          elements.json             Unstructured
          document.json             Docling 完整原生文档
          document.html             Docling 原生整体阅读视图
        normalized/<strategy>/
          tables.json
          tables/<table>.csv
          tables/<table>.html
    grouping/
      <PDF stem>/
        groups.json                  含准入评价、结论和全部组
        manifest.json
      group_views_for_manual_review/<PDF stem>/
        group_001.html
    scoring/
      <PDF stem>/
        scoring.json
        manifest.json
      score_views_for_manual_review/<PDF stem>/
        group_001.html
    calibration/
      admission-coverage-chart.html
  data/calibration/
    admission-labels.md              人工数据，暂保留 Markdown 格式
  docs/calibration/
    admission-thresholds.md          校准方法、分析与阈值结论
    admission-labels.md              标签格式、维护说明和数据入口
```

raw 下只生成对应工具的文件。`<PDF stem>` 不是内容唯一标识。准入包含在 grouping 中，不设独立产物目录；winner 保存在 scoring.json，本阶段不生成融合后的 RAG 文档。

校准文档是规格与知识，人工标签是应版本管理的实验输入，图是可再生成的派生产物。标签中的人工判断列不得被复算覆盖；现有覆盖率列仍允许程序更新，不为移动目录同时转换数据格式。将来若拆出独立计算结果，应放在 output/calibration，并先更新数据契约。

人工标签、派生图及提取结果已迁移到上述位置；默认参数和报告/HTML 相对路径同步更新。旧目录不作为静默回退来源；六份 PDF 的历史提取文件按哈希核验保持不变，分组与评分已从新路径重建。

### 8.2 序列化与引用

`format_version` 固定为 `table_candidate_selection_v2`。JSON 使用 UTF-8、`ensure_ascii=False` 和稳定字段/数组顺序。bbox 使用 `{x0, y0, x1, y1}`，不得输出 Python tuple 或不可序列化集合。

审计链固定为：

```text
candidate_views[candidate_id]
  → slot_match_evaluations[candidate_id]
  → candidate_admission_results[candidate_id]
  → groups[matched_slot_id]
```

`scoring.json` 是 `ScoringReport` 的唯一规范化 JSON，字段顺序固定为：`format_version`、`groups`、`source_pdf`、`source_grouping_report`、`warnings`。group、candidate、metric 和 pair evaluation 都按上游稳定顺序输出，不按最终分数重排原候选；winner 通过显式字段表达。

每个比例或分数直接输出 JSON number：

```json
{
  "precision": 1.0,
  "recall": 1.0,
  "f1": 1.0
}
```

JSON 输出不额外包装 numerator/denominator，也不为显示而提前舍入。指标对应的匹配数、总数、网格位置数和两两战绩继续保留，确保分数可以复算。token 频次按 normalized token 升序输出，warning 和 reason code 使用稳定顺序，不得序列化集合。

评分 `manifest.json` 只保存运行摘要和输入/输出路径，不复制候选指标。`selected_by_reason_counts` 必须显式包含三个 `SelectionReason` 键，即使某项计数为 0。

原始引用必须能从报告定位候选、raw 表和组成 cell。合并格需要支持全部组成原子格的追踪，不能只留锚点而丢掉其余文本来源。HTML 内路径相对其实际目录计算；不要假设报告和视图处于同一目录。视图展示契约集中在 validation.md。

### 8.3 发布与重跑

提取在目标同级临时目录完成计算与序列化，成功后再发布；预期启动/页级失败可以发布带明确诊断的新运行，成功页正常保留。无法归属页面的异常或序列化异常不覆盖旧结果。分组/评分在计算与不变量校验后，将报告及审核视图一起暂存：先将旧目录重命名为备份，再发布新目录，捕获异常时撤回新目录并恢复备份。该保证覆盖进程内可捕获异常，不保证断电或强杀进程的跨目录原子性；备份只在全部发布完成后清理。

## 9. 代码组织与模块职责

### 9.1 分层原则与依赖方向

以表格恢复与选优管道为一个能力模块，在其内部采用 Presentation、Application、Domain、Infrastructure 四层。提取、准入、分组、评分不是四个各自重复套四层的项目。包名暂保留 `experiments.table_extraction`，兼容现有 `python -m` 入口。

| 层 | 职责 | 不应承担 |
| --- | --- | --- |
| presentation | CLI 参数、交互确认、终端输出、公共 CSV/HTML 和 group/score 审核视图渲染 | 判断准入、重新计算评分政策、直接组织文件发布事务 |
| application | 提取/分组/评分/校准用例编排、执行状态协调、依赖补齐计划、外部能力端口 | 第三方 SDK 调用、工具原始字段解析、核心指标公式 |
| domain | 公共表格与决策模型、准入、分组、评分、通用几何和不变量 | PDF/文件 I/O、CLI、第三方工具类型、应用执行框架 |
| infrastructure | 四工具适配、PDF words/页面读取、原始响应、序列化、路径和持久化 | 决定准入阈值、组内质量规则或 HTML 展示政策 |

源码依赖约束：

```text
presentation → application → domain
infrastructure → application 定义的端口 / domain 公共模型
bootstrap → presentation、application、infrastructure，完成组装
```

domain 不导入其他三层；application 不导入具体 infrastructure 或 presentation。应用运行时经注入的端口调用外部能力，调用方向不等于源码 import 方向。bootstrap 是组装根，负责选择并注入具体实现，不放业务算法。

纯函数不必都归 domain：Camelot 缺边恢复、Unstructured HTML 解析等依赖工具或格式语义，归相应适配器。无工具语义的 bbox 运算、覆盖准入和评分 token 规则归 domain。适配代码可以复杂，但不能夹入公共选优政策。

### 9.2 代码目录

```text
experiments/table_extraction/
  __init__.py
  __main__.py                       保持命令入口
  bootstrap.py                      具体实现组装
  presentation/
    cli.py
    review_views/                   公共表格、分组、评分及校准图展示
  application/
    ports.py
    models.py                       运行请求、页级执行与用例结果
    extraction.py
    grouping.py
    scoring.py
    calibration.py
  domain/
    models/                         候选、卡位、准入、组、评分模型
    geometry.py
    admission.py
    grouping.py
    scoring/                        token、四指标、相对排名与选优
  infrastructure/
    extractors/
      pymupdf/
      camelot/
      docling/
      unstructured/
    pdf/                            页面几何和原生 word 读取
    artifacts/                      产物加载、序列化、路径、发布
```

上述目录已建立。可以按代码规模合并小文件；不为每个函数增加服务类，不引入依赖注入框架。校准的纯覆盖率计算复用 domain 规则，独立分析算法按其用途组织，不复制另一套准入公式。

### 9.3 端口与数据契约

第二阶段实现补充：无 HTML 可恢复结构、但存在原始整表文本时，TableCandidate 增加可空 `unplaced_text`，保存无法定位到物理格的文本；它不创建虚假的一格表。评分只在有该文本时将其作为独立候选文本流读取一次，对应 CandidateToken.cell_id=null，token_index 为该文本流内序号；结构指标仍按未知网格处理。正常 cells 已提供结构文本时，不重复追加整表文本。TableCell 增加 `source_refs` 列表保存合并格全部原子来源，原 source_ref 保留主要引用。新提取记录使用 table_extraction_v2，规范化容器使用 table_candidates_v2；旧文件缺新增字段时按空值读取，不补造原始事实。

端口由使用能力的 application 定义，可采用 Protocol 或显式可注入函数。优先隔离以下边界；实现签名见下文，不把第三方对象泄漏给应用层。

| 能力端口 | 输入/输出契约 | 实现责任 |
| --- | --- | --- |
| 表格提取 | PDF 身份/路径、请求策略和页面、有效配置 → 公共候选、StrategyExecution、原始产物引用 | 四工具适配器；不包含准入或评分 |
| PDF 参照读取 | PDF 与请求页面 → PageGeometry、公共 ReferenceWord 所需原生事实 | PyMuPDF 实现；slot 归属和 token 规范化由核心规则决定 |
| 产物存取 | 明确路径/输入身份、报告或公共候选 → 可验证的对象/发布结果 | 文件实现；异常显式返回或抛出，不伪装成功空结果 |

当前应用接口：

```python
ExtractionRequest(source_pdf: Path, output_dir: Path, tool: str,
                  strategies: tuple[str, ...] = (), pages: str = "all")
ExtractionResult(candidates: list[TableCandidate],
                 executions: list[StrategyExecution], manifest_path: Path)
TableExtractor.extract(request: ExtractionRequest, staging_dir: Path) -> ExtractionResult
ArtifactPublisher.stage(destination: Path) -> ContextManager[Path]
```

`strategies/pages` 的可选范围当前仅供 Camelot CLI 使用，其他工具执行其固定全页基线。`ExtractionResult.succeeded` 要求存在策略且每个请求页恰有一个 success；部分页成功不能让整次命令冒报全成功。`PipelineServices` 是应用拥有的可注入函数集合：页面几何、slot/candidate 加载、Docling 基线校验、失败页/不可用引用读取、评分输入加载、word 读取和报告导出。bootstrap 注入文件/PDF 实现及纯 HTML 渲染函数，不使用第三方 DI 框架。CLI 入口必须经过 bootstrap 完成组装。

提取端口统一上层结果，不强迫四工具使用同一个逐页底层 API。Docling 完整文档与表格引用必须保留；按页失败隔离仍遵守 §2.2。原始工具对象及 serializer 留在适配器内部，通过原始产物引用供审计和 Docling 卡位加载使用。

模型按语义归属：TableCandidate、TableSlot 及准入/评分事实在 domain；提取请求、PageExtractionResult、StrategyExecution 和运行摘要在 application；第三方原始响应在 infrastructure；展示专用结构在 presentation。序列化和文件格式适配在 infrastructure。不是所有 schema 都归 domain，混合模型已拆分：运行摘要在 application/models.py，领域事实在 domain/models/。

应用用例返回计算结果及执行事实，由外层协调渲染和保存；领域结果不能依赖 HTML 路径才能完成计算。若发布需要报告与视图一起暂存，外层提供渲染后的内容，文件发布实现负责写入。Docling 官方导出的原生 document.html 属于原始工具产物，仍由其适配器生成，不与自定义审核视图混淆。

### 9.4 原模块迁移对照

| 原文件/职责 | 现有归属与动作 |
| --- | --- |
| __main__.py、cli_workflow.py | 拆分 presentation 的参数/确认与 application 的补齐编排；由 bootstrap 注入实现 |
| table_models.py、selection_models.py、scoring_models.py | 公共概念进 domain/models；运行摘要等进 application/models；不按旧文件整体搬迁 |
| selection_metrics/admission/grouping、scoring_metrics/ranking | domain，保留纯函数与既有业务规则 |
| selection_runner、scoring_runner | application，经端口加载/持久化，直接调用 domain 算法 |
| scoring_reference.py | PDF I/O 进 infrastructure/pdf；token 规范化、多重集、slot 归属规则进 domain/scoring |
| coordinates.py | PDF 几何读取进 infrastructure/pdf；通用几何进 domain；工具坐标约定进各适配器 |
| 四工具 runner/raw/normalizer 与工具专属模型 | infrastructure/extractors/<tool>；保留原始事实与工具特有恢复规则 |
| 四工具 export、selection_export、scoring_export | 拆分通用文件存储与序列化、presentation 渲染；删除跨工具借用 PyMuPDF 私有导出函数的依赖 |
| selection/scoring loader | 文件/JSON 读取在 infrastructure/artifacts；身份校验协调在 application；领域不变量在 domain |
| admission_calibration.py | 拆分应用编排、标签文件读写、图展示；复用已有覆盖率核心 |
| config 与旧数据模型、docling_layout.py | 有效阈值归核心政策，工具配置归适配器，路径归 artifacts；审查死代码后删除，不恢复废弃功能 |

### 9.5 重构顺序与范围

先建立公共模型、核心算法及依赖边界，再拆工具/PDF/文件适配，接入应用用例和表现层，最后由 bootstrap 串联。目录迁移与行为修复分开验证：纯移动保持计算事实不变；页级错误隔离等修复按各自规格和用例允许结果变化。

本轮已迁移 output/extracting、校准文档/数据/图路径以及引用；现有 RAG 入口、chunking 和 embedding 不在本轮代码重构范围。四层设计有助于未来复用，但本阶段不提前创建正式 RAG parser 或通用插件框架。

## 10. 验证与实施约束

关键规则必须能映射到 [验收矩阵](validation.md)。阶段函数不读交互输入、不从 HTML 还原计算数据；持久化前验证模型不变量；异常几何、不可评价指标和缺上游文件分别处理。不得恢复旧 candidate-pair 分组、绝对质量淘汰或隐藏 fallback。

新版 producer、loader、CLI 就绪检查、原因统计及测试采用一致格式；历史提取的兼容读取不补造页级事实。实测结果见 [历史重构验收](history/refactor-validation.md)；自动测试与单份 PDF 的四工具实跑不能证明所有历史表格的识别质量，也不能证明未支持的旋转/裁切映射。
