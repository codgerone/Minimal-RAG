# V3.0 实施与逐阶段验收记录

本记录追踪编码进度，不取代 `spec/` 中的需求和架构。切换现行 CLI 入口之前，所有阶段和真实验收门必须通过。现存 V2 产物只读；现存未提交的 `docs/README.md` 与 V3 规格保持原状。

| 顺序 | 实施范围 | 阶段验收与规格核对 |
| --- | --- | --- |
| 1 | `rag/v3/contracts` 公共模型、接口/槽/边/插件注册、两份内置配置、参数和指纹、配置保存/加载 | 装配 A01—A09、C1/C2、单向 DAG、构建/查询身份和规范十进制 JSON 的正常/负例；逐项回核装配与文档契约。 |
| 2 | Registry、纯文本与 Docling 主解析适配，表格四工具九策略、选择、内容准备、组装 | T-01/T-02/T-09 与固定 PDF 节点、slot、候选、winner/回退、正文/来源等价；回核文档与表格需求/契约。 |
| 3 | 两种 Chunker、E5 Embedder、必需阶段快照及 HTML | T-03/T-04 的解析、chunk、metadata；全部角色/条件和回读、来源覆盖及真实 Prompt 输入；回核分块、编码、产物契约。 |
| 4 | 向量、manifest、journal、Publisher、Recovery、Indexer、Health | 全量 force、增量、prune、同身份隔离、缺失/损坏、完整故障注入矩阵、跨进程恢复；回核存储与生命周期契约。 |
| 5 | Retriever、PromptBuilder、LLM、Chatbot、CLI 配置引导与八类命令 | 同分 K 边界、健康门、消息逐字、只读命令、凭据重试和无旧运行分支；回核运行/检索契约。 |
| 6 | 评估数据映射、Preflight、指标、报告及不可变运行 | T-08、V2 历史只读比较、`comparison.json` 回读、invalid/failed/completed 故障注入；回核评估契约。 |
| 7 | 两份内置装配真实端到端对照、全量测试与入口切换 | T-04 两份配置的解析、chunk、索引、命中、Prompt 和正式指标，审计快照、发布恢复全部通过后才切换 `rag.__main__`；移除旧运行路径，保留历史产物只读。 |

## 2026-09-28 最终交付门与入口切换

- 阶段 1—6 已按上表顺序实现并通过对应装配/模型、解析表格、分块与快照、存储发布恢复、检索问答/CLI、正式评估测试。T-02 四工具的合并单元格、页号与缺坐标合成 fixture **4/4**；Docling 单次转换生命周期、独立进程发布/恢复与评估报告故障注入均在 V3 测试中通过。切换前完整 `tests/v3` **92 passed、2 条 Docling 弃用警告**；切换和旧代码清理后全项目 `pytest -q` 仍为 **92 passed、同两条警告**，`compileall -q rag` 通过。
- 阶段 7 真实等价复核：两份 V3 索引健康均为 5/5 `current`；plain_text 266 chunks、structured 101 chunks，审计快照分别回读 5/11 种必需角色，ID/正文/来源和审核 HTML 一致。正式 completed 运行各 24 道题的 Top-3 chunk ID 与对应 V2 历史运行逐题逐位均 **0 差异**，十项整体指标逐字段 **0 差异**；全部 367 个已发布 chunk 的 V3 Prompt 输入与 V2 逐字一致。`comparison.json` 对 V2/V3 标注规则变化正确报告 `protocol_changed`，两份 V3 装配之间为 `strictly_comparable`；旧 V2 run、ground truth 和旧 test set 文件未修改。
- 用户审核过的两份草稿原样保留在 `eval/reviews/`；批准后的对应映射页另存为 `v3-plain_text-mapping-approved.html` 与 `v3-structured-mapping-approved.html`，仅更新页眉审核状态，excerpt/组级证据内容与已批准 test set 保持相同。
- 发布恢复门包含 replace_document、全量 force、prune 各 15 个跨进程中断点（45 场景）、首次发布 15 场景、恢复自身再次中断 6 场景、损坏快照负例和多文档 force 中断；均已通过。正式评估运行及逐文档 JSON/HTML 已不可变发布，版本级 summary 与跨版本 comparison 已原子发布并回读。依已审核规格，不自动选定正式基线。
- 以上门通过后才将 `rag.__main__` 切到 V3；删除 29 个旧顶层运行模块、`rag/v2`、51 个仅测试旧运行实现的测试文件和 6 个依赖旧运行代码的脚本。V3 等价测试改用冻结的旧版事实 fixture，不再导入 V2 实现。真实 `python -m rag documents --config plain_text|structured` 均返回 5/5 `current`，旧 `--pipeline` 被拒绝；现行入口只有 V3。根 README、示例环境配置及文档地图已指向现行 V3 完整规格，V2 文档标明历史身份。预先存在的 `docs/README.md` 修改在原文件上保留并增量更新。
- 双向规格回核：内置纯文本参数为用户裁定的 300/50，Prompt 命中正文原样写入；逐 excerpt 映射只供审核，组级计分只接受完整事实组合；构建身份、只读健康门、快照角色、发布恢复、正式评估可比性与实现/测试一致。本轮未新增元数据过滤、关键词检索或图片解析。

## 2026-09-28 用户批准与正式评估

- 用户明确批准两份修订后的 V3 test set。只把各自 `manifest.json` 的 `review_status` 设为 `approved` 并记录同一 UTC 审核时间；两份数据集的文件映射、PDF 身份和指纹 `498ee87f…`、`4f5c74e7…` 均未改变。Repository 再次完整回读 5 PDF/24 题/43 组/49 excerpt。逐 excerpt 溯源仍不参与组级计分，完整事实组合仍是唯一计分依据，与评估需求第 4 节双向一致。
- 用真实活动索引分别运行正式 `eval --config plain_text --top-k 3`、`eval --config structured --top-k 3`，得到 immutable completed run `20260928T031403Z-87f81e5f215b`、`20260928T031446Z-eb97f3b4e77e`。RunStore 回读两次 completed、各 5 PDF；各自 24 道可回答题、0 失败/0 unmappable。两份 `aggregate.json` 与相应 V2 历史运行的十项整体指标逐字段相等。`comparison.json` 回读 4 条已完成运行；V2/V3 因标注规则版本改变标为 `protocol_changed`，V3 两装配之间为 `strictly_comparable`。跨版本报告按评估契约发布，V2 历史 run 文件未改写。
- 全量 V3 测试在批准后的首次运行是 **85 passed、2 failed**，两项失败仅因断言仍预期 `draft`；现已更新为 `approved`，Repository 定向测试 **4 passed**。命令作用域按装配契约补上逆序资源关闭和创建失败清理，CLI/RuntimeFactory 定向测试 **19 passed**；这些最后改动后的完整 V3 集仍需重跑。现行 `rag.__main__` 仍指向旧入口，阶段 7 的最终代码/规格回核与入口切换尚未完成。

## 2026-09-27 映射纠错与问答输入验收
- 审核页已去除完整 excerpt 映射与组级组合的重复 chunk 正文：该 excerpt 仅指向下方相同组级组合，局部证据仍显示独立 chunk 全文；test set 内容、fingerprint 和 draft 状态不变。用户正在审核两份更新页，正式评估尚未启动。
- 全量 force 对旧 manifest JSON/schema 损坏的恢复语义按存储契约落实：仅显式全量 force 能保存原始字节及 SHA，故障回滚按原始字节验证旧 manifest；已存在但损坏的新产物目标先做快照，覆盖后故障回滚恢复其原始目录字节。定向测试 3/3；完整 V3 测试 **70 passed、2 条 Docling 弃用警告**（随后新增目标碰撞测试 1/1）。
- CLI 在 Windows 真实只读 `browse` 暴露 GBK 不能编码西班牙文 PDF 字符的问题，入口标准输出与错误输出改用 UTF-8；重新执行 structured `browse --limit 1` 与 plain_text `chunks --document E001-602.pdf --page 1` 均成功。E5/LLM 服务异常按运行契约返回退出码 4，CLI 定向测试 6/6。
- 发布故障矩阵已扩大到单文档替换、全量 force、prune 各 15 个跨进程中断点，45 场景 1/1 通过（228.20 秒）；首次发布旧 collection/manifest 均不存在时 15 场景 1/1 通过（82.33 秒）；恢复向量、产物、manifest 三动作各自前后再次中断的 6 场景 1/1 通过（51.52 秒）。损坏恢复向量快照的负例 1/1：`recovery_failed` journal 保留、文档 unassessed 且读门阻断。完整 V3 测试截至这些补充为 **76 passed、2 条 Docling 弃用警告**；随后 CLI 定向继续新增用例。
- 配置向导现在列出每份已保存配置的插件绑定、构建指纹与真实索引健康状态；非法/重名 NAME 重新输入，不保存草稿。空工作区非交互请求只报缺配置且不写入；交互明确同意后才安装并选用内置 structured。`ingest --all-configs` 汇总每个配置的成功、跳过或失败并继续其他目标。最新 CLI 定向测试 **10/10**；完整集仍需在最后改动后重跑。

- 用户确认 Prompt 的命中正文原样写入，来源标签属性继续转义；先修订 `spec/architecture/retrieval-answering.md` 再改实现。`tests/v3/test_prompt.py` 为 4/4；两份真实 V3 活动索引的全部 266/101 个 chunk 逐条构造 V3 消息与 V2 `build_messages` 对照，367/367 逐字一致。
- 用户发现多 excerpt 证据组缺乏逐条映射。盘点两份草稿各 5 PDF、24 题、43 组、49 excerpt；各有 3 条 excerpt 的来源页未被原组级映射呈现。经用户裁定，组级指标仍要求完整事实，另为每条 excerpt 保存独立审核映射。先修订总需求、评估需求、总架构和评估数据契约，再增补模型、Repository、Preflight、逐题事实和审核页。两份 draft 的标注规则为 `evidence_chunk_mapping_v3`，新指纹分别为 `498ee87f…`、`4f5c74e7…`，仍未获人工批准。
- 修订版两份审核页位于 `eval/reviews/`；各 49 条 excerpt 均有独立映射，合同 Q002 的四条 excerpt 与 Q004 的第 2/6 页证据均展示真实 chunk 正文，部分证据明确标为不单独计分。Repository 回读 5/24/43/49 成功；真实索引 Preflight 对磁盘 draft 正确拒绝，对仅供机械测试的内存 approved 视图通过。新草稿的只读真实 K=3 干跑仍为两份各 24/24 命中顺序与 V2 相同、整体指标无差异；正式评估等待用户审核新版映射。
- 发布恢复跨进程中断矩阵已覆盖 replace_document、replace_collection force、prune_document 各自提交前后六种场景，定向测试 1/1（44.60 秒）；端口依赖重构后的发布/存储/Prompt 定向测试 8/8（55.11 秒）。完整 `.venv/Scripts/python.exe -m pytest tests/v3 -q` 为 **69 passed、2 条 Docling 弃用警告**。现行入口仍未切换。

阶段结束时记录命令、结果、与规格的双向差异及修订。任何改变业务结果的规格缺口先修订对应需求和架构，再写代码。

## 历史实施记录（当时未完成项由上方最终验收覆盖）

### 2026-09-26 真实检索与指标只读对照
- 按用户确认的 `plain_text` 300/50 重建后，两份 V3 内置装配均针对同一份已审核 ground truth 的 5 PDF、24 题运行真实 E5/Chroma Retriever，K=3。逐题 Top-3 chunk ID 及顺序与各自已发布 V2 历史评估完全一致：`plain_text` 0/24 差异，`structured` 0/24 差异；十项整体指标逐字段对照 V2 `aggregate.json`，两份装配均为 0 差异。只读试跑写入忽略的 `tmp/v3-*-evaluation-dryrun.json`，未生成正式运行或修改旧产物。
- V3 test set 磁盘状态仍为 `draft`，正式 Preflight 正确拒绝。仅在内存中构造 approved 视图时，两份真实索引、源 PDF、构建投影和映射均通过机械预检；这不替代用户审核。完整 `.venv/Scripts/python.exe -m pytest tests/v3 -q` 为 **55 passed、2 条 Docling 弃用警告**。阶段 6 与入口切换门仍未完成。
- 评估主链已新增 V2 历史只读回读、可比性比较、逐题事实 JSON/HTML、版本级报告、不可变 V3 RunStore 与 Evaluator 装配。发布事务在隔离测试中完成回读，注入版本级文件写入故障后恢复旧 summary/comparison，并把新运行记录为 failed；历史 completed 仍可回读。报告契约原先的比较投影只含整体指标且缺 collection，无法满足需求规定的逐 PDF 汇总和 collection 展示，已先修订 `evaluation-contracts.md` 的 `document_summaries`、`collection_name`，再同步实现及测试。正式数据集仍为 draft，未发布任何真实正式 V3 运行。
- 两份 test set 的人工审核页已从真实 V3 Chroma chunks 与 ground truth 生成于 `eval/reviews/`：每份 5 PDF、24 题、43 个 mapped 组、0 个 unmappable 组，并展示每个可接受组合的 chunk 全文与页面；已提交用户审核问题，当前状态仍是 draft。评估 RunStore 已增加另一进程在首个版本级文件发布后强制终止、再由新进程恢复旧三个版本级文件及 failed run 的实测。测试通过不代替用户标注审核。
- CLI 已在 `rag/v3/cli.py` 独立实现参数解析、默认/命名/批量选择、八命令主路径和逐接口配置向导，现行 `rag.__main__` 未切换。审核规格中 `eval --live` 只称“独立冒烟”，未定义题目与成功条件，已先在检索评估需求及架构补成“已审核 ground truth 24 题逐题问答、仅检查执行成功、不写正式指标”，再实现；OpenRouter 认证交互重试一次原 LLM 请求的消息保持性已测试。CLI 引导修复、完整错误码/退出码及独立进程发布矩阵还需继续验收。
- `tmp/v3_audit_snapshot_check.py` 已对两份真实活动索引逐文档回读全部必需快照角色及 SHA，比较 `chunks` 快照与 Chroma 的正文、ID、来源页，并确认审核 HTML 含每个 chunk ID：plain_text 5 PDF/266 chunks/5 角色，structured 5 PDF/101 chunks/11 角色，全部通过。该探针只读，不改变旧产物或正式评估状态。
- V3 CLI 已仅通过独立 `rag.v3.cli.main` 在真实活动索引运行 `documents --config plain_text`、`browse --config structured --limit 1` 与单题 `search --config plain_text --top-k 1`；均只读成功，未触碰 `rag.__main__`。工作区 V3 配置存储现已安装两份内置配置与 structured 默认指向。问答 Prompt 对照发现结构化历史命中含字面 `C&I`、`3&4`，当前已审核“正文转义”架构使 V3 输入变成 `C&amp;I`、`3&amp;4`，与“V2 问答输入等价”相冲突；已提交用户业务取舍问题，决定前不修改该规则。

### 2026-09-26 装配执行与入库错误边界

- 两份内置配置现在均通过 `RuntimeFactory` 解析出实际 Indexer、DocumentProcessor、Chunker、Embedder、Publisher、Health、Recovery、Retriever 和 Chatbot；结构化 TableSelector 从绑定的 `PdfEvidenceReader` 取原文与页面几何，未接入的纯文本分支保持缺席。工厂放在 `plugins/` 组合边界，不作为领域应用层。
- 全量 force 发布预检异常现在返回每文档 `publication_failed` 且清除未进入 journal 的暂存；`unprocessable` 的入库错误从实际 SourceProbe 原因产生，图像但无文本的 PDF 不再误报可处理。完整 `.venv/Scripts/python.exe -m pytest tests/v3 -q` 为 **49 passed，2 条 Docling 弃用警告**；随后端口重构专项 9 项、发布/入库专项 3 项通过，完整集仍需再跑。
- 发现应用层尚有对具体 adapter 的导入，与总架构依赖方向不一致；待改为端口依赖并做静态检查。评估插件、CLI、完整故障矩阵、两份内置装配 T-04/T-08 尚未验收，现行入口不切换。
- 已开始正式评估的 V3 公共事实模型与纯指标计算：多 chunk AND 组、排名和跨文档污染、零命中、不可回答题聚合的 2 项公式测试通过。核对现存已审核 ground truth 发现磁盘 `ground_truth_manifest_v1` 条目缺少 V3 完整模型中的文档 ID/hash；先在评估架构契约记录只读导入与派生规则，保留既有数据集版本和原文件。
- 只读评估 Repository 已按旧清单记录的 SHA 回读五份已审核 ground truth，补出内存文档身份并以规范文档集合核对原 fingerprint；实际结果为 5 PDF、24 题、原 `4612ed55…` fingerprint。文件改动拒绝测试与指标测试合计 4 项通过。V3 test set 映射、预检及正式运行仍待实现和人工审核。
- 两份实际内置装配均已在独立 `.rag/system-v3/indexes/<fingerprint>/` 对五份基准 PDF 完成真实解析、快照、编码和 Chroma 发布：`plain_text` 共 111 chunks（60/4/8/7/32），`structured` 共 101 chunks（34/4/11/12/40），两者健康回读均为 5/5 `current`、无 issue。两份构建指纹分别为 `83a24e5d…`、`1ab42635…`；旧 V2 索引与报告未写入。该证据说明 V3 自身链闭合，尚不等于逐阶段 V2 等价或正式评估通过。
- 真实结构化索引首份发布后的健康检查暴露 `BuildProjection` 中 tuple 参数经 JSON 回读为 list，导致对象相等误报 `configuration_mismatch`；已改用契约规范 JSON 字节比较，结构化后续增量发布及 5/5 current 验证通过，新增回归测试 1 项。评估架构补 `test_set_fingerprint` 规范算法；Repository 的 V3 test set 回读与 Preflight 代码已开始，尚需 fixture/实际数据验收。
- 完整结构化历史对照逐份回读 V2 只读 `chunks.json` 与 V3 实际 Chroma：五份共 101 个 chunk，ID、顺序、正文、token 数和来源在 JSON 语义下一一相同，0 差异。纯文本 V2 当前已发布构建与 `.env` 为 300/50，而已审核 V3 内置曾写 700/100；用户明确裁决内置改 300/50，已先同步修订总需求、分块需求、文档处理与装配架构，再改插件默认参数。用两边只读解析/分块函数按 300/50 对五份 PDF 对照：9/154、1/9、2/16、2/16、6/71 页/chunk，全部 ID、正文和页来源一致。V3 公共 `chunk_index` 按文档全局连续，旧 V2 纯文本字段按页重新起零；正式 V3 模型按已审核契约保留全局索引，T-04 对用户可见正文、顺序、ID、页来源和检索行为继续验收。
- 按用户裁决重建后，内置 `plain_text` 新指纹 `1ce9184d…` 的五份 PDF 实际 Chroma 共 266 chunks（154/9/16/16/71），Health 回读 5/5 `current`、无 issue；先前 700/100 的 `83a24e5d…` V3 隔离构建不再作为内置配置索引。结构化 `1ab42635…` 仍为 101 chunks、5/5 `current`。
- 结构化 test set 已从已审核 V2 映射生成新的 V3 `draft`：生成前对所有 101 个 V2 审计 chunk 与 V3 活动记录核对 ID、正文、类型、token 数和全部来源，0 差异；24 题对应 5 PDF，fingerprint `be2139b1…`。Repository 全量回读通过；Preflight 对真实 `draft` 正确拒绝，以仅供测试的内存 `approved` 视图对当前索引和映射机械检查通过。**磁盘草稿仍未经人工审核，不发布正式评估。**

### 2026-09-25 发布恢复与健康增量

- `LocalArtifactStore` 已验证发布、隔离和副本恢复，并对恢复副本逐文件检查身份及 SHA；`LocalRecoveryStore` 已实现严格 journal/schema/身份校验、原子比较替换、跨进程排他锁与三类恢复证据回读。
- `Publisher`/`Recovery` 的真实 Chroma 测试已覆盖首次发布、单文档向量替换失败回滚、生效点前后进程中断判定、全量 force 及 prune 隔离/恢复；`IndexHealth` 已覆盖新文档、完整 current、受损 artifact 的 invalid、pending journal 的 unassessed。该测试仍需扩展成独立进程故障矩阵与多文档场景，阶段 4 尚未完成。
- 双向核对发现运行架构对 `unprocessable` 的判定顺序比已审核操作需求宽，现已在 `runtime-contracts.md` 限定 Probe 只用于 new/changed 候选，已登记且 hash 未变的 current/invalid 不被覆盖。
- `.venv/Scripts/python.exe -m pytest tests/v3 -q`：39 passed、2 条 Docling 弃用警告；`compileall rag/v3` 通过。当前运行入口和 V2 历史产物均未修改。

- 阶段 1 已实现公共装配模型、静态插件目录、两份内置配置、构建指纹与配置存储；A01—A09 的完整负例、运行工厂与装配执行仍需补齐，故不标记完成。
- 阶段 2 已实现 Registry、纯文本主解析、Docling 单次转换资源及原生事实、四工具适配、候选准入/评分、表头/文本化与文档组装的独立组件；完整 `DocumentProcessor` 连接、九策略故障场景和全部 T-02 fixture 仍需完成。
- `.venv/Scripts/python.exe -m pytest tests/v3 -q`：31 passed；`compileall rag/v3` 通过。真实 `E001-602.pdf`：Docling 73 个阅读序元素 ID/正文、1 slot/原生表，与 V2 相同；原生表头和序列化正文相同。PyMuPDF 三策略、Camelot 四策略的状态、候选 ID、cell 事实与 V2 相同；Unstructured hi_res 经正常权限环境重跑，双方均成功产出 1 候选且 cell 事实一致。该局部证据不代表 T-04 全链完成。
- 双向规格核对发现并修订：`table-contracts.md` 补 `CoordinateTransform` 字段；`document-contracts.md` 补真实使用的 `formula_orig_fallback` warning；表头需求附录统一引用架构机器字段；`table-contracts.md` 明确无法归页但原工具已产出的 deferred 候选保留于报告、不伪造 PageExecution。实现与规格仍需在阶段结束时全量回核。
- 随后连接了实际 `DocumentProcessor`，以 `E001-602.pdf` 对照现存只读 V2 `parsed-document.json`：九策略均完成（lattice 为正常零表），73 个节点 ID/正文、slot winner、表格序列化文本一致；再对照 `chunks.json`，4 个 chunk 的 ID、顺序、正文、token 数、片段和来源一致。Unstructured 需在正常权限环境运行，沙箱的临时目录权限失败不计入产品结果。
- 阶段 3 已实现首版必需快照、四类自包含 HTML、文件哈希和暂存回读；纯文本 5 角色与结构化 11 角色的测试通过，结构化真实 PDF 的三张表格页保留 `slot_001` 分区。`.venv/Scripts/python.exe -m pytest tests/v3 -q` 最新为 **33 passed**；跨文件引用与 HTML 文本节点的完整机械校验、更多空态/故障注入、编码及发布实测仍未完成，因此阶段 3 尚未完成。
- 阶段 4 的隔离 Chroma 探针显示锁定环境把输入 `(0.5, 0.5)` 回读为 `(0.4999999701976776, 0.4999999701976776)`，其余 ID/正文/metadata 精确一致。已先修订存储契约，限定向量逐分量绝对容差 `1e-6`、相对容差 0；快照文件 SHA 和其他记录字段仍精确比较。适配器校验与恢复测试继续进行。
