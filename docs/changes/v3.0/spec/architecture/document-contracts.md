# 源文档、完整语义文档与 Chunk 公共契约

状态：**架构已审核，通过规格级编码就绪复审**。本文件定义[总架构入库主线](../architecture.md#2-入库主线从源事实到-active-索引)所用的源、主解析事实、最终文档与 Chunk 模型。阅读顺序即数据产生顺序；解析和分块行为以[文档处理需求](../requirements/document-processing.md)与[分块需求](../requirements/chunking.md)为准。表格选择结论和内部证据属于[表格子契约](table-contracts.md)，不在本文复制。

## 1. 公共约束

公共对象不可变；同一处理链的 `document_id`、`relative_path`、`file_hash` 和构建身份必须一致。`SourceDocument` 是输入事实；`PrimaryDocument` 是主解析适配器转换后的事实；`ContentResolution` 是局部选择器认定的结果；`ParsedDocument` 是组装器交付的唯一完整语义文档；`ChunkBatch` 是可持久化正文和来源。下游只能消费上一阶段公共契约，不直接消费 PyMuPDF/Docling 对象、HTML 审核视图或历史 V2.0 JSON。

字段表中的 `tuple<T>` 表示有序、不可变序列。只有明写 `?` 的字段可为空；空序列与未执行不可混淆。字符串默认非空且非纯空白；原文正文字段例外，可保留原始空白，但不得在下游冒充可入库正文。所有持久化 JSON 使用 UTF-8、显式 schema 版本与稳定字段名；缺失字段和显式 null 不互换。持久化 metadata codec 与哈希规则唯一见[存储发布契约](storage-publication.md)，不得改名或丢失公共字段。

## 2. 源与定位事实

```python
@dataclass(frozen=True)
class SourceDocument:
    document_id: str
    document_name: str
    relative_path: str
    absolute_path: Path
    file_hash: str

@dataclass(frozen=True)
class BoundingBox:
    x0: float
    y0: float
    x1: float
    y1: float
    coordinate_space: Literal["pymupdf_page_top_left_pt_v1"]

@dataclass(frozen=True)
class PageSpan:
    page_number: int
    bbox: BoundingBox | None
    source_ref: str

@dataclass(frozen=True)
class ProcessingWarning:
    code: WarningCode
    stage: ProcessingStage
    message: str
    source_refs: tuple[str, ...]
```

`SourceDocument` 由 Registry 创建，Indexer、Processor 与 Health 消费。ID 是规范相对路径 casefold 后 UTF-8 SHA-256 的前 16 位十六进制；`file_hash` 为文件字节完整 SHA-256；相对路径位于 `documents/` 内，使用 `/` 分隔且折叠后唯一。绝对路径只在运行时使用，不写入可迁移产物。`BoundingBox` 与 `PageSpan` 由 SDK 适配器创建，单位 pt、左上原点，坐标必须有限、`x1≥x0,y1≥y0`，页码为正的一基物理页；未知 bbox 为 null，不造零面积框。跨页节点使用有序多个 span。`ProcessingWarning` 由实际发生阶段创建，message 仅供展示，可继续异常才形成 warning；阻断错误由[运行契约](runtime-contracts.md)承载。

`WarningCode` 在公共处理证据中封闭为 `source_location_unavailable|detached_text_recovered|detached_list_item|unknown_docling_text_label|unsupported_nontext_item|orphan_nested_list_group|formula_orig_fallback|empty_text_node|optional_tool_page_failed|optional_tool_unavailable|artifact_cleanup_failed`。`formula_orig_fallback` 表示 Docling 公式的规范化 text 为空而保留其真实 orig 文字，沿用现行处理事实；旧 `legacy_config_ignored` 不进入 V3.0，因为没有旧配置兼容运行。`ProcessingStage` 封闭为 `configuration|docling_layout_parsing|layout_mapping|table_extraction|table_admission|table_grouping|table_scoring|parsed_document_fusion|table_header_detection|table_serialization|document_text_serialization|structured_chunking|artifact_staging|artifact_cleanup|index_publication`；跨系统错误阶段映射到[运行契约](runtime-contracts.md)的 StageCode，不把任意 message 当枚举。若新插件需要现有集合之外的 warning，先版本化扩展公共契约。

## 3. 主解析事实与完整文档

`PrimaryDocument` 使用带判别类型的有序元素，不强迫纯文本产生虚构表格或列表。MainParser 创建，TableSelector 与 DocumentAssembler 消费：

```python
@dataclass(frozen=True)
class PrimaryDocument:
    document_id: str
    file_hash: str
    page_count: int
    elements: tuple[PrimaryElement, ...]
    table_slots: tuple[TableSlot, ...]
    native_tables: tuple[NativeTableFact, ...]
    warnings: tuple[ProcessingWarning, ...]
    schema_version: str

PrimaryElement = PrimaryText | PrimaryList | PrimaryTablePlaceholder

@dataclass(frozen=True)
class PrimaryText:
    element_id: str
    kind: TextKind
    text: str
    sources: tuple[PageSpan, ...]
    source_ref: str | None

@dataclass(frozen=True)
class PrimaryList:
    element_id: str
    items: tuple[ListItem, ...]
    source_refs: tuple[str, ...]
    sources: tuple[PageSpan, ...]

@dataclass(frozen=True)
class ListItem:
    item_id: str
    text: str
    level: int
    ordinal: str | None
    parent_item_id: str | None
    sources: tuple[PageSpan, ...]
    source_ref: str | None

@dataclass(frozen=True)
class PrimaryTablePlaceholder:
    element_id: str
    slot_id: str
    source_ref: str
    sources: tuple[PageSpan, ...]

@dataclass(frozen=True)
class NativeTableFact:
    slot_id: str
    source_ref: str
    table: StructuredTable
```

`page_count>0`；元素按唯一阅读顺序，ID 唯一；占位、slot、原生结构按 `slot_id` 一一对应且同序。PyMuPDF 纯文本按页保留页边界，不产出表格 slot/list，也不伪造 Docling `source_ref`；Docling 保留已识别的 Text/List/Table 及 provenance。列表仅在真实识别时产生，`level≥0`，子项引用同列表中更早的父项，缺 ordinal 为 null。同一 slot 仅一份 `NativeTableFact`，供零候选或未接提取分支时的原生结构；不得包含 SDK 对象。`TableSlot` 与 `StructuredTable` 的字段权威见[表格契约](table-contracts.md)。

`TextKind` 是封闭集合：`title|paragraph|header|footer|footnote|caption|table_note|other_text`。纯文本页正文允许在 `PrimaryText` 中用 `paragraph` 表示，但其分块仍由所选字符 Chunker 的按页规则决定；不能因为统一类型而触发 Docling 文本组合规则。`source_ref` 只在原工具有可追溯引用时非空；不为 PyMuPDF 文本伪造 Docling ref。

组装器创建、Chunker 消费的最终文档如下：

```python
@dataclass(frozen=True)
class ParsedDocument:
    document_id: str
    document_name: str
    relative_path: str
    file_hash: str
    nodes: tuple[ParsedNode, ...]
    warnings: tuple[ProcessingWarning, ...]
    schema_version: str

ParsedNode = TextNode | ListNode | TableNode

@dataclass(frozen=True)
class TextNode:
    node_id: str
    kind: TextKind
    text: str
    sources: tuple[PageSpan, ...]
    source_ref: str | None

@dataclass(frozen=True)
class ListNode:
    node_id: str
    items: tuple[ListItem, ...]
    source_refs: tuple[str, ...]
    sources: tuple[PageSpan, ...]

@dataclass(frozen=True)
class TableNode:
    node_id: str
    slot_id: str
    source_ref: str
    origin: Literal["selected_winner", "docling_native_fallback"]
    table: StructuredTable
    selection_ref: str | None
    header: HeaderDecision
    serialized: SerializedTable
    sources: tuple[PageSpan, ...]
```

Node ID 在文档内唯一，节点保持输入阅读顺序。只有 `selected_winner` 允许 `selection_ref` 非空；`table/header/serialized` 的字段权威见[表格契约](table-contracts.md)。纯文本装配不产出 TableNode/ListNode，不添加标签改变正文。A01 的插件兼容性以[装配契约](assembly-contracts.md#3-跨插件兼容性)判定，不能从这里的共同类型推断。

`ContentResolution` 属于选择结论，按 `slot_id` 唯一关联主占位、最终采用结构和证据；字段与 winner/fallback 不变量以[表格契约](table-contracts.md#3-slot决策与最终结构)为准。Assembler 不重新评分或读取 SDK 对象。ProcessingResult 的 `table_branch_attached` 是不写入 PrimaryDocument 的装配事实，区分未接分支与已接但零候选；后者仍有明确回退结论。

## 4. Chunk 与来源

Chunker 创建以下三种模型，ArtifactBuilder、Embedder 与 Publisher 消费 `ChunkBatch`：

```python
@dataclass(frozen=True)
class ChunkSource:
    node_id: str
    page_spans: tuple[PageSpan, ...]
    source_text_start: int | None
    source_text_end: int | None
    repeated_context: bool
    context_kind: ContextKind

@dataclass(frozen=True)
class DocumentChunk:
    chunk_id: str
    document_id: str
    chunk_index: int
    kind: ChunkKind
    text: str
    token_count: int | None
    sources: tuple[ChunkSource, ...]
    parent_unit_id: str | None
    fragment_index: int
    fragment_count: int

@dataclass(frozen=True)
class ChunkBatch:
    document_id: str
    file_hash: str
    chunks: tuple[DocumentChunk, ...]
    character_count: int
    warnings: tuple[ProcessingWarning, ...]
```

来源起止同时存在或同时为空，存在时 `0≤start≤end`；`repeated_context` 当且仅当 kind 不为 `none`，有来源的真实正文至少一次以非重复范围覆盖。正文非空，chunk_index 从零连续，`page_numbers` 仅从 sources 有序去重派生，不另存第二份页事实。`token_count` 仅在 token 上限装配下非空，未计数为 null；拆分时 parent 非空，片段序号在范围内。`ChunkBatch.chunks` 非空，身份与 ParsedDocument 相同，ID 唯一；纯文本页内去重和结构化来源覆盖按相应需求校验。

`ChunkKind` 封闭为 `text|list|table`；`ContextKind` 封闭为 `none|overlap|table_header|merged_cell|list_ancestor|fallback_locator`。纯文本 chunk 对外仍用同一模型，但页内来源只指向该物理页，不添加结构化重复上下文；`token_count=null`，`kind=text`，未拆分时 `parent_unit_id=null, fragment_index=0, fragment_count=1`。结构化 token 分块的 token_count 为实际 passage（包括 `passage: ` 和 special tokens）计数。

稳定 ID 是输出契约而非运行路由：纯文本 `<document_id>-p<page_number>-c<至少两位页内序号>`；结构化 `<document_id>-v2-c<六位文档序号>`。结构化 ID 中历史 `v2` 字样仅为保持既有 ID，绝不触发旧实现。文档 ID 规则见第 2 节。来源页、正文和 ID 经过公共模型转换后必须与已批准需求及历史基线相符；公共模型无权重新生成不同 ID。

## 5. 序列化与跨阶段校验

公共 JSON 对象使用各自 `schema_version`，字段名保持本文件 snake_case，tuple 编码为数组、封闭枚举编码为固定字符串、Path 只在 SourceDocument 运行时存在不持久化。浮点 bbox 只允许有限数，页码/索引整数拒绝 bool；缺失来源框编码为显式 null，不能省略字段。ParsedDocument 的 `nodes` 采用 `node_type: text|list|table` 判别，反序列化必须拒绝未知类型或多余字段。ChunkBatch 中 `sources` 的 PageSpan 只存一份权威，`page_numbers` 在输出/Chroma metadata 中可物化但必须回读验证等于来源导出。跨文档/跨构建混用、重复 node/chunk ID、片段序号不连续、正文为空或来源越页范围均拒绝发布。

版本初值：`primary_document_v3`、`parsed_document_v3`、`chunk_batch_v3`；格式改变必须升版本并经适配/迁移审核，不能把旧 V2.0 JSON 自动反序列化为 V3.0 公共对象。相同公共模型允许纯文本页内容和结构化列表/表格节点的判别差异；不存在的结构以空序列或缺席分支事实表达，不伪造节点。

## 6. 实现一致性门

- 核对纯文本 `PrimaryText` 的每页表达与现有页内分块顺序，不得在统一 `ChunkSource` 时改变旧 `browse/chunks` 所见 metadata。
- 表格领域模型和 `ContentResolution` 的字段、封闭枚举及空值以表格子契约为准；测试 `TableNode` 引用完整性。
- `ProcessingWarning`、阻断错误、公共 JSON 与 metadata codec 已分别由本文、运行契约和存储契约定义；测试拒绝旧持久化格式。
- 对固定 PDF 对照两种预设的正文、节点/块顺序、页码、来源和稳定 ID，特别检查结构化 `-v2-` 仅是历史稳定标识。
