# 多工具表格恢复与选优管道需求规格

本文是实验链路的有效行为规格。架构定义实现契约，[当前限制](limitations.md)记录当前支持范围与剩余限制；实际验证见[重构验收记录](history/refactor-validation.md)。本阶段重构实验代码，不迁移到 RAG。

阅读导航：[总览](#1-管道总览) → [提取与结构恢复](#2-表格提取与结构恢复) → [准入](#3-候选准入) → [分组](#4-候选分组) → [评分选优](#5-评分与选优) → [运行与结果](#6-全链路运行与结果管理) → [辅助功能](#7-辅助功能) → [边界与验收](#8-整体边界与验收)。

## 1. 管道总览

目标是改善数字型 PDF 中 Docling 已发现表格的结构恢复质量，并让每次选择都有可复算的依据。整条链路为：

```text
PDF → 四工具九策略提取 → raw + 统一 TableCandidate
Docling 原生 TableItem → TableSlot
候选 + 卡位 → 准入 → 按卡位分组 → 四指标评分 → 每个 ready 组唯一 winner
```

| 阶段 | 输入 | 输出与职责 |
| --- | --- | --- |
| 提取与结构恢复 | PDF、工具配置 | 原始文档/表格事实、cell 级公共候选、页级执行结果 |
| 准入 | 候选区域、Docling 卡位、页面几何 | 可比较性、双向覆盖评价、候选准入结论 |
| 分组 | 全部卡位、准入结论 | 一卡位一组，保留空组与无法处理的组 |
| 评分选优 | ready 组、完整候选、原 PDF 数字文本 | 四指标、组内相对分、唯一 winner 与原因 |

总输出是原始事实、规范化候选、分组报告、评分报告及辅助审核产物；本阶段不生成融合后的 RAG 文档。winner 表示组内相对最优，不表示已达到绝对质量标准。Docling 的完整文档 JSON 同时保留非表格内容、层级和阅读顺序，供后续迁移设计研究。

## 2. 表格提取与结构恢复

### 2.1 目标、输入与边界

输入为一份存在且可打开的数字型 PDF。原 PDF 提供权威页面与数字文本；四工具各自识别表格并恢复到公共 cell 级结构。该阶段不执行准入、不删除低分候选、不评选 winner。识别错误与适配错误需要能通过 raw 对照区分。

### 2.2 工具、策略与原始事实（EX-01–02）

| 工具 | 策略 | 必须保留的事实 | 规范化跨度来源 |
| --- | --- | --- | --- |
| PyMuPDF | lines、lines_strict、text | table/row/cell bbox、extract 文本矩阵、header、页面 words | geometry_inferred |
| Camelot | lattice、stream、network、hybrid | Table/Cell 快照、原子格边界、解析报告、DataFrame | edge_inferred |
| Unstructured | hi_res，infer_table_structure=True | 完整 Element 列表，包括 Table 的 text_as_html 与 metadata | HTML 明确或默认合法 span 为 native |
| Docling | default；do_ocr=False，do_table_structure=True，accurate，generate_page_images=True | 完整原生文档 JSON、文档 HTML、TableItem/cell/provenance | native |

四工具共九种策略结果。`extract-all` 顺序执行 PyMuPDF → Camelot → Unstructured → Docling，控制峰值内存，不表示工具质量优先级。Docling 文档 JSON 保留非表格内容、阅读顺序和层级；本实验只把其中表格纳入候选管道。

### 2.3 统一候选的数据要求（EX-03）

每张工具表对应一个 TableCandidate，包含身份、工具/策略、原始引用、全部页面区域、行列维度、cell 列表、几何边界、未覆盖位置及诊断。每个 TableCell 表达一个物理格或合并格，包含文本、零基且终点排他的行列范围、跨度、跨度证据、可用 bbox 和原始引用。完整字段及角色枚举统一定义在架构数据模型章节。

合法空格、未知文本、合并延续位置和未覆盖位置必须区分；不能把合并文本复制到每个网格位置。未提供的 bbox、跨度、表头角色不得伪造。几何或跨度不可用时保留原始文本和可读事实，明确诊断，不能静默丢表或丢 cell。

### 2.4 各工具结构恢复要求

| 工具 | 原生跨度证据 | 恢复规则 | cell bbox 使用限制 |
| --- | --- | --- | --- |
| PyMuPDF | 没有本管道可直接使用的数值 rowspan/colspan；有物理 cell 区域 | 以边界聚类与映射推算跨度，标 geometry_inferred | 工具识别的格区域；text 策略可能推断虚拟边界，不视为 PDF 真实线框证明 |
| Camelot | 原子格与四边状态；hspan/vspan 是布尔值 | 双侧缺共享边连通，仅矩形分量恢复合并，标 edge_inferred | 原子网格区域；合并 bbox 为组成格联合，不是文本 bbox |
| Docling | 原生 span 与排他 offsets | 优先使用原生逻辑结构，矛盾时保留原值并标 unavailable | 可选模型/匹配结果；不得拿文本对齐区域重推逻辑跨度 |
| Unstructured | text_as_html 中 rowspan/colspan（缺省 1） | 按 HTML 行序与占位恢复逻辑格，标 native | 现有实验 raw 没有可用 cell bbox；整表坐标不能分摊伪造到格 |

PyMuPDF 的边界聚类和映射容差为 2.0 pt；退化区间、重叠格和原始矩阵维度不一致必须诊断。Camelot 的合并格内重复完整文本只保留一次：已确认同一合并格的 xyz/xyz 输出 xyz；不同格的同值及单格内部重复词仍保留。不同片段按原子行列顺序合并，原始组成格必须可追踪。

Docling 的跨页来源完整保留；缺 cell-to-page 关系时不猜 cell 页码。Unstructured 缺 HTML 或非法 span 时保留可读取文本事实，不伪造一列结构。各工具算法、异常处置和原始引用规则见架构，版本与 bbox 证据见 [工具调研](tool-findings.md)。

### 2.5 坐标、来源与精度

公共空间为 `pymupdf_page_top_left_pt_v1`：原 PDF 有效页面、左上原点、向右/向下、单位 pt。所有参与计算的 table/cell bbox 必须完成转换，并能追踪原坐标与实际变换。旋转、裁剪或单位未知时不得直接当 identity。

页面越界容差统一 **0.000001 pt**：仅容差内允许裁剪；更大越界标无效。页面尺寸与权威 PDF 每方向相差最多 1.0 pt。跨度聚类容差 2.0 pt、覆盖比例 epsilon 1e-12 分别有独立用途，不互相替代。

### 2.6 按页失败隔离（EX-04）

某策略某页失败，继续该策略下一页；成功页候选正常进入下游，不能因策略中一页失败而整体丢弃。某工具失败也不能阻止其他工具尝试。初始化失败表示未进入逐页执行；页面已执行失败、成功零表、成功有表必须可区分。错误、配置和页码应持久保存。

成功零表仍输出空 tables 和有效文件路径，不能把零表等同缺文件。Docling 全部成功且零表时，后续为零 slot、零 group、零 winner。Docling 部分页失败时，成功页正常处理；失败页缺少可靠卡位来源，其候选延后，不得误写为“没有表格”。页级状态与原因归属见架构。

### 2.7 输出与验收

输出 raw、normalized JSON、CSV/HTML 和运行摘要。JSON 是下游计算输入，CSV/HTML 是派生阅读视图。验收必须覆盖九策略配置、合并与空格、异常跨度、坐标转换、成功零表及“失败页之后仍有成功页”；成功页候选不能从报告中消失。

## 3. 候选准入

### 3.1 目标、输入与数据要求（SEL-01）

准入回答候选是否能唯一归属一个 Docling 卡位。输入是原 PDF、可验证的 Docling raw/normalized、各工具候选及执行事实；输出 TableSlot、CandidateView、SlotMatchEvaluation 和 CandidateAdmissionResult。此阶段只判断同页几何关系，不评价 cell 文本或表格质量。

Docling raw TableItem 是唯一卡位来源，normalized 则提供其候选，两者不可替代。权威文件缺失、损坏或来源不一致属于加载失败；缺可选工具/策略应记录 warning 后使用已提供的候选。按页可用性按 §2.6 处理。

### 3.2 卡位建立与资格

#### 卡位身份与数据含义

Docling `document.json` 中每个原生 `TableItem` 对应一个 `TableSlot`：


`slot_id` 按 `document.json.tables` 中的原生顺序稳定生成：`slot_{document_table_index:03d}`，索引从 1 开始。该 ID 不依赖卡位是否具有有效页码或 bbox；相同输入重复运行必须得到相同 ID。

`docling_table_ref` 必须指向 `raw/document.json` 中的原生 `TableItem`，使最终结果可以准确回填原文档结构。

#### 资格与延后原因

满足下列全部条件时，`TableSlot.status="eligible"`：

1. TableItem 的全部 provenance 对应同一个有效页面；
2. 每个 provenance 都具有可转换到公共坐标系的表级 bbox；
3. 页面宽高为有限正数；
4. bbox 四个坐标均为有限数，且 `x1 > x0`、`y1 > y0`。

其余卡位为 `deferred`，`deferred_reason` 只能为：

- `cross_page_slot`：TableItem 的 provenance 涉及多个不同页面；
- `missing_slot_provenance`：没有可用 provenance；
- `missing_slot_bbox`：provenance 缺少 bbox；
- `slot_coordinate_conversion_failed`：来源 bbox 无法可靠转换到公共坐标系；
- `invalid_slot_bbox`：转换后 bbox 非有限、面积不为正或超出页面有效区域；
- `invalid_slot_page_geometry`：页码、页面宽度或页面高度无效。

同页存在多条 provenance 时，分别完成坐标转换，再取能够包含全部 provenance bbox 的最小外接矩形作为 slot bbox。坐标只允许存在不超过 `COORD_EPSILON_PT = 1e-6` 的浮点误差；在该范围内越过页面边界时裁剪到页面有效范围，超过该范围则按 `invalid_slot_bbox` 延后。

deferred 卡位保留在报告中，但不接纳候选，也不进入 scoring。最终整合阶段必须保留其原生 Docling `TableItem`，不能因本流程无法处理而删除内容。

#### 卡位区域含义

卡位使用 Docling `TableItem.prov` 的表级 bbox，而不是 cell bbox。表级 bbox 只回答“最终文档的这个位置存在一张表格”，不代表 Docling 的单元格恢复结果一定最好。

### 3.3 候选可比较性

每个输入 `TableCandidate` 必须先转换为轻量候选视图，尚不与任何卡位比较：

```text
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

候选只有在全部 regions 指向同一个页面，且页码、页面宽高、公共 bbox 均有效时，才是 `comparable`。同页存在多个 regions 时，取能够包含全部 region bbox 的最小外接矩形作为候选 bbox；不得只选取其中一个 region。候选页面宽高必须与原始 PDF 对应页分别相差不超过 `1.0 pt`。

转换后的候选边界只允许存在不超过 `COORD_EPSILON_PT = 1e-6` 的浮点误差；在该范围内越过页面边界时裁剪到页面有效范围，超过该范围时为 `deferred`。deferred 候选不得与任何卡位建立匹配评价，也不得进入 grouping 或 scoring。

`deferred_reason` 只能为：

- `cross_page_candidate`：候选 regions 涉及多个页面，或无法无歧义归并为单页候选；
- `missing_candidate_region`：没有可用 region；
- `missing_candidate_bbox`：单页 region 缺少 bbox；
- `candidate_coordinate_conversion_failed`：候选 bbox 未可靠转换到公共坐标系；
- `invalid_candidate_bbox`：bbox 非有限、面积不为正或超出页面有效区域；
- `invalid_candidate_page_geometry`：页码、页面宽度或页面高度无效，或与原始 PDF 对应页分别相差超过 `1.0 pt`。

deferred 表示证据不足、无法作出准入判断，不等同于已经证明该内容不是表格。

### 3.4 同页双向覆盖评价（SEL-02）

#### 可创建评价的条件

只为同时满足以下条件的组合创建 `SlotMatchEvaluation`：

- candidate 为 `comparable`；
- slot 为 `eligible`；
- candidate 与 slot 的 `page_number` 相同。

不同页面的组合不创建虚假的 rejected 评价。没有同页 eligible slot 的事实由候选级准入结果表达。

#### 双向覆盖率

设候选 bbox 为 `C`，卡位 bbox 为 `S`，面积函数为 `area`，交集为 `C ∩ S`：

```text
candidate_coverage = area(C ∩ S) / area(C)
slot_coverage      = area(C ∩ S) / area(S)
iou                = area(C ∩ S) / area(C ∪ S)
```

- `candidate_coverage` 低，表示候选吞入了较多卡位之外的内容，可识别整页误检或多表合并；
- `slot_coverage` 低，表示候选只恢复了卡位的一部分，可识别碎片表；
- 只有两者同时较高，才能说明候选与卡位在范围上互相完整覆盖；
- IoU 只作为审计指标，不单独决定准入。

#### 数值精度

匹配时不扩张 candidate 或 slot bbox，也不计算容差覆盖率。双向覆盖率阈值本身承担边界差异的容错职责。

实现可以使用 `RATIO_EPSILON = 1e-12` 处理浮点比较，例如以 `coverage + RATIO_EPSILON >= threshold` 判断是否达到阈值。该 epsilon 只防止理论相等值因浮点表示误差而失败，不能补偿真实 bbox 偏移。

#### 已校准准入阈值

基于当前四份实验 PDF 的 109 条人工标签，最终确认本阶段统一阈值为：

```text
MIN_CANDIDATE_COVERAGE = 0.65
MIN_SLOT_COVERAGE = 0.77
```

当且仅当：

```text
candidate_coverage + RATIO_EPSILON >= 0.65
and slot_coverage + RATIO_EPSILON >= 0.77
```

该 candidate-slot 评价为 `decision="accepted"`，原因码唯一为 `bidirectional_coverage_passed`。否则为 `decision="rejected"`，原因码必须逐项列出：

- `candidate_coverage_below_minimum`；
- `slot_coverage_below_minimum`。

两个原因可以同时出现。阈值必须集中配置，后续只能依据带人工标签的样本校准，不得因为某个工具或策略而使用不同阈值。

阈值的样本、枚举方法、不可完全分离案例、精确率与召回率取舍以及稳定区间，记录在 `calibration/admission-thresholds.md`。人工标注和逐候选覆盖率保存在 `../data/calibration/admission-labels.md`。需求文档只规定当前生效值，不重复保存推导过程。

Docling 自身规范化得到的 `TableCandidate` 也必须按同一公式匹配，不得因其同时是卡位来源而无条件准入。Table Slot 只确认位置和内容类型，不能替代对 Docling 候选边界及后续恢复质量的检查。

#### 评价范围


本阶段不计算中心点距离、宽高比、边界差值或文本相似度。这些指标用于无外部锚点时推测两个候选是否同表；当前已有 Docling Table Slot 作为明确锚点，继续使用会增加规则复杂度而不改变核心判断。

### 3.5 候选级决定与边界

每个输入 candidate 都必须产生一条 `CandidateAdmissionResult`，保留实际比较对象与唯一决定。


状态和决策按固定顺序推导：

1. CandidateView 为 deferred：`processing_status="deferred"`，沿用其 `deferred_reason`；其余决策字段为 `null` 或空列表。
2. candidate 所在页面没有 eligible slot：`completed + rejected`，原因 `no_eligible_table_slot_on_page`。
3. 存在同页评价，但没有 accepted 评价：`completed + rejected`，原因 `no_table_slot_passed_bidirectional_coverage`。
4. 恰有一个 accepted 评价：`completed + admitted`，`matched_slot_id` 为对应 slot，原因 `matched_single_table_slot`。
5. 有两个或更多 accepted 评价：`completed + rejected`，原因 `multiple_table_slots_passed_bidirectional_coverage`。不得自动选择最高分卡位，也不得把同一 candidate 放入多个组。

`evaluated_slot_ids` 保存实际比较过的全部卡位；`accepted_slot_ids` 保存通过双向覆盖阈值的卡位。这样可以直接追溯“没有卡位”“比较后失败”和“同时命中多个卡位”三种不同事实。

rejected 和 deferred candidate 必须保留在报告中，但不得进入 grouping、scoring 或本实验的下游候选选择。

页级前置例外：几何检查之后、创建任何比较之前，若 Docling 该页失败，准入结果为 deferred，原因 `slot_source_page_failed`，不执行“无同页卡位则拒绝”的分支。该规则落实本轮确认的按页继续要求，由 selection v2 报告及页级提取记录实现。

### 3.6 输出与验收

每个已加载候选有且只有一份几何视图和准入结论；每个实际比较有一份评价；无比较不得伪造 rejected pair。验收覆盖阈值等于/略低、零/一/多卡位通过、跨页、无坐标、Docling 页失败，以及所有结论到输入与指标的追溯。

## 4. 候选分组

### 4.1 目标、输入与数据要求（SEL-03）

输入为全部 TableSlot 与准入结论，输出 TableGroup 和包含全链路准入事实的 GroupingReport。分组只按唯一 matched_slot_id 聚合，不重新计算候选之间的相似关系。

### 4.2 分组规则

grouping 不再进行 candidate 与 candidate 的两两匹配。每个 TableSlot 固定对应一个 TableGroup，所有 `admission_decision="admitted"` 且 `matched_slot_id` 相同的候选直接成为该组成员。

因此必须满足以下不变量：

- 一个 candidate 最多属于一个 group；
- 一个 group 只对应一个 slot；
- 不同页面的 candidate 永远不能进入同一 group；
- 单候选组是正常且完整的分组结果；
- 同一工具或同一策略的多个 candidate 如果都独立通过该 slot 的准入条件，可以同时留在组内，不能仅因来源相同提前删除；
- rejected 或 deferred candidate 不得出现在任何组的成员列表中。

### 4.3 组身份与状态


`group_id` 按 slot 的稳定顺序生成 `group_001`、`group_002`。它只是输出层的连续标识；`slot_id` 才是表格在 Docling 文档结构中的稳定身份。

状态规则：

- `ready_for_scoring`：slot 为 eligible，且至少有一个 admitted candidate；`unresolved_reason=null`。成员数为 1 或更多不影响状态；
- `unresolved`：slot 为 deferred，或 eligible slot 没有 admitted candidate。

`unresolved_reason` 只能为：

- TableSlot 的六种 `deferred_reason`，表示卡位本身无法处理；
- `no_admitted_candidate`，表示卡位有效，但没有候选通过准入过滤。

不再保留旧版字段或语义（仅说明删除边界，不是有效枚举）：

- `matched/unmatched`：候选数量不再代表分组是否成功；
- `unmatched_reason`：由 `status/unresolved_reason` 取代；
- `representative_bbox`：由权威 `slot_bbox` 取代，不再计算成员 bbox 中位数；
- `accepted_relation_ids` 和 `pair_evaluations`：新版不存在候选对关系。

### 4.4 排序稳定性

具有有效页面和 bbox 的 TableSlot 与 TableGroup 依次按页面、slot bbox 的 `y0`、`x0`、原生 TableItem 顺序排列；缺少页面或 bbox 的 deferred slot 统一排在其后，并按原生 TableItem 顺序排列。组内 candidate 按 `tool`、`strategy`、`candidate_id` 升序排列。排序只保证输出稳定，不表示工具优先级或候选质量。

### 4.5 输出与验收

每个 slot 恰好一组，包括空组和 deferred slot；每个 admitted candidate 恰在其对应组；其他候选仅留在审计报告。单成员组可以评分。验收检查成员集合、不变量、稳定顺序、空组原因以及一组一 slot 的原生引用。

## 5. 评分与选优

### 5.1 目标、输入与数据要求（SCORE-01–03）

输入为 ready group、所有成员的完整 TableCandidate 和原 PDF 数字文本；输出参照 token、候选 token、四项原始指标、逐指标两两战绩、相对分和唯一选择结果。完整模型在架构统一定义。不能静默删除一个已准入成员来完成评分。

### 5.2 职责与不变量

scoring 只接收 `status="ready_for_scoring"` 的组及其 admitted members，并回答：

> 对于 Docling 已确认存在的这张表，哪个候选对表格文本和结构的恢复效果相对最好？

必须满足以下不变量：

- scoring 不再判断候选区域是不是表格、列表、正文、页眉或整页误检；这些内容类型问题已经由 Table Slot 准入边界处理；
- rejected、deferred candidate 和 `status="unresolved"` 的组不得参与评分；
- candidate 只与同组成员比较，不得跨 slot 比较、拼接或借用其他组的名次；
- 不设置总分门槛、单项门槛或评分后的 rejected 状态；每个 ready group 必须选出一个且仅一个 winner；
- 工具名称和策略名称不得进入四项指标或总分，只能在最终同分时按 11.9 节执行确定性选择；
- scoring 必须读取统一 `TableCandidate/TableCell`，评价统一规范化结果，不评价工具内部未进入公共模型的临时对象；
- 同一工具的不同策略、同一策略产生的不同 candidate 均作为独立成员参与共识统计和组内比较，不做工具内投票汇总；
- 单成员组仍计算全部可计算的原始指标；因为没有对手，其四项相对分按 11.8 节约定处理并直接成为 winner；
- 所有原始指标、中间计数、相对分、总分、并列集合和最终选择依据都必须保留，以支持复核和调试。

第一版只使用以下四项指标，每项权重均为 `25%`：

| 维度 | 指标 | 原始比较值 | 越优方向 | 权重 |
|---|---|---|---|---:|
| 文本质量 | 文本双向覆盖率 | `text_f1` | 越高越好 | 25% |
| 文本质量 | 关键值词完整度 | `critical_token_integrity` | 越高越好 | 25% |
| 结构质量 | 网格形状共识度 | `shape_support` | 越高越好 | 25% |
| 结构质量 | 空白网格异常度 | `blank_anomaly` | 越低越好 | 25% |

文本质量和结构质量各占总分 `50%`。关键值词完整度有意再次强调高业务价值文本；空白网格异常度有意再次强调过度切分和未恢复合并单元格造成的结构碎片。

### 5.3 文本评分参照与统一 token

文本评分以原始 PDF 数字文本层为参照，不使用任何候选工具推断出的行、列、cell 或表格文本作为标准答案。PyMuPDF 只承担底层 PDF word 对象读取，不得调用 `find_tables()` 为文本评分提供参考结构。

设 `R` 为当前 Table Slot bbox 内的原生 PDF word token 多重集，`C(c)` 为 candidate `c` 的 token 多重集：

- 每个原生 PDF word 对象是 `R` 的一个最小 token，不得为了提高匹配率把它拆成字符，也不得与相邻 word 自动拼接；
- candidate 必须遍历其物理 `TableCell`；每个物理 cell 的 `text` 只读取一次，合并单元格覆盖的其他逻辑位置不得重复文本；
- reference word 和 candidate cell text 必须先经过同一套文本规范化；candidate 再按 Unicode 空白字符切分为 token，不同 cell 的 token 不得跨 cell 拼接；
- 通用规范化包括 Unicode NFKC、去除首尾空白和大小写折叠；原始文本仍须保留用于审计；
- 当且仅当撇号型字符位于两个十进制数字之间时，`'`、`‘`、`’`、`ʼ`、`´` 及其 NFKC 产生的组合重音形式统一为 ASCII `'`，并移除该分隔符两侧不含换行的噪声空白；例如 `1´ 131,056.00` 与 `1’131,056.00` 统一为 `1'131,056.00`；
- 除上述有上下文约束的等价字符外，标点、数字、小数点、千位分隔符、连字符和字母数字组合必须保留，不得删除字符或交换逗号、小数点等可能改变语义的符号；
- `R` 和 `C(c)` 都是多重集，同一 token 出现多次必须保留次数，不能退化为普通集合。

原生 word 是否位于 Slot 内的几何归属规则、word 读取顺序和规范化函数必须在 scoring 架构文档中集中定义，所有 candidate 共用同一份 `R`，不得按工具或策略变化。

### 5.4 文本双向覆盖率

该指标判断 candidate 是否漏掉 Slot 中的文本，或者加入原始 Slot 文本中不存在的内容。

对于每种规范化 token `t`，其匹配次数取两个多重集计数的较小值：

```text
matched_text_count(c)
  = Σ_t min(count_R(t), count_C(c)(t))
```

设该值为 `M`，则：

```text
text_recall    = M / |R|
text_precision = M / |C(c)|
text_f1        = 2 × text_precision × text_recall
                 / (text_precision + text_recall)
```

- `text_recall` 低表示 candidate 漏掉原始表格文本；
- `text_precision` 低表示 candidate 加入了原始 Slot 中不存在的 token，或把原始完整 word 切成了无法匹配的碎片；
- `text_f1` 是本指标参与组内排序的唯一原始比较值；precision、recall、reference/candidate/matched token count 和未匹配 token 必须同时保留供审计。

边界情况：

- `R` 非空、`C(c)` 为空时，precision、recall、F1 均为 `0`；
- 整个组的 `R` 为空时，该指标为 `not_evaluable`，所有组成员在本指标上视为并列，不得把“没有参考文本”记作满质量；
- `R` 非空而某 candidate 的文本无法读取时，该 candidate 的本指标按 `0` 处理，并记录原因。

### 5.5 关键值词完整度

该指标判断原始 PDF 中具有高业务价值的完整 word 是否在 candidate 最终文本中仍作为一个完整 token 存在。它关注“是否切碎或缺失”，不要求关键值位于特定物理单元格。

第一版将 `R` 中**至少包含一个 Unicode 十进制数字**的 token 定义为关键值 token，多重集记为 `K`。该定义统一覆盖金额、数量、日期、百分比、型号、订单号及其他含数字标识；不使用 LLM 或工具私有类型判断。后续扩展类型时必须修改需求并用实际样本验证，不得在代码中静默增加词表。

匹配只允许使用 candidate 中一个完整的规范化 token。不得连接同一 cell 或不同 cell 中的多个碎片来恢复匹配。例如原生关键 token 为 `500` 时，以下结果均不完整：

```text
同一 cell 中的两个 token：5  00
两个 cell 中的两个 token：5 | 00
```

如果统一 `TableCell.text` 最终已经恢复为单个 token `500`，则视为完整；工具内部曾经如何分段不参与评分。

数字撇号分隔符的等价字形和其紧邻噪声空格在 token 切分前完成规范化，不属于把多个普通数字 token 拼接起来。`1´ 131` 可以规范化为 `1'131`，但缺少明确分隔符的 `5 00` 仍必须保留为两个 token，不能恢复成 `500`。

关键值同样按多重集匹配：

```text
matched_critical_count(c)
  = Σ_t min(count_K(t), count_C(c)(t))

critical_token_integrity(c)
  = matched_critical_count(c) / |K|
```

- 该值越高越好；
- 一个原始关键 token 只能抵消 candidate 中一个相同 token，重复值不能互相冒充；
- `K` 为空时，该指标为 `not_applicable`，所有组成员在本指标上视为并列，不得记作候选自身的质量优势；
- 该指标与文本双向覆盖率的重叠是有意设计，用于提高关键业务值错误对最终排序的影响。

### 5.6 网格形状共识度

第一版不宣称在没有结构真值时计算“行列准确率”，而是计算 candidate 级网格形状共识。candidate `c` 的网格形状是有序二元组：

```text
shape(c) = (row_count, column_count)
```

不得使用 `row_count × column_count` 的乘积代替二元组；`(3, 4)` 与 `(4, 3)` 是不同形状。

设组内成员总数为 `N`：

```text
shape_support(c)
  = count(d in group where shape(d) == shape(c)) / N
```

- 每个 candidate 独立贡献一次支持；PyMuPDF、Camelot 等同一工具的不同策略不做内部合并或降权；
- 具有相同 shape 的 candidate 获得相同原始值；
- `shape_support` 越高，表示该网格形状得到越多工具/策略候选支持；
- `row_count` 或 `column_count` 缺失、非整数或不为正时，该 candidate 的 `shape_support=0` 并记录结构异常，但 candidate 不被硬筛除；
- 单成员组中有效 shape 的支持度为 `1`。

第一版不比较行列边界位置、cell bbox 对齐程度或 span 布局共识。因此，两张行列数量相同但边界位置不同的候选在本指标上无法区分。这是明确接受的版本边界，不得由编码者自行加入边界评分。

### 5.7 空白网格异常度

该指标根据当前实验样本中“低质量候选通常因多余行列或合并失败而产生更多空白位置”的事实，评价 candidate 的空白率相对于组内参照值增加了多少。当前四份校准 PDF 中几乎没有出现将多个单元格错误合并为非空大单元格的情况，因此第一版在缺乏明确空白率共识时以组内最小值作为参照。

先使用 `row_count`、`column_count` 和物理 `TableCell` 的 offset/span 重建逻辑网格。对于每个 `text` 非空的物理 cell，其完整 `row_span × col_span` 覆盖范围都视为非空覆盖；合并单元格的锚点之外位置不得被错误计为空白。多个 cell 重叠时按逻辑位置并集计数，不得重复。

```text
logical_position_count(c) = row_count × column_count

nonempty_covered_position_count(c)
  = 非空物理 cell 的有效跨度所覆盖逻辑位置的并集大小

blank_ratio(c)
  = 1 - nonempty_covered_position_count(c)
          / logical_position_count(c)
```

统计组内所有可计算的 `blank_ratio`，并按以下固定规则确定参照值：

1. 使用 `(blank_position_count, logical_position_count)` 交叉相乘判断两个空白率是否相等，并据此统计频次；不得对浮点结果四舍五入后再统计；
2. 如果只有一个空白率具有最高出现次数，且出现次数至少为 `2`，该值是明确且唯一的众数，取其为 `reference_blank_ratio`；
3. 如果所有值都只出现一次，或者有两个及以上并列众数，则不存在明确且唯一的众数，取全部可计算空白率中的最小值为 `reference_blank_ratio`。

```text
reference_blank_ratio
  = unique_mode(blank_ratio), if a unique repeated mode exists
  = min(blank_ratio), otherwise

blank_anomaly(c)
  = max(0, blank_ratio(c) - reference_blank_ratio)
```

- `blank_anomaly` 越低越好；
- `blank_ratio` 低于或等于参照值时，`blank_anomaly=0`，这些 candidate 在该指标上并列，不因空白更少而继续加分；
- 多个 candidate 具有相同空白率时，唯一众数表达多工具、多策略候选在空白程度上的直接共识；
- 没有明确唯一众数时，最小值基线使异常增加的空白位置具有稳定排序能力；
- 合法业务空值如果在多个合理候选中一致出现，会通过众数参照降低误伤；
- 多余行列、合并单元格未恢复或过度切分造成的异常空白膨胀会提高该值；
- 网格维度无效、cell offset/span 无法建立任何有效逻辑网格时，本指标为 `not_evaluable` 并记录原因，不得伪造空白率。

如果整个组都无法计算空白率，所有成员在本指标上视为并列。如果只有部分成员不可计算，可计算成员优于不可计算成员；不可计算本身不删除 candidate。

“唯一众数优先，否则最小值”是基于当前四份 PDF 的 V1 经验规则。后续加入更多人工审阅样本后，如果错误过度合并开始成为常见失败类型，必须重新评估该参照规则，不得在未记录依据的情况下静默改变实现。

### 5.8 原始指标与排序方向

四项指标必须先产生原始事实，再执行相对赋分：

```text
text_f1                   higher_is_better
critical_token_integrity  higher_is_better
shape_support             higher_is_better
blank_anomaly             lower_is_better
```

组内选优只使用这些方向，不使用旧版结构完整性、主体行稳定性、表头质量、列语义一致性、几何合理性、连续文本跨列切分率或工具私有置信度。旧版指标即使仍存在于代码中，也不得被新 scoring runner 间接调用。

### 5.9 组内两两胜率赋分与总分

每个指标分别在同一 group 内做 candidate 两两比较：

- 按该指标方向更优：本次比较得 `1`；
- 原始值相等，或双方在该指标上均不可评价/不适用：各得 `0.5`；
- 原始值更差：得 `0`；
- 仅一方不可评价时，可评价的一方得 `1`，不可评价的一方得 `0`。

设 candidate `c` 在指标 `m` 上与其他成员比较得到的胜、平次数为 `wins_m(c)`、`ties_m(c)`，组内成员数为 `N`：

```text
relative_score_m(c)
  = (wins_m(c) + 0.5 × ties_m(c)) / (N - 1)
```

数值范围为 `[0,1]`。指标比较和总分同分判断统一使用 `SCORE_EPSILON = 1e-12` 的绝对容差：`abs(a-b) <= SCORE_EPSILON` 时视为相等，否则按指标方向判定胜负。该容差只吸收浮点表示尾差，不能把真实分数差距调整为并列。所有由计数产生的指标必须保留其原始业务计数，使浮点比例可以复算和审计。

单成员组不存在对手。其四项 `relative_score` 按约定记为 `1.0`，但该值只表示“组内没有更差排名”，不得解释为候选绝对质量满分；原始指标仍必须正常计算和输出。

最终总分为：

```text
total_score(c)
  = 0.25 × relative_text_f1
  + 0.25 × relative_critical_token_integrity
  + 0.25 × relative_shape_support
  + 0.25 × relative_blank_anomaly
```

总分只用于同一 group 内排序。不同 group 的总分不具有横向质量可比性，禁止用它们对不同 Table Slot 排名或设置统一质量门槛。

该方法有意采用序数判断：`1.00` 对 `0.99` 仍是一次完整胜出，不根据差距大小缩放胜负分。为避免序数结果掩盖原始差距，报告必须同时输出所有原始值和计数。

### 5.10 winner 与同分规则

每个 ready group 必须按以下固定顺序确定唯一 winner：

1. 选择 `total_score` 最高的 candidate；
2. 多个 candidate 总分在 `SCORE_EPSILON` 容差内相同时，按工具优先级选择：`pymupdf > camelot > docling > unstructured`；
3. 同一工具仍有多个同分 candidate 时，按 `candidate_id` 升序选择第一个。

工具优先级只用于总分在容差内相同后的确定性消歧，不增加任何指标值或总分。不得随机选择 winner；相同输入重复运行必须得到相同结果。

结果必须记录：

- `selected_candidate_id`；
- 最高总分及全部并列最高 candidate；
- 是否触发工具优先级或 `candidate_id` 消歧；
- 最终选择原因：`highest_total_score`、`tool_priority_tiebreak` 或 `candidate_id_tiebreak`。

### 5.11 必须保留的评分事实

scoring 的逻辑输出至少必须使以下事实可追溯；具体数据类和文件结构见 [架构](architecture.md)：

- `group_id`、`slot_id`、参与评分的全部 `candidate_id`；
- 文本参照的来源、原始 word 数、规范化 token 多重集或可审计等价表示；
- 每个 candidate 的 candidate token 数、文本匹配数、precision、recall、F1 和未匹配 token；
- 关键值 token 总数、逐 token 匹配结果、匹配数和完整度；
- row count、column count、shape、同 shape candidate 数及支持度；
- 逻辑网格位置数、非空覆盖位置数、blank ratio、各空白率频次、参照值来源（唯一众数或最小值）、reference blank ratio 和 blank anomaly；
- 四项两两比较的胜、平、负计数和四项 relative score；
- 四项权重、total score、并列最高集合、winner 和选择原因；
- 指标不可评价、公共模型异常和源事实缺失的 warning/reason。

### 5.12 评分人工审核视图

每个 ready group 生成包含全部候选的独立 HTML，保留 winner 标记、原始指标和相对分。展示与排序契约见 [验收与辅助审核](validation.md#人工审核视图)。

### 5.13 输出与验收

每个 ready group 恰有一个 winner，unresolved group 不进入评分。分数必须从整数计数与 pair 战绩复算；不提前舍入，不跨组比较。验收覆盖重复词、多种数字撇号、空参照、非法网格、span 覆盖、众数并列、单成员及全部同分。

## 6. 全链路运行与结果管理

### 6.1 命令与依赖补齐（EX-05）

单工具、extract-all、grouping、scoring 均显式指定 PDF；同一 PDF 重跑保留最新结果，不创建常态时间戳目录。目录和完整参数见 [架构运行接口](architecture.md#7-全链路编排与运行接口)。

- grouping 缺上游时列出问题并询问是否补齐。Y 执行 extract-all；N、空输入、EOF 不执行补齐。只要 Docling 可用，缺可选工具可明确告警后继续；Docling 不可用则停止。
- scoring 缺提取或默认分组产物时询问是否补齐。Y 按需执行 extract-all → grouping → scoring，单次确认覆盖已说明的动作；N、空输入、EOF 停止评分。
- 补齐中可选工具失败不阻止后续；Docling 或 grouping 硬前置失败才停止。提取重建后必须重建 grouping，避免旧组引用被覆盖的候选。
- 无效回答重新询问；提示说明覆盖对应工具旧产物。CLI 可以编排补齐，内部 scoring runner 不得自行重新准入、修改组成员或偷偷回退旧评分。

### 6.2 身份、重复运行与失败结果

运行须明确输入 PDF、工具/版本、有效策略配置和产物位置。同一输入重跑使用稳定排序，不常态生成时间戳目录。旧产物不得在输入未通过校验时提前删除；失败记录与可用成功页结果必须区分。按页存在失败时 extract-all 汇总非零退出，成功页仍可由 grouping 加载。

新提取 manifest 记录源 PDF 的规范路径与 SHA-256、工具版本/配置身份及计算用 JSON 的 SHA-256；分组记录源文件、提取输入集合和报告内容身份。来源或内容变化必须拒绝复用，配置变化触发 CLI 补齐检查。历史提取文件只以显式 legacy warning 读取，不补造页级事实。相同 stem 仍共享一个输出位置，来源校验防止串用，不提供多版本缓存服务。

### 6.3 可审计性（AUDIT-01）

`groups.json` 是新版 `GroupingReport` 的规范化 JSON：


每个输入 candidate 必须恰好出现在一个 `CandidateView` 和一个 `CandidateAdmissionResult` 中。每个实际发生的 candidate-slot 比较必须恰好产生一个 `SlotMatchEvaluation`。每个 TableSlot 必须恰好对应一个 TableGroup，即使该组 unresolved 或没有成员。

`manifest.json` 只保存运行摘要，至少包括：输入 PDF、Docling 卡位数、各工具候选加载数、comparable/deferred 数、admitted/rejected 数、ready/unresolved 组数、warning、耗时和输出路径。它不替代 `GroupingReport`。

输出路径、完整目录和各 manifest 的字段职责统一见架构。JSON 保留原始精度、稳定数组顺序及原因；HTML 只格式化显示。缺整个工具、缺策略、页失败、候选延后、准入拒绝、组未评分、指标不可评价是不同事实，禁止用统一 failed 状态覆盖。

## 7. 辅助功能

### 7.1 结构视图与人工审核（EX-06）

normalized CSV 用于二维文本阅读，HTML 用于跨度检查。group/score 视图是当前实验的必要辅助输出，帮助核对候选归属、指标和 winner，不参与决策。评分显示保留两位小数，JSON 保留完整精度；详细布局与异常显示见 [验收及辅助功能](validation.md)。

### 7.2 阈值评定

人工标签用于离线比较准入阈值，复算覆盖率和误判；校准命令不自动修改执行阈值、不修改人工正误判断。当前使用 candidate_coverage≥0.65、slot_coverage≥0.77；详细证据独立保存在 [校准报告](calibration/admission-thresholds.md)，不混入主链路实现。

## 8. 整体边界与验收

### 8.1 非目标

- 不把 Unstructured 的 `Table` 与 Docling `TableItem` 合并为联合卡位来源；
- 不使用候选之间的 IoU、文本相似度或图连通关系发现新的表格组；
- 不让未匹配 Docling 卡位的候选进入 scoring；
- 不处理跨页表格拼接和跨页 cell-to-page 推断；
- 不修正 Docling 对内容类型的误判，也不补回 Docling 漏识别的表格；
- 不在过滤或分组阶段评价单元格结构和文本恢复质量；
- scoring 不设置候选质量淘汰门槛，也不因总分较低拒绝填充 ready Table Slot；
- 第一版行列结构共识只比较 `(row_count, column_count)`，不比较行列边界位置；
- 第一版不使用 LLM 判断文本语义完整性，不自行重建第五套标准表格；
- 不跨 group 比较候选分数、候选质量或 winner；
- 不在本阶段接入 `rag/`、chunking 或向量化。

### 8.2 端到端与 docs-as-code

验收链为 PDF → raw → cell 级候选 → 准入评价 → slot 组 → 原始指标 → pair 分 → winner → 审核引用。成功零表、部分页失败、无可评分组和正常多工具竞争都应有可解释输出。

需求规则 ID、架构模型/算法和验收场景必须同步维护；行为变化同一次变更更新三者。工具版本变化先复核原始输出假设，再调整适配与测试。历史审查、校准样本和当前规格分开保存；代码不符合规格时明确记录问题及对当前能力的影响，不能通过删除有效需求掩盖问题。


### 8.3 架构与目录验收约束（ARCH-01、PATH-01）

实验能力内部采用四层职责与向内依赖，完整契约见架构 §9。准入、分组和评分应能脱离第三方工具与文件系统独立验证；应用层通过可替换契约使用提取、PDF 参照读取和产物存取。四工具专属恢复逻辑仍属于适配器，不以纯函数为由混入公共领域规则。

提取产物统一迁移到 `experiments/output/extracting/<tool>/<PDF stem>/`，与 grouping/scoring/calibration 阶段并列。校准说明放 docs/calibration，人工标签放 data/calibration/admission-labels.md，派生图放 output/calibration/admission-coverage-chart.html。人工数据纳入版本管理，复算只更新派生列，不修改人工判断。

保持现有模块命令入口；统一 --output-dir 为所属命令输出父目录并追加 PDF stem。迁移须同步默认路径、依赖补齐、读写链路、测试、标签及 HTML 引用；不得静默回退旧目录而混用新旧输入。目录变更本身不得改变候选、准入、分组、指标和 winner。
