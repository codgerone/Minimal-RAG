# V2.0 数据、标识与配置契约

本文是[当前架构](../../architecture.md)的实现子文档，补全文档处理模型、ID、JSON 和向量记录。BuildConfig、manifest 和发布状态模型以[运行与持久化模型契约](runtime-persistence-models.md)为准。业务结果字段必须可由输入和配置确定性复现；build_id、路径和时间戳属于明确登记的运行事实。

## 1. JSON 公共规则

- UTF-8、无 BOM、结尾一个换行、缩进 2 空格。
- object key 按模型声明顺序输出；用于 fingerprint 的 canonical JSON 另按 key 字典序、无空白输出。
- enum 写字符串值；tuple/list 写 JSON array；Path 写相对项目根目录的 POSIX 字符串。
- datetime 写 UTC RFC 3339 秒精度 `YYYY-MM-DDTHH:MM:SSZ`。
- 非有限浮点数禁止写入；bbox 数值不预先舍入。
- 可空字段必须显式写 `null`，不能因值为空省略；新增字段需要提升对应 schema version。

## 2. 标识规则

| ID | 格式与生成 | 稳定性 |
| --- | --- | --- |
| document_id | `sha256(relative_path.casefold())` 十六进制全文 | 路径不变则稳定 |
| build_id | `<UTC年月日时分秒>-<file_hash前12>-<config_hash前12>-<8位随机十六进制>` | 每次实际重建不同 |
| slot_id | 按 Docling table 阅读顺序 `slot_001` 起 | 同输入与 mapper 版本稳定 |
| group_id | 等于 `group_<slot_id去掉slot_>`，如 `group_001` | 与 slot 一致 |
| candidate_id | `<tool>_<strategy>_p<四位页或multi>_t<四位工具原序号>` | 同工具原始输出顺序稳定 |
| cell_id | `<candidate_id>_r<起行四位>c<起列四位>_r<末行四位>c<末列四位>` | 网格不变则稳定 |
| node_id | `node_` + sha256(`document_id|docling_ref`) 前 20 位 | Docling ref 不变则稳定 |
| item_id | `<node_id>_item_<六位阅读序号>` | 列表顺序不变则稳定 |
| line_id | `<table node_id>_line_<六位输出序号>` | 文本化结果不变则稳定 |
| v1 chunk_id | `<document_id>-p<page_number>-c<至少两位页内chunk_index>` | 严格保留当前 V1 规则 |
| v2 chunk_id | `<document_id>-v2-c<六位全局chunk_index>` | 同分块顺序稳定；内容变化由替换操作更新同 ID |

候选多 region 但可归于一页时使用该页；跨页或无法唯一归页时使用 `multi`。strategy 使用固定小写枚举，不使用用户显示名称。所有顺序均在 ID 生成前按对应需求定义的稳定顺序确定。

## 3. 公共来源和 warning

```python
class BoundingBox:
    x0: float
    y0: float
    x1: float
    y1: float
    coordinate_space: Literal["pymupdf_page_top_left_pt_v1"]

class CoordinateTransform:
    source_origin: Literal["top_left", "bottom_left"]
    source_units: Literal["pt", "px"]
    scale_x: float
    scale_y: float
    page_height_before_scale: float

class PageSpan:
    page_number: int
    bbox: BoundingBox | None
    source_ref: str

WarningCode = Literal[
    "source_location_unavailable", "detached_text_recovered",
    "detached_list_item", "unknown_docling_text_label",
    "unsupported_nontext_item", "orphan_nested_list_group",
    "empty_text_node", "optional_tool_page_failed",
    "optional_tool_unavailable", "artifact_cleanup_failed",
    "legacy_config_ignored",
]
ProcessingStage = Literal[
    "configuration", "docling_layout_parsing", "layout_mapping",
    "table_extraction", "table_admission", "table_grouping",
    "table_scoring", "parsed_document_fusion",
    "table_header_detection", "table_serialization",
    "document_text_serialization", "structured_chunking",
    "artifact_staging", "artifact_cleanup", "index_publication",
]

class ProcessingWarning:
    code: WarningCode
    stage: ProcessingStage
    message: str
    source_refs: tuple[str, ...]
```

BoundingBox 使用左闭右开意义的几何边界，必须满足 `0 <= x0 <= x1 <= page_width`、`0 <= y0 <= y1 <= page_height`，越界比较使用 `0.000001 pt` 容差，落在容差内时钳制到页面边界。CoordinateTransform 表示 `x'=x*scale_x`；top-left 的 `y'=y*scale_y`，bottom-left 的边界先以 source page height 翻转再乘 scale_y。page_number 一基且为正。warning.code 是机器判断字段；message 只供人读，不参与下游分支。V2.0 的 `WarningCode` 全集如下；新增值必须先修改本文并提升受影响 schema/rule version，适配器不能临时创造字符串：

| code | 产生条件 | 下游影响 |
| --- | --- | --- |
| source_location_unavailable | 元素有正文但没有可用页码/provenance | 保留正文，来源为空 |
| detached_text_recovered | 非 body 文字按 provenance 被补入阅读流 | 保留正文和原引用 |
| detached_list_item | ListItem 不属于 list group | 降为 other_text |
| unknown_docling_text_label | 未登记 label 含可用正文 | 降为 other_text |
| unsupported_nontext_item | 未登记类型且无正文 | 不生成节点 |
| orphan_nested_list_group | 嵌套 list group 没有上级 item | 降至当前层级 |
| empty_text_node | 规范化后正文为空 | 不生成 chunk，保留来源事实 |
| optional_tool_page_failed | 非必需工具某页失败且其他页继续 | 候选照常参与，诊断保留异常 |
| optional_tool_unavailable | 非必需工具无法加载或整策略失败 | 其他策略继续；不得伪造候选 |
| artifact_cleanup_failed | 新 build 已发布但旧/孤立产物清理失败 | 发布有效；健康检查报告孤立产物 |
| legacy_config_ignored | 检测到已弃用索引身份环境变量 | 忽略旧值，固定 v1/v2 身份不变 |

warning 必须归属实际产生它的阶段，不能使用 `unknown` 或任意字符串。

## 4. LayoutDocument 字段

```python
class SourceDocument:
    document_id: str
    document_name: str
    relative_path: str
    absolute_path: Path
    file_hash: str

TextKind = Literal[
    "title", "paragraph", "header", "footer", "footnote",
    "caption", "table_note", "other_text",
]

class LayoutText:
    element_id: str
    docling_ref: str
    kind: TextKind
    text: str
    sources: tuple[PageSpan, ...]

class LayoutListItem:
    item_id: str
    docling_ref: str
    text: str
    level: int
    ordinal: str | None
    parent_item_id: str | None
    sources: tuple[PageSpan, ...]

class LayoutList:
    element_id: str
    docling_refs: tuple[str, ...]
    items: tuple[LayoutListItem, ...]
    sources: tuple[PageSpan, ...]

class LayoutTablePlaceholder:
    element_id: str
    docling_ref: str
    slot_id: str
    sources: tuple[PageSpan, ...]

class LayoutDocument:
    schema_version: Literal["layout_document_v1"]
    document_id: str
    page_count: int
    elements: tuple[LayoutText | LayoutList | LayoutTablePlaceholder, ...]
    table_slots: tuple[TableSlot, ...]
    warnings: tuple[ProcessingWarning, ...]
```

element_id 对 LayoutText/TablePlaceholder 使用 node_id 规则；LayoutList 使用其第一个 item ref 生成 node_id。level 从 0 开始；非顶层 item 必须引用同 List 中更早的父项。sources 按首次出现顺序去重。完全无法定位时为空并产生 `source_location_unavailable`。

## 5. ParsedDocument 辅助字段

TextNode 直接由 LayoutText 构造，node_id=element_id。ListNode 直接由 LayoutList 构造并保留 item_id。TableNode.selection_ref 在 winner 时为 `<group_id>:<candidate_id>`，fallback 时为 null。完整模型如下：

```python
class TextNode:
    node_id: str
    kind: TextKind
    text: str
    docling_ref: str
    sources: tuple[PageSpan, ...]

class ListNode:
    node_id: str
    items: tuple[LayoutListItem, ...]
    docling_refs: tuple[str, ...]
    sources: tuple[PageSpan, ...]

class StructuredTable:
    table_id: str
    tool: ToolName
    strategy: TableStrategy
    row_count: int | None
    column_count: int | None
    cells: tuple[TableCell, ...]
    uncovered_grid_positions: tuple[GridPosition, ...]
    unplaced_text: str | None
    sources: tuple[PageSpan, ...]
    warnings: tuple[ProcessingWarning, ...]

class HeaderPath:
    column_index: int
    cell_ids: tuple[str, ...]
    display_parts: tuple[str, ...]

class SerializedTableLine:
    line_id: str
    kind: Literal["header", "data", "merged", "unplaced_text"]
    text: str
    source_rows: tuple[int, ...]
    source_cell_ids: tuple[str, ...]

class SerializedTable:
    rule_version: Literal["table_text_v1"]
    text: str
    lines: tuple[SerializedTableLine, ...]

class TableNode:
    node_id: str
    slot_id: str
    docling_ref: str
    origin: Literal["selected_winner", "docling_native_fallback"]
    table: StructuredTable
    selection_ref: str | None
    header: HeaderDecision
    serialized: SerializedTable
    sources: tuple[PageSpan, ...]

class ParsedDocument:
    schema_version: Literal["parsed_document_v1"]
    document_id: str
    document_name: str
    relative_path: str
    file_hash: str
    nodes: tuple[TextNode | ListNode | TableNode, ...]
    warnings: tuple[ProcessingWarning, ...]
```

fallback 的 table_id 使用对应 Docling candidate_id；不另造第二份结构。HeaderPath.column_index 零基，display_parts 是表格文本化实际使用的有序字段片段。

```python
class HeaderSkippedRow:
    row_index: int
    reason: Literal["missing_position", "horizontal_span", "blank_row"]
    cell_ids: tuple[str, ...]

HeaderInputIssueCode = Literal[
    "invalid_dimensions", "duplicate_cell_id", "unavailable_cell_position",
    "invalid_cell_interval", "span_mismatch", "overlapping_cells",
    "unplaced_text",
]
HeaderStructuralIssueCode = Literal[
    "boundary_crosses_cell", "missing_header_position", "internal_blank_row",
    "internal_full_width_row", "crossing_column_intervals", "no_nonempty_path",
]

class HeaderIssue:
    code: HeaderInputIssueCode | HeaderStructuralIssueCode
    row_indices: tuple[int, ...]
    column_indices: tuple[int, ...]
    cell_ids: tuple[str, ...]

class HeaderObservation:
    row_index: int
    cell_id: str
    raw_text: str
    value_type: Literal["number", "date", "text_shape"]

class HeaderColumnObservation:
    column_index: int
    lowest_header_cell_id: str | None
    header_value_type: Literal["number", "date", "text_shape", "empty"]
    observations: tuple[HeaderObservation, ...]
    stable_body_type: Literal["number", "date"] | None
    supports_transition: bool

class HeaderCandidateEvaluation:
    start_row: int
    end_row: int
    result_reason: Literal["structural_rejection", "no_type_transition", "supported"]
    structural_issues: tuple[HeaderIssue, ...]
    sampled_row_indices: tuple[int, ...]
    skipped_rows: tuple[HeaderSkippedRow, ...]
    column_observations: tuple[HeaderColumnObservation, ...]
    supporting_columns: tuple[int, ...]
    tool_header_cell_ids_inside: tuple[str, ...]
    tool_header_cell_ids_outside: tuple[str, ...]
```

HeaderDecision.input_issues 只能使用 HeaderInputIssueCode；HeaderCandidateEvaluation.structural_issues 只能使用 HeaderStructuralIssueCode。issue 的行列和 cell 数组按首次检查顺序去重；某类事实不适用时对应数组为空，不能用虚构索引占位。invalid_dimensions 可以三组引用均为空；其余 code 至少有一个引用。结构拒绝时 sampled_row_indices、skipped_rows、column_observations、supporting_columns 均为空。`supports_transition=true` 当且仅当列号出现在 supporting_columns；stable_body_type 为 null 时必须为 false。

```python
class HeaderDecision:
    rule_version: Literal["table_header_v1"]
    sample_row_budget: Literal[8]
    minimum_independent_observations: Literal[2]
    outcome: Literal["identified", "undetermined"]
    reason: Literal[
        "invalid_grid", "unplaced_content", "no_candidate_region",
        "no_supported_candidate", "ambiguous_candidates",
        "unique_supported_candidate",
    ]
    input_issues: tuple[HeaderIssue, ...]
    skipped_prefix_rows: tuple[int, ...]
    evaluations: tuple[HeaderCandidateEvaluation, ...]
    header_start_row: int | None
    header_end_row: int | None
    paths: tuple[HeaderPath, ...]
```

identified 时 reason 必须为 unique_supported_candidate，header 范围非空且 paths 数量等于 column_count；undetermined 时三个最终字段分别为 null、null、空数组。

无法定位的原文使用 SerializedTableLine.kind=`unplaced_text`，正文格式为 `结构无法定位：<JSON 字符串形式原文>。`，source_rows 为空，source_cell_ids 保存可用引用，否则为空。SerializedTable.text 严格等于各 line.text 以 `\n` 连接；零行时为空字符串。

## 6. ChunkSource、统计和构建结果

```python
class ChunkSource:
    node_id: str
    page_spans: tuple[PageSpan, ...]
    source_text_start: int | None
    source_text_end: int | None
    repeated_context: bool
    context_kind: Literal["none", "overlap", "table_header", "merged_cell", "list_ancestor", "fallback_locator"]

class DocumentBuildStats:
    page_count: int
    character_count: int
    chunk_count: int

class ArtifactStageResult:
    staging_path: Path
    raw_docling_path: Path
    parsed_document_path: Path
    chunks_path: Path
    selection_summary_path: Path
    winner_review_path: Path
    diagnostics_path: Path | None
    winner_count: int
    chunk_count: int
```

source text range 为零基半开区间；无法对应连续原文或纯系统上下文时两个值都为 null。repeated_context=false 时 context_kind 必须为 none。ArtifactStageResult 的六个路径都必须位于 staging_path 内；前五个文件必须存在且完成身份及内容校验，diagnostics_path 只在启用诊断且目录存在时非空。

v1 character_count 是 PageText.text 长度之和；v2 是所有节点分块前正文长度之和，List item 和表格正文各计一次，不计 overlap、重复表头和定位标识。chunk_count 必须等于 chunks 长度和 ArtifactStageResult.chunk_count。

```python
class V1DocumentChunk:
    chunk_id: str
    document_id: str
    document_name: str
    relative_path: str
    page_number: int
    chunk_index: int
    text: str
    file_hash: str

class V2DocumentChunk:
    chunk_id: str
    document_id: str
    pipeline_id: Literal["v2"]
    chunk_index: int
    kind: Literal["text", "list", "table"]
    text: str
    token_count: int
    sources: tuple[ChunkSource, ...]
    parent_unit_id: str | None
    fragment_index: int
    fragment_count: int

DocumentChunk = V1DocumentChunk | V2DocumentChunk
```

V1DocumentChunk 与现有 TextChunk 字段和 ID 完全一致，不增加 token、source、pipeline 等 metadata，也不改变 page-bounded 分块。V2 的 chunk_index 与 fragment_index 均从 0 开始；fragment_count 为正。未拆分块的 fragment_index=0、fragment_count=1、parent_unit_id=null；拆分块共享原 node/item/table 的 parent_unit_id，fragment_index 连续且小于 fragment_count。

## 7. 产物 JSON schema

每个 JSON 顶层公共字段：schema_version、pipeline_id、document_id、build_id、file_hash、build_config_fingerprint、created_at、payload。

| 文件 | schema_version | payload |
| --- | --- | --- |
| parsed-document.json | `parsed_document_v1` | ParsedDocument |
| chunks.json | `document_chunks_v1` | `{chunks: V2DocumentChunk[]}` |
| selection-summary.json | `table_selection_summary_v1` | `{slots, groups, winners, warnings}` |

selection summary 的 slot 保存 slot_id/status/deferred_reason/docling_ref；group 保存 group_id/slot_id/status/unresolved_reason/member_candidate_ids；winner 保存 group_id/slot_id/candidate_id/tool/strategy/selection_reason/total_score。零 winner 时 winners 为空数组。详细逐对指标不进入默认 summary。

manifest 的 artifact_path 指向当前不可变 build 目录，而不是某个单文件。读取入口只通过 manifest 解析 active build，不扫描目录猜测。

## 8. BuildConfig 引用

BuildConfig 使用[运行与持久化模型契约第 1 节](runtime-persistence-models.md)的判别联合，本文件不再保留第二份字段定义。任何参与正文、来源、winner 或向量计算的字段变化均改变 fingerprint；运行路径、诊断、检索和展示配置不进入 fingerprint。

## 9. 运行配置

| 配置 | 默认值 | 用途 |
| --- | --- | --- |
| RAG_DB_PATH | `.rag/chroma` | 两条链路共享 Chroma 根目录 |
| RAG_ARTIFACTS_PATH | `.rag/artifacts` | v2 产物根目录 |
| EMBEDDING_MODEL | `intfloat/multilingual-e5-small` | v1/v2 embedding 与 tokenizer 模型 |
| EMBEDDING_MODEL_REVISION | `614241f622f53c4eeff9890bdc4f31cfecc418b3` | 固定模型/tokenizer snapshot；不能为空或使用可变别名 |
| CHUNK_SIZE | `700` | 仅 v1 |
| CHUNK_OVERLAP | `100` | 仅 v1 |
| V2_MAX_INPUT_TOKENS | `512` | v2 硬上限，不得超过模型能力 |
| V2_TEXT_OVERLAP_TOKENS | `32` | v2 超长文字/List item overlap 上限 |
| RAG_DIAGNOSTICS | `false` | 是否保存可选诊断 |

collection 和 manifest 名称按 pipeline 固定，不提供覆盖变量。升级后若检测到旧 `RAG_COLLECTION` 或 `RAG_MANIFEST_PATH`，输出弃用警告并忽略；不得把其值暗中套给 v1 或 v2。路径配置必须位于项目根目录内。

## 10. 向量记录契约

Chroma 每个 chunk 保存一条记录：

```python
class VectorRecordMetadata:
    schema_version: Literal["vector_metadata_v2"]
    pipeline_id: Literal["v1", "v2"]
    document_id: str
    document_name: str
    relative_path: str
    file_hash: str
    build_id: str
    build_config_fingerprint: str
    chunk_index: int
    chunk_kind: Literal["text", "list", "table"]
    token_count: int
    page_numbers_json: str
    node_ids_json: str
    sources_json: str
    parent_unit_id: str
    fragment_index: int
    fragment_count: int
```

本模型只适用于 V2。V1 继续使用现有 TextChunk metadata，不迁移成该结构。Chroma metadata 只写标量；三个 `*_json` 字段使用第 1 节 canonical JSON。page_numbers 升序去重；node_ids 按 chunk 来源首次出现顺序去重；sources_json 完整序列化 ChunkSource。`parent_unit_id` 无值时写空字符串。record id 等于 chunk_id，document 等于 `V2DocumentChunk.text`，embedding 由该正文加模型 passage 前缀后计算。读取时 JSON 解码或枚举失败即为 `document_vector_mismatch`，不得静默丢弃来源。

## 11. 验收

模型构造测试覆盖全部可空条件和不变量；ID 做确定性快照测试；JSON 做 round-trip 和 schema 拒绝测试；fingerprint 测试逐项证明结果相关字段会改变、运行时字段不会改变；legacy 环境变量测试验证仅警告且不改变固定索引身份。
