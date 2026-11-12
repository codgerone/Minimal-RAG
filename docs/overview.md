# 系统概览（V4.1）

## 这是什么

一个本地运行的 PDF 知识库 RAG 系统：把 `documents/` 里的 PDF（合同、订单、发票等，包括扫描件）解析、切块后建立向量索引和 BM25 倒排索引，然后支持混合检索、依据证据的问答，以及用人工标注的题目评估检索效果。问答有两路：纯检索（原问题检索一次后作答），以及多轮检索（LLM 读过检索到的材料后，为还缺的部分派生子问题或换方式再检索，再作答）。

系统的每个关键环节都可以替换（OCR 引擎、解析器、表格提取器、表格结构、表格写法、表格检索文本、分块器、编码器、检索器、LLM）。用一个配置文件选择各环节的实现，就得到一份"装配"；不同装配各自建索引，可以放在一起比较效果。它也是 Sales Operations Agent 的检索模块原型。

## 能做什么

| 命令 | 作用 |
|---|---|
| `python -m rag status` | 查看哪些文档已入库、需要更新或可以清理 |
| `python -m rag ingest` | 入库新增或修改过的 PDF；`--force` 全量重建，`--prune` 清理已删除的文档，`--all-configs` 对所有配置执行 |
| `python -m rag chunks` | 浏览已入库的 chunk（可按 `--document`、`--page` 筛选） |
| `python -m rag search "问题"` | 检索，显示检索范围、命中的 chunk 及其在向量与 BM25 两路的名次 |
| `python -m rag ask "问题"` | 检索后让 LLM 依据证据回答；`--debug` 显示命中内容和完整 Prompt（多轮检索时显示问题列表） |
| `python -m rag chat` | 连续提问（每个问题独立检索，不记忆上下文） |
| `python -m rag eval` | 用标注题目评估检索效果并生成报告（K 取配置中的 `[eval] top_k`，内置为 3）；`--budgets 3,6,9,12` 每题检索一次、每档 K 各出一份报告。多轮检索一路时生成多轮检索评估页 `reports/multi_round/NAME/`（`--name`，默认配置名；`--cases`、`--generations` 只跑部分题或部分生成） |
| `python -m rag anchors` | 在 PDF 上定位标注的 excerpt，生成高亮审核文件；核对后加 `--write` 写入标注。扫描件在 OCR 派生的文字层 PDF 上定位，纸面文字与 OCR 读出的不同时近似匹配，审核页蓝色标出 |
| `python -m rag config list` / `config show NAME` | 查看装配配置（`configs/` 的七份内置装配与 `configs/extra/` 的 `plain_text`） |
| `python -m rag compare RUN... --name NAME` | 把几次评估并排对比（每组第一次为基线），生成 `reports/compare/NAME/`；`--table-spec` 给出表格证据的对照组、排除组与结论 |
| `python -m rag query-compare --name NAME` | 在 13 种组合上评估查询侧模块（分解、去编码、写法、融合、合并）的全部配置，生成查询侧对比页 `reports/query/NAME/`（[rules/query-planning.md](rules/query-planning.md)）；规划调用 LLM，结果缓存 |
| `python -m rag report` | 由已保存的结果重新生成入库审核页、评估报告、对比页和报告首页（不重新入库、不重新评估） |

`ask`、`chat`、`eval` 走哪一路由配置 `[query] mode` 决定（见"两路查询"），加 `--query-mode retrieval|multi_round` 可临时换路。

`search`、`ask`、`chat` 会按问题中的标识编码限定检索范围（见下节）；`--document` 可手动指定只在某个文档内检索。`search`、`ask`、`chat`、`eval` 可加 `--retriever semantic|bm25|hybrid` 临时换检索器，加 `--table-return-unit chunk|table` 临时改变命中表格时的返回方式，加 `--bm25-weight α` 临时改变混合检索中 BM25 的权重，都不需要重建索引。

所有命令都可以加 `--config NAME` 选择装配，默认使用 `structured`（也可以用环境变量 `RAG_CONFIG` 指定）。

## 文档标识表

`documents/documents.toml` 为每份 PDF 登记一个或多个标识编码（由你分配）。问题中出现某个编码时，只在对应文档内检索；出现多个文档的编码时，在这些文档内检索；一个都没有时，在全库检索。编码比较时忽略大小写、全半角、空格和标点。

```toml
[[document]]
file = "ORDER PERU 24-12 ANDET.pdf"
codes = ["ORDER2025001"]
```

- 编码不少于 4 个字符（忽略标点后），不能重复登记给两份文档，否则命令报错。
- `status` 列出还没有登记编码的 PDF。
- 配置 `[retriever] document_filter = false` 可关闭过滤；改标识表和开关都不需要重建索引。
- 规则细节：[rules/document-filter.md](rules/document-filter.md)。

## 内置装配

`configs/` 中的七份装配只在表格处理上不同，其余相同：没有文字层的扫描页先 OCR（见下节）；Docling 版面解析；四个工具九种策略提取表格、评分选出最佳结构，跨页的续表与前表合并（[rules/table-continuation.md](rules/table-continuation.md)）；按阅读顺序把正文、列表项合并成块，表格单独成块，每块不超过 512 token（按 multilingual-e5 分词器计数，[rules/chunking.md](rules/chunking.md)）；bge-m3 向量索引 + BM25 倒排索引，混合检索；按标识编码限定文档；命中表格时返回整张表；问答取 Top-4，评估取 Top-3。不指定 `--config` 时用 `structured`。

| 装配 | 表格结构 | 表格写法 | 索引中放什么 | 入库时调用 LLM |
|---|---|---|---|---|
| `structured`（默认） | 规则判定表头 | Markdown 行，表头写成一行列名路径 | 表格行 | 否 |
| `structured_labeled` | 规则判定表头 | 表头映射：每格写成"列名: 值" | 表格行 | 否 |
| `structured_llm` | LLM 结构标注（表头、分节、隐式合并），不通过时退回规则 | Markdown 行；分节标题重复进该节的每一块 | 表格行 | 是 |
| `structured_llm_labeled` | 同上 | 表头映射 | 表格行 | 是 |
| `structured_llm_lead` | 同上 | Markdown 行 | 表格行，每块开头加一句引导句 | 是（与标注同一次调用） |
| `structured_llm_labeled_lead` | 同上 | 表头映射 | 同上 | 是（同上） |
| `structured_summary` | 不判定：LLM 读原样表格写摘要 | — | 每张表只放一段摘要 | 是 |

`structured_summary` 的摘要不通过校验时，该表按规则表头、Markdown 行退回为表格 chunk。返回单元是查询设置，同一份索引可以只返回命中的 chunk（`--table-return-unit chunk`）或返回整表，因此七份装配给出 13 种组合（摘要只能返回整表）；`bash scripts/eval_tables.sh <名称>` 依次评估这 13 种组合并生成对比页。

表格怎么写见 [rules/table-text.md](rules/table-text.md)，LLM 怎么处理表格见 [rules/table-llm.md](rules/table-llm.md)。返回整表时交给回答 LLM 的整表文本，七份装配相同：每行原样写出，不含结构判定。入库用的 LLM 是 `google/gemini-3.8-flash`，结果缓存在 `.rag/llm-cache/`，重建索引时不重复计费。

`configs/extra/plain_text` 同样可以用 `--config` 选择：PyMuPDF 按页取文本，每页内 300 字符、重叠 50，不处理表格；不在 `ingest --all-configs` 中。

新建装配：复制 `configs/structured.toml` 改几行即可，启动时会校验组合是否合法。

## 扫描件

每份 PDF 入库时逐页判定：有文字层的页原样使用；没有文字层、但有图片的页是扫描页，先 OCR；什么都没有的页跳过。所以部分页扫描的 PDF 只 OCR 扫描页。

- OCR 只认字（RapidOCR：每行、每词的文字、框、置信度），结果存为 `.rag/ocr/<文档>/ocr.json`，各配置共用，OCR 设置不变时不重做。
- 由原 PDF 和 OCR 结果派生一份带隐形文字层的 `ocr.pdf`，生成后自检（读回的词与 OCR 逐词相同、词框偏差 ≤ 1 点）。之后的解析、表格、分块、坐标都按它进行，标题、段落、表格等结构由 Docling 与表格工具从它恢复，与数字 PDF 相同。
- 页面方向（含页面的 `/Rotate`）、轻微倾斜自动纠正；入库审核页的"OCR"标签页逐页显示判定、方向、纠偏和叠在原图上的识别框，低置信度的词标红。
- 配置 `[ocr]`（`use = "rapidocr"`，`dpi = 300`）；七份内置装配与 `plain_text` 都有。没有这一节时扫描页不读，全部页面都没有文字层的 PDF 为"无法读取"。

规则见 [rules/ocr.md](rules/ocr.md)；为什么 OCR 独立成一步、而不是让解析器自己 OCR，见[决策 020](decisions/020-ocr-route.md)。不认手写、印章、签名，也不纠正 OCR 认错的字。

## 检索方式

每份装配入库时建两份索引：向量索引（bge-m3）和 BM25 倒排索引（配置 `[lexical]`：默认去西、英停用词，按文档语言词干化）。检索器由 `[retriever] use` 选择：

| 检索器 | 排序依据 |
|---|---|
| `hybrid`（内置装配默认） | 向量排名与 BM25 排名按倒数排名融合（RRF）；BM25 一项的权重为 `[retriever] bm25_weight`，默认 1 |
| `semantic` | 向量相似度 |
| `bm25` | BM25 关键词得分 |

BM25 只能匹配问题中照抄原文的词（外文术语、型号、公司名、数字），中文部分依靠向量检索。规则细节：[rules/lexical.md](rules/lexical.md)。

**命中表格即返回整表**（`[retriever] table_return_unit = "table"`，内置装配默认）：检索命中表格的任一块时，返回整张表，同一张表在结果中只占一个位置，名次取它排名最靠前的一块，空出的位置由后面的结果补上。整表文本在入库时存入整表库（不做向量化）。长表格被切成多块后，分组标题、合计行与数据行分处不同块，整表交给 LLM 才能看懂（[决策 012](decisions/012-whole-table-return.md)）。代价是返回的文本变长：本库最大的表约 5000 token，对表格大小不设上限。

## 两路查询

| 一路 | `[query] mode` | 怎么做 | 每题耗时与花费 |
|---|---|---|---|
| 纯检索（内置默认） | `retrieval` | 原问题检索一次，取 Top-K 交给问答 LLM 作答 | 检索约 1 秒；作答一次 |
| 多轮检索 | `multi_round` | 第 1 轮用去掉标识编码的原问题检索；之后每次由 LLM 读材料，判断哪些问题可回答、接受哪些材料为证据，为还不能回答的问题派生子问题（如先查出型号再问单价）或换方式再检索，最后作答 | 约 22 秒、$0.014（规划模型 `google/gemini-3.8-flash`） |

多轮检索的设置在 `[multi_round]`，都是查询设置，改动不需要重建索引：

| 设置 | 含义 | 默认 |
|---|---|---|
| `max_llm_calls` | LLM 调用上限，含作答调用 | 3 |
| `units_per_attempt` | 每个问题、每种方式、每次检索交给 LLM 的单元数（跳过已交过的） | 3 |
| `fallback_schedule` | 兜底检索的安排：`"A"` 为一次用完全部方式；也可写成分组列表，每组一次检索 | `"A"` |
| `model` | 规划与作答的模型 | `google/gemini-3.8-flash` |

一个问题最多检索两次：第 1 次，以及一次兜底（往下取、改写、HyDE、step-back 同时检索，原问题另加补上翻译）。LLM 只给判断与文字，问题列表、状态与检索由代码维护；每次调用的结果缓存在 `.rag/llm-cache/`。规则见 [rules/multi-round.md](rules/multi-round.md)，取舍见[决策 018](decisions/018-multi-round-retrieval.md)。

## 当前效果（K=3，9 份 PDF，数据集 4.1.0：58 题，52 道可回答，混合检索，返回整表）

| 装配 | 命中率 | 完整覆盖 | 证据组召回 | MRR | Chunk 精确率 | 跨文档污染 | 文档识别 | 每题返回 token |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| structured（默认） | 92.3% | 73.1% | 82.7% | 86.9% | 46.2% | 0.0% | 52/52 | 1315 |
| structured_labeled | 92.3% | 80.8% | 85.7% | 86.9% | 47.4% | 0.0% | 52/52 | 1307 |
| structured_llm | 92.3% | 73.1% | 82.7% | 86.9% | 46.2% | 0.0% | 52/52 | 1315 |
| structured_llm_labeled | 92.3% | 78.8% | 84.6% | 87.8% | 47.4% | 0.0% | 52/52 | 1304 |
| structured_llm_lead | 96.2% | 84.6% | 89.7% | 87.5% | 50.0% | 0.0% | 52/52 | 1290 |
| structured_llm_labeled_lead | 96.2% | 80.8% | 87.1% | 89.4% | 50.0% | 0.0% | 52/52 | 1286 |
| structured_summary | 94.2% | 82.7% | 87.8% | 89.4% | 50.0% | 0.0% | 52/52 | 1248 |

返回整表时，结果按"返回单元"计：一张整表算一个结果，视为返回了它的全部 chunk；Chunk 精确率、跨文档污染也按结果计。每题返回 token 是全部可回答题返回文本之和（按 multilingual-e5 分词器计数）除以 52；structured 每题最多 2603。

按文档类型分列的完整覆盖题数（每题按它所属文档的类型归类；13 种组合见对比页 `reports/compare/v4.1/`）：

| 组合 | 数字 PDF（5 份，32 题） | 扫描 PDF（3 份，14 题） | 部分扫描（1 份，6 题） |
|---|---:|---:|---:|
| structured（RM-T） | 22 | 12 | 4 |
| structured_labeled（RL-T） | 25 | 13 | 4 |
| structured_llm_lead（LM+-T） | 25 | 14 | 5 |
| structured_summary（S-T） | 24 | 14 | 5 |

扫描件是 4 份真实扫描合同（150–300 dpi，其中一份第 1 页有文字层），每份 5–7 题，含 1 道无答案题。各组合未完整覆盖的扫描件题目，漏掉的证据组都只需 1 个返回单元、K = 4–7 即可覆盖：证据所在的 chunk 都已入库，差距在排序（原因未做对照实验）。数字 PDF 的 32 题与 V3.15 逐题相同（文档过滤开启时，新文档不进入这些题的检索范围）。

以下段落为各版本测量时的数据集上的结果（V3.14、V3.15 为数据集 3.4.1：5 份数字 PDF、34 题、32 道可回答；V3.13 及以前为数据集 3.2.0、24 题）。

13 种组合在 K = 3、6、9、12 下的完整覆盖题数（`bash scripts/eval_tables.sh --budgets 3,6,9,12`，对比页 `reports/compare/v3.14-baseline/`；R/L 为规则 / LLM 表格结构，M/L 为 Markdown / 表头映射写法，+ 为引导句，S 为摘要，-C / -T 为返回命中 chunk / 整表）：

| 组合 | K = 3 | K = 6 | K = 9 | K = 12 |
|---|---:|---:|---:|---:|
| RM-C、RM-T | 19、22 | 24、27 | 28、28 | 29、29 |
| RL-C、RL-T | 19、25 | 24、27 | 25、28 | 27、29 |
| LM-C、LM-T | 19、22 | 22、27 | 25、28 | 29、29 |
| LL-C、LL-T | 18、25 | 23、27 | 25、28 | 27、29 |
| LM+-C、LM+-T | 20、25 | 24、27 | 28、28 | 29、29 |
| LL+-C、LL+-T | 19、25 | 24、27 | 26、28 | 28、29 |
| S-T | 24 | 26 | 28 | 29 |

K = 12 时各组合都未完整覆盖 CONTRACT-105-Q003（交货地点）、Q011（罚金条款）、Q014（交货地点）。数据集 3.4.1 上 plain_text（返回命中 chunk）完整覆盖 25.0%、命中 46.9%、每题返回 215 token。

**多轮检索**（V3.15，structured 返回整表，数据集 3.4.1，4 次 LLM 生成的平均，`reports/multi_round/a-v3/`）：上限 3 时交给 LLM 的材料对 30/32 题凑齐全部信息项，凑齐 78/80 个信息项；单轮按同样的 token 截取，原问题检索一次为 25 题、去编码 + 原句 + 译文为 26 题。每题平均调用 LLM 1.4 次（多数题第一次调用即作答）、交给 LLM 约 1800 token。误判可回答（判为可回答但证据未凑齐）1 题：CONTRACT-105-Q010 需用签约日期换算各批次日期，LLM 按原文的"签约后 N 天"作答。答案正确性尚未评估。

表格题另看表格证据指标（[rules/evaluation.md](rules/evaluation.md)"表格证据指标"），以 12 道表格题、排除 3 个与表格入库无关的证据组计，每题所需返回单元数：structured_llm_lead 1.92，structured、structured_llm、structured_summary 2.08，structured_llm_labeled_lead 2.17，structured_labeled 2.33，structured_llm_labeled 2.42；13 种组合的对比见 `reports/compare/v3.13-llm-tables/`。

V3.12 测量的纯向量检索（表格写法为当时的表头映射）：plain_text 58.3% / 29.2% / 39.2% / 77.8% / 45.8%，structured 91.7% / 79.2% / 83.6% / 80.6% / 47.2%（次序为命中率、完整覆盖、证据组召回、MRR、Chunk 精确率）。

证据对应的 chunk 按 PDF 坐标判定：excerpt 写了什么，就要求检索到包含这些内容的全部 chunk。部分证据组需要的结果数超过 3，K=3 时不可能被覆盖，因此完整覆盖率的上限为 plain_text 17/24、structured 24/24（报告中标出这些证据组）。

以下数字为数据集 2.1.0 上的测量：关闭文档过滤时（同一题目），plain_text 命中率 4.2%、跨文档污染 56.9%，structured 命中率 20.8%、跨文档污染 69.4%。题目都含标识编码，所以以上结果只反映识别正确时的效果。剩余差距在文档内部的排序上。

V3.5 的实验表明：用 multilingual-e5-small 时，chunk 正文只要含中文，就会对中文问题普遍靠前，与内容是否相关无关（[决策 005](decisions/005-table-text-format.md)）。V3.4 的表格文本含中文脚手架，structured 的完整覆盖 37.5% 有一部分来自这一偏向；去掉后表格题的排名回到内容本身的匹配水平。剩余的表格题失败主要出在表头未认出的表格（没有列名可写）和跨语言匹配。

V3.6 修正了表头判定的类型词法（[决策 006](decisions/006-cell-type-sets.md)），PO 25-19 的交货日期表和产品表由认错、认不出改为认对。structured 的命中率由 70.8% 降到 62.5%，下降的两题此前靠交货日期表的错误文本命中；改为正确文本后，该 chunk 排在第 4，与第 3 名的相似度只差千分之几。文档内排序不稳定仍是主要差距。

V3.7 继续调整表头判定的取样与类型转变（[决策 007](decisions/007-header-sampling.md)）：PO 25-15 的分批交货表（日期表头、数量表身）改为认对；只有一条数据行的表格（PO 24-12 产品表）按规则认不出。指标与 V3.6 相同：这些表格的 chunk 原本就排第 1，或排名受中文问题与英文表格的跨语言匹配限制，与表头无关。

V3.8 把编码器由 multilingual-e5-small 换成 bge-m3（[决策 008](decisions/008-embedder-bge-m3.md)），chunk 不变。structured 完整覆盖由 7/24 升到 14/24、MRR 由 42.4% 升到 72.2%；plain_text 命中由 9/24 升到 14/24、MRR 由 46.5% 升到 74.3%。用 e5-small 时把问题译成文档语言，structured 完整覆盖为 11/24；用 bge-m3 时译文只再多 1 题，跨语言差距基本消除。bge-m3 没有中文偏向，泛化 chunk 反复进入 Top-3 的次数也减少。各编码器的逐题对比见 `reports/compare/v3.8-encoders/`。

V3.9 加入 BM25 倒排索引，内置装配默认混合检索（[决策 009](decisions/009-hybrid-retrieval.md)）。题目原先以文档标题开头，标题词只出现在标题 chunk 中，使 BM25 把标题 chunk 排在前面，也干扰向量检索；数据集 3.2.0 去掉标题、只保留标识编码，纯向量 structured 完整覆盖由 14/24 升到 18/24。混合检索相对纯向量：structured 变好 2 题、变差 0 题（都是问题含原文术语、型号的题）；plain_text 变好 1 题、变差 2 题，变差的两题各需 4–9 个 chunk 才能覆盖一个证据组，混合检索返回的相关 chunk 更多但未覆盖任何证据组。两套装配的证据组召回、MRR、Chunk 精确率都提高。逐题对比见 `reports/compare/v3.9-hybrid/`。

V3.10 让列表不再单独成块，与相邻正文按预算合并（[决策 010](decisions/010-list-merge-and-budget-sweep.md)）；表格仍单独成块，表前引导句留待由 LLM 理解表格结构时一起处理。列表与它的条目标签落进同一块后，CONTRACT-105-Q004 在各预算下都由部分覆盖变为完整覆盖；但分界整体移动，把另两题原本同块的证据切开。单次评估中 structured 混合检索命中、完整覆盖各少 1 题。只改预算、规则不变也会使 0–2 题改变状态，因此分块改动改为按多预算配对比较验收（[rules/evaluation.md](rules/evaluation.md)）：448–576 token 五档下两套规则的完整覆盖差为 0、0、−1、+1、+1，判为随机扰动，按设计理由接受。纯向量检索下有一题在 4 档中变差，原因是合同开头一块与价格表相似度几乎打平时的翻转。

V3.11 把跨页的续表与前表合并成一张表（[决策 011](decisions/011-table-continuation.md)，[rules/table-continuation.md](rules/table-continuation.md)）。本库只有 PO 25-19 的数量表跨页，合并后第 3 页各行带上列名，其他文档的 chunk 不变。合并后各行变长，表内分界移动，PO-25-19-Q002 所需的分组标题（型号只写在这一行）与 ELUC 行被切进相邻两块，structured 混合检索完整覆盖少 1 题、纯向量命中少 1 题；根因是分组标题不写进它所管的各行，留给后续的表格优化。审核页"续表判定"逐对列出各条件的实际值。

V3.12 在命中表格时返回整张表（[决策 012](decisions/012-whole-table-return.md)），chunk 不变。structured 混合检索完整覆盖由 17/24 升到 20/24，变好的是 PO 25-19 的三道题，没有题变差：PO-25-19-Q001、Q003 的表格证据组原先要前 12–14 个结果才能找全，现在前 1–3 个；V3.11 变差的 Q002 恢复。每题返回 token 由 1149 增到 1860。只多返回几块也能得到相近的总分（不返回整表、K=6 时完整覆盖同为 20/24，每题 1939 token），但多覆盖的是 CONTRACT-105-Q001、Q007 和 PO-25-19-Q002，PO-25-19-Q001、Q003 在 K=6 下仍找不全。纯向量完整覆盖由 16/24 升到 19/24。plain_text 没有表格，结果不变。逐题对比与所需 K 的变化见 `reports/compare/v3.12-whole-table/`。

V3.13 用 LLM 处理表格（[决策 013](decisions/013-llm-tables.md)）：表格处理拆成表格结构、写法、表格检索文本三个环节，七份装配覆盖全部组合。LLM 只标注结构（表头、分节、隐式合并），chunk 中的表格文字全部由代码从原单元格写出；12 张表的标注全部正确，但只有 PO 25-19 数量表有分节，其余表与规则判出的表头相同，返回整表时 structured_llm 与 structured 逐题相同。引导句使表格题所需返回单元由 2.08 降到 1.92；摘要在排除 3 组后为 2.08，不排除时最少（3.42 对 3.58）。这些差距只有 1–3 题，与 LLM 重新生成一次文字带来的变动同一量级，引导句与摘要分不出优劣。默认装配改为 Markdown 写法（只返回命中 chunk 时明显优于表头映射，返回整表时相近），完整覆盖由 20/24 变为 18/24，少的是 PO-25-19-Q001、Q003：两题需要的第 3、4 个返回单元次序互换；命中由 22 升到 23。整表文本改为原样网格写出，每题返回 token 由 1860 降到 1255。

V3.15 加入多轮检索作为第二路查询（[决策 018](decisions/018-multi-round-retrieval.md)），默认仍为纯检索。收益主要来自 LLM 读过第一轮材料后派生的子问题（多跳、复合问题）；兜底检索只在 5 题中出现，各兜底方式之间尚分不出高下。上限 4、5 只比 3 多 1 题（CONTRACT-105-Q003 的交货地点在第 3 轮检索才凑齐），上限 2 只剩第一轮材料、明显变差。

V3.14 在查询侧对分解、去编码、改写、翻译、HyDE、step-back、融合与合并方式、混合检索的 BM25 权重逐项摸底（[决策 015](decisions/015-query-modules.md)、[016](decisions/016-strip-codes-switch.md)、[017](decisions/017-bm25-weight.md)），默认配置不变。数据集扩为 34 题（新增分解题、多跳题、隐含前提题与无答案题）。按凑齐向量的支配关系、4 次 LLM 生成逐题判定：去编码 + 原句 + 译文 RRF 相对原问题检索一次，32 题中 12 题 4 次都变好、5 题都变差；改写、step-back 变差不少于变好；HyDE、预先分解以各有优势为主；分解的收益在合成一个返回顺序后被抵消。这些结论用于 V3.15 多轮检索。逐题对比见 `reports/query/v3.14/`，各措施的逐题原因见 `reports/experiments/v3.14-factors/`。

V4.1 支持扫描 PDF（[决策 019](decisions/019-line-orientation.md)、[020](decisions/020-ocr-route.md)）：OCR 作为解析前独立的一步，结果作为事实保存，派生带隐形文字层的 PDF 交给现有解析与表格流程。路线由对照实验决定：把 5 份数字 PDF 渲染成模拟扫描件，"OCR 事实 + 文字层 PDF"与 Docling 解析器内 OCR 的完整覆盖相同（24/32），但前者结构与数字原件相同的表格多 3 张（9 对 6），因为多个表格工具选优仍然起作用。RapidOCR 的行方向分类器会把部分正向长行判为倒置、转过来后认成乱码，改为分类器开、关各认一遍取置信度高者，4 份真实扫描件中低置信度词的比例由 4.5–8.0% 降到 1.9–3.1%。现有 5 份 PDF 的 chunk 在七份装配下逐字节不变。

## 不做什么（当前范围外）

手写、印章、签名的识别，OCR 后的文字纠错，图片内容理解、重排模型、从文档内容自动抽取编号、按文档属性（类型、客户、日期）过滤、多轮对话记忆、Agent/工具调用、Web 界面。

纯检索一路不分解、不改写问题；预先分解、改写等查询侧模块只在 `query-compare` 中实验（[决策 015](decisions/015-query-modules.md)），多轮检索一路由 LLM 读过材料后再派生子问题、写兜底文本。最终答案的正确性尚未评估。

## 怎么验收

1. 打开 `reports/index.html`：每份装配都显示"可用"，文档都是"已入库"，向量索引、倒排索引、整表库都是"完整"。点进解析、表格、分块页，抽查内容是否与 PDF 一致。
2. 含扫描件的文档：入库审核页的"OCR"标签页逐页核对判定、方向与识别框。
3. 打开最新的评估报告：指标不低于上表，"按文档类型"一节各类型都有数字；文档识别准确率 52/52；"含文字兜底"为 0，或者已抽查确认。
4. `uv run pytest` 全部通过。

## 数据放在哪里

| 位置 | 内容 | 进 git |
|---|---|---|
| `documents/` | 你的 PDF | 否 |
| `documents/documents.toml` | 文档标识表：每份 PDF 的标识编码 | 否 |
| `configs/`、`configs/extra/` | 装配配置：七份内置装配；`extra/` 放不参与 `--all-configs` 的 `plain_text` | 是 |
| `eval/ground-truth/` | 人工标注的问题、信息项与证据，以及每段证据在 PDF 上的已确认位置 | 是 |
| `reports/` | 给人看的报告：`index.html` 入口、`ingest/` 入库审核页、`eval/` 评估报告、`multi_round/` 多轮检索评估页（`data/runs.json` 为运行事实，页面由它生成） | 评估报告、多轮检索评估页进 git，入库审核页不进 |
| `.rag/indexes/<配置名>/` | 机器数据：`manifest.json`、向量库 `chroma/`、BM25 倒排索引 `bm25.sqlite`、整表库 `tables.sqlite`、各文档中间结果 JSON（含 chunk 来源区域、表格结构与 LLM 调用记录；审核页由它生成） | 否 |
| `.rag/ocr/` | 扫描件的 OCR 事实 `ocr.json` 与派生的文字层 PDF `ocr.pdf`，各配置共用 | 否 |
| `.rag/llm-cache/` | LLM 调用的缓存：入库时的表格处理、查询规划、多轮检索 | 否 |
| `eval/table-structure/` | 测试集表格的结构标注正确答案（审核页据此判定 LLM 标注是否正确） | 是 |
| `tmp/anchor-review/` | excerpt 定位审核文件（临时） | 否 |
