# 系统设计（V3.11）

## 1. 数据主链

```
入库   PDF ─→ Parser ─→ 主文档（文本 / 列表 / 表格位置，按阅读顺序）
              │
              └─→ TableExtractor × N ─→ 准入·分组·评分选优 ─→ 续表合并 ─→ 表头判定 ─→ TableFormatter ─┐
                                                                                                     ↓
                       组装后的文档（ParsedDocument）─→ Chunker ─→ chunks ─→ 来源区域 ─┬─→ Embedder ─→ Chroma（向量索引）
                                                                                       ├─→ Analyzer ─→ SQLite（BM25 倒排索引）
                                                                                       └─→ 中间结果 JSON + 审核 HTML
查询   问题 ─→ 检索范围（文档标识表）─┬─→ Embedder ─→ 范围内向量排名 ─┐
                                    └─→ Analyzer ─→ 范围内 BM25 排名 ─┴─→ Retriever（选用一路或 RRF 融合，确定性排序）─→ 返回单元（整表库）─→ Prompt ─→ LLM ─→ 回答
评估   标注题目（含 excerpt 坐标）─→ 同一检索范围、Retriever 与返回单元 ─→ 证据判定（excerpt 坐标 × chunk 来源区域）─→ 指标 ─→ 评估报告
```

各阶段的数据模型定义在 `rag/models.py`（文档与 chunk）和 `rag/ingest/tables/models.py`（表格证据）。设计原则：解析器和工具的原始结果是"事实"，不会被后续阶段原地修改；选优、表头这类判断单独记录，审核页可以逐层追溯。

## 2. 可替换环节

只有确实需要比较或替换的环节才定义接口（Python `Protocol`），实现登记在 `rag/registry.py`：

| 接口 | 定义位置 | 内置实现 |
|---|---|---|
| Parser | `rag/ingest/parsers/__init__.py` | `pymupdf_pages`、`docling_layout` |
| TableExtractor（可配多个） | `rag/ingest/tables/extractors.py` | `pymupdf`、`camelot`、`docling`、`unstructured` |
| TableFormatter | `rag/ingest/tables/formatters.py` | `labeled_rows_v1`、`markdown_rows_v1` |
| Chunker | `rag/ingest/chunkers/__init__.py` | `characters`、`structured_tokens` |
| Embedder | `rag/index/embedder.py` | `bge_m3`、`e5_small`、`e5_large_instruct`、`qwen3_embedding_0_6b` |
| Retriever | `rag/query/retriever.py` | `semantic`、`bm25`、`hybrid` |
| LLM | `rag/query/llm.py` | `openrouter` |

编码器都是固定版本（revision）的 sentence-transformers 模型，按 float32 加载，查询与文档各加固定前缀或指令，向量 L2 归一化；编码前用模型自己的分词器检查 chunk 不超过其输入上限，超过则报错而不截断。模型选择见 [decisions/008-embedder-bge-m3.md](decisions/008-embedder-bge-m3.md)。

表格选优、续表判定、表头判定、检索范围识别、向量库（Chroma）、BM25 分析器与倒排索引、整表库与返回单元目前都只有一种实现，作为普通模块存在，等出现第二种实现时再抽象成接口。

**新增一种实现**：写一个满足接口的类，在 `registry.py` 对应的字典里登记一个名字，然后在配置文件里 `use = "名字"` 即可，主流程不用改。

**装配校验**（`registry.assemble`，在读取任何 PDF、加载任何模型之前执行）：
- 实现名必须存在，参数必须是构造函数能接受的；
- `docling` 表格提取器只能搭配 `docling_layout` 解析器，`characters` 分块器只能搭配 `pymupdf_pages`；
- 解析器不提供表格位置时，不能配置表格提取器；解析器会产出表格时，必须配置 `table_formatter`；
- 表格提取器不能重复。

`docling_layout` 解析器不配任何表格提取器也是合法组合：这时所有表格都采用 Docling 原生结构。

## 3. 配置与构建指纹

`configs/<名字>.toml` 中，解析、表格、分块、编码相关的设置，加上编码器身份（模型名与版本）和 `RULES_VERSION`，一起计算出**构建指纹**，记录在索引的 `manifest.json` 中。配置了 `[lexical]` 时，其分析器设置（去停用词、词干化）也进入指纹；没有这一节的配置不建 BM25 索引，指纹也不含它。检索器 `[retriever] use`、命中表格时的返回单元 `table_return_unit`、检索的 `top_k`（问答用，默认 4）、文档过滤开关 `document_filter`、评估的 `[eval] top_k`（默认 3，与历史基线一致）、LLM 设置不参与计算，修改它们不需要重建索引。

配置改动导致指纹变化后，`status` 会显示"配置已改变"，这时需要用 `ingest --force` 全量重建。内置算法的输出发生变化时，要同步提升 `rag/config.py` 中的 `RULES_VERSION`。

## 4. 索引一致性

每份装配的索引放在 `.rag/indexes/<配置名>/` 下，包含 `manifest.json`、`chroma/`（向量索引）、`bm25.sqlite`（BM25 倒排索引，配置了 `[lexical]` 时）、`tables.sqlite`（整表库）和 `documents/<文档名>/`。它们由同一次入库建立，共用一份 manifest；`status` 逐文档检查向量、倒排的 chunk 与 manifest 一致，整表库中的表数与 manifest 记录的表格数一致。

**整表库**记录每张表格的整表文本（表格文本化阶段的序列化结果，续表已合并，表头只出现一次）和它被切成的 chunk，不做向量化，只按 chunk 查表。查询时检索器先给 chunk 排序，再由返回单元一步（`rag/query/units.py`）映射：`table_return_unit = "table"` 时表格 chunk 换成整表，占据它排名最靠前的 chunk 的位置，同表的其他 chunk 跳过，后面的结果依次补上；`"chunk"` 时原样返回。原因见 [decisions/012-whole-table-return.md](decisions/012-whole-table-return.md)。

- **manifest 是唯一的入库记录，并且最后写入**（先写临时文件再原子替换）。单个文档的入库顺序是：处理 → 写中间结果 → 替换该文档的向量 → 替换该文档的 BM25 记录 → 更新 manifest。
- **状态由事实比对得出**（`rag/index/status.py`）：PDF 哈希与 manifest 不同，记为"已修改"；manifest 中的 chunk ID 集合与向量库、BM25 索引或中间结果对不上，记为"不完整"，并分别记录两份索引是否完整。中途崩溃最多只会留下"需要重做"的文档，再运行一次 `ingest` 即可修复。所有数据都由 PDF 派生，所以不需要事务日志或回滚。
- **全量重建**：先把所有 PDF 处理、编码完，中间结果写到 `documents.staging/`；全部成功后才清空并重写向量库与 BM25 索引、切换目录、写 manifest。任何一份失败，现有索引都不受影响。全量重建后，索引内容与 `documents/` 完全一致。
- **查询**只检查索引存在且与配置一致；有文档不是最新时给出提示，但不阻止查询。

旧版用 journal、跨进程锁和快照回滚来保证事务性；对单用户的本地派生数据来说成本过高，V3.1 改为"可检测、可重做"。

## 5. 检索范围

检索前先确定范围：问题中出现文档标识表 `documents/documents.toml` 登记的编码时，只在对应文档内检索，规则见 [rules/document-filter.md](rules/document-filter.md)。标识编码是用户分配的业务数据，所以放在文档以外的表里，查询时由编码查到 `document_id`，再按 chunk 已有的 `document_id` 过滤；编码不写进 PDF、manifest 或 chunk 元数据，原因见 [decisions/002-document-catalog.md](decisions/002-document-catalog.md)。

## 6. 评估

评估流程和指标定义见 [rules/evaluation.md](rules/evaluation.md)。关键设计是：证据按"信息项 → 方案 → excerpt"组织，原因见 [decisions/003-evidence-schemes.md](decisions/003-evidence-schemes.md)；excerpt 与 chunk 的对应按 PDF 坐标判定，原因见 [decisions/004-coordinate-mapping.md](decisions/004-coordinate-mapping.md)。excerpt 的位置只依赖 PDF，人工确认一次后写入 ground-truth；chunk 的来源区域在入库时由 chunk 来源记录和解析器坐标统一推出（`rag/ingest/regions.py`），所以任何装配都不需要为评估另做确认。对插件的要求：解析器提供元素坐标，分块器记录 chunk 内容来自哪个元素的哪段文字；缺细坐标时记粗略区域，评估在其中按片段文字兜底。每次评估在 `reports/eval/<日期>_<配置>_k<K>_filter-on|filter-off[_<检索器>][_table]/` 下生成 `index.html` 和 `result.json`（检索器不是 `semantic` 时目录名带上检索器名，返回整表时加 `_table`），同名时在目录名后加 `_2`、`_3`。评估页的"较上次"对比同一配置、同一检索器、同一返回单元、同一 K、同一数据集版本的上一次运行（不论过滤状态）。

`compare` 把几次评估并排生成 `reports/compare/<名称>/`：构建设置只差规则版本、编码器与 BM25 分析器、K 相同的评估放在同一标签页（检索器、返回单元可以不同），第一次为基线，每列标明规则版本、编码器、检索器和是否返回整表，可另给显示名（`目录名=显示名`，如版本号），存进 `runs.json`；分块预算等其他构建设置不同的评估分在不同标签页，因此多预算配对比较按预算分页；每题状态按 未命中 < 部分覆盖 < 完整覆盖 比较，标出变好、变差，列出所需 K 有变化的证据组，并列出在 ≥3 题 Top-K 中出现的不相关结果。`runs.json` 记录输入，`report` 会按它重新生成。

## 7. 代码地图

```
rag/
  cli.py           命令入口
  config.py        读取 configs/*.toml
  registry.py      实现登记、装配校验、构建指纹
  models.py        文档、chunk 等公共模型
  paths.py         目录约定（.rag/ 与 reports/）
  ingest/          sources（发现 PDF）、parsers/、tables/、chunkers/、assemble、pipeline
  index/           embedder、store（Chroma）、analyzer 与 lexical（BM25）、manifest、status、builder
  query/           scope（检索范围）、retriever、units（返回单元）、prompt、llm
  eval/            dataset、judge、metrics、runner
  reports/         html（页面外壳；样式与脚本写入 reports/assets/，改样式只需 `python -m rag report`）、ingest、evaluation、index_page
tests/             算法、装配校验、索引一致性、检索范围、检索、评估
```
