# Minimal RAG

Minimal RAG 是一个面向本地 PDF 知识库的教学型、可运行 RAG V1，也是未来
Sales Operations Agent 的知识检索模块原型。

当前代码仍运行 V1 链路；V2.0 需求已经确认、尚未实现。需求、历史规格和 V2.0 实施入口见[文档导航](docs/README.md)。

V1 完整覆盖两条链路：

```text
离线索引线路：
PDF → 按页解析 → 分块 → E5 Embedding → Chroma → Manifest

在线问答线路：
问题 → E5 Embedding → Top-K 检索 → 受控 Prompt → OpenRouter → 带来源回答
```

## V1 范围

- 支持递归发现 `documents/` 中的多份 PDF。
- 支持新增、变化、未变化和已移除文档的生命周期。
- 所有 chunks 存入同一个 Chroma Collection。
- 提供文档状态、chunks、检索、问答、chat 和最小评估 CLI。
- 每个回答来源包含文档名、页码和 Chunk ID。

V1 不支持 OCR、非 PDF 文件、Web UI、混合检索、reranker、Agent、Tool
Calling、对话 Memory 或自动表格结构化。

## 环境与安装

需要 Python 3.11。推荐在 Windows PowerShell 中使用
[uv](https://docs.astral.sh/uv/)：

```powershell
uv sync --python 3.11
uv run python -m rag --help
```

项目的 `requires-python` 固定为 `>=3.11,<3.12`，`uv.lock` 保存已验证的依赖解析结果。

首次执行需要 Embedding 的命令时，会从 Hugging Face 下载
`intfloat/multilingual-e5-small`。模型下载失败时，请检查网络、模型名称和可用磁盘空间。
Windows 未启用 Developer Mode 时，Hugging Face 可能提示无法使用缓存符号链接；这通常只会
增加磁盘占用，不影响模型运行。

## 配置

复制环境变量模板：

```powershell
Copy-Item .env.example .env
```

默认配置：

```dotenv
OPENROUTER_API_KEY=
OPENROUTER_MODEL=google/gemini-2.5-flash-lite
DOCUMENTS_DIR=documents
RAG_DB_PATH=.rag/chroma
RAG_MANIFEST_PATH=.rag/manifest.json
RAG_COLLECTION=minimal_rag_documents
EMBEDDING_MODEL=intfloat/multilingual-e5-small
CHUNK_SIZE=700
CHUNK_OVERLAP=100
TOP_K=4
```

只有 `ask`、`chat` 和 `eval --live` 需要 `OPENROUTER_API_KEY`。不要提交
`.env`，也不要把 API Key 写入代码、命令输出或测试。

## 准备 PDF

将 PDF 固定放入项目内的 `documents/`：

```text
documents/
├─ order-a.pdf
└─ region-b/
   └─ order-b.pdf
```

系统不会修改、移动或删除源 PDF。`documents/` 已被 Git 忽略，避免真实业务资料被误提交。
测试 PDF 会在临时目录中动态生成，不复制真实订单正文。

V1 不执行 OCR。没有可提取文字层的 PDF 会被标记为 `invalid`。

## CLI

查看帮助：

```powershell
uv run python -m rag --help
```

查看文档状态（只读，不生成 Embedding，不调用 LLM）：

```powershell
uv run python -m rag documents
```

建立或增量更新索引：

```powershell
uv run python -m rag ingest
uv run python -m rag ingest --file "relative/path.pdf"
```

普通 `ingest`：

- `new`：新增；
- `changed`：只重建变化文档；
- `current`：跳过且不重复生成 Embedding；
- `missing`：报告但不删除；
- `invalid`：报告失败，并继续处理其他 PDF。

强制重建：

```powershell
uv run python -m rag ingest --force
uv run python -m rag ingest --file "relative/path.pdf" --force
```

全量 `--force` 会先在内存中完成所有文档的解析、分块和 Embedding；任何 PDF
预构建失败时不会替换旧 Collection。Embedding 模型或分块配置变化后必须执行全量 force。

清理已经从 `documents/` 移除的文档索引：

```powershell
uv run python -m rag ingest --prune
```

`--prune` 只删除 Chroma 和 Manifest 中的索引记录，绝不删除源 PDF。

查看已有 chunks：

```powershell
uv run python -m rag chunks --document "order-a.pdf"
uv run python -m rag chunks --document "order-a.pdf" --page 1
```

执行原始向量检索：

```powershell
uv run python -m rag search "订单总金额是多少？"
uv run python -m rag search "付款条件是什么？" --top-k 2
uv run python -m rag search "订单总金额是多少？" --document "order-a.pdf"
```

输出同时展示余弦 `Distance` 和 `Similarity = 1 - Distance`。

执行一次受控问答：

```powershell
uv run python -m rag ask "付款条件是什么？"
uv run python -m rag ask "付款条件是什么？" --debug
```

`--debug` 会显示检索片段和最终 Prompt，但不会显示 API Key。

启动终端问答循环：

```powershell
uv run python -m rag chat
```

支持 `/help`、`/exit`、`/quit`、`/debug on` 和 `/debug off`。每个问题彼此独立，
不会把上一轮问题或回答发送给下一轮。

## 测试与评估

默认测试不访问 OpenRouter，也不下载 E5 模型：

```powershell
uv run pytest
```

默认评估只测试检索，不调用 LLM：

```powershell
uv run python -m rag eval
```

answerable 问题的 PASS 条件是 Top-K 中至少一个 hit 同时命中预期文档和页码。
由于 V1 没有相似度阈值，向量检索总会返回近邻，因此不可回答题在默认 eval
中只报告来源，不计入检索召回率。

配置 API Key 后可运行 live smoke test：

```powershell
uv run python -m rag eval --live
```

live eval 只检查预期词或拒答表达，不代表完整语义正确率。

## 数据边界

- PDF 原文、E5 模型、embeddings、Chroma 和 Manifest 保存在本地。
- `search` 和默认 `eval` 不向 OpenRouter 发送任何数据。
- `ask`、`chat` 和 `eval --live` 仅发送用户问题、系统 Prompt 与 Top-K
  检索片段。
- 不会把整个知识库、Embedding 或 API Key 发送给 OpenRouter。
- 检索片段会由 OpenRouter 转发给所选模型的上游提供商，请在使用真实业务资料前确认
  组织的数据合规要求。

## 已知边界

- 每一页单独解析和分块，chunk 永不跨页；跨页段落可能因此被切断。
- PDF 表格只按文字层提取，不自动恢复结构。
- V1 不删除重复页眉页脚。
- chat 不保存或传递对话历史。
- Embedding 模型和分块配置全局统一，修改后需要 `ingest --force`。

## 常见错误

- `documents/` 不存在或没有 PDF：创建目录并放入具有文字层的 PDF。
- PDF 显示 `invalid`：文件可能损坏或需要 OCR。
- 索引有 `new`/`changed`：执行 `uv run python -m rag ingest`。
- 索引有 `missing`：恢复文件，或明确执行 `ingest --prune`。
- 全局配置不一致：执行 `ingest --force`。
- 缺少 API Key：在项目根目录 `.env` 中设置 `OPENROUTER_API_KEY`。
- OpenRouter 认证、限流或服务错误：检查 Key、模型名和网络后重试。
