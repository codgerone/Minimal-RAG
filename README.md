# Minimal RAG

Minimal RAG 是一个面向本地 PDF 知识库的双链路 RAG 项目，也是未来 Sales Operations Agent 的知识检索模块原型。

- `v2` 是默认链路：保留 Docling 文档结构，执行四工具九策略表格提取与确定性选优，生成结构化文本、token 安全分块和可审核产物。
- `v1` 是兼容链路：保留原有 PyMuPDF 按页解析和字符分块行为，通过 `--pipeline v1` 显式使用。
- 两条链路使用独立 collection、manifest、健康检查与恢复事务，不混查、不自动回退。

完整规格与进度见[文档导航](docs/README.md)，从旧版本升级请阅读[升级指南](docs/upgrade-guide.md)。

## 安装与配置

需要 Python 3.11。推荐使用 uv：

```powershell
uv sync --python 3.11
uv run python -m rag --help
Copy-Item .env.example .env
```

主要配置：

```dotenv
OPENROUTER_API_KEY=
OPENROUTER_MODEL=google/gemini-2.5-flash-lite
DOCUMENTS_DIR=documents
RAG_DB_PATH=.rag/system-v2/chroma
RAG_ARTIFACTS_PATH=.rag/system-v2/artifacts
EMBEDDING_MODEL=intfloat/multilingual-e5-small
EMBEDDING_MODEL_REVISION=614241f622f53c4eeff9890bdc4f31cfecc418b3
CHUNK_SIZE=700
CHUNK_OVERLAP=100
V2_MAX_INPUT_TOKENS=512
V2_TEXT_OVERLAP_TOKENS=32
TOP_K=4
RAG_DIAGNOSTICS=false
```

`RAG_COLLECTION` 和 `RAG_MANIFEST_PATH` 已弃用。collection 与 manifest 由 pipeline 固定：

| Pipeline | Collection | Manifest |
| --- | --- | --- |
| v1 | `minimal_rag_documents_v1` | `.rag/system-v2/pipelines/v1/manifest.json` |
| v2 | `minimal_rag_documents_v2` | `.rag/system-v2/pipelines/v2/manifest.json` |

不要提交 `.env`、真实业务 PDF、`.rag/` 运行数据或模型缓存。

## PDF 与 CLI

将 PDF 放入 `documents/` 或其子目录。系统不会修改源文件。V2 固定 `do_ocr=false`，扫描件应先完成 OCR。

所有命令均接受 `--pipeline {v1,v2}`，省略时使用 `v2`：

```powershell
uv run python -m rag documents --pipeline v2
uv run python -m rag ingest --pipeline v2 --all
uv run python -m rag ingest --pipeline v2 --file "relative/path.pdf"
uv run python -m rag ingest --pipeline v2 --force
uv run python -m rag ingest --pipeline v2 --prune

uv run python -m rag chunks --pipeline v2 --document "order.pdf" --page 2
uv run python -m rag browse --pipeline v2 --limit 20
uv run python -m rag browse --pipeline v2 --document "order.pdf"
uv run python -m rag search --pipeline v2 "付款条件是什么？" --top-k 3
uv run python -m rag ask --pipeline v2 "订单总金额是多少？" --debug
uv run python -m rag chat --pipeline v2

uv run python -m rag search --pipeline v1 "付款条件是什么？"
```

`ingest --all` 会递归处理 `documents/` 下全部 PDF；为兼容旧用法，省略 `--all` 和 `--file` 时也仍然执行全量增量索引。若要分别建立两个 collection：

```powershell
uv run python -m rag ingest --pipeline v1 --all
uv run python -m rag ingest --pipeline v2 --all
```

`browse` 是项目内对 Chroma records 的只读浏览器，按纵向列表显示 ID、chunk 文本和 metadata，不读取 embedding 浮点数组。默认隐藏冗长的 `sources_json` 和 `node_ids_json`；需要原始完整 metadata 时追加 `--full-metadata`。可使用 `--offset`、`--limit` 分页，并用 `--document` 限定文档。

`ask`、`chat` 和 `eval --live` 才需要 OpenRouter API Key。目标 pipeline 不健康时命令会阻断，不会切换到另一条链路。

## V2 产物与恢复

每个已发布 V2 文档在 `.rag/system-v2/artifacts/v2/documents/<可读文件名>--<document_id>/<build_id>/` 保存 `raw-docling-document.json`、ParsedDocument、chunks、selection summary、`winner-review.html` 和可选 diagnostics。manifest 指向的 `build_id` 目录就是唯一正式快照；成功替换后默认只保留新快照，不另建 `latest`、`document.json` 或 `builds/` 层。

审核 HTML 把每个 winner 按实际 rowspan/colspan 渲染为可直接阅读的表格，明确标注表头识别成功/失败，成功时用黄色背景标出表头单元格，并在表格下展示实际送入 Embedding 的最终文本；页面不混入来源坐标和评分调试信息。

向量、artifact 和 manifest 通过持久化 snapshot+journal 发布。进程中断后，显式 `ingest` 会先完成提交收尾或回滚；只读命令只报告问题，不擅自写入。

## 测试与评估

```powershell
uv run pytest

uv run python scripts/validate_v2_technical_gates.py tokenizer
uv run python scripts/validate_v2_technical_gates.py docling-conversion --pdf documents/E001-602.pdf
uv run python scripts/validate_v2_runtime.py --pipeline v2 --pdf documents/E001-602.pdf --output tmp/v2-smoke
uv run python scripts/validate_v2_runtime.py --pipeline v1 --pdf documents/E001-602.pdf --output tmp/v1-smoke
uv run python scripts/compare_v1_v2.py --output tmp/v1-v2-comparison
uv run python scripts/compare_answers.py --index-root tmp/v1-v2-comparison --output tmp/v1-v2-comparison/live-answers.json

uv run python -m rag eval --pipeline v2 --top-k 3
uv run python -m rag eval --pipeline v1 --top-k 3
```

默认 `eval` 使用已批准的 ground truth 与当前 pipeline/build 对应测试集运行正式检索评估，逐 PDF 输出原始 JSON 与人工审核 HTML，并重建汇总和跨链路对比；省略 `--top-k` 时使用 `TOP_K`。`eval --live` 保留为不进入正式报告的旧 LLM 冒烟入口。

## 已知边界

- 不执行 OCR、reranker、混合检索、Agent、Tool Calling 或对话 Memory。
- Docling 是 V2 布局主链的必需依赖；可选表格策略的单页失败会记录诊断并继续其他策略。
- 表头可能合法地得到 `undetermined`；系统保留原始行列事实，不猜测字段含义，也不自动继承续表表头。
- Windows 上打开的审核 HTML 可能暂时阻止旧 artifact 清理；已发布 build 仍有效，残留作为非阻断问题报告。
- 功能测试与 winner 选优通过不等同于问答效果优于 V1；效果结论必须来自固定题集的双链路对比。
- 当前五文档/24 题正式基线中，V2 的 Hit@3、证据组召回、完整覆盖、Chunk Precision 和 MRR 均高于 V1，但跨文档污染也由 54.17% 上升到 65.28%；相似采购明细表在向量空间中互相竞争仍是下一轮重点。完整事实见 `validation/retrieval/`，该结果不能外推为生产问答质量。
