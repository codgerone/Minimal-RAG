# 系统设计（V3.14）

## 1. 数据主链

```
入库   PDF ─→ Parser ─→ 主文档（文本 / 列表 / 表格位置，按阅读顺序）─→ 文档语言
              │
              └─→ TableExtractor × N ─→ 准入·分组·评分选优 ─→ 续表合并 ─→ 规则表头判定
                    ─→ TableStructurer（表格结构）─→ TableFormatter（入库文本）+ 整表文本 ─→ 表格检索文本（行 / 行 + 引导句 / 摘要）─┐
                                                                                                                         ↓
                       组装后的文档（ParsedDocument）─→ Chunker ─→ chunks ─→ 来源区域 ─┬─→ Embedder ─→ Chroma（向量索引）
                                                                                       ├─→ Analyzer ─→ SQLite（BM25 倒排索引）
                                                                                       ├─→ 整表库（整表文本与成员 chunk）
                                                                                       └─→ 中间结果 JSON ─→ 入库审核页
查询   问题 ─→ 检索范围（文档标识表）─┬─→ Embedder ─→ 范围内向量排名 ─┐
                                    └─→ Analyzer ─→ 范围内 BM25 排名 ─┴─→ Retriever（选用一路或 RRF 融合，确定性排序）─→ 返回单元（整表库）─→ Prompt ─→ LLM ─→ 回答
评估   标注题目（含 excerpt 坐标）─→ 同一检索范围、Retriever 与返回单元 ─→ 证据判定（excerpt 坐标 × chunk 来源区域）─→ 指标 ─→ 评估报告 ─→ 对比页
查询侧对比  问题 ─→ 规划（LLM：分解、写法）─→ 每个问题的检索文本（去编码）─→ 各自检索 ─→ 问题内 RRF 融合 ─→ 跨问题合并（轮流取 / 全局 RRF / 不合并）─→ 凑齐名次 ─→ 查询侧对比页
```

**查询侧模块**（分解、去编码、写法、融合、合并，[rules/query-planning.md](rules/query-planning.md)）只在查询侧对比中使用；`ask`、`chat`、`eval` 仍用原问题检索一次。规划与检索执行分开：规划输出问题列表与每个问题的各写法（`rag/query/planning.py`），检索执行按配置取用其中一部分（`rag/query/execution.py`）。各模块的评估结论与在多轮检索中的用法见 [decisions/015-query-modules.md](decisions/015-query-modules.md)。

各阶段的数据模型定义在 `rag/models.py`（文档与 chunk）和 `rag/ingest/tables/models.py`（表格证据）。设计原则：解析器、工具和 LLM 的原始结果是"事实"，不会被后续阶段原地修改；选优、表头、结构标注是否采用这类判断单独记录，审核页可以逐层追溯。

**表格的三个环节**：一张表经过选优与续表合并后，先由规则判定表头（这是事实，续表判定也要用到）；然后：

1. **表格结构**（`[table_structure]`）：决定文本从什么结构写出——规则表头（`header_rules`），或 LLM 结构标注（`llm_annotation`，含表头行、分节、隐式合并；未通过校验时退回规则表头）。
2. **入库文本**（`[table_formatter]`）：按结构写成 `markdown_rows` 或 `labeled_rows` 文本，分块器从它切出表格 chunk。另生成一份不经过任何结构判定的整表文本（`grid_rows_v1`），存入整表库，返回整表时交给回答 LLM。
3. **表格检索文本**（`[table_index_text]`）：索引里为这张表放什么——表格行（`rows`），每块开头加引导句的表格行（`rows_with_lead`，引导句来自结构标注的同一次调用），或只放一个摘要（`summary`，另一次 LLM 调用，LLM 读原样网格写出，不用前两个环节的结果；前两个环节只在摘要不通过、该表退回表格 chunk 时起作用）。

规则见 [rules/table-text.md](rules/table-text.md)、[rules/table-llm.md](rules/table-llm.md)，原因见 [decisions/013-llm-tables.md](decisions/013-llm-tables.md)。

## 2. 可替换环节

只有确实需要比较或替换的环节才定义接口（Python `Protocol`），实现登记在 `rag/registry.py`：

| 接口 | 定义位置 | 内置实现 |
|---|---|---|
| Parser | `rag/ingest/parsers/__init__.py` | `pymupdf_pages`、`docling_layout` |
| TableExtractor（可配多个） | `rag/ingest/tables/extractors.py` | `pymupdf`、`camelot`、`docling`、`unstructured` |
| TableStructurer | `rag/ingest/tables/structure.py` | `header_rules`、`llm_annotation` |
| TableFormatter | `rag/ingest/tables/formatters.py` | `markdown_rows`、`labeled_rows` |
| 表格检索文本 | `rag/ingest/pipeline.py`（`TableSteps`） | `rows`、`rows_with_lead`、`summary` |
| Chunker | `rag/ingest/chunkers/__init__.py` | `characters`、`structured_tokens` |
| Embedder | `rag/index/embedder.py` | `bge_m3`、`e5_small`、`e5_large_instruct`、`qwen3_embedding_0_6b` |
| Retriever | `rag/query/retriever.py` | `semantic`、`bm25`、`hybrid` |
| LLM | `rag/query/llm.py` | `openrouter` |
| 跨问题合并（Selector） | `rag/query/execution.py` | `round_robin`、`rrf` |

表格检索文本的三种取值由分块器按名称处理，只有 `summary` 需要一个 LLM 步骤对象（`LLMSummary`），所以没有单独的 `Protocol`。

编码器都是固定版本（revision）的 sentence-transformers 模型，按 float32 加载，查询与文档各加固定前缀或指令，向量 L2 归一化；编码前用模型自己的分词器检查 chunk 不超过其输入上限，超过则报错而不截断。模型选择见 [decisions/008-embedder-bge-m3.md](decisions/008-embedder-bge-m3.md)。

文档语言判定、表格选优、续表判定、规则表头判定、检索范围识别、向量库（Chroma）、BM25 分析器与倒排索引、整表库与返回单元、查询规划（LLM）、问题内融合（RRF）目前都只有一种实现，作为普通模块存在，等出现第二种实现时再抽象成接口。

**新增一种实现**：写一个满足接口的类，在 `registry.py` 对应的字典里登记一个名字，然后在配置文件里 `use = "名字"` 即可，主流程不用改。

**装配校验**（`registry.assemble`，在读取任何 PDF、加载任何模型之前执行）：
- 实现名必须存在，参数必须是构造函数能接受的；
- `docling` 表格提取器只能搭配 `docling_layout` 解析器，`characters` 分块器只能搭配 `pymupdf_pages`；
- 解析器不提供表格位置时，不能配置表格提取器，也不能配置三个表格环节；解析器会产出表格时，三个表格环节都必须配置；
- `rows_with_lead` 需要 `llm_annotation`；`summary` 只能搭配 `header_rules`，返回单元不能是 chunk；`llm_annotation`、`summary` 需要 `model`，并需要 `structured_tokens` 分块器；
- 表格提取器不能重复。

`docling_layout` 解析器不配任何表格提取器也是合法组合：这时所有表格都采用 Docling 原生结构。

## 3. 配置与构建指纹

`configs/*.toml` 是内置装配：七种表格处理组合各一份，`ingest --all-configs` 重建它们，13 种查询组合（加上返回单元）都从这些索引评估（`scripts/eval_tables.sh`）。`configs/extra/*.toml` 是不参与 `--all-configs` 的配置，同样按名称用 `--config` 选择；名称在两处之间不能重复。

配置中解析、表格（提取器与三个表格环节）、分块、编码相关的设置，加上编码器身份（模型名与版本）和 `RULES_VERSION`，一起计算出**构建指纹**，记录在索引的 `manifest.json` 中。用 LLM 的表格环节，其模型、提示词版本、推理强度、长度与上下文规则也进入指纹。配置了 `[lexical]` 时，其分析器设置（去停用词、词干化）也进入指纹；没有这一节的配置不建 BM25 索引，指纹也不含它。检索器 `[retriever] use`、命中表格时的返回单元 `table_return_unit`、混合检索中 BM25 排名的权重 `bm25_weight`（默认 1，[decisions/017-bm25-weight.md](decisions/017-bm25-weight.md)）、检索的 `top_k`（问答用，默认 4）、文档过滤开关 `document_filter`、评估的 `[eval] top_k`（默认 3，与历史基线一致）、问答 LLM 设置不参与计算，修改它们不需要重建索引。

配置改动导致指纹变化后，`status` 会显示"配置已改变"，这时需要用 `ingest --force` 全量重建。内置算法的输出发生变化时，要同步提升 `rag/config.py` 中的 `RULES_VERSION`；LLM 提示词改动时提升 `rag/ingest/tables/llm_tables.py` 中的 `PROMPT_VERSION`。

## 4. 索引一致性

每份装配的索引放在 `.rag/indexes/<配置名>/` 下，包含 `manifest.json`、`chroma/`（向量索引）、`bm25.sqlite`（BM25 倒排索引，配置了 `[lexical]` 时）、`tables.sqlite`（整表库）和 `documents/<文档名>/`（中间结果）。它们由同一次入库建立，共用一份 manifest；`status` 逐文档检查向量、倒排的 chunk 与 manifest 一致，整表库中的表数与 manifest 记录的表格数一致。

**整表库**记录每张表格的整表文本（`grid_rows_v1`，续表已合并）和它被切成的 chunk（摘要 chunk 也算该表的成员），不做向量化，只按 chunk 查表。查询时检索器先给 chunk 排序，再由返回单元一步（`rag/query/units.py`）映射：`table_return_unit = "table"` 时表格 chunk 换成整表，占据它排名最靠前的 chunk 的位置，同表的其他 chunk 跳过，后面的结果依次补上；`"chunk"` 时原样返回。原因见 [decisions/012-whole-table-return.md](decisions/012-whole-table-return.md)。

**中间结果**（`documents/<文档名>/`）：解析器原始输出、组装后的文档（含表格结构与 LLM 调用记录）、表格选优与续表的全部证据、chunk 及其来源区域。它们按类型存成 JSON，可以原样读回（`rag/index/audit.py`）；入库审核页和评估都从这里读取，不重新处理 PDF。

**LLM 缓存**：`.rag/llm-cache/` 按请求内容缓存表格 LLM 调用的结果，不属于任何索引，全量重建时保留并复用（[rules/table-llm.md](rules/table-llm.md)）。

- **manifest 是唯一的入库记录，并且最后写入**（先写临时文件再原子替换）。单个文档的入库顺序是：处理 → 写中间结果 → 替换该文档的向量 → 替换该文档的 BM25 记录 → 替换该文档的整表 → 更新 manifest → 由中间结果生成审核页。
- **状态由事实比对得出**（`rag/index/status.py`）：PDF 哈希与 manifest 不同，记为"已修改"；manifest 中的 chunk ID 集合与向量库、BM25 索引或中间结果对不上，记为"不完整"，并分别记录两份索引是否完整。中途崩溃最多只会留下"需要重做"的文档，再运行一次 `ingest` 即可修复。所有数据都由 PDF 派生，所以不需要事务日志或回滚。
- **全量重建**：先把所有 PDF 处理、编码完，中间结果写到 `documents.staging/`；全部成功后才清空并重写向量库、BM25 索引与整表库、切换目录、写 manifest。任何一份失败，现有索引都不受影响。全量重建后，索引内容与 `documents/` 完全一致。
- **查询**只检查索引存在且与配置一致；有文档不是最新时给出提示，但不阻止查询。

旧版用 journal、跨进程锁和快照回滚来保证事务性；对单用户的本地派生数据来说成本过高，V3.1 改为"可检测、可重做"。

## 5. 检索范围

检索前先确定范围：问题中出现文档标识表 `documents/documents.toml` 登记的编码时，只在对应文档内检索，规则见 [rules/document-filter.md](rules/document-filter.md)。标识编码是用户分配的业务数据，所以放在文档以外的表里，查询时由编码查到 `document_id`，再按 chunk 已有的 `document_id` 过滤；编码不写进 PDF、manifest 或 chunk 元数据，原因见 [decisions/002-document-catalog.md](decisions/002-document-catalog.md)。

## 6. 评估与报告

评估流程和指标定义见 [rules/evaluation.md](rules/evaluation.md)。关键设计是：证据按"信息项 → 方案 → excerpt"组织，原因见 [decisions/003-evidence-schemes.md](decisions/003-evidence-schemes.md)；excerpt 与 chunk 的对应按 PDF 坐标判定，原因见 [decisions/004-coordinate-mapping.md](decisions/004-coordinate-mapping.md)。excerpt 的位置只依赖 PDF，人工确认一次后写入 ground-truth；chunk 的来源区域在入库时由 chunk 来源记录和解析器坐标统一推出（`rag/ingest/regions.py`），所以任何装配都不需要为评估另做确认。对插件的要求：解析器提供元素坐标，分块器记录 chunk 内容来自哪个元素的哪段文字；缺细坐标时记粗略区域，评估在其中按片段文字兜底。

每次评估在 `reports/eval/<日期>_<配置>_k<K>_filter-on|filter-off[_<检索器>][_table]/` 下生成 `index.html` 和 `result.json`（检索器不是 `semantic` 时目录名带上检索器名，返回整表时加 `_table`），同名时在目录名后加 `_2`、`_3`。评估另报表格证据指标（表格 chunk 名次、所需返回单元数与 token、K 曲线）和入库的 LLM 成本。评估页的"较上次"对比同一配置、同一检索器、同一返回单元、同一 K、同一数据集版本的上一次运行（不论过滤状态）。

`compare` 把几次评估并排生成 `reports/compare/<名称>/`：构建设置只差规则版本、编码器、BM25 分析器与表格环节、K 相同的评估放在同一标签页（检索器、返回单元可以不同），第一次为基线，每列标明规则版本、编码器、检索器和是否返回整表，可另给显示名（`目录名=显示名`）；分块预算等其他构建设置不同的评估分在不同标签页，因此多预算配对比较按预算分页；每题状态按 未命中 < 部分覆盖 < 完整覆盖 比较，标出变好、变差，列出所需 K 有变化的证据组，并列出在 ≥3 题 Top-K 中出现的不相关结果。评估记录了表格证据时，另有"表格证据"标签页：指标 1–3 总表、支配判定、对照、逐题、K 曲线；`--table-spec` 文件给出对照组、排除的证据组与结论。`runs.json` 记录输入。

`eval --budgets` 每题检索一次、取全量排名，按每档预算各出一份评估。`query-compare` 在 13 种组合上评估全部查询侧配置，生成 `reports/query/<名称>/`：比较两种查询方式按凑齐向量的支配关系判定变好、变差（[rules/evaluation.md](rules/evaluation.md)"查询侧的比较"），页面由使用者选择两侧配置。每条检索文本在每份索引上的全量排名缓存在 `.rag/retrieval-cache/`（以索引的 manifest 识别；混合检索存两个通道的名次，读出时按当前 BM25 权重重新融合），规划调用缓存在 `.rag/llm-cache/`。

**报告由已保存的结果生成**：`report` 从各索引的中间结果重新生成入库审核页，从 `result.json` 重新生成评估页，从 `runs.json` 重新生成对比页，重新生成查询侧对比页，再生成入口 `reports/index.html`。改了页面的样式或结构，运行 `report` 即可，不需要重新入库或评估。

## 7. 代码地图

```
rag/
  cli.py           命令入口
  config.py        读取 configs/*.toml 与 configs/extra/*.toml
  registry.py      实现登记、装配校验、构建指纹
  models.py        文档、chunk 等公共模型
  jsonio.py        JSON 写出与按类型读回
  paths.py         目录约定（.rag/ 与 reports/）
  ingest/          sources（发现 PDF）、parsers/、language（文档语言）、tables/（提取、选优、续表、表头、结构、写法、LLM）、chunkers/、assemble、pipeline
  index/           embedder、store（Chroma）、analyzer 与 lexical（BM25）、tables（整表库）、audit（中间结果）、manifest、status、builder
  query/           scope（检索范围、去编码）、retriever、units（返回单元与检索缓存）、planning（规划）、execution（检索执行、融合与合并）、prompt、llm
  eval/            dataset、judge、metrics、table_metrics、table_structure（结构标注答案）、runner、query_compare（查询侧对比数据）
  reports/         html（页面外壳；样式与脚本写入 reports/assets/）、ingest（入库审核页）、evaluation、compare 与 table_compare（对比页）、query_compare（查询侧对比页）、index_page
tests/             算法、装配校验、索引一致性、检索范围、检索、评估、报告
```
