# 系统概览（V3.1）

## 这是什么

一个本地运行的 PDF 知识库 RAG 系统：把 `documents/` 里的 PDF（合同、订单、发票等）解析、切块、编码后存进向量库，然后支持语义检索、依据证据的问答，以及用人工标注的题目评估检索效果。

系统的每个关键环节都可以替换（解析器、表格提取器、表格文本化、分块器、编码器、检索器、LLM）。用一个配置文件选择各环节的实现，就得到一份"装配"；不同装配各自建索引，可以放在一起比较效果。它也是 Sales Operations Agent 的检索模块原型。

## 能做什么

| 命令 | 作用 |
|---|---|
| `python -m rag status` | 查看哪些文档已入库、需要更新或可以清理 |
| `python -m rag ingest` | 入库新增或修改过的 PDF；`--force` 全量重建，`--prune` 清理已删除的文档，`--all-configs` 对所有配置执行 |
| `python -m rag chunks` | 浏览已入库的 chunk（可按 `--document`、`--page` 筛选） |
| `python -m rag search "问题"` | 语义检索，显示命中的 chunk |
| `python -m rag ask "问题"` | 检索后让 LLM 依据证据回答；`--debug` 显示命中内容和完整 Prompt |
| `python -m rag chat` | 连续提问（每轮独立检索，不记忆上下文） |
| `python -m rag eval` | 用标注题目评估检索效果并生成报告（K 取配置中的 `[eval] top_k`，内置为 3） |
| `python -m rag config list` / `config show NAME` | 查看装配配置 |
| `python -m rag report` | 重新生成报告首页 |

所有命令都可以加 `--config NAME` 选择装配，默认使用 `structured`（也可以用环境变量 `RAG_CONFIG` 指定）。

## 两份内置装配

| | `plain_text` | `structured` |
|---|---|---|
| 解析 | PyMuPDF 按页取文本 | Docling 版面解析（标题、段落、列表、表格、阅读顺序） |
| 表格 | 不处理 | 四个工具九种策略提取，评分选出最佳结构，判定表头后转成文本 |
| 分块 | 每页内 300 字符、重叠 50 | 按结构分块，每块不超过 512 token |
| 编码 / 检索 | multilingual-e5-small，余弦相似度；问答取 Top-4，评估取 Top-3 | 同左 |

新建装配：复制 `configs/structured.toml` 改几行即可，启动时会校验组合是否合法。

## 当前效果（K=3，5 份 PDF，24 道题）

| 装配 | 命中率 | 完整覆盖 | 证据组召回 | MRR | Chunk 精确率 | 跨文档污染 |
|---|---:|---:|---:|---:|---:|---:|
| plain_text | 4.2% | 4.2% | 4.2% | 9.0% | 5.6% | 54.2% |
| structured | 20.8% | 8.3% | 13.9% | 16.0% | 11.1% | 65.3% |

效果明显偏低，下一阶段专门优化。可疑原因：中文提问检索西班牙语/英语文档时，小型多语言模型的跨语言能力弱；表格文本中的脚手架稀释了语义；问题里点名了订单号，却没有按文档过滤或做关键词匹配。

## 不做什么（当前范围外）

扫描件 OCR、图片理解、跨页表格拼接、关键词/混合检索、元数据过滤、多轮对话记忆、Agent/工具调用、Web 界面。

## 怎么验收

1. 打开 `reports/index.html`：每份装配都显示"可用"，文档都是"已入库"。点进解析、表格、分块页，抽查内容是否与 PDF 一致。
2. 打开最新的评估报告：指标不低于上表；"含自动判定"为 0，或者已抽查确认。
3. `uv run pytest` 全部通过。

## 数据放在哪里

| 位置 | 内容 | 进 git |
|---|---|---|
| `documents/` | 你的 PDF | 否 |
| `configs/` | 装配配置 | 是 |
| `eval/ground-truth/` | 人工标注的问题与证据 | 是 |
| `eval/mappings.json` | 已确认的证据 ↔ chunk 对应关系 | 是 |
| `reports/` | 给人看的报告：`index.html` 入口、`ingest/` 入库审核页、`eval/` 评估报告 | 评估报告进 git，入库审核页不进 |
| `.rag/indexes/<配置名>/` | 机器数据：`manifest.json`、向量库、各文档中间结果 JSON | 否 |
