# 验收、人工审核与校准使用

本文给出应验收的场景，不把测试计划当作现有覆盖率。重构前基线为 44 项测试；新增测试与真实链路结果见[重构验收记录](history/refactor-validation.md)。已重跑一份 PDF 的四工具九策略，未逐页人工确认全部 winner。具体实施差异见 [当前限制](limitations.md)。

## 规则追踪矩阵

| 规则 ID | 需求位置 | 架构/适配位置 | 验收重点与现有测试文件 |
| --- | --- | --- | --- |
| EX-01–02 | 需求 §2.1–2.2 | 架构 §3、§7；工具调研 | 四工具九策略及实际版本；CLI 测试仅覆盖编排 |
| EX-03 | 需求 §2.3–2.5 | 架构 §2.3、§3 | span、原文、bbox、空格与未覆盖；三工具 normalizer 测试，Unstructured 无结构文本及故障测试见 test_experiment_refactor.py |
| EX-04 | 需求 §2.6–2.7 | 架构 §2.2、§3.7 | 零表、初始化失败、页失败继续、成功页仍使用；四工具故障测试见 test_experiment_refactor.py |
| EX-05 | 需求 §6.1–6.2 | 架构 §7–8 | 补齐、身份与发布；test_table_extraction_cli.py |
| EX-06 | 需求 §7 | 架构 §8；本文辅助功能 | 视图不改分、校准不改人工判断；test_admission_calibration.py |
| SEL-01 | 需求 §3.1–3.3 | 架构 §2.4–2.5、§4 | 卡位与几何 deferred；test_table_grouping.py |
| SEL-02 | 需求 §3.4–3.6 | 架构 §2.6–2.7、§4.3 | 双向覆盖、零/一/多匹配、页来源失败；页级例外见 test_experiment_refactor.py |
| SEL-03 | 需求 §4 | 架构 §2.8–2.9、§5 | 一 slot 一组、singleton、unresolved、顺序；test_table_grouping.py |
| SCORE-01 | 需求 §5.3–5.5 | 架构 §2.11–2.12、§6.1–6.4 | 原生 word、Counter、数字规范化；test_table_scoring.py |
| SCORE-02 | 需求 §5.6–5.7 | 架构 §6.5–6.7 | shape、span 并集、唯一众数/最小值；test_table_scoring.py |
| SCORE-03 | 需求 §5.8–5.10 | 架构 §2.13–2.15、§6.8–6.10 | 相对分、singleton、工具及 ID 同分消歧；test_table_scoring.py |
| AUDIT-01 | 需求 §5.11、§6.3、§8 | 架构 §8、§10 | 字段、引用、完整产物及视图；版本、发布回滚测试及真实产物回归见重构验收记录 |
| ARCH-01 | 需求 §8.3 | 架构 §9 | 禁止反向 import；核心无 I/O；应用可注入 fake；导入方向测试见 test_experiment_refactor.py |
| PATH-01 | 需求 §8.3 | 架构 §7–8 | extracting、校准迁移、参数一致、完整读取及审核链接；已完成六份 PDF 新路径回归，见重构验收记录 |

上述文件均位于仓库 `tests/`。规则编号为文档追踪标识，不是运行时状态。完整前置条件、每个枚举的触发和下游影响见需求及架构所属模型章节。

## 核心边界验收场景

至少覆盖：

准入与分组验收场景：

- Docling TOPLEFT/BOTTOMLEFT 转换、同页 provenance 外接矩形和跨页 deferred；
- candidate 单 region、同页多 region 外接矩形、跨页、缺 bbox、坐标系错误和页面尺寸错误；
- 两个覆盖率恰好等于阈值、略低于阈值及零交集；
- 无同页 slot、零/一/多个 accepted slot 的候选级决定；
- eligible 空组、deferred 空组、单成员组和多成员组；
- ID 唯一性、成员唯一性、排序和重复运行结果稳定；
- manifest/source PDF 不一致时硬失败；
- 中文 PDF 文件名和 Windows 路径。

scoring 验收场景：

- NFKC、casefold 和 Unicode 空白切分正确；除有上下文约束的数字撇号等价转换外，标点、小数点、千位符和连字符不会被删除；
- `1´ 131,056.00`、`1’131,056.00`、`1'131,056.00` 和 `1ʼ131,056.00` 产生相同 token；非数字上下文中的不同撇号不折叠；
- reference word 按 bbox 中心点闭区间归属 Slot，边界外 word 不进入参照；
- reference/candidate 重复 token 使用 Counter 较小次数匹配；
- `R>0,C=0` 得到三个零比例，`R=0` 得到 `not_evaluable/empty_reference_tokens`；
- 原始关键 token `500` 对 candidate `500` 完整，对同 cell `5 00` 和跨 cell `5 | 00` 均不完整；
- 不含关键 token 时所有 candidate 在该指标上不可适用并两两打平；
- `(3,4)`、`(4,3)` 分属不同 shape，同工具不同策略分别计数，无效 shape 支持度为零；
- 合并 cell 的全部 span 位置计为非空覆盖，空白位置不会包含其锚点外跨度；
- 非空 cell placement 无效时 blank metric 不可评价，覆盖重叠时按并集计算并告警；
- 空白率 `[1/10,1/10,1/5]` 通过整数比例键选择唯一众数 `1/10`；全部不同和多个众数并列时选择最小值；
- 低于参照值不产生负 anomaly，高于参照值只计算超出部分；
- 四项 higher/lower 方向、双方相等、双方不可评价和仅一方可评价的 pair 决策；
- 容差以内的浮点尾差打平，`1.0` 与 `0.99` 不会被容差合并；
- wins/ties/losses 可复算 relative score，四项 0.25 权重可复算 total score；
- 单成员组四项 relative score 为 `1.0` 且唯一成员被选中；
- 唯一最高分、跨工具总分并列、同工具多策略并列分别触发三个 SelectionReason；
- grouping report 身份不符、member 缺失或 normalized candidate 身份漂移时整次失败且不覆盖旧输出；
- 相同输入重复执行时 token、pair、分数、并列集合和 winner 顺序稳定。

## 已确认规则的专项验收

- 页面越界恰好 1e-6 pt、稍大于 1e-6 pt，规范化与准入结论一致；不能经旧 0.01 pt 裁剪抹去超界事实。
- 已确认 Camelot 合并格的两原子格各为 xyz：最终一格文本 xyz；两个独立格各为 xyz：仍是两个格；源格文本为 xyz xyz：内部重复保留；不同片段按原子顺序保留。
- 同策略第一页成功、第二页失败、第三页成功：第一与第三页候选正常发布和加载，第二页错误可查，第三页实际执行。
- 初始化失败没有伪造的 page results；成功零表有真实空文件和路径；extract-all 非零退出不使成功页产物不可用。
- Docling 第二页失败：其他成功页继续准入/评分；第二页候选准入 deferred/slot_source_page_failed，不误写为 no_eligible_table_slot_on_page；几何视图可仍为 comparable。
- 新格式与旧 v1 明确区分，loader 不从历史缺字段推断逐页成功；旧格式不被新原因静默污染。

上述场景作为持续验收要求；本次故障注入、真实提取和迁移结果见 [历史重构验收](history/refactor-validation.md)。未列入实测记录的边界不宣称已全部覆盖。

## 提取与坐标补充验收

- 每工具每策略成功零表、正常多表、第三方异常；成功产物与失败运行记录不能混淆。
- PyMuPDF 三策略跨度恢复、重叠 cell、原始矩阵与推断边界一致性告警；Camelot 矩形合并、单侧缺边、非矩形分量、额外文本。
- Docling 混合坐标原点、跨页不猜 cell 页、原生 offset/span 异常事实保留、零表原始文档有效。
- Unstructured 缺 HTML、合法/非法 span、重叠 span、可用文本保留、PixelSpace 非等比缩放、未知 system 与畸形字段。
- 页面越界浮点误差、零面积、非有限坐标、旋转/非默认裁剪页、来源尺寸不同；未实测不得写“已支持”。
- 完整 load → compute → validate → export 的回归；缺 member、不同来源、输出失败不应得到伪成功。

## 人工审核视图

### 分组视图

每个 slot 的 group 都生成页面，包括 unresolved 空组。展示 PDF、group/slot ID、页码、状态/原因、slot bbox、Docling ref 和可读基准，以及所有 admitted 候选的 ID、工具/策略、覆盖率与表格 HTML。Docling 候选兼为基准与成员时可以一次展示双重标记。

源 HTML 缺失明确提示，不默默删除该候选。rejected/deferred 候选不混入组页面，完整决策在 groups.json。目录：`output/grouping/group_views_for_manual_review/<PDF stem>/`。

### 评分视图

每个 ready group 必须生成一个独立 HTML，集中展示该组全部 candidate，不得只展示 winner。页面顶部显示 PDF、group/slot、Slot bbox、文本参照来源和 token 数、关键值 token 数、候选数、空白率参照、四项权重、最高总分、并列最高集合、winner 和选择原因等组内共同事实。

页面中的 candidate 按总分排名从高到低展示，最终 winner 固定在最前；同一总分名次内的其他 candidate 保持 `GroupScoringResult.candidates` 原始稳定顺序。每个 candidate 区域必须显示：

- `candidate_id`、tool、strategy、warning 和原表格 HTML；
- 文本指标的 reference/candidate/matched token count、precision、recall、F1、相对分、胜平负和组内排名；
- 关键值指标的 reference/matched count、integrity、相对分、胜平负和组内排名；
- 网格形状的 row/column count、同 shape candidate 数、support、相对分、胜平负和组内排名；
- 空白指标的逻辑位置数、非空覆盖数、空白数、blank ratio、参照来源、reference ratio、anomaly、相对分、胜平负和组内排名；
- total score 和总分排名；最终 winner 必须有醒目标记。

四项指标排名按各自 `relative_score` 从高到低计算，总分排名按 `total_score` 从高到低计算。差值不超过 `SCORE_EPSILON` 的值共享名次；名次采用竞赛排名，即 `rank(c)=1+严格高于 c 的 candidate 数`。HTML 中浮点数统一显示两位小数，`null` 显示为 `N/A`；该显示格式不得回写或改变 `scoring.json` 中的原始浮点值。

每张指标卡的标题旁直接显示该指标的 `relative_score`，使其无需进入明细即可读取。指标明细固定使用两列矩阵排列；较窄屏幕允许响应式降为单列。标题中的相对分不在明细中重复显示。

视图目录：`output/scoring/score_views_for_manual_review/<PDF stem>/`。使用顺序：先对照 PDF 判断真表边界，再看 raw、normalized，最后看准入与评分；区分工具识别错误、适配错误、准入错误和选优错误。

## 阈值校准

执行前提：已有人工标签及对应工具 normalized 产物。命令会更新标签中的派生覆盖率并覆盖交互图，不修改执行阈值：

```powershell
uv run python -m experiments.table_extraction admission-calibration
```

人工标签见 [标签入口](calibration/admission-labels.md)，详细推导见 [阈值报告](calibration/admission-thresholds.md)。当前 0.65/0.77 基于四份 PDF 的 109 个标签，其中 95 个可比较样本为 TP=55、FP=0、FN=1、TN=39；另外 14 个无同页 slot。该校准子集包含 8 个 slot，不能与全部六份现有产物的 14 个组混称。

更换工具版本、坐标/候选 bbox 规则或文档分布后，应补人工标签再复核，不围绕单个工具或 PDF 加特判。交互图是辅助观察，不是正式准入执行器。

## 重构验收与可复现性

1. 保存原 PDF、normalized 输入、grouping/scoring 及执行配置的基线；新版使用内容哈希与配置身份检查失效，历史数据明确保留 legacy warning。
2. 纯结构移动/删死代码必须保持候选、准入、组成员、指标和 winner 不变；路径、耗时可单独比较。
3. 修复规范偏差必须有对应失败样本、允许变化的字段及原因；不能要求错误结果永久不变。
4. 当前六份产物共 14 个 ready group；格式/数量不是质量真值。分别复核校准子集与新增样本，记录所有变化。
5. 文档与代码同次维护：规则 → 模型/模块 → 验收场景 → 测试/样本。检查相对链接、状态枚举、配置值和输出字段一致性；不为文档复制另一份评分公式。


## 四层架构与目录迁移验收

1. 检查 domain 不依赖 application/presentation/infrastructure 或第三方提取 SDK；application 不导入具体适配实现；bootstrap 集中组装。可通过静态 import 检查固定这些边界。
2. 核心算法测试直接输入模型，不读取 PDF、JSON 或 HTML；应用测试注入 fake 提取、参照读取和存储，覆盖成功零表、页失败继续和持久化失败。
3. scoring_reference 的读取与 token 规则、export 的渲染与落盘拆分后，计算事实不变。工具 normalizer 保持工具语义归属，不泄漏第三方对象到应用端口。
4. 默认 extract-all → grouping → scoring 读取新 extracting 根目录；所有单工具 --output-dir 都追加 PDF stem，Camelot 不例外；项目外输出路径不因展示 relative_to 失败。
5. 校准命令从 data/calibration 读取标签，将图写到 output/calibration；人工判断与样本 ID 保持不变，派生列可复算，标签和 HTML 的文件引用有效。
6. 旧路径存在时不静默回退；迁移后的候选、准入结论、组成员、原始指标、pair 战绩及 winner 与迁移前基线一致。仅路径、耗时等非计算字段允许变化；行为修复另行验收。

这些要求用于约束后续的结构调整；已完成重构的实际检查范围见[历史验收记录](history/refactor-validation.md)。文档声明不替代测试，新增变更仍需执行对应检查。
