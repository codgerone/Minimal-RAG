# 处理产物与审核视图契约

状态：**架构已审核，通过规格级编码就绪复审**。回链[总架构的入库主线](../architecture.md#2-入库主线从源事实到-active-索引)；必需阶段和可见行为以[处理产物需求](../requirements/processing-artifacts.md)为准；存储生效和回滚以[存储发布契约](storage-publication.md)为准。本文是文件集合、快照清单和跨文件校验的唯一权威。

## 1. 位置、职责与生成时机

`ArtifactBuilder` 是系统入库层必接 one 的派生支路：接收同次 `ProcessingResult`、`ChunkBatch` 和已验证 `BuildProjection`，观察本次 MainParser、已接入的表格阶段、DocumentAssembler 和 Chunker 已完成的不可变结果，形成可审计文件；不重跑解析、不重做准入/评分/表头/分块，不改变业务结果。`BuildProjection` 仅提供本次已验证的 slot、binding、插件及顺序事实，其规范哈希须等于 `ProcessingResult.index_identity.build_fingerprint`；Builder 不从预设名称或报告内容猜绑定。主解析适配器在其单文档资源作用域内提供本次实际使用的可序列化原生事实；Builder 仅复制、封装和验证，不能为了快照再次打开 PDF 调用解析器。Builder 在所有处理阶段完成后生成四类审核页，因此 TableExtractor 的 HTML 可以只读借用 TableSelector 已完成的 slot 匹配证据用于排版；对应 JSON 仍是纯粹的提取阶段输出。产物暂存与回读成功是 passage 编码和发布的前置条件，两份内置配置及合法自定义配置无关闭开关。

所有文件在本次事务私有 staging 中生成；Builder 返回前回读全部适用文件。Publisher/ArtifactStore 在持锁发布时再次校验，然后与向量、active manifest 同事务生效。HTML/JSON 均不被 DocumentProcessor、Chunker、Retriever 或 Embedder 读取为业务输入。

## 2. 目录、角色与条件

每份文档每次构建的 active 目录由 manifest 引用，位于 `artifacts/documents/<安全文档名短前缀>--<document_id>/<build_id>/`。安全文件名规则和 namespace 见[存储发布契约](storage-publication.md)。目录中的固定文件如下；`required` 表示每次构建必有，`conditional` 表示对应接口已接入时整组必有、未接入时整组禁止出现。接口已接入但本 PDF 结果为空仍生成合法空结果及 HTML，不使用文件缺席表达零结果。

| 文件角色 | 文件名 | 接入条件 | 内容来源与消费者 |
| --- | --- | --- | --- |
| `snapshot_manifest` | `snapshot-manifest.json` | required | Builder 记录文件清单、身份与未接入阶段；Publisher/Health/Recovery 校验 |
| `main_parser_native` | `main-parser-native.json` | required | 实际 MainParser 适配器的 PyMuPDF/Docling 原生解析事实；仅供审计 |
| `main_parser` | `main-parser.json` | required | `PrimaryDocument` 原样序列化；仅供审计 |
| `table_extractor` | `table-extractor.json` | conditional：TableExtractor 已接入 | 按绑定顺序合存所有 `TableExtractionReport`；仅供审计 |
| `table_extractor_review` | `table-extractor-review.html` | 同上 | 候选人工审核页；可读 Selector 匹配证据作展示分组 |
| `table_selector` | `table-selector.json` | conditional：TableSelector 已接入 | 完整 `GroupingReport`、`ScoringReport`、`ContentResolution`；仅供审计 |
| `table_selector_review` | `table-selector-review.html` | 同上 | 准入、分组、选优人工审核页 |
| `table_content_preparation` | `table-content-preparation.json` | conditional：TableContentPreparation 已接入 | 每 slot 的 `PreparedTableContent`，包含最终采用结构、HeaderDecision 与 SerializedTable |
| `table_content_preparation_review` | `table-content-preparation-review.html` | 同上 | 被采用表格、表头结论、完整序列化文本 |
| `parsed_document` | `parsed-document.json` | required | DocumentAssembler 的 `ParsedDocument`；不另存 Composer/Processor 同阶段结果 |
| `chunks` | `chunks.json` | required | Chunker 实际交付 Embedder 的 `ChunkBatch` |
| `chunk_review` | `chunk-review.html` | required | 按最终顺序逐块显示实际 chunk 正文、来源和重复上下文 |

不生成 `winner-review.html`、`selection-summary.json` 或逐插件 extractor JSON。以表格阶段为例，`table-extractor.json`、`table-selector.json`、`table-content-preparation.json` 分别对应事实、判断和交付，不合成一个会模糊因果的 JSON。辅助 diagnostics 如需启用，必须放在 `diagnostics/`，不得参与上述必需角色，也不得反向影响业务结果。

## 3. 文件模型

以下是完整的公共文件外壳与清单字段；各 `payload` 的字段级权威分别是[文档模型](document-contracts.md)、[表格模型](table-contracts.md)和[处理交接](document-processing.md)。审核 JSON 使用 UTF-8、snake_case、封闭 schema_version，不接受未知字段；写盘时按键排序、两空格缩进、末尾换行，便于直接审阅，文件摘要按实际写盘字节计算。此展示序列化不改变构建投影的规范序列化与 fingerprint。tuple 编码为数组，受限路径使用 `/` 分隔且不得包含绝对路径、`..` 或空段。HTML 为 UTF-8 自包含页面，所有来自 PDF 的字符串须转义，不依赖联网资源。

```python
@dataclass(frozen=True)
class ArtifactEnvelope:
    schema_version: Literal[
        "main_parser_native_v3", "main_parser_v3", "table_extractor_v3",
        "table_selector_v3", "table_content_preparation_v3",
        "parsed_document_v3", "document_chunks_v3",
    ]
    document_id: str
    build_id: str
    file_hash: str
    build_fingerprint: str
    payload: (
        NativeParserEvidence | PrimaryDocument | TableExtractionSnapshot |
        TableSelectionSnapshot | TableContentPreparationSnapshot |
        ParsedDocument | ChunkBatch
    )

@dataclass(frozen=True)
class BoundExtractionReport:
    binding_id: str
    plugin_id: str
    order: int
    report: TableExtractionReport

@dataclass(frozen=True)
class TableExtractionSnapshot:
    reports: tuple[BoundExtractionReport, ...]

@dataclass(frozen=True)
class TableSelectionSnapshot:
    grouping: GroupingReport
    scoring: ScoringReport
    resolutions: tuple[ContentResolution, ...]

@dataclass(frozen=True)
class TableContentPreparationSnapshot:
    prepared_tables: tuple[PreparedTableContent, ...]
```

| `schema_version` | payload 类型 | 空结果规则 |
| --- | --- | --- |
| `main_parser_native_v3` | `NativeParserEvidence` | 主解析成功必须有真实原生证据；不得为空壳 |
| `main_parser_v3` | `PrimaryDocument` | 遵从 MainParser 契约，不伪造页/节点 |
| `table_extractor_v3` | `TableExtractionSnapshot` | `reports` 恰等于已接 multi 绑定数；单份报告可零候选 |
| `table_selector_v3` | `TableSelectionSnapshot` | 已接入即保存真实 grouping/scoring/resolutions；零 winner 不等于未运行 |
| `table_content_preparation_v3` | `TableContentPreparationSnapshot` | 已接入而无表格时 `prepared_tables=()` |
| `parsed_document_v3` | `ParsedDocument` | 仅 DocumentAssembler 的最终结果 |
| `document_chunks_v3` | `ChunkBatch` | 恰是向量化和发布使用的 batch，不能重构第二份正文 |

`schema_version` 与 `payload` 必须严格按上表成对，不能因为联合类型允许而交叉组合。`NativeParserEvidence` 的字段与两种封闭原生格式以[主解析适配契约](document-processing.md#3-首批主解析与资源适配)为唯一权威，Builder 不能自由决定 raw schema。原生证据的 `parser_plugin_id/format_id` 必须与本次已解析绑定匹配。`TableExtractionSnapshot.reports` 与 `ProcessingResult.extraction_reports` 按索引逐项相同；每项的 `binding_id/plugin_id/order` 来自同次 BuildProjection 的 `document_processor.table_extractor` multi 绑定，顺序和数量必须完全一致，任何缺项、重号或错位均为产物身份不一致。

```python
@dataclass(frozen=True)
class ArtifactFileRef:
    role: Literal[
        "main_parser_native", "main_parser", "table_extractor",
        "table_extractor_review", "table_selector", "table_selector_review",
        "table_content_preparation", "table_content_preparation_review",
        "parsed_document", "chunks", "chunk_review",
    ]
    relative_path: str
    sha256: str
    size_bytes: int

@dataclass(frozen=True)
class SnapshotManifest:
    schema_version: Literal["snapshot_manifest_v3"]
    document_id: str
    build_id: str
    file_hash: str
    build_fingerprint: str
    main_parser_plugin_id: str
    attached_slots: tuple[str, ...]
    unattached_slots: tuple[str, ...]
    files: tuple[ArtifactFileRef, ...]

@dataclass(frozen=True)
class StagedArtifacts:
    staging_path: Path
    document_id: str
    build_id: str
    file_hash: str
    build_fingerprint: str
    snapshot_manifest_sha256: str
    files: tuple[ArtifactFileRef, ...]
```

Builder 创建 `SnapshotManifest` 和 `StagedArtifacts`，Publisher/ArtifactStore 消费。`files` 排除 `snapshot-manifest.json` 自身以避免自哈希循环；清单文件的 SHA-256 由 `StagedArtifacts.snapshot_manifest_sha256` 和 active manifest 产物引用保存。SHA 均为 64 位小写十六进制；`size_bytes≥0`；每 role 恰一文件、路径唯一且恰等于第 2 节的固定名。`attached_slots/unattached_slots` 是本次有效装配中上述三个条件阶段的互斥完备划分，机器 slot ID 以[装配契约](assembly-contracts.md)为准；顺序按 slot ID，不能从文件缺席反推出配置。`files` 按上表角色顺序，恰覆盖全部适用角色，不包含额外角色；StagedArtifacts 与清单中的身份、文件列表逐项相同。HTML 也计算哈希，但不另套 JSON envelope。

## 4. HTML 审核页及跨文件校验

四份页面的共同页首显示 document_id、可读文件名、build_id、主解析器、页面阶段和该页统计。每份 PDF 的所有 slot 汇总在各阶段一张自包含 HTML 中，按 `PrimaryDocument` 的 slot 顺序渲染独立 `<section data-slot-id="…">`，每区明确显示 slot 标识/页码、定位框、组状态和候选；无归属候选单列，不能把相邻 slot 的候选或指标混用。提取页中的分区来自已保存的 `GroupingReport` 的匹配/准入证据；一个候选在多个已评估 slot 出现时使用相同 candidate ID，注明最终 matched_slot_id 或未归属，不复制候选 JSON 或重跑匹配。候选表格以 cell 的行列起点、跨度和未覆盖网格位置恢复为可滚动的 HTML `<table>`，无法定位的原文另列；不得用串接 cell 文本代替物理网格，也不依赖外部 iframe。选优页只读已保存证据显示组共同事实、每候选四项原始指标和相对分、总分、winner/原生回退，并在同一候选卡展示其物理表格；零候选/零 winner 有明确空态。内容准备页按 slot 显示采用表格的物理网格、`identified|undetermined` 表头及原因、完整 `SerializedTable.text`；只有 identified 表头着色，原生回退不标成 winner。分块页按 chunk_index 逐块成框显示完整 `Chunk.text`、ID、类型、页码、来源及重复上下文；不因正文过长截断。序列化文本不是最终 chunk 文本，后者只从 ChunkBatch 显示。

Builder 必须执行以下回读校验；Publisher、Health 和 Recovery 使用同一角色清单与校验器，不另维护按预设名称分支的文件名单：

1. 文件路径在本次 staging/build 根内，文件名、角色、大小、SHA、JSON schema、身份和清单内容一致；已接入/未接入集合与有效配置一致。
2. 原生证据的 parser 身份、文件哈希与 MainParser 结果一致；主解析输出与 DocumentAssembler 的节点/slot 引用无跨文档混用。
3. 所有 extractor 绑定各有一份且按绑定顺序排列；候选 ID 唯一并能被 grouping 引用。grouping 每候选的准入及每 slot 的组、scoring 的 ready 组、resolution 的选用/回退，遵守[表格契约](table-contracts.md)的封闭引用与不变量。HTML 中的 slot/candidate/winner 标识与对应 JSON 一致。
4. 每个准备结果的 slot 和表格结构与最终 `ParsedDocument` 中的 TableNode 对应；HTML 的表头状态和序列化文本必须与 JSON 精确一致。ChunkBatch 的 node/source/正文/ID/顺序与 ParsedDocument、待写入向量记录逐项一致；HTML chunk 卡片数量与顺序相同，正文精确一致。

HTML 是派生表示；无法机械回读 HTML 全部自然语言时，至少须以固定 `data-*` 身份与正文摘要、实际文本节点做渲染测试，并禁止页面通过额外业务计算生成新结论。查看器样式调整不改变业务输出，但只要会改变必需 HTML 字节或校验规则，须版本化产物规则并进入构建投影，不能用旧索引冒充满足新必需文件集合。

## 5. 失败与发布边界

任一必需阶段证据无法取得、JSON/HTML 生成失败、路径/哈希/身份/引用不一致或暂存回读失败，Builder 不返回 `StagedArtifacts`，本 PDF 构建停止，旧 active 保持不变。机器错误 `ArtifactErrorCode` 封闭为 `native_evidence_missing|stage_result_missing|json_generation_failed|review_generation_failed|artifact_staging_write|artifact_staging_readback|artifact_identity_mismatch|artifact_reference_mismatch|artifact_role_mismatch`；`stage_result_missing`、`json_generation_failed`、`review_generation_failed` 必带发生的 role，不能靠 message 解析；`artifact_role_mismatch` 包含缺文件、多文件、错误条件或文件名。失败 stage 固定为 `artifact_staging`，原先特定于 winner 页的 `winner_review_generation` 不再使用。发布/回滚错误以[存储发布契约](storage-publication.md)为准。

新产物先暂存并完整回读，再进入同一 IndexIdentity 的写锁事务；旧目录的隔离、向量替换、manifest 生效点与恢复按[存储发布契约](storage-publication.md)执行。已发布 V2.0 目录不回填、不写入也不作为 V3.0 正常运行数据源。技术验证只证明文件系统机制可用，不取代真实 V3.0 发布、健康及恢复的故障注入测试。
