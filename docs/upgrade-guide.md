# V2.0 升级指南

V2.0 将 CLI 默认链路从 V1 切换为 V2，同时完整保留显式 V1。旧的无版本号索引不会进入当前运行链路；一次性存储迁移只把它原样归档到 `.rag/legacy-system-v1/`，当前系统的 pipeline v1/v2 则隔离保存在 `.rag/system-v2/`。

## 1. 安装与配置

```powershell
uv sync --python 3.11
Copy-Item .env.example .env
```

新增或变更的配置包括 `RAG_ARTIFACTS_PATH`、`EMBEDDING_MODEL_REVISION`、`V2_MAX_INPUT_TOKENS`、`V2_TEXT_OVERLAP_TOKENS` 和 `RAG_DIAGNOSTICS`。删除 `RAG_COLLECTION` 与 `RAG_MANIFEST_PATH`；若暂时保留，程序会警告并忽略其值。

## 2. 建立新索引

```powershell
uv run python -m rag documents --pipeline v1
uv run python -m rag documents --pipeline v2
uv run python -m rag ingest --pipeline v2 --all --force
```

V2 不复用 V1 chunk 或 embedding。V2 生效后仍可单独构建和查询 V1：

```powershell
uv run python -m rag ingest --pipeline v1 --all --force
uv run python -m rag search --pipeline v1 "问题"
```

force、prune、恢复和健康修复都只作用于指定 pipeline。

若项目已经存在升级前的混合 `.rag/` 布局，先预检，再执行可恢复的两阶段迁移：

```powershell
uv run python scripts/migrate_storage_layout.py
uv run python scripts/migrate_storage_layout.py --apply
uv run python scripts/migrate_storage_layout.py --cleanup-old
```

`--apply` 会复制并校验三个 collection、manifest 和当前 active artifacts，同时在 `tmp/storage-layout-backup-<时间>/` 留下旧布局备份；它不会在同一进程中删除旧数据。`--cleanup-old` 会再次核对新 collection 数量和备份后再清理旧路径。迁移不重建 embedding，也不保留已被 manifest 淘汰的历史 build。

## 3. 验证升级

```powershell
uv run pytest
uv run python -m rag documents --pipeline v2
uv run python -m rag search --pipeline v2 "测试问题"
```

检查 `.rag/system-v2/artifacts/v2/documents/` 中每份文档的 `winner-review.html`。它是默认必需产物，不受 diagnostics 开关影响。

如 ingest 期间进程中断，下次显式执行同一 pipeline 的 ingest。恢复器会先依据 `.rag/system-v2/recovery/<pipeline>/` 判断事务已经提交还是需要回滚；不要用 force 删除恢复证据。

## 4. 行为变化

| 项目 | V1 | V2 |
| --- | --- | --- |
| PDF 主解析 | PyMuPDF 按页文字 | Docling 结构与阅读顺序 |
| 表格 | 普通页文字 | 四工具九策略、确定性选优与结构化文本 |
| 分块 | 页内字符上限 | Text/List/Table 结构化 token 上限 |
| 来源 | 单页 | 可包含多页与多个节点 |
| 构建产物 | manifest + Chroma | manifest + Chroma + 可审核 artifact |
| 默认 CLI | 显式 `--pipeline v1` | 省略参数即 V2 |

## 5. 已知限制

- V2 不执行 OCR；扫描 PDF 需预处理。
- 表头 `undetermined` 是正常结果，不会被强行猜测。
- 可选表格工具失败不会自动切换到 V1。
- 旧 HTML 在 Windows 上被打开时可能产生延迟清理残留，不影响已提交的新 build。
- V1/V2 功能均可运行不代表 V2 问答效果必然更好；应使用固定题集分别运行评估后再下结论。
- 当前五文档/24 题基线中，V2 top-3 标准证据命中为 9/24，低于 V1 的 14/24；相似订单表格会竞争检索结果。需要稳定效果时保留显式 V1 对照，不要仅因默认链路变化推定 V2 更优。
- 同一基线的严格人工答案评分中，V2 仅 2/24 完整、V1 为 0/24；当前版本用于结构化 RAG 学习和可审核基线，不代表生产问答质量。
