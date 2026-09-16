# V2.0 运行与持久化模型契约

本文是[当前架构第 3.6、4、8—10 节](../../architecture.md)的字段子文档，补全构建配置、运行时、manifest、产物 envelope 和恢复日志。字段不得因实现方便而省略、改名或增加；模型演进必须提升对应 schema version。

## 0. 应用配置

```python
class Settings:
    project_root: Path
    documents_dir: Path
    db_path: Path
    artifacts_path: Path
    embedding_model: str
    embedding_model_revision: str
    v1_chunk_size: int
    v1_chunk_overlap: int
    v2_max_input_tokens: int
    v2_text_overlap_tokens: int
    top_k: int
    diagnostics_enabled: bool
    openrouter_api_key: str | None
    openrouter_model: str
```

Settings 不包含 collection_name 或 manifest_path，两者由 PipelineRegistry 根据 pipeline 固定生成。环境变量映射依次为 PROJECT_ROOT（代码确定，不从环境读取）、DOCUMENTS_DIR、RAG_DB_PATH、RAG_ARTIFACTS_PATH、EMBEDDING_MODEL、EMBEDDING_MODEL_REVISION、CHUNK_SIZE、CHUNK_OVERLAP、V2_MAX_INPUT_TOKENS、V2_TEXT_OVERLAP_TOKENS、TOP_K、RAG_DIAGNOSTICS、OPENROUTER_API_KEY、OPENROUTER_MODEL。布尔值只接受大小写不敏感的 `true|false|1|0|yes|no`，其他值报 ConfigurationError。

documents_dir、db_path、artifacts_path 必须位于 project_root；embedding_model 和 embedding_model_revision 去除首尾空白后都不得为空；所有数值上限为正，两个 overlap 非负并分别小于对应上限，top_k 为正。`RAG_COLLECTION`、`RAG_MANIFEST_PATH` 只触发弃用 warning，不进入 Settings。

## 1. 构建配置

```python
PipelineId = Literal["v1", "v2"]

class V1DependencyVersions:
    pymupdf: str
    sentence_transformers: str
    transformers: str
    chromadb: str
    langchain_text_splitters: str

class V2DependencyVersions:
    pymupdf: str
    camelot: str
    docling: str
    docling_core: str
    unstructured: str
    sentence_transformers: str
    transformers: str
    chromadb: str

class TokenizerConfig:
    model_name: str
    revision: str
    passage_prefix: Literal["passage: "]
    query_prefix: Literal["query: "]
    add_special_tokens: Literal[True]
    truncation: Literal[False]

class EmbeddingConfig:
    model_name: str
    revision: str
    normalize_embeddings: Literal[True]
    vector_dimension: int

class V1ParserConfig:
    rule_version: Literal["pymupdf_text_v1"]

class V1ChunkerConfig:
    rule_version: Literal["recursive_character_v1"]
    chunk_size_characters: int
    chunk_overlap_characters: int
    separator_version: Literal["legacy_default_v1"]

class V1BuildConfig:
    schema_version: Literal["build_config_v1"]
    pipeline_id: Literal["v1"]
    parser: V1ParserConfig
    chunker: V1ChunkerConfig
    tokenizer: TokenizerConfig
    embedding: EmbeddingConfig
    dependency_versions: V1DependencyVersions

class V2ParserConfig:
    rule_version: Literal["docling_layout_v1"]
    docling_version: Literal["2.121.0"]
    do_ocr: Literal[False]
    do_table_structure: Literal[True]
    table_mode: Literal["accurate"]
    do_cell_matching: Literal[True]
    generate_page_images: Literal[False]
    mapper_version: Literal["docling_mapper_v1"]

class TableSelectionConfig:
    rule_version: Literal["table_selection_v1"]
    strategies: tuple[TableStrategy, ...]
    page_bounds_tolerance_pt: Literal[0.000001]
    page_size_tolerance_pt: Literal[1.0]
    boundary_cluster_tolerance_pt: Literal[2.0]
    comparison_epsilon: Literal[0.000000000001]
    candidate_coverage_minimum: Literal[0.65]
    slot_coverage_minimum: Literal[0.77]
    metric_weights: tuple[float, float, float, float]
    tool_tiebreak_order: tuple[ToolName, ToolName, ToolName, ToolName]

class TableHeaderConfig:
    rule_version: Literal["table_header_v1"]
    sample_row_budget: Literal[8]
    minimum_independent_observations: Literal[2]

class TableSerializationConfig:
    rule_version: Literal["table_text_v1"]

class DocumentTextConfig:
    rule_version: Literal["document_text_v1"]

class V2ChunkerConfig:
    rule_version: Literal["structured_chunk_v1"]
    maximum_input_tokens: int
    text_overlap_tokens: int
    separator_version: Literal["recursive_boundaries_v1"]

class V2BuildConfig:
    schema_version: Literal["build_config_v1"]
    pipeline_id: Literal["v2"]
    parser: V2ParserConfig
    table_selection: TableSelectionConfig
    table_header: TableHeaderConfig
    table_serialization: TableSerializationConfig
    document_text: DocumentTextConfig
    chunker: V2ChunkerConfig
    tokenizer: TokenizerConfig
    embedding: EmbeddingConfig
    dependency_versions: V2DependencyVersions

BuildConfig = V1BuildConfig | V2BuildConfig
```

`TableSelectionConfig.strategies` 固定为 `(lines,lines_strict,text,lattice,stream,network,hybrid,accurate,hi_res)`。metric_weights 固定为 `(0.25,0.25,0.25,0.25)`，依次对应 text_f1、critical_token_integrity、shape_support、blank_anomaly；tool_tiebreak_order 固定为 pymupdf、camelot、docling、unstructured。maximum_input_tokens 默认为 512 且必须为正；text_overlap_tokens 默认为 32，必须非负且小于 maximum_input_tokens。V1 的字符参数保留现有值，不套用 V2 token 参数。

默认 embedding_model 为 `intfloat/multilingual-e5-small`，默认 revision 固定为 `614241f622f53c4eeff9890bdc4f31cfecc418b3`。TokenizerConfig 与 EmbeddingConfig 的 model_name/revision 必须分别相等，adapter 必须把 revision 同时传给 tokenizer 和 SentenceTransformer；禁止把可变 `main`、空 revision 或本机未解析的缓存别名写入 BuildConfig。更换模型或 revision 均改变 fingerprint。Docling raw 和 mapper 直接依赖 docling-core schema，因此 docling_core 与 docling 一样进入 V2DependencyVersions。

`legacy_default_v1` 精确表示现有 separators `( "\n\n", "\n", ". ", "; ", ", ", " ", "" )`、keep_separator=`end`、length_function=`len`、逐页切分、strip 空结果并去除同页相邻重复正文。该定义用于锁定 V1 行为，不要求重写其实现。

fingerprint 对完整 BuildConfig 执行 canonical JSON 后计算 SHA-256。依赖版本只记录可能改变该 pipeline 输出的包；V2 表格工具变化不得触发 V1 重建。不得把运行路径、top_k、LLM、Prompt、日志、诊断开关、HTML 样式、批大小或时间戳放入 BuildConfig。

## 2. 运行时和文档交付

```python
class DocumentBuildResult:
    source: SourceDocument
    pipeline_id: PipelineId
    build_id: str
    chunks: tuple[DocumentChunk, ...]
    stats: DocumentBuildStats
    artifact_stage: ArtifactStageResult | None

class PipelineRuntime:
    pipeline_id: PipelineId
    collection_name: str
    manifest_path: Path
    build_config: BuildConfig
    build_config_fingerprint: str
    document_processor: DocumentProcessor
```

V1 runtime 固定使用 `minimal_rag_documents_v1` 与 `.rag/manifest.v1.json`；V2 固定使用 `minimal_rag_documents_v2` 与 `.rag/manifest.v2.json`。runtime 的 pipeline_id、BuildConfig.pipeline_id、collection 和 manifest 必须一致。V1 的 artifact_stage 必须为 null；V2 必须非空。V1 chunks 的 document/file/path 字段必须与 source 一致；V2 chunks 还必须与 build 的 pipeline 身份一致。staged 产物公共身份必须与 DocumentBuildResult 一致。

`DocumentProcessor.process(source, build_id)` 是唯一公共执行方法。processor 不写 collection 或 manifest，不发布 staging，也不读取另一 pipeline 的状态。

## 3. 检索结果

```python
class V1RetrievalHit:
    chunk_id: str
    document_id: str
    document_name: str
    relative_path: str
    page_number: int
    chunk_index: int
    text: str
    distance: float

class V2RetrievalHit:
    chunk_id: str
    document_id: str
    document_name: str
    relative_path: str
    chunk_index: int
    chunk_kind: Literal["text", "list", "table"]
    text: str
    distance: float
    page_numbers: tuple[int, ...]
    sources: tuple[ChunkSource, ...]

RetrievalHit = V1RetrievalHit | V2RetrievalHit
```

similarity 均为 `1.0-distance`。V1 字段和引用展示保持现状；V2 page_numbers 从 metadata 解码并升序去重，sources 必须完整还原。V2 引用展示全部 page_numbers；空集合显示“页码不可用”，不得虚构第一页。

## 4. Manifest

```python
class ManifestDocumentRecord:
    document_id: str
    document_name: str
    relative_path: str
    file_hash: str
    build_id: str
    build_config_fingerprint: str
    page_count: int
    character_count: int
    chunk_count: int
    artifact_path: str | None
    indexed_at: str

class PipelineManifest:
    schema_version: Literal["pipeline_manifest_v2"]
    pipeline_id: PipelineId
    collection_name: str
    build_config: BuildConfig
    build_config_fingerprint: str
    documents: dict[str, ManifestDocumentRecord]
    created_at: str
    updated_at: str
```

documents 的 key 必须等于记录的 document_id，序列化时按 key 升序。V1 的 artifact_path 必须为 null；V2 必须是项目根目录内的 POSIX 相对路径，并精确指向 `.rag/artifacts/v2/documents/<document_id>/<build_id>`。created_at 在同一 manifest 生命周期不变；updated_at 只在原子发布成功时更新。空索引也保存完整 BuildConfig 和空 documents。

读取 manifest 时，未知 schema、缺字段、额外字段、错误类型、身份错配或 fingerprint 复算不一致均产生阻断问题，不做宽松修复。旧 `.rag/manifest.json` 不反序列化成该模型。

## 5. Artifact envelope 和默认 payload

```python
T = TypeVar("T")

class ArtifactEnvelope(Generic[T]):
    schema_version: str
    pipeline_id: Literal["v2"]
    document_id: str
    build_id: str
    file_hash: str
    build_config_fingerprint: str
    created_at: str
    payload: T

class SelectionSlotSummary:
    slot_id: str
    status: SlotStatus
    deferred_reason: SlotDeferredReason | None
    docling_ref: str

class SelectionGroupSummary:
    group_id: str
    slot_id: str
    status: GroupStatus
    unresolved_reason: GroupUnresolvedReason | None
    member_candidate_ids: tuple[str, ...]

class SelectionWinnerSummary:
    group_id: str
    slot_id: str
    candidate_id: str
    tool: ToolName
    strategy: TableStrategy
    selection_reason: SelectionReason
    total_score: float

class TableSelectionSummary:
    slots: tuple[SelectionSlotSummary, ...]
    groups: tuple[SelectionGroupSummary, ...]
    winners: tuple[SelectionWinnerSummary, ...]
    warnings: tuple[ProcessingWarning, ...]

class DocumentChunksPayload:
    chunks: tuple[V2DocumentChunk, ...]
```

`parsed-document.json` 使用 `ArtifactEnvelope[ParsedDocument]` 和 schema `parsed_document_v1`；`chunks.json` 使用 `ArtifactEnvelope[DocumentChunksPayload]` 和 schema `document_chunks_v1`；`selection-summary.json` 使用 `ArtifactEnvelope[TableSelectionSummary]` 和 schema `table_selection_summary_v1`。raw Docling JSON 保持其原生 schema，不套 envelope，但路径和内容哈希由 ArtifactStageResult 校验。

winner 必须与每个 ready group 一一对应并按 group 顺序排列；unresolved group 没有 winner。summary 不保存逐对评分证据，详细事实只存在内存报告或启用的 diagnostics。

## 6. 发布和恢复状态

```python
PublicationState = Literal[
    "staged", "vector_replaced", "published", "orphan", "inconsistent"
]
JSONScalar = str | int | float | bool | None

class VectorSnapshot:
    document_id: str
    ids: tuple[str, ...]
    documents: tuple[str, ...]
    embeddings: tuple[tuple[float, ...], ...]
    metadatas: tuple[dict[str, JSONScalar], ...]

class CollectionSnapshot:
    collection_name: str
    ids: tuple[str, ...]
    documents: tuple[str, ...]
    embeddings: tuple[tuple[float, ...], ...]
    metadatas: tuple[dict[str, JSONScalar], ...]

class RecoveryJournal:
    schema_version: Literal["index_recovery_v1"]
    pipeline_id: PipelineId
    collection_name: str
    operation: Literal["replace_document", "force_rebuild", "prune_document"]
    document_id: str | None
    build_id: str
    state: Literal["prepared", "mutating", "restoring", "recovery_failed"]
    current_step: Literal[
        "vector_replace", "vector_verify", "artifact_publish",
        "manifest_publish", "vector_restore", "manifest_restore",
        "artifact_restore", "artifact_cleanup",
    ]
    old_manifest_sha256: str | None
    old_manifest_path: str
    snapshot_path: str
    artifact_quarantine_path: str | None
    created_at: str
    error_type: str | None
    error_message: str | None
```

每个操作的恢复目录固定为 `.rag/recovery/<pipeline>/<build_id>/`，其中 `journal.json` 是唯一 journal，`snapshot.json` 保存 VectorSnapshot 或 CollectionSnapshot，`old-manifest.bin` 保存操作前 manifest 原始 bytes；prune 还可使用 `artifact-quarantine/`。`snapshot_path`、`old_manifest_path` 和非空的 `artifact_quarantine_path` 都必须位于该目录内，使用项目根目录相对 POSIX 路径。

在第一次删除向量、重建 collection、移动 active artifact 或替换 manifest 前，必须先完整写入 snapshot、old manifest 和 `state=prepared` 的 journal，并分别 flush/fsync；随后原子更新 journal 为 `mutating` 才可改变旧状态。每完成一个破坏性步骤便原子更新 current_step。捕获异常或启动时发现 journal 时进入 `restoring`，依据 snapshot、old manifest 和 quarantine 恢复并逐项回读验证；成功后删除整个恢复目录。恢复本身失败时写 `recovery_failed`、error_type 和安全 error_message，并由 `recovery_journal_present` 阻止查询。正常提交完成后才能删除恢复目录；仅内存 snapshot 不满足本契约。

document_id 在 replace_document/prune_document 时必须非空，在 force_rebuild 时必须为 null。artifact_quarantine_path 仅 prune_document 可非空。old_manifest_sha256 必须等于 old_manifest_path 内容的 SHA-256；旧 manifest 不存在时为 null，old_manifest_path 仍指向记录“原文件不存在”的零字节标记文件。

prepared/mutating/restoring 时 error_type 和 error_message 必须为空；recovery_failed 时两者必须非空。prepared/mutating 的 current_step 表示下一个或正在执行的破坏性步骤，restoring/recovery_failed 的 current_step 表示触发恢复的最后步骤。prepared journal 的路径文件必须全部已经存在并通过校验。

## 7. 文档发现和健康状态

```python
DocumentState = Literal[
    "current", "new", "changed", "missing", "invalid",
    "unprocessable", "unassessed",
]

class DocumentStatus:
    document_id: str
    relative_path: str
    state: DocumentState
    file_hash: str | None
    recorded_file_hash: str | None
    issues: tuple[IndexIssue, ...]

class IndexInspection:
    pipeline_id: PipelineId
    usable: bool
    manifest: PipelineManifest | None
    documents: tuple[DocumentStatus, ...]
    issues: tuple[IndexIssue, ...]
    collection_count: int | None
```

blocking issue 存在时 usable=false。pipeline 级阻断导致源目录和 manifest 并集中所有文档为 unassessed；不存在 pipeline 阻断时才计算逐文档状态。unprocessable 只表示源 PDF 只读预检不可处理，且不进入 Indexer；invalid 只表示已有 manifest 记录但该文档的向量或 active artifact 与记录不一致。一个 DocumentStatus 恰有一个状态；unassessed 不得同时表达具体文档错误。

DocumentStatus.file_hash 在源文件存在且哈希读取成功时非空，源文件 missing 或哈希尚未执行的 unassessed 时为空；哈希读取失败作为文档选择/文件系统错误抛出，不伪装成 unprocessable。recorded_file_hash 在 manifest 有该 document_id 记录时非空，否则为空。current/changed/invalid 必须同时有两个 hash，new/unprocessable 只有 file_hash，missing 只有 recorded_file_hash；unassessed 按阻断发生前已取得的事实填写，不补造缺值。IndexInspection.manifest 仅在 manifest 不存在或无法通过 schema/身份校验时为空；无法解析的原始错误仍由 IndexIssue 保留。collection_count 仅在 collection 尚未安全打开或读取失败时为空，合法空 collection 为 0。

## 8. 不变量测试

实现前先把本文件模型写成不可变 dataclass 或等价结构，并覆盖：严格反序列化、额外字段拒绝、枚举全集、V1/V2 判别联合、fingerprint 稳定性、manifest 身份错配、artifact envelope 身份一致、snapshot 数组等长、journal 路径约束和状态派生。任何测试需要新增业务字段时先修改本文，而不是在代码中私自扩充。
