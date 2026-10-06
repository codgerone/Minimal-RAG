# 系统设计（V3.2）

## 1. 数据主链

```
入库   PDF ─→ Parser ─→ 主文档（文本 / 列表 / 表格位置，按阅读顺序）
              │
              └─→ TableExtractor × N ─→ 准入·分组·评分选优 ─→ 表头判定 ─→ TableFormatter ─┐
                                                                                         ↓
                       组装后的文档（ParsedDocument）─→ Chunker ─→ chunks ─→ Embedder ─→ Chroma
                                                                  └─→ 中间结果 JSON + 审核 HTML
查询   问题 ─→ 检索范围（文档标识表）─→ Embedder ─→ Retriever（范围内 Chroma 检索，确定性排序）─→ 命中 ─→ Prompt ─→ LLM ─→ 回答
评估   标注题目 ─→ 同一检索范围与 Retriever ─→ 证据判定（已确认映射 / 自动判定）─→ 指标 ─→ 评估报告
```

各阶段的数据模型定义在 `rag/models.py`（文档与 chunk）和 `rag/ingest/tables/models.py`（表格证据）。设计原则：解析器和工具的原始结果是"事实"，不会被后续阶段原地修改；选优、表头这类判断单独记录，审核页可以逐层追溯。

## 2. 可替换环节

只有确实需要比较或替换的环节才定义接口（Python `Protocol`），实现登记在 `rag/registry.py`：

| 接口 | 定义位置 | 内置实现 |
|---|---|---|
| Parser | `rag/ingest/parsers/__init__.py` | `pymupdf_pages`、`docling_layout` |
| TableExtractor（可配多个） | `rag/ingest/tables/extractors.py` | `pymupdf`、`camelot`、`docling`、`unstructured` |
| TableFormatter | `rag/ingest/tables/formatters.py` | `row_text_v1` |
| Chunker | `rag/ingest/chunkers/__init__.py` | `characters`、`structured_tokens` |
| Embedder | `rag/index/embedder.py` | `e5_small` |
| Retriever | `rag/query/retriever.py` | `semantic` |
| LLM | `rag/query/llm.py` | `openrouter` |

表格选优、表头判定、检索范围识别、向量库（Chroma）目前都只有一种实现，作为普通模块存在，等出现第二种实现时再抽象成接口。

**新增一种实现**：写一个满足接口的类，在 `registry.py` 对应的字典里登记一个名字，然后在配置文件里 `use = "名字"` 即可，主流程不用改。

**装配校验**（`registry.assemble`，在读取任何 PDF、加载任何模型之前执行）：
- 实现名必须存在，参数必须是构造函数能接受的；
- `docling` 表格提取器只能搭配 `docling_layout` 解析器，`characters` 分块器只能搭配 `pymupdf_pages`；
- 解析器不提供表格位置时，不能配置表格提取器；解析器会产出表格时，必须配置 `table_formatter`；
- 表格提取器不能重复。

`docling_layout` 解析器不配任何表格提取器也是合法组合：这时所有表格都采用 Docling 原生结构。

## 3. 配置与构建指纹

`configs/<名字>.toml` 中，解析、表格、分块、编码相关的设置，加上编码器身份（模型名与版本）和 `RULES_VERSION`，一起计算出**构建指纹**，记录在索引的 `manifest.json` 中。检索的 `top_k`（问答用，默认 4）、文档过滤开关 `document_filter`、评估的 `[eval] top_k`（默认 3，与历史基线一致）、LLM 设置不参与计算，修改它们不需要重建索引。

配置改动导致指纹变化后，`status` 会显示"配置已改变"，这时需要用 `ingest --force` 全量重建。内置算法的输出发生变化时，要同步提升 `rag/config.py` 中的 `RULES_VERSION`。

## 4. 索引一致性

每份装配的索引放在 `.rag/indexes/<配置名>/` 下，包含 `manifest.json`、`chroma/` 和 `documents/<文档名>/`。

- **manifest 是唯一的入库记录，并且最后写入**（先写临时文件再原子替换）。单个文档的入库顺序是：处理 → 写中间结果 → 替换该文档的向量 → 更新 manifest。
- **状态由事实比对得出**（`rag/index/status.py`）：PDF 哈希与 manifest 不同，记为"已修改"；manifest 中的 chunk ID 集合与向量库或中间结果对不上，记为"不完整"。中途崩溃最多只会留下"需要重做"的文档，再运行一次 `ingest` 即可修复。所有数据都由 PDF 派生，所以不需要事务日志或回滚。
- **全量重建**：先把所有 PDF 处理、编码完，中间结果写到 `documents.staging/`；全部成功后才改动向量库、切换目录、写 manifest。任何一份失败，现有索引都不受影响。全量重建后，索引内容与 `documents/` 完全一致。
- **查询**只检查索引存在且与配置一致；有文档不是最新时给出提示，但不阻止查询。

旧版用 journal、跨进程锁和快照回滚来保证事务性；对单用户的本地派生数据来说成本过高，V3.1 改为"可检测、可重做"。

## 5. 检索范围

检索前先确定范围：问题中出现文档标识表 `documents/documents.toml` 登记的编码时，只在对应文档内检索，规则见 [rules/document-filter.md](rules/document-filter.md)。标识编码是用户分配的业务数据，所以放在文档以外的表里，查询时由编码查到 `document_id`，再按 chunk 已有的 `document_id` 过滤；编码不写进 PDF、manifest 或 chunk 元数据，原因见 [decisions/002-document-catalog.md](decisions/002-document-catalog.md)。

## 6. 评估

评估流程和指标定义见 [rules/evaluation.md](rules/evaluation.md)。关键设计是：证据映射用 chunk 正文哈希标识，不绑定 chunk ID 或构建配置，原因见 [decisions/001-evidence-mapping.md](decisions/001-evidence-mapping.md)。每次评估在 `reports/eval/<日期>_<配置>_k<K>/` 下生成 `index.html` 和 `result.json`，同一天重复运行时在目录名后加 `_2`、`_3`。

## 7. 代码地图

```
rag/
  cli.py           命令入口
  config.py        读取 configs/*.toml
  registry.py      实现登记、装配校验、构建指纹
  models.py        文档、chunk 等公共模型
  paths.py         目录约定（.rag/ 与 reports/）
  ingest/          sources（发现 PDF）、parsers/、tables/、chunkers/、assemble、pipeline
  index/           embedder、store（Chroma）、manifest、status、builder
  query/           scope（检索范围）、retriever、prompt、llm
  eval/            dataset、judge、metrics、runner
  reports/         html（页面外壳；样式与脚本写入 reports/assets/，改样式只需 `python -m rag report`）、ingest、evaluation、index_page
tests/             算法、装配校验、索引一致性、检索范围、检索、评估
```
