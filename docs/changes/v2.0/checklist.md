# V2.0 实施清单

更新日期：2026-09-20。状态：**核心 RAG 与正式检索评估均已完成实现、回归、真实双链路基线生成和用户人工审核**。

依据：[V2.0 变更提案](proposal.md)。勾选表示对应交付物已经完成；实现项还必须有验证证据，不能只以“讨论同意”勾选。后续每项完成时补充相关文件、测试结果和残留限制；RAG 的验收成绩只采用自身运行证据。

## 0. 文档基线与审阅

- [x] DOC-01：汇总已确认规则，区分待验证算法与非目标，生成提案和本清单。
- [x] DOC-02：增加根文档导航，区分当前有效 V2.0 需求和 V1/V1.1 历史实现规格。
- [x] DOC-03：完成增量需求审阅，修正遗漏和歧义，记录已确认决定及明确后置事项。
- [x] DOC-04：编写[完整系统需求](../../requirements.md)及表格提取选优、表头、表格文本化、文档文本与分块四份子需求，保留全部有效 v1 行为；实现前阶段曾明确标记 V2.0 尚未实现，交付后已更新为验收状态。
- [x] DOC-05：审阅并定稿[V2.0 当前架构](../../architecture.md)。已统一七个 DocumentState、持久化恢复事务、prune 隔离回滚、表格模型不变量、错误映射及权威导航，并把四项技术验证结果回写架构。

出口：提案范围已审阅；实施相关关键语义有定义。不必等待所有工作结束才更新文档，也不能先实现未确定业务规则。

## 1. 实现前需要完成的定向设计

- [x] DES-01（T-02）：完成[表头判定需求](../../requirements/v2.0/table-header-detection.md)，覆盖无表头续表、全文本正文、数字表头、多层/跨行表头、全宽顶部文字和工具角色误报；明确七步优先顺序、流程图、结果原因、类型词法、规则版本、固定参数和验收场景。代码实现与样本验证仍由 TXT-01/TXT-06 跟踪。
- [x] DES-02（F-01～F-03）：[Docling 2.121.0 映射规格](../../architecture/v2.0/docling-mapping.md)已定义节点、列表、阅读顺序、家具元素、多页来源、表格附属文字及异常边界；SDK 字段探针和真实 PDF 转换已经通过。mapper 实现时必须建立 RAG 自有脱敏 fixture 并执行契约测试，由 PAR-01/PAR-04 跟踪。
- [x] DES-03（T-03～T-05、C-01～C-06）：[结构表格文本化需求](../../requirements/v2.0/table-text-serialization.md)与[普通文本、List 文本化及 V2 分块需求](../../requirements/v2.0/document-text-and-chunking.md)已固化正文格式、递归边界、overlap、父项/表头退化和终止规则；数据契约已定义 DocumentChunk、ChunkSource、HeaderIssue；实际 E5 tokenizer 的前缀、special tokens、512 上限和关闭截断已验证。
- [x] DES-04（I-01～I-03）：collection/manifest 命名及旧索引重建处理已确认；运行与持久化模型已定义 BuildConfig、manifest、artifact envelope、持久化 snapshot/journal 和七个 DocumentState；模型 revision 和 docling-core 已进入 fingerprint。所有操作只作用于所选链路。
- [x] DES-05（I-04）：[索引、产物发布与恢复契约](../../architecture/v2.0/index-publication.md)已定义默认/诊断产物、staging、单文档/全量事务、artifact 生命周期和健康检查；Chroma snapshot/恢复/重启及 Windows replace/rename/打开 HTML 清理行为已验证。事务逐阶段故障注入由 IDX-03/IDX-05 实现测试跟踪。
- [x] DES-06（D-03）：当前架构已用 DocumentProcessor→DocumentBuildResult 连接新能力与旧 Indexer，并区分 bootstrap 装配和 application 编排；表格领域模型已补齐提取、准入、分组和评分模型及不变量。

U-03 保持“有真实场景再讨论”，不把假定的 Docling 部分失败发布政策作为本阶段必做功能。

## 2. 链路框架与 v1 回归

依赖：DOC-03、DES-04、DES-06。

- [x] PIPE-01（P-01）：增加链路注册/选择，v1 使用原 parser、chunker、embedder 行为，v2 接口单独组织。实现见 `rag/pipeline_registry.py`、`rag/bootstrap.py` 和 processor 边界测试。
- [x] PIPE-02（P-02）：documents、ingest、browse、chunks、search、ask、chat、eval 统一选择；显示实际链路；chat 会话固定。CLI 与检索回归已覆盖显式/默认选择和多页结果。
- [x] PIPE-03（I-01）：独立 collection/manifest，状态、清理、重建和检索均限定选定链路。固定身份、事务根目录和恢复器均以 pipeline 隔离。
- [x] PIPE-04：相同输入及配置下验证 v1 文本块、来源、原有分块参数和 embedding 输入行为未被新规则改变。V1 继续调用原 parser/chunker，相关回归通过。
- [x] PIPE-05：验证目标索引缺失时不静默切换、不同链路索引不会混查或覆盖。目标链路不健康时 Retriever 阻断，未实现 fallback。

出口：保留可运行 v1，建立新能力接入口；代码尚未验收时不得宣称默认 v2 已经可用，V2.0 发布后的默认行为以 P-01 为准。

## 3. 四工具能力独立迁移

依赖：DES-05、DES-06。

- [x] EXT-01（D-02、E-01）：把模型、工具适配、归一化、几何、准入、分组和评分迁入 RAG，自有配置与持久化；测试确认 RAG 无 experiments 导入或运行时路径依赖。
- [x] EXT-02：逐条落实[自有表格提取与选优需求](../../requirements/v2.0/table-extraction-selection.md)，覆盖九策略配置、坐标事实、跨度恢复、文本去重、阈值、评分及决胜规则；实现与测试均位于 RAG 自有模块，不读取实验规格或产物。
- [x] EXT-03（E-02）：验证失败页之后仍提取、成功零表与失败区分、可选工具故障诊断、成功候选保留。证据见 `tests/test_v2_table_extraction.py`。
- [x] EXT-04：使用 RAG 自有测试输入和期望结果验证准入、分组、指标及 winner，不以读取实验产物作为测试前置条件。
- [x] EXT-05：在 RAG 入口运行真实数字 PDF，记录环境、依赖、工具结果与限制；结果见技术验证记录第 6 节和 `scripts/validate_v2_runtime.py`。

出口：RAG 独立完成表格选择，实验入口及实现保持独立。

## 4. ParsedDocument 和融合

依赖：DES-02、EXT-01。

- [x] PAR-01（F-01）：实现自有模型与 Docling 映射，保存 raw，保留顺序、层级、原文及可用来源；使用 RAG 自有 Docling fixture 验证。
- [x] PAR-02（F-02）：winner 替换正确原节点，关联 slot/candidate/决策；未评分表使用原生结果并说明原因，不标为 winner。
- [x] PAR-03（F-03）：保留页眉页脚、脚注及独立图注等文字，排除图片；列表及嵌套子项归组；跨页原生节点保持完整。
- [x] PAR-04：验证同一表格正文不因替换重复，未评分原生文字不丢失，缺失 bbox/页映射不被补造。证据见 mapper、parsed document 和 Docling adapter 测试。

出口：可检查统一文档；下游不需要读取 Docling SDK 内部模型或实验输出。

## 5. 表格文本化

依赖：DES-01～DES-03、PAR-01。

- [x] TXT-01（T-02）：实现已审阅表头规则，输出判定依据及不确定结论；保留工具原 role 事实。规则与边界测试见 `tests/test_v2_table_header.py`。
- [x] TXT-02（T-03）：实现多级表头路径、跨行单格去重、不同同文格保留、空白标题列编号。
- [x] TXT-03（T-04）：实现无可靠表头的原始行列表示、所有合并方向的范围表达，不推断合计、共享独立值或分组作用域。
- [x] TXT-04（T-05）：区分空白、原文占位符、合并覆盖、缺失记录；系统标记不写回 cell，原文同名内容仍按 JSON 字符串表达。
- [x] TXT-05（T-01、T-06）：验证不调用 LLM、不用样本业务关键词分支、不自动继承续表表头、不生成双份 embedding/LLM 正文。
- [x] TXT-06：检查现有 5 份 PDF 的全部 13 个 winner，并由审核 HTML 输出原结构→判定→文本→chunk 对照；8 个表头 identified、5 个 undetermined，未确定原因保留。通用边界由表头、文本化与分块测试覆盖。

出口：规则可解释，所有原内容有去向，未确定语义不被强行赋义；测试必须使用 RAG 自有输入和期望结果。

## 6. 结构分块与 tokenizer 预算

依赖：DES-03、PAR-01、TXT-01～TXT-04。

- [x] CHK-01（C-01）：接入实际 tokenizer，计入全部模型输入开销，embedding 前验证无超限，不依赖静默截断；真实主链最大最终输入 425 tokens。
- [x] CHK-02（C-02）：表格/List 独立，普通文字跨章节跨页按顺序合并；装不下下一完整单元时换块，不为填空拆单元。
- [x] CHK-03（C-03）：List 按项拆、嵌套父项上下文；仅超长段落/列表项内部至多 32 tokens 自然边界 overlap；标题无 overlap。过长祖先链从最远祖先开始退化为稳定同级位置标识。
- [x] CHK-04（C-04）：表格按行并优先使用字段分隔边界，必要时递归拆格内文字；纵向合并事实在覆盖行跨块时以 `merged_cell` 重复并保留原范围，数据正文无 overlap。
- [x] CHK-05（C-05）：超长表头/父项退回稳定定位标识；定位标识计入 token 预算，若标识加单一 code point 仍超限则明确失败，算法每轮均消费新正文。
- [x] CHK-06（C-06）：chunk source 保存序列化文字精确范围、父单元、片段序号、多页来源及重复上下文类型；测试按非重复范围重建正文，重复内容不计为新增数据。

出口：每个最终 passage 输入满足实际上限，拆分的来源及上下文退化可检查。

## 7. embedding、索引和问答接通

依赖：PIPE、PAR、TXT、CHK 对应功能完成，DES-04、DES-05。

- [x] IDX-01（I-01、I-02）：manifest 保存选定链路构建配置；严格反序列化并复算 fingerprint，仅同链路兼容比较，配置不符要求该链路 force。
- [x] IDX-02（I-03）：需更新文档全阶段重跑，未变文档仍可跳过；全局配置变化限定该链路全量范围；无中间阶段复用。
- [x] IDX-03（I-04）：实现默认产物和可选诊断产物，隔离二者的开关与保留策略；winner 审核 HTML 始终属于默认必需产物。发布使用持久化 snapshot+journal，已覆盖异常回滚与进程中断恢复。
- [x] REV-01（R-01）：默认每 PDF 生成一个面向人工验收的 winner HTML，按 rowspan/colspan 展示全部 winner 表格，明确表头识别状态，只对 identified 表头单元格着色，并展示实际 Embedding chunk 正文；坐标、来源和评分诊断不混入主视图。直接使用实际结果，不重算展示数据。测试覆盖零 winner、多 winner、合并单元格、多块及发布前构建失败保留旧向量与 manifest。
- [x] IDX-04（C-06、T-01）：向量元数据、检索结果、chunks 页过滤、引用和评估适配多页来源；LLM 使用同一份 chunk 正文。V2 eval 对任一来源页命中均可判定。
- [x] IDX-05：五文档 V1/V2 构建、查询和 48 次真实 LLM 问答均运行成功；聊天复用同一固定 pipeline/prompt/LLM 链并有 CLI/会话测试，健康恢复有逐阶段故障注入。legacy collection/manifest 不读取、不迁移、不改写，升级指南要求分别重建固定 V1/V2 索引。

出口：选定链路从 PDF 到问答贯通，索引和运行产物作用域一致。

## 8. 功能验收与效果报告

- [x] VAL-01（V-01）：2026-09-17 在 Python 3.11 环境运行完整测试集，结果为 214 passed、3 warnings；另行完成技术门、单文档冒烟和五文档双链路批量验证。未验证边界继续由本清单未勾选项明确保留。
- [x] VAL-02（U-06、V-02）：固定现有 5 份 PDF、24 题、文档/页标准证据及相同 embedding/top-k，覆盖普通文本、表格和 List；旧页面级协议仅作初步观察，正式基线见[检索效果汇总](../../../validation/retrieval/system-v2.0/summary.md)。
- [x] VAL-03：已分别运行 v1/v2，记录检索证据、48 个真实回答、失败案例与入库成本；报告明确表头不确定、独立表格缺少文档身份上下文和跨订单竞争的影响。V2 检索 9/24、V1 14/24；严格人工评分 V2 完整 2/24、V1 0/24，均未达到生产质量。
- [x] VAL-04：已分开给出功能通过、旧页面级检索观察和人工回答质量结论；不以 winner 或测试通过宣称问答提升，回答质量也不混入新的正式检索指标。

### 正式检索评估闭环

- [x] EVAL-01：完成检索效果评估需求、总需求摘要、文档导航和旧报告非正式基线定位。
- [x] EVAL-02：完成 ground truth、配置 test set、运行 JSON、指标聚合、HTML、summary、comparison、失败发布和模型不变量的架构契约。
- [x] EVAL-03：Chroma 1.5.9 原始 Top-K 在同距离边界不稳定；已验证自适应扩大候选池、覆盖完整同分组后按 chunk ID 决胜，可在同进程和独立进程稳定返回严格 K 条。
- [x] EVAL-04：实现新评估模型、repository、runner、calculator、renderer、正式 CLI 和自动化测试，替换旧页面相交评分；完整回归为 233 passed。
- [x] EVAL-05：人工完成并审核五份 PDF 的 ground truth，以及当前 V1/V2 BuildConfig 对应的两套 test set；三者均已批准为 2.0.0。
- [x] EVAL-06：已运行双链路 Top-3 正式基线，生成每 PDF JSON/HTML、aggregate、summary 和 comparison，并于 2026-09-20 通过用户人工审核。

最低验收场景索引：

| 场景 | 预期事实 | 规则 |
| --- | --- | --- |
| 某策略第一页失败、后页成功 | 后页仍执行且候选可用 | E-02 |
| 单候选或同分组 | 唯一 winner，不按分数淘汰 | E-01 |
| slot 未进入评分 | 原生 Docling 内容及原因保留 | F-02 |
| 图片、图注、页眉页脚共存 | 仅图片元素排除，文字进入流程 | F-03 |
| 工具误标续表第一行为表头 | 不只依据 role，按审阅规则输出结论及证据 | T-02 |
| 数字纵向合并、全宽说明 | 不自动推断合计或分组传播 | T-04 |
| 空格、缺格、合并覆盖、原文 N/A | 四类表达不混淆 | T-05 |
| 普通文本跨页、下一元素装不下 | 可跨页，装不下则换块，不切完整元素 | C-02 |
| 超长嵌套 List 和段落 | 范围明确的 overlap 与父项上下文 | C-03 |
| 表格多行合并、单行/单格超限 | 原始编号稳定，范围保留，无数据内容 overlap | C-04 |
| 表头或父项上下文本身超限 | 缩减重复上下文而非丢弃正文 | C-05 |
| passage 正文加前缀/特殊 token 恰好超限 | 最终输入重新分配，模型不截断 | C-01 |
| 已有 v1 collection `minimal_rag_documents_v1` 和 manifest `.rag/system-v2/pipelines/v1/manifest.json`，再构建/重建 v2 | v1 两项产物均不被覆盖且仍可查询 | I-01 |
| v2 构建配置变化、只改 top_k | 前者提示确认重建，后者不重建 | I-02 |

## 9. 文档收敛与发布

- [x] REL-01（D-01）：完整当前需求、架构、技术验证与实现验收已有唯一导航；旧版规格保留在 history，链接检查通过。
- [x] REL-02：`upgrade-guide.md` 已说明默认链路变化、显式选择、索引迁移、依赖安装和已知限制。
- [x] REL-03：README 已列出双链路能力、完整命令示例和验证入口。
- [x] REL-04：V2.0 按已定稿规格完成发布收敛并作为默认链路；显式 v1 在同批五文档构建、检索和真实问答中仍可运行。效果退化不作隐藏，作为已知限制和后续版本优化输入。

此清单不包含 git commit、部署或后续版本开发的自动执行要求。
