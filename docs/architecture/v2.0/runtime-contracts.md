# V2.0 运行时、健康检查与错误契约

本文是[当前架构第 10—12 节](../../architecture.md)的运行边界子文档；状态字段模型以[运行与持久化模型契约](runtime-persistence-models.md)为唯一权威，本文定义触发顺序、动作和错误传播。

## 1. PipelineRegistry

Registry 只接受 `v1|v2`，默认值由 CLI 显式传入 `v2`。每个 runtime 在创建时验证 pipeline_id、固定 collection、固定 manifest 和 BuildConfig.pipeline_id 一致；错配抛 ConfigurationError，不打开 Chroma。

`documents` 只装配 registry、manifest store、document registry 和 Chroma metadata reader；`browse/chunks` 只装配 read store，且 browse 的 Chroma `get` 明确排除 embeddings；`ingest` 再装配 processor/embedder；`search` 装配 read store 和 query embedder；默认 `eval` 另装配评估 repository、calculator 和 renderer，但不装配 LLM；`ask/chat/eval --live` 才装配 LLM。正式评估的详细依赖和产物以[检索效果评估架构契约](retrieval-evaluation.md)为准。

## 2. IndexIssue

```python
IndexIssueCode = Literal[
    "manifest_missing_with_collection_data", "manifest_schema_mismatch",
    "pipeline_identity_mismatch", "build_config_mismatch",
    "collection_missing", "collection_manifest_mismatch",
    "document_vector_mismatch", "document_unprocessable",
    "artifact_index_mismatch",
    "orphan_artifact_build", "stale_artifact_staging",
    "recovery_journal_present",
]

class IndexIssue:
    code: IndexIssueCode
    scope: Literal["pipeline", "document", "artifact"]
    document_id: str | None
    blocking: bool
    message: str
    remediation_command: str
```

| code | scope | blocking | 触发 |
| --- | --- | --- | --- |
| manifest_missing_with_collection_data | pipeline | 是 | 无 manifest、collection 非空 |
| manifest_schema_mismatch | pipeline | 是 | schema 不支持 |
| pipeline_identity_mismatch | pipeline | 是 | pipeline/collection/manifest 错配 |
| build_config_mismatch | pipeline | 是 | fingerprint 不同 |
| collection_missing | pipeline | 是 | manifest 有记录、collection 不存在 |
| collection_manifest_mismatch | pipeline | 是 | 总数或文档集合不一致 |
| document_vector_mismatch | document | 是 | count/build/file/config 不一致 |
| document_unprocessable | document | 是 | new/changed 源 PDF 只读预检无法生成可用内容 |
| artifact_index_mismatch | artifact | 是 | active v2 必需产物缺失或身份不一致 |
| orphan_artifact_build | artifact | 否 | build 无 manifest 引用 |
| stale_artifact_staging | artifact | 否 | staging 超过 24 小时 |
| recovery_journal_present | pipeline | 是 | 上次发布/删除在清理恢复证据前中断，必须先判定提交或回滚 |

pipeline blocking issue 存在时，源目录与 manifest 文档并集全部为 unassessed；否则按源文件、记录、向量和 active artifact 计算 current/new/changed/missing/invalid。unprocessable 只来自 new/changed 文档的只读 PDF 预检异常，不写 manifest、不进入 Indexer；已有 current/invalid 文档的旧索引不因一次预检失败被重新标为 unprocessable。

pipeline scope 的 document_id 必须为 null；document/artifact scope 必须指向一个非空 document_id。unprocessable 状态必须同时产生 document_unprocessable；new/changed/missing 是可操作状态而非结构损坏，不要求额外伪造 IndexIssue。usable 当且仅当没有 blocking issue，且不存在 new、changed、missing、invalid、unprocessable、unassessed 状态。

问题优先级按表格顺序；输出全部问题，但恢复协调器只执行最高优先级动作，成功后重新完整检查。

## 3. 恢复命令

- pipeline 级结构/config 问题：`uv run python -m rag ingest --pipeline <id> --force`。
- collection_missing 且 manifest 可读：普通 `ingest --pipeline <id>`；Indexer 创建空目标 collection，并把 manifest 中仍有源文件的全部文档视为待构建，不以 unassessed 跳过。manifest 独有的 missing 记录不写入新 collection，也不自动从 manifest 删除；构建结束仍报告 missing，需另行确认 prune。任一待构建文档失败时本次恢复不发布部分 collection，保留原 manifest，其他 pipeline 不受影响。
- new/changed/invalid：普通 ingest；invalid 重建后重新检查向量和 active artifact。
- unprocessable：不调用 Indexer，提示人工修复 PDF。
- missing：只有用户确认永久删除后执行 `--prune`。
- artifact_index_mismatch：`ingest --pipeline v2 --file <relative_path> --force`。
- recovery_journal_present：不得 force 覆盖证据；交互式只读命令经确认后调用当前 pipeline 的恢复器，非交互式命令输出 `uv run python -m rag ingest --pipeline <id>`。该 ingest 在正常发现/构建前先完成 journal 恢复或提交收尾；恢复失败立即停止，不进入文档处理。
- 非阻断 orphan/staging 不阻止查询，不在只读命令中自动删除。

非交互环境输出命令并以错误退出，不确认、不写入。交互取消后停止原命令。

## 4. RagError

```python
ErrorStage = Literal[
    "configuration", "document_selection", "pdf_parse",
    "docling_layout_parsing", "layout_mapping", "table_extraction",
    "parsed_document_fusion", "table_header_detection",
    "table_serialization", "document_text_serialization",
    "structured_chunking", "winner_review_generation", "artifact_staging",
    "embedding_validation", "llm_service", "vector_store", "manifest",
    "index_publication", "index_readiness",
]

class RagError(Exception):
    message: str
    remediation: str | None
    stage: ErrorStage | None
    pipeline_id: Literal["v1", "v2"] | None
    document_id: str | None
    cause: Exception | None
```

| 异常 | stage | exit code |
| --- | --- | --- |
| ConfigurationError、DocumentDirectoryError、DocumentSelectionError | configuration/document_selection | 2 |
| PdfParseError、LayoutParsingError、LayoutMappingError | pdf_parse/docling_layout_parsing/layout_mapping | 2 |
| TableExtractionError | table_extraction | 2；ingest summary 有单文档失败时命令返回 3 |
| DocumentFusionError | parsed_document_fusion | 2；ingest summary 有单文档失败时命令返回 3 |
| TableHeaderDetectionError | table_header_detection | 2；ingest summary 有单文档失败时命令返回 3 |
| TableSerializationError | table_serialization | 2；ingest summary 有单文档失败时命令返回 3 |
| DocumentTextSerializationError | document_text_serialization | 2；ingest summary 有单文档失败时命令返回 3 |
| ChunkingError | structured_chunking | 2；ingest summary 有单文档失败时命令返回 3 |
| WinnerReviewError | winner_review_generation | 2；ingest summary 有单文档失败时命令返回 3 |
| ArtifactStagingError | artifact_staging | 2；ingest summary 有单文档失败时命令返回 3 |
| EmbeddingError、EmbeddingInputTooLong、LlmServiceError | embedding_validation/llm_service | 4 |
| VectorStoreError、ManifestError、IndexPublicationError、IndexNotReadyError | vector_store/manifest/index_publication/index_readiness | 2 |

预期错误不打印 traceback。debug 可以显示 cause 类型和安全摘要，但不显示 API Key、完整文档、完整 prompt 或 embedding。

表头规则正常得出 `undetermined`、文本化得到空表文本、成功零表和可选工具页失败都不是异常。只有模型不变量被破坏、必需输入缺失或 I/O 失败才使用上表异常。单文档在 DocumentBuildResult 交付前失败时清理本次 staging、保留旧向量/manifest/active artifact，并继续批量中的其他文档；最终 ingest summary 有任一单文档失败则返回 3。进入发布事务后的错误统一包装为 IndexPublicationError，按发布契约恢复。artifact cleanup 发生在提交之后，失败只产生 `artifact_cleanup_failed` warning，不回滚已发布 build，也不改变成功退出码。

## 5. CLI 参数和显示

八个命令都接受 `--pipeline {v1,v2}`，省略等于 v2。`ingest --file` 与 `--prune` 互斥。`chunks --page` 必须为正整数；对 v2 匹配任一来源页，对 v1 匹配唯一页。`browse` 在应用层按 relative_path、chunk_index、ID 稳定排序后执行 offset/limit，默认省略 sources_json 与 node_ids_json，只有 `--full-metadata` 展开。

每个命令首个状态输出包含 `Pipeline: v1|v2`。documents 额外显示 collection 和 manifest；ingest summary 显示目标 collection 总数。目标索引不可用时不得改用另一 pipeline。

## 6. 配置弃用

启动时发现 `RAG_COLLECTION` 或 `RAG_MANIFEST_PATH`：输出一次弃用警告，说明 v1/v2 固定名称并忽略旧值。警告不改变退出码。`.env.example` 在 V2.0 删除这两个键，新增 RAG_ARTIFACTS_PATH、EMBEDDING_MODEL_REVISION、V2_MAX_INPUT_TOKENS、V2_TEXT_OVERLAP_TOKENS、RAG_DIAGNOSTICS。
