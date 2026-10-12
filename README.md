# Minimal RAG

本地 PDF 知识库 RAG 系统：解析 → 表格提取与选优 → 分块 → 向量索引 + BM25 倒排索引 → 混合检索 → 证据问答 → 检索评估。每个环节都可以通过配置文件替换实现，不同装配可以并排比较效果。它也是 Sales Operations Agent 的检索模块原型。

- 系统能做什么、怎么验收：[docs/overview.md](docs/overview.md)
- 设计与代码地图：[docs/design.md](docs/design.md)
- 全部文档：[docs/README.md](docs/README.md)

## 安装

需要 Python 3.11，推荐使用 uv：

```powershell
uv sync --python 3.11
Copy-Item .env.example .env   # 用到 ask/chat 时填写 OPENROUTER_API_KEY
```

把 PDF 放进 `documents/`（可以有子目录）。只支持带文本层的 PDF，扫描件需要先做 OCR。

## 使用

```powershell
uv run python -m rag ingest --all-configs        # 两份内置装配都入库
uv run python -m rag status --config structured
uv run python -m rag search "订单 24-12 的总金额是多少？" --config structured
uv run python -m rag ask "订单 24-12 的总金额是多少？" --debug
uv run python -m rag eval --config structured    # 生成评估报告
uv run python -m rag eval --config structured --retriever semantic   # 临时换检索器，不需要重建
uv run python -m rag compare RUN1 RUN2 --name NAME   # 几次评估并排对比
```

检索器有三种：`hybrid`（内置默认，向量与 BM25 排名按 RRF 融合）、`semantic`（纯向量）、`bm25`（纯关键词）。`search`、`ask`、`chat`、`eval` 都可以用 `--retriever` 临时切换。问题中出现 `documents/documents.toml` 登记的标识编码时，只在对应文档内检索。

审核与评估报告入口：`reports/index.html`。

## 装配配置

`configs/plain_text.toml`、`configs/structured.toml` 是两份内置装配。新建装配时，复制一份修改即可，例如：

```toml
name = "docling_native"
[parser]
use = "docling_layout"
[table_formatter]
use = "markdown_rows_v1"
[chunker]
use = "structured_tokens"
max_tokens = 512
[embedder]
use = "bge_m3"
[lexical]            # 建 BM25 倒排索引；检索器用 bm25 或 hybrid 时必须有
stopwords = true
stemming = true
[retriever]
use = "hybrid"
top_k = 4
[llm]
use = "openrouter"
```

可用的实现名见 `rag/registry.py`。修改解析、表格、分块、编码、`[lexical]` 相关的设置后，需要运行 `ingest --force` 重建索引；检索器、`top_k` 等查询设置改了不需要重建。

## 测试

```powershell
uv run pytest
```
