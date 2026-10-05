# Minimal RAG

Minimal RAG 是一个面向本地 PDF 知识库的 RAG 项目，也是未来 Sales Operations Agent 的知识检索模块原型。现行 V3.0 通过接口、插件和装配配置组织解析、表格处理、分块、索引、检索、问答与评估。

内置 `plain_text` 装配使用 PyMuPDF 按页解析和 300/50 字符分块；`structured` 装配使用 Docling 版面、四工具九策略表格处理及 token 分块。初始默认装配是 `structured`。两份装配各有独立的构建身份、索引、审计快照和发布恢复作用域。已发布的 V2 索引与评估运行只作为历史证据保留，现行命令不提供旧版运行模式。

正式规格与文档地图见 [V3.0 规格入口](docs/changes/v3.0/spec/README.md) 和 [项目文档导航](docs/README.md)。

## 安装与配置

需要 Python 3.11。推荐使用 uv：

```powershell
uv sync --python 3.11
Copy-Item .env.example .env
uv run python -m rag --help
```

在 `.env` 中按需设置 `OPENROUTER_API_KEY`。只有 `ask`、`chat` 和 `eval --live` 需要该凭据。`TOP_K` 默认 4；内置装配支持 `CHUNK_SIZE`、`CHUNK_OVERLAP`、`V2_MAX_INPUT_TOKENS`、`V2_TEXT_OVERLAP_TOKENS`、`EMBEDDING_MODEL`、`EMBEDDING_MODEL_REVISION` 与 `OPENROUTER_MODEL` 环境覆盖。会改变构建结果的覆盖会生成新的构建指纹和独立索引，不改写已保存的装配配置。

将 PDF 放在 `documents/` 或其子目录。源文件不会被修改。现行结构化装配关闭 OCR；扫描件应先完成 OCR。

## 命令

省略 `--config` 时使用已保存的默认装配。`--configure` 可查看现有装配的插件和索引状态，或交互创建新装配。`ingest --all-configs` 逐份处理全部已保存配置。

```powershell
uv run python -m rag documents
uv run python -m rag ingest --config structured --all
uv run python -m rag ingest --config plain_text --all
uv run python -m rag ingest --all-configs
uv run python -m rag ingest --config structured --file "relative/path.pdf"
uv run python -m rag ingest --config structured --force
uv run python -m rag ingest --config structured --prune

uv run python -m rag chunks --config structured --document "order.pdf" --page 2
uv run python -m rag browse --config structured --limit 20
uv run python -m rag search --config structured "付款条件是什么？" --top-k 3
uv run python -m rag ask --config structured "订单总金额是多少？" --debug
uv run python -m rag chat --config structured
uv run python -m rag eval --config structured --top-k 3
```

`browse` 仅读取 chunk 的 ID、正文和 metadata，不读取 embedding。`search`、`ask` 和 `chat` 在索引不健康时停止；交互命令会说明可执行的恢复操作。显式 `ingest` 先处理未完成发布事务。

## 索引、审计与评估

V3.0 的装配配置与索引位于 `.rag/system-v3/`，每份文档的构建阶段保存必需 JSON 快照和可阅读的审核 HTML。评估使用经审核的 V3 test set，运行记录位于 `validation/retrieval/system-v3.0/`；跨版本比较位于 `validation/retrieval/comparison.json` 和 `comparison.md`。标注规则版本不同时，报告只展示协议差异，不计算跨协议的直接指标差值。

两份内置装配已对同一批五份 PDF 做真实 V3 索引、K=3 的 24 题正式检索评估，以及 V2 基线的解析、chunk、命中顺序、Prompt 和指标核对。实现与验收记录见 [实施计划](docs/changes/v3.0/implementation-plan.md)。

```powershell
uv run pytest tests/v3
```

不要提交 `.env`、业务 PDF、`.rag/` 运行数据或模型缓存。
