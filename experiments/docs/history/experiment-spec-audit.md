# 实验现状与规格对照清单

审查日期：2026-09-08。状态：历史审查快照，用于回溯调查依据，不是当前有效需求。整理后的文档见 [实验入口](../../README.md)，当前实施差异见 [当前限制](../limitations.md)。下文原文件名保留作历史标识，链接已经更新到新归属。

本清单将审查当时的有效规则、实现偏差、遗留代码、验证缺口与未来 RAG 需求分开记录。本文中的问题编号和规则编号是审查索引，不新增运行时状态或原因码。已有需求未定义的修复细节，须在后续规格中补齐，不能凭本清单直接改变业务语义。

## 1. 审查范围与证据强度

- 梳理 `experiments/` 下 15 份既有 Markdown 文档的职责和相互替代关系；本文件不计入该数量。
- 对 `experiments/table_extraction/` 的 41 个 Python 文件建立职责清单，重点阅读工具规范化、坐标、准入、分组、评分、加载、导出与 CLI 的实现。
- 核对 `rag/` 中解析、模型、分块、embedding、索引与 manifest 的迁移边界，以及现有 `docs/` 和评估集。
- 上一轮审查执行了七个实验测试文件：44 passed in 0.77s。先前沙箱内运行因临时目录权限失败；获准在沙箱外重跑后通过。该结果是本次清单的测试基线，本轮未重复运行未发生变化的测试。
- 本轮以不写产物的函数级探针复核了坐标容差差异、Docling 异常 offset 清空、Camelot 重复文本合并三个行为，见 F-04、F-06、F-08。
- 核对已有六份 PDF 的 grouping/scoring JSON 格式和组数；没有重新执行四工具提取，也没有逐页人工验收所有表格、HTML 或原 PDF。
- 不读取 `.env` 凭证；不把 `.venv/`、缓存、模型文件、Git 内部文件作为项目源码逐一审查。不声称已对所有文件完成逐行审计。

证据分类：**已核实**指代码、文档或产物可直接支持；**已复现**指本轮最小输入验证了该行为；**待验证风险**指存在实现缺口，但尚未证明当前业务样本受影响；**建议**指尚未落实到正式规格的改造方向。

## 2. 当前有效链路与边界

```text
输入 PDF
  ├─ PyMuPDF：lines / lines_strict / text
  ├─ Camelot：lattice / stream / network / hybrid
  ├─ Unstructured：hi_res
  └─ Docling：default，保留完整原生文档
           ↓
各工具 raw → normalized TableCandidate
           ↓
Docling 原生 TableItem → TableSlot
           ↓
单页可处理性检查 → candidate-slot 双向覆盖准入
           ↓
按唯一 matched_slot_id 分组 → GroupingReport
           ↓
ready group → PDF word 文本参照 → 四项原始指标
           ↓
组内两两比较 → 相对分 → 唯一 winner → ScoringReport
```

当前主规范是[候选选优需求](../requirements.md)与[候选选优架构](../architecture.md)，辅以工具适配、坐标、CLI 和校准文档。不是简单采用“最后修改的文件”或“代码实际怎么做”。存在明确替代声明时使用新规则；没有明确替代依据的冲突单独列出。

多工具仅改善 Docling 已识别表格的恢复结果，不发现遗漏表格，不纠正 Docling 的内容类型误判。跨页 slot/candidate 延后，不执行跨页拼接。Docling 当前基线关闭 OCR；本轮不扩大扫描件支持范围。最终文档融合、结构分块、表格 embedding 文本化和正式 RAG 接入均尚未完成。

### 2.1 有效规则对照

| 编号 | 当前规则 | 规格依据 | 实现与测试证据 | 结论 |
| --- | --- | --- | --- | --- |
| R-01 | 四工具九种策略保留各自 raw 与统一候选 | 工具架构、CLI 架构 | 四工具 runner/normalizer/exporter；三种工具的 normalizer 测试 | 主链存在；失败隔离、异常保留有缺口 |
| R-02 | 公共坐标为 `pymupdf_page_top_left_pt_v1`，左上原点、pt | 坐标需求、选优需求 §4–6 | `coordinates.py`、四工具 normalizer、slot loader | 部分落实；见 F-04、F-05 |
| R-03 | 一个 Docling 原生 TableItem 对应一个稳定 slot；必须验证 Docling raw 与 normalized 基线 | 选优需求 §4–5 | `selection_slot_loader.py`、`validate_docling_baselines()`；grouping 测试 | 主路径有测试；零表文档存在 F-01 |
| R-04 | slot 分 eligible/deferred；candidate view 分 comparable/deferred；跨页和无可靠坐标不得参与比较 | 选优需求 §5–6 | slot/candidate loader；grouping 测试 | 主规则落实；不代表坐标输入边界已充分验证 |
| R-05 | 只创建同页可比较 candidate-slot 评价 | 选优需求 §7 | `evaluate_slot_matches()`；grouping 测试 | 已落实 |
| R-06 | candidate coverage ≥ 0.65 且 slot coverage ≥ 0.77；比例 epsilon 为 1e-12；IoU 仅审计 | 选优需求 §7、校准报告 | `selection_config.py`、`selection_admission.py`；校准与 grouping 测试 | 生效值一致；校准样本仍有限 |
| R-07 | 唯一匹配才 admitted；无同页 slot、零通过、多通过分别 rejected；deferred 不等于 rejected | 选优需求 §8 | `decide_candidate_admissions()`；grouping 测试 | 已落实 |
| R-08 | 一 slot 一 group；单成员可 ready；deferred slot 或无准入候选为 unresolved | 选优需求 §9 | `build_slot_groups()`、不变量校验；grouping 测试 | 已落实 |
| R-09 | scoring 只读取 ready group 的完整 admitted candidates | 选优需求 §11、架构评分输入章节 | `scoring_loader.py` | 有身份/成员校验；内容新鲜度不足，见 F-03 |
| R-10 | PDF 原生 word 中心落入 slot 闭区间才进入文本参照；候选文本按物理 cell 分词，不拼数字碎片 | 选优需求 §11 | `scoring_reference.py`；scoring 文本测试 | 分词有测试；真实 PDF word 取样集成覆盖不足 |
| R-11 | text F1 按 token 多重集；含 Unicode 十进制数字的完整 token 为关键值 | 选优需求 §11.3–4 | `scoring_metrics.py`；scoring 测试 | 已落实；上游去重风险见 F-08 |
| R-12 | shape 比较 `(row_count,column_count)`；每个候选投一票，同工具策略不降权 | 选优需求 §11.5 | `compute_shape_support()`；scoring 测试 | 已落实；不是行列准确率 |
| R-13 | 非空 cell 的全部跨度覆盖计为非空；空白参照优先唯一重复众数，否则最小值 | 选优需求 §11.6 | `compute_blank_ratio()`、`apply_blank_reference()`；scoring 测试 | 已落实；部分边界测试仍需补充 |
| R-14 | 四指标等权；按胜平负相对赋分；单成员相对分 1.0；无绝对质量门槛 | 选优需求 §11.7–8 | `scoring_config.py`、`scoring_ranking.py`；scoring 测试 | 已落实；禁止跨组比较总分 |
| R-15 | ready group 必须唯一 winner；同分按 pymupdf、camelot、docling、unstructured，再 candidate ID | 选优需求 §11.9 | `_select_winner()`、评分不变量；scoring 测试 | 已落实；工具顺序仅消歧 |
| R-16 | 分组与评分保存 JSON、manifest、人工 HTML；视图不改变决策 | 选优需求 §10、§11.10–11 | 两个 exporter；评分显示测试；现有产物 | 有产物；完整加载到导出的回归不足 |
| R-17 | 提取顺序执行、跨工具失败继续；缺依赖交互补齐；Docling 为硬依赖，其他工具可降级 | 外部工具需求、CLI 架构 | `cli_workflow.py`、`__main__.py`；CLI 测试 | 工具级已落实；策略级与前置检查有缺口 |
| R-18 | winner 按 Docling ref 回填；unresolved 保留原表；其余内容与阅读顺序保持 | 选优需求 §12 | 尚无正式融合模块 | 已定义的后续约束，不是已完成能力 |

### 2.2 状态职责必须保留

| 所属模型 | 字段及取值 | 触发事实与下游影响 |
| --- | --- | --- |
| TableSlot | `status=eligible/deferred` | 原生位置可处理则 eligible；跨页、provenance/坐标/页面无效则 deferred，不接纳候选 |
| CandidateView | `processing_status=comparable/deferred` | 单页区域及公共几何有效才 comparable；deferred 不创建 slot 评价 |
| SlotMatchEvaluation | `decision=accepted/rejected` | 单个 candidate-slot 双向覆盖是否通过；两个阈值失败原因可并存 |
| CandidateAdmissionResult | `processing_status=completed/deferred`；`admission_decision=admitted/rejected/null` | deferred 决策为 null；completed 仅唯一 accepted slot 才 admitted，其他情况 rejected |
| TableGroup | `status=ready_for_scoring/unresolved` | eligible 且有成员则 ready；否则 unresolved。无成员不是“评分后失败” |
| 评分各指标模型 | 文本 `evaluated/not_evaluable`；关键值 `evaluated/not_applicable`；形状 `evaluated`；空白 `evaluated/not_evaluable` | 依所属指标表达参考缺失、无关键值或结构不可评价；不得升格成候选淘汰状态 |
| GroupScoringResult | `selected_candidate_id` 与三种 `selection_reason` | 每个 ready group 必须有 winner；不为 unresolved 伪造评分结果 |

全部原因码的逐项定义以现有选优需求/架构为来源。正式重构要把原因码清单、触发顺序、字段组合与测试映射放在模型所属章节，避免仅列字符串而缺少因果关系。

## 3. 文档逐项处置清单

“归档”是建议的后续动作，本次没有移动或删除原文。工具专属规则可以分章节维护；公共规则不得在四份文档中各维护一份。

| 现有文档 | 判定 | 后续处置 |
| --- | --- | --- |
| [table_candidate_selection_requirements.md](../requirements.md) | 当前主需求；开头仍称 scoring 架构待输出，已过时 | 保留生效规则，修正状态；区分实验实现范围与未来融合约束 |
| [table_candidate_selection_architecture.md](../architecture.md) | 当前主架构；含已完成的实施步骤及旧文件迁移说明 | 整理为当前架构；旧迁移过程移入历史记录；补实现偏差 |
| [table_grouping_requirements.md](refactor-validation.md#修复对照记录) | 复核更正：实际已是废止导航页，没有旧算法正文 | 本轮由统一入口替代旧导航 |
| [table_grouping_architecture.md](refactor-validation.md#修复对照记录) | 复核更正：实际已是废止导航页 | 同上；不要把旧分组与 Camelot cell 连通恢复混淆 |
| [table_candidate_scoring_requirements.md](refactor-validation.md#修复对照记录) | 旧路由、绝对分及质量门槛，与现行选优冲突 | 归档；65 分门槛、旧指标等不得恢复到新流程 |
| [external_tools_experiment_requirements.md](../requirements.md) | 混合初期对比范围、旧输出结构和后加 CLI 需求 | 拆出当前提取/CLI 公共需求；旧阶段历史归档；LlamaParse 保留为未实施背景，不加入本轮范围 |
| [pymupdf_experiment_architecture.md](../architecture.md) | 几何 span 规则仍有效；提及不存在的 extraction/raw/helper 文件和未来步骤 | 合并为工具适配章节；目录以最终职责为准，勿为满足旧文件名新增空模块 |
| [camelot_experiment_architecture.md](../architecture.md) | `edge_inferred` 与矩形连通恢复为有效规则 | 保留；覆盖坐标架构里仍称 geometry_inferred 的旧描述；补额外文本保留规则 |
| [docling_experiment_architecture.md](../architecture.md) | 原生 span、跨页 cell 不猜页码、精简 raw 规则有效 | 保留；核对 offset 异常、尺寸告警和零表文档；移除陈旧未来时态 |
| [unstructured_experiment_architecture.md](../architecture.md) | HTML span 与 PixelSpace 适配规则有效 | 保留；“未来非表格用 Unstructured chunking”不纳入已确认 Docling 骨架方案；修复文本保留偏差 |
| [table_coordinate_system_requirements.md](../architecture.md) | 公共几何契约有效；部分指标措辞来自旧分组 | 保留坐标规范，删除对中心距离等已作废决策的暗示；明确旋转/裁剪支持边界 |
| [table_coordinate_system_architecture.md](../architecture.md) | 接口名、实施时态、Camelot 跨度来源陈旧；与实际职责有差异 | 与公共模型章节统一；不要仅把实际缺口改写成“已支持” |
| [table_extraction_cli_architecture.md](../architecture.md) | 当前编排依据 | 保留；修正“任一补齐失败终止”与“可选工具失败继续”的歧义，以 Docling 硬依赖规则为准 |
| [table_candidate_admission_threshold_calibration.md](../calibration/admission-thresholds.md) | 校准证据，不是另一份算法定义 | 独立保留；标明四份样本及适用范围，链接到生效阈值 |
| [table_candidate_admission_labels.md](../../data/calibration/admission-labels.md) | 人工标签与样本证据 | 独立保留；为样本增加内容身份后再迁移，不能因整理目录丢掉标注 |

校准 HTML 是派生交互视图，不是需求文档；应与标签、生成程序关联。当前校准报告记录 109 个候选标签，其中 95 个可比较样本取得 TP=55、FP=0、FN=1、TN=39。这是既有报告结论，本轮未重新执行阈值枚举，不能称为独立测试集效果。

## 4. 代码与目录职责清单

下表覆盖 41 个实验 Python 文件；判断属于职责盘点，不代表每个文件均已完成第三方真实输入验收。

| 文件组 | 当前职责 | 处置方向 |
| --- | --- | --- |
| `__init__.py`、`__main__.py`、`cli_workflow.py` | 包入口、参数、运行编排、交互补齐 | 保留入口；抽离输出路径/发布策略，统一参数语义；交互仅留 CLI 层 |
| `table_models.py`、`coordinates.py` | 公共候选、单元格、坐标与转换证据 | 保留并完善契约；F-04、F-05 优先处理 |
| `config.py` | 三个 PyMuPDF 策略、公共 span 容差与旧评分参数混放 | 删除无人使用的旧评分常量；将仍有效配置按职责归属 |
| `pymupdf_runner.py`、`pymupdf_normalizer.py`、`pymupdf_export.py` | 原始快照、几何跨度、候选与导出 | 保留；删除 `legacy_candidates`；公共渲染函数迁出 |
| `camelot_models.py`、`camelot_runner.py`、`camelot_raw.py`、`camelot_normalizer.py`、`camelot_export.py` | 四 flavor 调用、快照、边界连通跨度恢复、导出 | 保留；DisjointSet 是有效算法；额外文本策略待补 |
| `docling_models.py`、`docling_runner.py`、`docling_normalizer.py`、`docling_export.py` | 文档版面转换、原生表格适配、导出 | 保留；修复零表与异常事实保留；未来转换结果供融合复用 |
| `docling_layout.py` | 阅读顺序预览和 overlay 派生视图 | 当前无调用方；建议移入明确的按需审核工具，若不提供入口则归档/删除，不能宣称正在输出 |
| `unstructured_models.py`、`unstructured_runner.py`、`unstructured_normalizer.py`、`unstructured_export.py` | 分区、HTML 网格适配、导出 | 保留；补异常 HTML/坐标与文本保留验证 |
| `selection_config.py`、`selection_models.py`、`selection_metrics.py`、`selection_admission.py`、`selection_grouping.py` | 阈值、模型、几何、准入、按 slot 分组 | 主逻辑保留；旧候选对分组规则不得回迁 |
| `selection_slot_loader.py`、`selection_candidate_loader.py`、`selection_runner.py`、`selection_export.py` | 文件加载、基线检查、编排、报告 | 加强输入身份、缺策略告警和发布一致性；未来核心计算接受模型输入 |
| `scoring_config.py`、`scoring_models.py`、`scoring_reference.py`、`scoring_metrics.py`、`scoring_ranking.py` | 四指标、原生词参照、组内相对排名 | 保留规则；评分 token 与 embedding tokenizer 是两件事，不能混用 |
| `scoring_loader.py`、`scoring_runner.py`、`scoring_export.py` | 上游校验、评分编排、不变量、JSON/HTML | 加强产物版本关联；拆机器结果与人工展示的发布职责 |
| `admission_calibration.py` | 读取人工标签、计算覆盖、生成标注/图 | 作为离线校准工具保留；不进入 RAG 在线问答路径 |

建议最终按 `adapters/`、公共模型/坐标、准入分组、评分、产物/审核、CLI、校准划分职责；具体目录在架构重构时确定。避免现在先做大规模移动，迁入 `rag/` 时再做一次同等规模移动。

## 5. 实现偏差与待处理问题

优先级：**P1** 表示实验规格/代码收敛时优先处理，可能影响输入事实、结果或验收结论；**P2** 表示结构、可维护性或非主路径问题。迁移项另列，不暗示现有实验必须提前实现所有产品能力。

| 编号 | 类型/优先级 | 已观察事实与证据 | 影响 | 后续动作与验收场景 |
| --- | --- | --- | --- | --- |
| F-01 | 已核实偏差/P1 | [docling_export.py](../../table_extraction/infrastructure/extractors/docling/export.py) 的 raw/normalized 路径取决于 `normalized_count`；[slot loader](../../table_extraction/infrastructure/artifacts/slots.py) 要求 raw 路径字符串 | 成功解析零表文档仍会因 manifest 路径为空而无法建 slot | 成功且零表必须有有效文档路径与空候选列表；零 slot 可正常输出空报告；与解析失败分开测试 |
| F-02 | 已核实偏差/P1 | [pymupdf_runner.py](../../table_extraction/infrastructure/extractors/pymupdf/runner.py) 的 page/strategy 循环未隔离异常；CLI 工具级 catch 无法继续该工具其余策略 | 未落实单策略失败后继续的提取要求 | 定义策略失败与部分页失败的语义，保留成功策略；模拟中间策略失败验证后续继续和 manifest 完整 |
| F-03 | 已核实设计缺口/P1 | [cli_workflow.py](../../table_extraction/presentation/workflow.py) 主要查 manifest；候选加载按 stem 找目录；[scoring_loader.py](../../table_extraction/infrastructure/artifacts/scoring_input.py) 比对 ID/tool/strategy/source_ref，未比对候选内容哈希 | 同路径 PDF 更新、同名不同文件、候选内容原地更新，可能复用旧组或混入不同批次结果 | 规格新增 PDF 内容哈希、运行/配置身份及阶段依赖校验；覆盖同名、同路径改内容、同 ID 改 bbox/text；这不是已有文件哈希要求已落实的宣称 |
| F-04 | 已复现容差差异/P1 | [coordinates.py](../../table_extraction/infrastructure/pdf/coordinates.py) 先以 0.01 pt 裁剪；准入用 1e-6 pt。输入 x0=-0.005 在准入直接校验失败，先规范化裁剪后却通过 | 较宽松的上游裁剪会抹去超出准入容差的事实；两层容差的关系未明确 | 明确规范化与准入各自契约、是否保留裁剪证据，避免静默统一数值；补 epsilon 边界及零面积测试，重新核对受影响候选 |
| F-05 | 待验证风险/P1 | `PageGeometry` 仅宽高/rotation，公共转换未表达 crop 偏移或实际旋转变换；PyMuPDF normalizer 写 rotation=0；Docling 未比较原生 page.size 与公共尺寸 | 文档要求旋转/裁剪处理及尺寸告警，但当前代码/测试不足以证明支持；不能断言所有旋转 PDF 一定错误 | 先用旋转、CropBox/MediaBox 差异样本实测；支持则完成转换证据，不支持则明确延后；补 Docling 尺寸差异告警测试 |
| F-06 | 已复现偏差/P1 | [docling_normalizer.py](../../table_extraction/infrastructure/extractors/docling/normalizer.py) `_cell_span()` 对 span/offset 矛盾返回六个 None；有效 offset 也被清空 | 与 Docling 架构“保留可读取字段并标记 unavailable”冲突；原始 JSON 尚在，但公共模型诊断事实减少 | 明确公共模型如何保存异常原值；覆盖只缺一个 span、跨度矛盾、负/布尔 offset；有效偏移不能被无说明抹掉 |
| F-07 | 已核实偏差/P1 | [unstructured_normalizer.py](../../table_extraction/infrastructure/extractors/unstructured/normalizer.py) 重叠 span 分支直接 continue；缺 HTML 时不把 Element.text 纳入候选文本；空 coordinates 无 warning | 与架构“保留可用文本、位置不可靠则 null、记录 warning”不符；raw 保留不等于候选保留 | 补冲突 cell 文本保留策略，不虚构结构；测试缺 HTML、有 Element.text、非法 span、重叠 span、缺坐标 |
| F-08 | 已复现行为/待定语义/P1 | [camelot_normalizer.py](../../table_extraction/infrastructure/extractors/camelot/normalizer.py) `_component_text()` 使用 `dict.fromkeys`；两个原子 cell 的 `500` 合并后为单个 `500` | 可能消除真实重复值，影响 token 多重集评分；也可能消除工具重复拷贝，不能一律判为错误 | 用 raw/原 PDF 区分复制文本与独立重复值，再确认保留规则；禁止在结构重构中悄悄修改 |
| F-09 | 已核实追溯缺口/P2 | PyMuPDF cell ref 为 candidate/raw_cells/...，不是实际 raw JSON Pointer；Camelot 合并 cell ref 只指锚点 | 工具间 cell 溯源约定不统一，复查合并来源依赖额外推断 | 明确引用协议；合并 cell 能关联全部原子来源；校验引用可解析及文本不丢失 |
| F-10 | 已核实遗留/P2 | [config.py](../../table_extraction/infrastructure/extractors/pymupdf/config.py) 旧评分常量无代码引用；`legacy_candidates` 仅在 PyMuPDF normalizer 定义/构建/返回 | 读者误认为旧算法仍有效，额外构造无人消费的数据 | 删除旧常量和 legacy 字段/构建；保留三策略与 span 容差；清理后核心结果不变 |
| F-11 | 已核实耦合/P2 | 三个 exporter 引用 `pymupdf_export._write_*`；多个文件重复 JSON 写入和路径校验 | 工具模块承担公共职责，接口难复用 | 公共渲染/序列化迁出，工具专属 raw 保留；验证合并格 HTML 与 CSV 视图语义 |
| F-12 | 已核实接口差异/P2 | [__main__.py](../../table_extraction/__main__.py) Camelot 自定义 output-dir 是最终目录，其他工具追加 stem；输出提示调用 `relative_to(PROJECT_ROOT)` | 同名参数含义不同；项目外输出可能在写完后打印时报错 | 明确 output root/最终目录语义并统一；测试自定义目录，目录删除范围必须可验证 |
| F-13 | 已核实发布风险/P1 | 提取 CLI 先删除旧目录；grouping/scoring 先暂存再分别删除替换报告目录与 review 目录 | 新提取失败丢旧结果；两个目录的替换不是完整事务 | 明确最新成功结果与失败运行记录，验证写入失败/替换中断；按实验需求选择最小可靠方案 |
| F-14 | 已核实校验缺口/P1 | candidate loader 扫 `*/tables.json`；缺整个工具会告警，但缺其中一种策略不一定在 GroupingReport 告警；未核验可选工具 manifest 来源 | CLI 提示与持久报告可能不一致；直接 runner 更易接受来源不明的候选 | 记录预期/实际策略及缺失原因，校验各工具来源；覆盖只缺一个策略、manifest 属于另一 PDF |
| F-15 | 待验证风险/P1 | [docling_runner.py](../../table_extraction/infrastructure/extractors/docling/runner.py) 将未抛异常的转换结果直接记 success，未显式映射第三方转换状态 | 部分成功等第三方状态可能被隐藏；尚未实测该情形 | 依据锁定安装版本核对返回状态，模拟并定义成功/部分成功/失败映射；不直接添加未确认的业务状态 |
| F-16 | 已核实健壮性缺口/P2 | PixelSpace 入口对非空值判断后直接运算；畸形 points/类型可能抛异常；Docling 越界 bbox 返回 None 时不总附 warning | 部分坐标失败可能升级为整工具失败，或缺失解释 | 验证缺字段、错误类型、非正尺寸、非有限数；做到保留表格文本结构，仅对不可靠 bbox 置空并告警 |

F-04、F-06、F-08 的探针仅验证函数行为，不证明已有样本发生多少次。F-05、F-15 必须完成针对性验证后才可写入“已修复缺陷”统计。

## 6. 测试与实验产物基线

### 6.1 已有自动测试

| 文件 | 已覆盖重点 | 不能据此声称已覆盖 |
| --- | --- | --- |
| [test_pymupdf_normalizer.py](../../../tests/test_pymupdf_normalizer.py) | 基础几何 span 恢复 | 逐策略故障隔离、旋转/裁剪、raw 引用解析 |
| [test_camelot_normalizer.py](../../../tests/test_camelot_normalizer.py) | 公共坐标、缺边恢复行列跨度 | 合并区重复文本、非矩形/异常分量的全部事实保留 |
| [test_docling_normalizer.py](../../../tests/test_docling_normalizer.py) | 单页 cell 归属、跨页不猜 cell 页码 | 零表导出、矛盾 span 原值、页面尺寸差异 |
| [test_table_grouping.py](../../../tests/test_table_grouping.py) | 覆盖、deferred、无/零/多匹配、slot 基线、一 slot 一组、不变量 | 各工具畸形文件、输入内容更新、完整产物链 |
| [test_table_scoring.py](../../../tests/test_table_scoring.py) | 分词与数字完整度、多重集、shape、空白、相对分、同分规则、显示 | 所有 loader 验证分支、真实 PDF word 取样、完整 runner/export 重放 |
| [test_table_extraction_cli.py](../../../tests/test_table_extraction_cli.py) | 工具级失败继续、manifest 就绪、交互补齐 | 工具内部单策略失败、先删后写故障、内容哈希新鲜度 |
| [test_admission_calibration.py](../../../tests/test_admission_calibration.py) | 覆盖计算、标签读取、幂等更新、图表状态 | 新文档独立验证集、评分 winner 的人工正确性 |

当前没有独立的 Unstructured normalizer 测试文件，也没有公共坐标的完整边界测试集。现有 44 项通过只能说明被覆盖的行为没有失败。

### 6.2 已有产物

| PDF 文件名（省略 .pdf） | grouping 组数 | ready 组数 | scoring 组数 |
| --- | ---: | ---: | ---: |
| Contrato 5000000202 - HEXING ELECTRICAL - v1-15 | 1 | 1 | 1 |
| Contrato N 105-2025 LP-002-2024-FONAFE Adquisición de medidores ítem 1 3 y 4 - HEXING ELECTRICAL (1)[R][R] | 5 | 5 | 5 |
| E001-602 | 1 | 1 | 1 |
| ORDER PERU 24-12 ANDET | 1 | 1 | 1 |
| ORDER PERU 25-15 ANDET VF | 2 | 2 | 2 |
| 签字-ORDER PERU 25-19 FONAFE | 4 | 4 | 4 |
| 合计 | 14 | 14 | 14 |

grouping 格式为 `table_candidate_selection_v1`，scoring 格式为 `table_candidate_scoring_v1`。六份产物与四份阈值校准样本不可混称。格式和数量一致不证明这些产物来自当前文件内容/配置，也不证明 winner 是人工真值。

### 6.3 重构验收要求

1. 清理前保存可识别输入与配置的基线：源 PDF 哈希、规范化候选、slot、准入、成员、原始指标、winner；区分机器相关路径/耗时与业务结果。
2. 单纯移动、改名、抽公共模块、删无调用代码：业务结果应完全一致，不接受“差不多”。
3. 修复 F-01 等偏差：每项对应明确规格与失败样本，允许变化的字段和原因逐条记录，不能与纯结构重构混为无行为变化。
4. 增加必要测试：零表成功、策略失败继续、输入更新、坐标边界、异常 span、Unstructured 文本保留、完整加载到导出重放。
5. 用已有六份样本检查业务结果差异；涉及 bbox、token 或 span 变化时复查准入/winner，必要时重做校准。保留原人工标签，不为通过测试改标签。
6. 新 PDF 作为独立验证集；分别评估表格位置准入、结构/文本恢复与最终检索/问答，不能用一种指标替代全部效果。

## 7. 已确认的正式 RAG 迁移需求

本节记录用户本轮明确补充的方向，尚未实现。实验整理不得擅自变为 RAG 改造。

| 编号 | 用户确认的要求 | 对后续规格的约束 |
| --- | --- | --- |
| M-01 | 表格、整个 List、Docling 基本单元原则上在 embedding 输入中保持完整 | 不采用“默认把表格拆成检索子块，再返回整表”替代用户要求 |
| M-02 | 当前优先服从 embedding 最大长度；超长基本单元允许切割 | 原子性是有超长例外的规则：能独立装入预算的单元不因当前 chunk 剩余空间不足被切碎，应另起 chunk；只有单元本身超限才拆分 |
| M-03 | 暂时保留当前 embedding 模型；效果不佳后再评估更换 | 按实际 tokenizer 计数，不用字符数近似 tokens；长度包含 passage 前缀、特殊 token、重复表头/上下文等最终输入，禁止模型静默截断 |
| M-04 | 保留纯文本 parser 和新融合 parser，可供调用，新链路作为默认方向 | 采用显式命名和版本的策略；未来升级能并存，不覆盖旧策略身份 |
| M-05 | 同一批 PDF 跑不同链路，实测版本效果差异 | 每条实验链路有独立索引身份、manifest 和评估结果，防止不同版本 chunks 混检 |
| M-06 | 表格需转换为适合 embedding 的文本 | 增加独立表格文本序列化职责，输入统一表格结构，保留行列、表头、金额/型号/单位关系；不是简单拼接所有 cell.text |

现有默认模型在 `rag/config.py` 中为 `intfloat/multilingual-e5-small`；用户已同意暂按其 512-token 上限设计。实施时应读取并核对实际配置模型/tokenizer 的有效输入上限；512 是总输入限制，不是可直接分配给表格正文的固定预算。

### 7.1 建议采用的可切换架构

采用常见的策略模式（Strategy）与依赖注入：定义 parser 的共同输出契约，由命名注册表选择实现，Indexer 接收选定策略。无需引入复杂插件平台。

```text
命名 PipelineProfile（版本与配置）
  ├─ Parser 策略：纯文本 / Docling + 多工具表格融合
  ├─ 文本序列化策略
  ├─ Chunking 策略及长度策略
  └─ Embedding 配置
          ↓
独立索引身份 + manifest + 评估记录
```

这比仅增加 `if parser == ...` 更适合持续比较，因为新的效果同时受 parser、表格文本化、chunking 影响。未来可做两类比较：固定下游比较 parser，或对比完整历史链路。必须注明对比变量；保留旧 parser 不等于已经保留旧版字符 chunking 的整套行为。

profile 名称、接口签名、索引路径和命令参数是后续架构设计项。本轮不定稿。旧策略只保留有明确对照价值的版本，不为每次修小 bug 永久复制一套实现。

### 7.2 表格文本化与超长例外的待设计点

- 先根据统一表格结构产生确定性的可读文本，再按实际 embedding 输入计算 token；保留原结构，序列化只是派生表示。
- 首版建议不调用 LLM 生成表格描述，避免金额/单位被改写和新增模型成本。选择 Markdown、行记录或混合形式需基于样本验证，不在本清单宣称某格式已经最佳。
- 无表头、重复表头、多级表头、row/col span、合法空值、未知结构、标题/注释必须明确处理；没有证据的列名或值不得猜测。
- 超长表建议先沿完整行边界切分并带必要表头；超长 List 先沿完整条目边界切分；单行、单项或单 cell 仍超限时，再定义更细粒度兜底。该顺序是建议，尚不是已确认算法。
- 每个拆分结果应可追溯父元素与覆盖范围；重复表头属于上下文复制，不能被误统计成新增业务行。列横跨合并单元格时不得静默丢 span 语义。
- overlap 必须在预算内，不能把原本可完整容纳的元素仅为满足 overlap 切碎。不能用 embedding 前的临时截断补救不合格 chunk。
- 检索后 LLM 的上下文预算与 embedding 上限分开设计；长列表、跨页表格的多页来源不能压成虚假单页引用。

## 8. 文档与代码的迁移归属建议

- 实验阶段：在 `experiments/docs/` 建立唯一入口，整合有效需求/架构，工具专属规则按章节或附录拆分；旧规则进入历史区，标签与校准证据独立保存。
- 正式迁移阶段：新增系统需求/架构，定义 M-01–M-06、融合、索引生命周期、失败处理和评估；现有 `docs/` V1.0/V1.1 的有效系统规则继续保留，明确被替代章节。
- 公共表格算法正式迁入后，只维护一份实现和对应有效组件规格；实验调用它，保留校准、审核和对照实验职责。`rag/` 不长期依赖实验目录里的实现。
- 最终解析内容与 provenance 是正式数据；候选/准入/评分是诊断与重放事实；HTML/overlay 是按需派生视图。具体保留周期和默认生成开关待系统规格定义。
- 当前 `experiments/` 与七个实验测试文件整体尚未被 Git 跟踪；`pyproject.toml`、`uv.lock` 也存在未提交修改。后续先界定可提交源码/标签与生成产物，再建立基线，不执行无差别 `git add .`。

## 9. Docs-as-code 的最低完成标准

1. 有一个有效规格入口，读者不必猜新旧文档优先级；历史文档注明替代来源。
2. 每个关键业务规则有编号、输入、输出、边界条件、例外和验收场景；原因码归属明确。
3. 规则可以追踪到实现与测试；文档字段不允许长期停留在“未来应该有”，代码字段也不能无业务定义。
4. 配置、格式版本及算法调整与文档/测试同次变更；结构重构和行为修复分别记录。
5. 相对链接可校验；校准命令、样本身份和结果可以复算。自动检查只验证机器可验证的部分，不代替业务审阅。
6. 不重复维护多份生效阈值；规格表达业务含义，配置保存执行值，测试核对两者一致。
7. 不能为了让文档与代码“看起来一致”，把未验证的旋转支持、文本保留或失败处理缺口从需求里删掉。

## 10. 后续执行顺序与讨论范围

1. 确认当前规则及本清单中的文档替代关系，保留已有样本和规则证据。
2. 整理有效实验规格；对 F-04、F-08 等语义不闭合项补充决策；对未定义的失败粒度补需求。
3. 建立输入/配置可识别的回归基线，再删除死代码、整理公共职责；不同时更换评分方法。
4. 按问题编号修复偏差，补必要测试，复核六份样本差异，完成实验重构验收。
5. 单独编写正式 RAG 新规格，落实已确认 M-01–M-06，再编码融合、文本化、分块、索引隔离及端到端适配。

当前已经明确，无需再次确认：保留旧 parser；新 parser 可选并作为默认方向；当前模型保持；可容纳单元保持完整；单元超限允许切分；新增表格 embedding 文本化。

仍待设计：超长拆分的具体边界、异常表结构的可读表达、配置/profile 身份、多页引用、失败和降级契约。它们不阻塞本清单交付，也不应借此提前扩展 OCR、跨页拼接或新评分算法。
