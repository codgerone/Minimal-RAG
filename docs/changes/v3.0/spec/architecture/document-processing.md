# 文档解析、组装与分块接口

状态：**架构已审核，通过规格级编码就绪复审**。回链[总架构](../architecture.md#2-入库主线从源事实到-active-索引)；业务规则来自[文档处理需求](../requirements/document-processing.md)及[分块需求](../requirements/chunking.md)，公共对象由[文档契约](document-contracts.md)定义。本文件定义接口的运行职责和适配边界，不重复表格选优公式。

## 1. DocumentRegistry

```python
@dataclass(frozen=True)
class RegistryRequest:
    documents_root: Path
    selector: str | None
```

Registry 接受已解析且限制在项目 `documents/` 内的根；selector 为空时返回按规范相对路径排序的 `tuple[SourceDocument, ...]`，非空时仅接受相对路径或唯一文件名并返回一项，不返回“随便第一项”。选择不匹配、越界或歧义均使用[运行契约](runtime-contracts.md)中的机器错误。内置本地 PDF 插件忽略隐藏与临时文件，大小写折叠路径冲突报错，文件字节计算完整 SHA-256。Registry 不预检 PDF 可处理性，也不读取 manifest 判断 current；结果由 Processor、Indexer、Health 与显式文档限定解析共用。

### 1.1 Indexer 的入库交接

每次 Indexer 调用只针对一个已验证配置和一个 IndexIdentity；`ingest --all-configs` 在 CLI 外层逐目标调用，不把多个索引混进一次事务。

```python
@dataclass(frozen=True)
class IndexerInput:
    configuration_name: str
    index_identity: IndexIdentity
    sources: tuple[SourceDocument, ...]
    selected_document_id: str | None
    force: bool
    prune: bool
    prune_authorized: bool

@dataclass(frozen=True)
class IngestDocumentResult:
    document_id: str
    relative_path: str
    state: Literal["added", "updated", "skipped", "failed", "missing", "pruned", "not_published"]
    error: OperationError | None
    transaction_id: str | None

@dataclass(frozen=True)
class IngestResult:
    configuration_name: str
    index_identity: IndexIdentity
    scanned: int
    documents: tuple[IngestDocumentResult, ...]
    collection_count: int
```

sources 按 Registry 规范顺序且 document_id 唯一；selected_document_id 非空时必须恰好命中一份 source，且 prune=false。prune=true 时必须显式授权，且 selected_document_id 为空；未授权不得删除。`scanned` 是本次实际检查的源文档数，`collection_count` 是目标索引完成后回读的 active 记录数，均非负。结果按规范相对路径和 document_id 稳定排序；CLI 展示的 added/updated/skipped/failed/missing/pruned 计数从 documents 派生，不再存第二份可矛盾计数。

`added|updated|pruned` 必有已提交 transaction_id、无 error；`skipped|missing` 无 transaction_id、无 error；`failed` 必有机器 error，不得显示为成功；`not_published` 仅表示全量 force 某文档预构建失败后其他已准备文档**未生效**，两可空项均空，不能计为 added/updated。全量 force 失败时不发布任何新文档，原 active 保留；批量增量可继续其他文档。运行结果不得仅用人类 detail 字符串驱动退出码或状态。

## 2. DocumentProcessor 的内部 DAG

本大接口必接 one，以 `DocumentRequest` 为输入、`ProcessingResult` 为输出；后者包含主解析事实、条件表格证据与最终 ParsedDocument，但不含 chunk 或已发布路径。`table_branch_attached` 是运行绑定注入的装配事实，不能从报告个数推断；完整字段和空值条件紧接下方定义。

Indexer 创建请求；DocumentProcessor 完成解析与组装后交付结果：

```python
@dataclass(frozen=True)
class DocumentRequest:
    source: SourceDocument
    index_identity: IndexIdentity
    build_id: str
    table_branch_attached: bool

@dataclass(frozen=True)
class ProcessingResult:
    source: SourceDocument
    index_identity: IndexIdentity
    build_id: str
    table_branch_attached: bool
    native_parser_evidence: NativeParserEvidence
    primary_document: PrimaryDocument
    extraction_reports: tuple[TableExtractionReport, ...]
    resolutions: tuple[ContentResolution, ...]
    grouping_report: GroupingReport | None
    scoring_report: ScoringReport | None
    prepared_tables: tuple[PreparedTableContent, ...]
    parsed_document: ParsedDocument
    warnings: tuple[ProcessingWarning, ...]
```

`build_id` 非空，在文档处理、分块、编码与发布中固定；所有 document_id/file_hash 必与 source 一致。快照对每次入库都是必需的，因此请求不再携带 `artifact_required` 布尔分支。表格提取分支未接时 reports/resolutions 为空，grouping/scoring 均为空；已接时每个已绑定提取器恰一报告、两个报告均非空、每个主解析 table slot 恰一 resolution，即使零候选也明确原生回退。内容准备未接时 prepared_tables 为空；已接时按每个主解析 table slot 各有一项，允许本 PDF 零表格。`table_branch_attached` 来自已验证装配，不能从报告个数猜测。原生证据与主解析结果来自同一次解析，不为快照再打开 PDF。

内部槽：`MainParser` 必接 one；`TableExtractor` 无条件选接 multi；`TableSelector` 在提取器接入时条件必接 one、未接入时禁止；`DocumentAssembler` 必接 one。主解析与提取都以同一个源 PDF 身份和 file_hash 为输入，彼此无数据依赖；Selector 同时依赖主解析的 slot/原生结构、候选报告和 PDF 原文证据；Assembler 依赖主解析与条件 Selector 结果。权威连接由[装配契约](assembly-contracts.md)统一边表表达，此处描述依赖语义，不保留可单独编辑的反向关系。

`MainParser.parse(source, context) -> (PrimaryDocument, NativeParserEvidence)`；`TableExtractor.extract(source, context) -> TableExtractionReport`；`TableSelector.select(primary, reports, evidence) -> (tuple<ContentResolution>, GroupingReport, ScoringReport)`；`DocumentAssembler.assemble(primary, reports, resolutions, branch_attached) -> (ParsedDocument, tuple<PreparedTableContent>)`。Assembler 内部先条件调用 `TableContentPreparation.prepare(primary, reports, resolutions, branch_attached) -> tuple<PreparedTableContent>`，再必经 `DocumentComposer.compose(primary, resolutions, prepared_tables, branch_attached) -> ParsedDocument`；C2 为 false 时 `prepared_tables=()`，不得调用内容准备。`ContentResolution` 只指认采用候选，实际结构由内容准备阶段按 candidate ID 在同次不可变报告中严格查找；无提取分支时从 PrimaryDocument 的原生表事实取得。找不到、重复 ID 或跨 slot 引用均阻断，不让内容准备或 Composer 重新评分。括号中的多个值在运行接口中可以封装为不可变结果对象，不能丢失任何一项；Assembler 对外的主业务输出仍是唯一 ParsedDocument，准备结果只供审计。每个同类插件同签名、同公共输出语义。multi 报告收集按绑定的稳定顺序，不按完成时刻。表格提取某页/策略失败可记录 warning 继续；主解析整体失败或组装不变量失败停止该文档，不改用其他主解析插件。

```python
@dataclass(frozen=True)
class ChunkingContext:
    index_identity: IndexIdentity
    build_id: str
    embedding_identity: EmbeddingIdentity
    character_size: int | None
    character_overlap: int | None
    maximum_input_tokens: int | None
    text_overlap_tokens: int | None
```

字符实现只允许字符两项非空且 `size>0, 0≤overlap<size`；结构化 token 实现只允许 token 两项非空且 `max>0, 0≤overlap<max`，内部 passage tokenizer 与 `embedding_identity` 一致。两组上限不能同时填写；参数来自已校验 PluginBinding，Chunker 不读取全局环境。解析器与 Chunker 的允许配对由[装配规则 A01](assembly-contracts.md#3-跨插件兼容性)在调用前确定。

## 3. 首批主解析与资源适配

MainParser 对外的原生证据是本次实际解析的只读事实，而不是 ArtifactBuilder 重新解析的结果。其字段权威如下；`raw_payload` 的两种封闭格式与缺失规则见下文，不允许任意 SDK 对象或不明 JSON。

```python
JsonValue: TypeAlias = "None | bool | str | int | float | list[JsonValue] | dict[str, JsonValue]"

@dataclass(frozen=True)
class NativeParserEvidence:
    parser_plugin_id: str
    format_id: Literal["pymupdf_pages_v1", "docling_document_v1"]
    raw_payload: PyMuPDFNativePayload | DoclingNativePayload

@dataclass(frozen=True)
class PyMuPDFNativeBlock:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    block_no: int
    block_type: int

@dataclass(frozen=True)
class PyMuPDFNativePage:
    page_number: int
    blocks: tuple[PyMuPDFNativeBlock, ...]

@dataclass(frozen=True)
class PyMuPDFNativePayload:
    pages: tuple[PyMuPDFNativePage, ...]

@dataclass(frozen=True)
class DoclingNativePayload:
    export: dict[str, JsonValue]
```

`JsonValue` 是递归的 `null|bool|str|有限数值|list[JsonValue]|dict[str,JsonValue]`，不得包含 SDK 对象、bytes、NaN 或 Infinity；这里只是原生审计证据的封装，不作为业务解析或判定的可变 schema。`parser_plugin_id` 与本次已验证 MainParser 绑定完全一致；format_id 与 payload 类型须严格成对。`pymupdf_pages_v1` 的 payload 按物理页顺序保存每页一基页码和本次 `get_text("blocks", sort=True)` 实际返回的有序块；每块恰保留 x0/y0/x1/y1、原始 `text`、block_no、block_type，坐标有限且原始文本不做清洗，空白页保留空块数组。`docling_document_v1` 的 `export` 是同次 Docling 转换对象调用 `export_to_dict()` 得到的完整可序列化字典；转换只执行一次，输出不经 ParsedDocument 反向重建。Docling 字典的 SDK 私有键不进入 V3 公共业务分支；业务字段只从适配器当次转换为公共 `PrimaryDocument`。两个格式都必须能与 SourceDocument 的 file_hash 和公共 PrimaryDocument 的页/slot 来源核对；第三方字段变化须通过[技术验证](../technical-validation.md)确认并版本化格式。

纯文本主解析插件将 PyMuPDF 每页 `get_text("blocks", sort=True)` 的非空文本块按需求拼成页正文，并在公共 `PrimaryDocument` 中保持页边界与物理页次。不得创建表格占位或 Docling 引用。组装器在无补充分支时按顺序交付相同正文与来源；字符 Chunker 从这些页事实恢复逐页输入，不跨页。

结构化主解析插件将 Docling 阅读序、Text、List、table placeholder、按 slot 关联的 `NativeTableFact` 和 provenance 映射为公共元素，保留已形成的跨页节点。NativeTableFact 中 `StructuredTable.tool=docling,strategy=accurate` 表示主解析转换所用的锁定表格模式；它是原生表事实，不声称 `extractor.docling` 插件已绑定或独立执行。Docling 原始对象只可在适配器资源作用域内存在；同次转换的可序列化 `NativeParserEvidence` 与 PrimaryDocument 一起交付，不能泄漏 SDK 对象到 ParsedDocument。缺失 bbox、cell 页码或阅读关系时使用公共空值与 warning，不猜测。

Docling 主解析和 Docling 表格提取共享一次文档级转换：由装配根建立同源、同 hash、同转换参数的作用域资源，两个适配器读取同一次结果；生命周期到本份文档构建完成即结束。它是本次执行资源，不是跨运行缓存。若共享转换失败，主解析失败；不能让表格分支单独产生看似成功但与主文档不一致的候选。其他工具各自只把 SDK 原始事实转换为公共候选，表格字段及坐标规则归表格子契约。

## 4. 组装和表格内容准备

DocumentAssembler 是组合接口：TableContentPreparation 在 C2 时对已认定的每张表先调用 HeaderDetector、后调用 TableSerializer；DocumentComposer 必接 one，接收主解析文档、条件选优结论和准备结果，按 PrimaryDocument 的唯一阅读顺序构成最终 ParsedDocument。普通文字与列表直接转成最终节点；每个表格占位恰好置换为一个最终 TableNode。Selector 已决定 winner 或原生回退；Composer 不重新准入、评分或比较插件名称。若没有提取分支，Composer 原样保留主解析正文与来源。winner 和原生回退使用相同表头、文本化规则。内容准备插件只接统一表格结构，不依赖 Camelot/Docling 类名。

TableContentPreparation 创建、DocumentComposer 消费的内部交接模型：

```python
@dataclass(frozen=True)
class PreparedTableContent:
    slot_id: str
    table_id: str
    adopted_table: StructuredTable
    header_decision: HeaderDecision
    serialized_table: SerializedTable
```

两个 ID 非空。每个需要准备的 PrimaryDocument 表格 slot 恰一项，`table_id=adopted_table.table_id`，采用结构来自本次被选候选或 PrimaryDocument 原生表事实；顺序按主文档 slot 顺序，不按插件完成时刻。HeaderDecision、SerializedTable 的字段见[表格契约](table-contracts.md)，均针对同一 `adopted_table`；Composer 仅按 `slot_id` 关联占位并使用该结构，不猜工具私有 ID 或回读候选报告。C2 为 false 时此集合为空且主解析器不得产出表格结构。此结果只在可审计快照中持久化，不反向影响选优。

`ContentResolution` 缺失、winner 不属于组、原生回退缺失、占位重复或来源不匹配均为组装失败；不交付部分 ParsedDocument。表头正常 `undetermined` 不是失败，按需求兜底。审核 HTML 仅消费已经认定的解析/分块结果，不反向给组装器供数。

## 5. Chunker

接口 `chunk(parsed: ParsedDocument, context: ChunkingContext) -> ChunkBatch` 必接 one，只消费完整文档。首批字符实现按页递归，内置 `plain_text` 绑定 300 字符/50 重叠并使用需求分隔顺序，保留原页内稳定 ID；结构化 token 实现按 Text/List/Table 各自规则、实际 E5 passage tokenizer 512-token 硬限制和确定性来源生成，保留结构化稳定 ID。两种实现都交付统一 DocumentChunk/ChunkBatch，不把各自的算法参数塞进公共正文。

结构化 Chunker 插件内部的 `count_passage(text)` 必须使用与实际 Embedder 相同模型名、不可变 revision、`passage: ` 前缀和 special-token 行为；装配时校验插件声明的身份，运行中按最终完整 passage 复算。计数实现不另暴露为可独立选择的接口或插件。字符实现不因此改变字符长度语义。正常空文本节点可 warning 并跳过；整份文档没有可入库正文、单字符加最短上下文仍超限、原文覆盖失败、重复 ID 或无法消耗输入时返回阻断错误，不交付空批次。

## 6. 下游交付与验证门

ArtifactBuilder 可读取处理证据和最终 ChunkBatch，Embedder 只读取 ChunkBatch 的正文，Publisher 接收已经校验的同一构建身份的 chunk/向量/产物引用。只有 Publisher 可改变 active 存储。文档处理失败不产生新 active 记录；批量中其他文档是否继续由 Indexer 决定。

请求/结果字段、空值、封闭错误码、表格内容准备和共享资源规则已由本契约及其链接子契约固定。实现完成门为：Docling 适配器证明一次转换共享；两种 Chunker 对固定输入核对正文、来源、ID 与 token/字符边界；失败用既定机器码且不得默默新增降级策略。[技术验证](../technical-validation.md)已确认 Docling 单对象与 E5 边界机制在锁定环境可行。
