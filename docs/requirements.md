# Minimal RAG 当前系统需求

需求版本：V2.0。状态：核心功能已实现并通过回归；检索评估数据集与正式基线报告待按本文完成。运行形态：Windows 本地终端、Python 3.11、单用户、多 PDF。

本文描述 V2.0 完成后系统的全部有效行为。以下详细规则属于本文组成部分：

- [表格提取、准入、分组与选优](requirements/v2.0/table-extraction-selection.md)
- [表头判定](requirements/v2.0/table-header-detection.md)
- [结构表格文本化](requirements/v2.0/table-text-serialization.md)
- [普通文本、List 文本化与 V2 分块](requirements/v2.0/document-text-and-chunking.md)
- [检索效果评估](requirements/v2.0/retrieval-evaluation.md)

[V2.0 增量需求](changes/v2.0/proposal.md)只说明本次变化，[实施清单](changes/v2.0/checklist.md)记录交付进度，均不替代本文。

## 1. 目标与非目标

系统针对 `documents/` 中的数字型 PDF 建立本地向量索引，支持文档发现、增量入库、检索、问答、聊天和评估。V2.0 同时支持两条可比较链路：v1 保留纯文本基线，v2 恢复文档结构、列表和表格语义。

本版不增加 OCR 扫描件支持、图片理解、自动跨表续表拼接、LLM 表格文本生成、多轮聊天记忆、Agent/工具调用、Web UI、阶段缓存恢复或 LangGraph。未验证的旋转、CropBox/MediaBox 不一致页面不扩大支持承诺。

## 2. 文档来源与身份

- 默认扫描项目根目录 `documents/` 下全部 `.pdf`，包含子目录，扩展名大小写不敏感。
- 路径必须位于项目内；CLI 的文件选择器使用相对路径或唯一文件名，不接收越界路径。
- `relative_path` 统一为相对 `documents/`、使用 `/` 的 POSIX 形式。
- `document_id = sha256(relative_path.casefold())`；文件内容另计算 SHA-256 `file_hash`。
- 两个不同文件若文件名相同但相对路径不同，仍是两个文档；只用文件名选择且不唯一时必须报歧义。
- 文件不存在、目录为空、PDF 损坏、可解析但无可用文本/结构是不同事实，分别报告。

## 3. 链路选择

### P-01 链路定义与默认值

| pipeline | 解析和分块 | collection | manifest |
| --- | --- | --- | --- |
| v1 | PyMuPDF 按页纯文本；每页独立递归字符分块 | `minimal_rag_documents_v1` | `.rag/system-v2/pipelines/v1/manifest.json` |
| v2 | Docling 版面、四工具表格处理、ParsedDocument、结构化 token 分块 | `minimal_rag_documents_v2` | `.rag/system-v2/pipelines/v2/manifest.json` |

两条链路共享当前系统的 Chroma 持久化目录 `.rag/system-v2/chroma`，但 collection、manifest、状态检查、构建和查询完全隔离。**所有支持 pipeline 的命令在省略 `--pipeline` 时等同 `--pipeline v2`。** 既有编号的含义不能随版本更新而改变。

升级前未带版本号的 `minimal_rag_documents` 和旧 `.rag/manifest.json` 是上一代系统的 legacy 索引。迁移时原样归档到 `.rag/legacy-system-v1/`，不得作为当前系统 v1/v2 的查询来源；当前系统运行命令不得读写该目录。需要 v1 对照时使用当前系统的 pipeline v1 索引。

### P-02 命令范围

```text
documents [--pipeline v1|v2]
ingest [--pipeline v1|v2] [--file PDF] [--force] [--prune]
chunks --document PDF [--page PAGE] [--pipeline v1|v2]
browse [--document PDF] [--offset N] [--limit N] [--full-metadata] [--pipeline v1|v2]
search QUESTION [--top-k N] [--document PDF] [--pipeline v1|v2]
ask QUESTION [--debug] [--pipeline v1|v2]
chat [--pipeline v1|v2]
eval [--live] [--top-k N] [--pipeline v1|v2]
```

每条命令显示实际 pipeline。chat 启动后固定 pipeline。选定索引不可用时只引导构建/修复该 pipeline，不自动切换。
`browse` 只读目标 collection 的 ID、chunk 正文和 metadata，禁止加载或显示 embedding；结果按相对路径、chunk_index、ID 稳定排序，并支持分页与文档限定。默认隐藏体积较大的 `sources_json`、`node_ids_json`，显式 `--full-metadata` 才展开完整 metadata。

`ingest --file` 只处理一份文档，不能与 `--prune` 同用。`--force` 强制重建所选范围；`--prune` 只清理所选 pipeline 中源文件已删除的文档。

## 4. v1 纯文本基线

### P-03 解析

PyMuPDF 按物理页顺序读取 `page.get_text("blocks", sort=True)`，按返回顺序拼接非空文本块；统一换行及首尾空白，保留页码。某 PDF 无可用文本时为 unprocessable，不启用 OCR。

### P-04 分块

每页单独使用递归字符分块，长度函数为 Python `len`：默认上限 700 字符、重叠目标 100 字符，由 `CHUNK_SIZE`、`CHUNK_OVERLAP` 配置，满足 size>0、0≤overlap<size。

分隔顺序为 `\n\n`、`\n`、`. `、`; `、`, `、空格、字符兜底；分隔符保留在前一片段末尾。实际 overlap 受自然边界影响，不保证恰好 100 字符。chunk 不跨页，去除首尾空白及同页相邻完全重复块。

V2.0 的 token、结构单元和新 overlap 规则不改变 v1 输出。

## 5. v2 结构化解析

### E-01 表格处理

v2 执行四工具九策略提取、cell 结构恢复、Docling slot 准入、按 slot 分组和四指标选优。完整输入、配置、阈值、状态、原因与公式见[表格提取与选优需求](requirements/v2.0/table-extraction-selection.md)。RAG 的代码、配置、运行产物和文档均不依赖 `experiments/`。

某策略某页失败后继续下一页，成功页候选保留。可选工具失败而 Docling 可用时继续其他候选，不切换 v1。Docling 部分页失败的索引发布规则不在当前支持范围；出现可复现真实样例后另行制定。

### F-01 ParsedDocument

保留 Docling 原始文档，另建项目自有 ParsedDocument，包含文档身份、阅读顺序、层级、文本、列表、表格和来源。下游不能直接依赖 Docling SDK 对象。

ParsedDocument 是 v2 完整的语义解析结果，不是等待后续补字段的半成品。每个表格节点必须已经包含最终采用的结构、winner 或 Docling 原生回退来源、表头判定结果和规则文本化结果；结构分块以该完整结果为输入。

- winner 替换对应 Docling 原表格节点，保存 slot、group、candidate 和选择关联，同一表格不重复输出。
- 原生表格未进入评分时使用 Docling 已有结构，记录原因，不标为 winner。
- 段落、标题、图注、表注、脚注、页眉、页脚均保留；图片元素排除，独立文字图注仍保留。
- 一个 Docling 列表组连同嵌套子列表构成完整 List；相邻独立列表不自行合并。
- Docling 已形成的跨页节点保持完整并保存全部页面来源；本系统不自行关联独立续表。
- 缺失的 cell 页码、bbox、阅读关系或结构不补造。

### T-01 表格转为检索文本

先按[表头判定需求](requirements/v2.0/table-header-detection.md)确定表头，再按[表格文本化需求](requirements/v2.0/table-text-serialization.md)生成文本。无可靠表头时使用行列编号；合并格表达覆盖范围；空白、原文占位符和缺失位置分别表达。

第一版使用规则，不调用 LLM，不按业务关键词写专用分支。embedding 和检索后交给 LLM 使用同一份 chunk 正文。

## 6. v2 结构化分块

### C-01 token 硬上限

使用实际 embedding 模型 tokenizer 计数，当前模型上限基线为 512 tokens。最终 passage 输入必须包括正文、格式、重复上下文、overlap、`passage: ` 前缀和特殊 token，不能依赖模型静默截断。更换模型或 tokenizer 规则属于索引构建配置变化。

### C-02 单元组合

本节及 C-03、C-04 的确定性文本格式、边界顺序、overlap 和兜底算法以[普通文本、List 文本化与 V2 分块需求](requirements/v2.0/document-text-and-chunking.md)为准。

- 整张表格和整个 List 优先分别独立成块，不与相邻普通文字混装。
- 标题、段落、页眉、页脚、脚注、图注、表注等普通文字按阅读顺序在预算内合并，允许跨章节、跨页。
- 遇到表格或 List 先结束普通文本块；它们结束后重新开始普通文本块。
- 加入下一完整单元会超限时先结束当前块，不为填满空间切开本可独立容纳的单元。
- 只有单个基本单元本身超限才拆分。

### C-03 超限与 overlap

- 段落、页眉、页脚、脚注、图注、表注按段落、句子、短语等自然边界递归拆分；仅在同一超长元素内部允许最多 32 tokens overlap。
- 标题同样递归拆分，但不使用 overlap。
- List 先按完整列表项拆；嵌套项片段重复必要父项。单项仍超限时递归拆文字，并仅在该超长项内部允许最多 32 tokens overlap。完整列表项之间无 overlap。
- 表格先按完整数据行拆并重复必要表头；纵向合并覆盖行尽量同块。仍超限时依次按完整单元格、单格内部自然边界拆分。数据行和单元格内容不 overlap。
- 重复上下文本身放不下时改用简短行列/父项定位，正文仍须完整出现在全部片段中。

32 tokens 是上限而非必须值；优先使用自然边界，可少于 32 或为零。所有重复文字计入硬上限。

### C-04 来源

每个 chunk 保存文档、原节点、页面集合、父单元、片段次序、原文范围和可用 bbox。多页 chunk 不伪装为单页，不构造跨页统一 bbox。重复表头、父项、合并上下文和 overlap 与本片段实际正文可区分。

## 7. Embedding、向量存储与检索

### I-01 Embedding

默认模型为 `intfloat/multilingual-e5-small`，模型名及不可变 revision 可配置但不能为空；默认 revision 由架构锁定并进入构建指纹，不允许用可变 `main` 代表已发布索引。passage 加 `passage: `，query 加 `query: `；批量编码并归一化向量。空 passage/query 拒绝执行，向量数量、维度和非空性必须验证。

应用显式生成 embedding 并传入 Chroma，不注册或依赖 Chroma 默认 embedding function。

### I-02 向量记录

每个 chunk 一条记录，ID 在同一 pipeline 和文档内稳定且唯一。document 保存最终 chunk 正文；metadata 至少支持 document_id、文档名、相对路径、pipeline、chunk 次序和页面来源过滤。复杂来源的具体序列化由架构定义，但查询后必须可还原。

### I-03 检索

对查询生成 E5 query embedding，在所选 collection 执行距离排序。默认 top_k 由配置提供且为正整数；命令参数可覆盖。`--document` 通过 document_id 限定一份文档。

返回 chunk ID、正文、文档来源、全部页面来源、chunk 次序、距离和相似度。`chunks --page` 在 chunk 页面集合包含指定页时命中，而不是假定单页字段。

检索必须严格返回最多 K 条且结果可复现。应用先取得至少 K+1 个候选；若按距离升序排列后的第 K 条与当前候选池最后一条距离完全相同，则扩大候选池，直到最后一条距离已越过第 K 条或已读取当前检索作用域全部记录。随后按距离升序、chunk ID 升序决胜并严格截取 K 条。不得使用显示舍入值判断同分；普通无边界同分查询不得扩大到全部 collection。

## 8. Manifest、增量索引与发布

### I-04 每 pipeline 一份构建记录

manifest 保存 schema、pipeline、collection、embedding 模型、该 pipeline 的 parser/文本化/chunker 配置和规则版本，以及每份已发布文档的 file hash、build 身份、产物引用、页数、字符数、chunk 数和索引时间。`current/new/changed/missing/invalid/unprocessable/unassessed` 是运行时派生状态，不作为事实字段写入 manifest；各状态的输入范围和触发条件见 H-01。影响结果的配置只和同 pipeline 记录比较。

日志、人工视图样式、top_k 和 prompt 不触发重建。parser、表格选择、表头/文本化规则、chunker、tokenizer、embedding 模型或其结果相关参数变化则不兼容。

### I-05 重建范围

- 单份 PDF 新增或内容变化：该文档在所选 pipeline 全阶段执行。
- 所选 pipeline 的全局构建配置不兼容：说明原因，经用户确认后重建该 pipeline 受影响文档，通常为全部文档。
- 未变文档正常跳过；另一 pipeline 不受影响。
- v2 不从中间产物恢复，需更新文档从解析到 embedding 全部重跑。

单文档更新先完整构建新 payload 和必要产物，再替换该文档旧向量，验证数量后更新 manifest。失败时不得把 manifest 写成成功；已有可用旧索引尽量保留。跨 Chroma、文件和 manifest 的具体发布与回滚由架构定义。

### I-06 v2 产物及审核视图

默认保存 Docling raw、完整 ParsedDocument、最终 chunks 及来源、包含 slot/group/winner 和最小原因链的 selection summary，并在 manifest 保存完整构建配置。每份 PDF 还必须生成一个 winner 审核 HTML。

审核 HTML 按文档顺序包含全部 winner：按 rowspan/colspan 展示的肉眼可读结构表格、明确的表头识别成功/失败状态、只在成功时对实际表头单元格着背景色，以及该表最终生成的全部 Embedding chunk 正文。页面不展示 cell 坐标清单、来源范围、评分明细或 embedding 浮点数组；零 winner 输出明确空报告。

审核页面必须读取实际 winner 网格、HeaderDecision 和最终 chunk，不重算展示数据；表格与文本不截断，原文须 HTML 转义。未评分原生回退表格不冒充 winner。HTML 生成失败时，本次该 PDF 的 v2 入库失败，不发布新索引和 manifest，保留旧索引及旧 HTML，错误阶段为 `winner_review_generation`。

完整四工具 raw、全部候选、详细准入/分组/评分和额外审核视图属于可选诊断，不作为检索输入。具体路径由架构定义。

## 9. 索引健康与引导恢复

### H-01 文档状态

DocumentState 仅为：

| 状态 | 触发条件 | 用户含义 |
| --- | --- | --- |
| current | 源文件、manifest 和 collection 对该文档一致 | 可用 |
| new | 源 PDF 存在但 manifest 无记录 | 需增量索引 |
| changed | document_id 相同但 file_hash 已变化 | 需更新该文档 |
| missing | manifest 有记录但源 PDF 不存在 | 需恢复源文件或确认 prune |
| invalid | manifest 已有记录，但该文档的向量或 active artifact 与记录不一致 | 需重建该文档 |
| unprocessable | 源 PDF 存在，但只读预检无法生成可用内容 | 需人工修复 PDF；不进入 Indexer |
| unassessed | pipeline 级阻断使逐文档一致性判断尚未执行 | 先修复 pipeline 级问题，不能解释为文档可用或不可处理 |

全局配置不兼容、manifest/collection 缺失或计数不一致是 pipeline 级 IndexIssue，不把单文档错误显示为 current。检查和修复只针对所选 pipeline。

### H-02 阻断问题与动作

| 问题 | 动作 | 交互默认 |
| --- | --- | --- |
| manifest 缺失但 collection 有记录、未知文档记录、总数不一致、构建配置不兼容 | 全量 force 重建所选 pipeline | N |
| manifest 有记录但 collection 缺失 | 普通增量构建 | Y |
| new、changed、invalid | 对相应文档执行普通增量；invalid 重建后必须重新检查 | Y |
| unprocessable | 不自动入库，显示只读预检失败事实和人工修复建议 | 无写操作 |
| unassessed | 不直接对文档操作；先执行其 pipeline 级问题对应动作 | 继承 pipeline 级动作 |
| missing | 仅明确确认永久删除后 prune | N |
| collection 与 manifest 都为空/不存在 | 正常未构建状态，按需增量构建 | Y |

结构性问题优先于单文档问题。非交互环境不等待输入、不自动修复、不修改配置；输出修复命令并失败返回。交互修复在当前进程调用 Indexer，成功后重新检查，再继续原 search/ask/chat/eval/documents 目标；ingest 本身不通过恢复协调器重试自己。

### H-03 API Key 与服务恢复

documents、ingest、browse、chunks、search 和默认 eval 不要求 OpenRouter API Key。ask、chat、eval --live 在索引可用后检查 key。

交互环境缺 key 时可仅用于当前进程，或经明确确认只更新项目 `.env` 的 `OPENROUTER_API_KEY`；写入原子化，不打印秘密。认证失败允许重新输入 key 后重试一次原 LLM 请求；限流、超时及上游失败不自动循环重试。模型配置缺失或其他配置错误通过 `.env` 修复，不自动改写。

## 10. 问答、聊天与 Prompt

Prompt 只允许使用检索到的 chunk 作为事实依据，要求证据不足时明确拒答，并保留文档和页面引用。不得把配置、manifest、审核视图或工具诊断作为业务事实注入。

ask 执行一次检索和生成；`--debug` 显示命中和最终消息，但不得泄露 API Key。chat 每轮独立检索，不保存跨轮历史，支持 `/help`、`/exit`、`/quit`、`/debug on`、`/debug off`；会话 pipeline 固定。

## 11. 错误、退出与安全

- 预期错误输出简洁原因和下一步，不默认打印堆栈。
- 参数/配置错误、文档目录错误、PDF 解析错误、embedding/向量库/manifest/LLM 错误使用可区分的异常和阶段。
- 正常搜索零结果、证据不足拒答、成功零表不写成系统异常。
- 非交互命令不得等待确认。取消确认不执行写操作，原命令停止。
- `.env`、`.rag/`、业务 PDF、生成产物和日志按用途加入 `.gitignore`；`.env.example` 不含秘密。
- 默认日志不写文档全文、prompt 全文、embedding 或 API Key；显式 debug 只向当前终端展示必要内容。

## 12. 评估与验收

### V-01 功能验收

- v1 输出在必要 pipeline 接入后保持原解析和分块行为。
- v1/v2 collection、manifest、健康检查、prune 和重建互不影响。
- v2 表格结果满足三个表格子需求，ParsedDocument 内容不重复、不伪造来源。
- 所有最终 v2 passage 输入满足模型硬上限，结构单元及 overlap 行为符合第 6 节。
- 每 PDF winner HTML 与实际 winner、表头、文本化及 chunk 完全一致。
- documents、ingest、browse、chunks、search、ask、chat、eval 均按默认或显式 pipeline 正常运行。

### V-02 效果对比

同一批 PDF 和固定问题分别运行 v1/v2。先由人工直接阅读 PDF，建立与 pipeline 无关的标准答案及必需 evidence group；再在检索运行前，把每个组映射到目标构建中的可接受 chunk。检索只返回 chunk，评估器依据映射派生组命中，不能用实际排名反向制定标准答案。

评估必须分别回答相关 chunk 是否前置、回答所需独立事实是否覆盖完整、相似订单是否造成跨文档污染。逐题保留可审计分子分母，并按 PDF 和全题集汇总；功能通过与效果改善分开报告，不预设提升比例。数据集分层、指标公式、空值规则、人工审核 HTML、版本汇总和变更控制以[检索效果评估需求](requirements/v2.0/retrieval-evaluation.md)为唯一权威。

本阶段只量化检索，不把 LLM 回答质量混入检索指标。默认 pytest 不访问 OpenRouter、不下载模型；外部模型/LLM 测试显式标记。

## 13. 架构与验收边界

以下事项按已确认边界处理：

| 项目 | 当前处理 |
| --- | --- |
| Docling 类型到 ParsedDocument 的字段映射、产物路径和配置指纹 | 已完成架构设计、真实 SDK/PDF/文件系统技术验证及 fixture 契约测试，不新增业务语义 |
| Docling 部分页失败发布政策 | 等真实可复现样例，不在当前支持范围内造规则 |
| 检索评估题集和报告 | ground truth、双 pipeline test set、正式 JSON/HTML、汇总与对比均已按检索效果评估子需求完成 |

当前没有尚待业务确认而可以由实现自行猜测的表头、表格转换或分块规则。
