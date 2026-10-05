# V3.0 双解析路线可审计快照设计（已废弃讨论稿）

状态：**已废弃的讨论记录，不得作为编码依据**。用户已明确要求按重要接口的阶段性成果设计快照；正式文件清单见[处理产物需求](../spec/requirements/processing-artifacts.md)与[产物架构](../spec/architecture/processing-artifacts.md)。V2.0 的 `.rag/system-v2/` 产物保留作历史基线，不在原目录补写纯文本快照。

## 1. 决策边界

- 每份成功入库的 V3.0 PDF **必须**有一份与向量、manifest 同次发布的完整可审计快照；快照失败则该 PDF 不发布，旧 active 不变。ArtifactBuilder 和 ArtifactStore 从条件接入改为入库、健康与恢复所需的必接端口，不设“关闭快照”开关。
- 快照用于核对处理过程，不是检索数据源。Retriever、Chunker、Assembler 不能回读 HTML/JSON 改写业务结果；`chunks.json` 必须等于实际向量化的 ChunkBatch，而不是另生成一版正文。
- 两种快照共享发布、哈希、身份和回读规则，但按各自真实处理链保存不同证据。不能为了文件名整齐给纯文本伪造表格选择摘要，也不能给 Docling 伪造 PyMuPDF 逐页块。
- 新增快照是 V3.0 行为要求，不回填或重写既有 V2.0 索引。V3.0 的构建指纹包含所选快照插件及其输出规则版本；审核 HTML 的版式若不影响必需文件内容/哈希则按规则版本判断，不能凭同名配置复用不同产物要求的索引。

## 2. 每份 PDF 的必需文件

V3.0 的目标目录仍由构建指纹与 manifest 决定：`.rag/system-v3/indexes/<fingerprint>/artifacts/documents/<safe-document-name--document-id>/<build-id>/`。只有 manifest 引用的目录是 active；目录名和时间戳不能决定生效状态。下表是两种 ArtifactBuilder 插件的**必需文件全集**，均在同一 build 目录根下。

| 文件 | `parser.pymupdf_pages` + `artifacts.page_text_v3` | `parser.docling_layout` + `artifacts.structured_v3` |
| --- | --- | --- |
| 解析原始证据 | `pymupdf-page-extraction.json`：逐物理页、有序保存 `get_text("blocks", sort=True)` 返回块的原始 `block[4]` 字符串（包括空块），以及原顺序；不复制 PDF，不声称保存了 SDK 的全部对象。 | `raw-docling-document.json`：同次 Docling 转换的原始可序列化文档，不从最终 ParsedDocument 反推。 |
| 主解析事实 | `primary-document.json`：MainParser 实际交付的 `PrimaryDocument`，保留非空页正文、页序、来源和警告。 | `primary-document.json`：新增保存 Docling 适配后、表格选优与最终组装前的阅读序、table slot、原生回退结构及来源；由此可以区分映射错误与选优/组装错误。 |
| 最终语义文档 | `parsed-document.json`：DocumentAssembler 实际交付的 `ParsedDocument`。 | 同左。 |
| 最终分块 | `chunks.json`：实际送入 Embedder 的完整 `ChunkBatch`，含正文、稳定 ID、来源和顺序；不存 embedding 浮点数组。 | 同左。 |
| 规则判断证据 | 无此文件：纯文本构建没有表格准入、分组和 winner。 | `selection-summary.json`：已有的 slot/group/winner 最小原因链；零 winner 仍生成真实空摘要。 |
| 人类审核页 | `chunk-review.html`：逐物理页展示提取块、清洗后的正文、最终该页 chunks 的完整正文、ID、顺序与来源/重叠标记；空白页明确显示“无可提取正文”。不重算清洗或分块。 | `winner-review.html`：沿用已批准的 winner 物理网格、表头结论和实际 chunks 展示；零 winner 生成空报告。 |

因此纯文本有五个必需文件；结构化保留原有五个文件名，并增加一个 `primary-document.json`，共六个。增加主解析事实是为了让两类快照都能审计“解析器输出 → 最终组装”这一跳，不改变结构化正文、winner 或分块。纯文本的审核页对原文做 HTML 转义、不截断；同页 chunk 文本与 `chunks.json` 精确一致。重复上下文标记只从 `ChunkSource` 读取，不能靠 HTML 自行推断。页码是一基物理页；没有正文的页仍在提取证据与审核页中出现，但不得伪造空 chunk。

## 3. 公共接口与数据交接

`ArtifactBuilder` 保持一个公共接口，接收同一文档的 `ProcessingResult`（内含主解析事实及原始证据引用）、`ChunkBatch` 和构建身份，返回统一的 `StagedArtifacts`；首批有 `artifacts.page_text_v3`、`artifacts.structured_v3` 两个插件。插件负责本路线的序列化、审核 HTML 和跨文件验证；Store/Publisher 不写 parser 名称分支，只按已验证的产物 profile、必需角色集合与文件哈希发布。静态装配规则只允许已证明的 parser–builder 配对：`parser.pymupdf_pages → artifacts.page_text_v3`，`parser.docling_layout → artifacts.structured_v3`。新增 parser/builder 必须登记真实输入能力与审核文件集合，不能仅靠相同返回类型放行。

主解析器不能把 SDK 对象藏在公共 `PrimaryDocument` 中。建议将主解析输出显式改成以下统一交接：

```python
@dataclass(frozen=True)
class ParserRawEvidenceRef:
    document_id: str
    file_hash: str
    format_id: Literal["pymupdf_ordered_block_text_v1", "docling_document_2_121_v1"]
    staged_path: Path
    sha256: str

@dataclass(frozen=True)
class MainParseResult:
    primary_document: PrimaryDocument
    raw_evidence: ParserRawEvidenceRef

# DocumentProcessor 将上述两项原样纳入 ProcessingResult；其余处理字段保持不变。

@dataclass(frozen=True)
class ArtifactFileRef:
    role: Literal["parser_raw", "primary_document", "parsed_document", "chunks", "selection_summary", "human_review", "diagnostic"]
    relative_path: str
    sha256: str

@dataclass(frozen=True)
class StagedArtifacts:
    staging_path: Path
    document_id: str
    build_id: str
    file_hash: str
    build_fingerprint: str
    profile_id: Literal["page_text_v3", "structured_v3"]
    files: tuple[ArtifactFileRef, ...]
    chunk_count: int
```

`ParserRawEvidenceRef` 只指向本次文档私有暂存区内已序列化、已哈希的原始解析证据；路径不写入 ParsedDocument、manifest 或向量 metadata。两种 parser 均须产生它，Builder 回读并核对 `document_id/file_hash/sha256/format_id` 后写入正式快照。纯文本证据 JSON 另有封闭 schema：`schema_version,document_id,file_hash,relative_path,page_count,pages`；每页 `page_number,blocks`，每块 `ordinal,raw_text`，块文本是 `str(block[4])` 的精确值，含空白；页和块按实际遍历顺序，空白页保留。`PrimaryDocument` 必须由对同一 pages 中非空白块按原顺序连接、再应用已批准 `clean_page_text` 规则得到；快照不能重新解析 PDF 生成第二套事实。

`ArtifactFileRef` 的 `role` 是消费者需要的通用含义，不是插件字段。两种 profile 均必需 `parser_raw,primary_document,parsed_document,chunks,human_review`；`structured_v3` 额外必需 `selection_summary`。除可选 `diagnostic` 外，每个角色恰一文件；角色—文件名—schema 的对应关系由所选 Builder 插件注册并纳入构建身份，Publisher/Health/Recovery 使用同一注册快照校验，不能各维护一份文件名单。`diagnostic` 可多份，仅在存在时记录哈希，不是发布成功条件。所有相对路径受限于本 build 目录；文件角色、路径和哈希按固定角色顺序保存且唯一。

## 4. 发布、健康与失败验收

ArtifactBuilder 在 passage 编码前完成当前 profile 的全部必需文件暂存及回读；Publisher 在写锁内再次验证后，与向量及 manifest 同事务发布。V3.0 每条已发布 `ManifestDocumentRecord` 的 artifact 引用和文件哈希均非空；Health 对任何缺失、哈希不符、schema 不符、身份不一致、chunk 正文/ID/来源与向量不一致，判定该文档 invalid，不能仅凭 hash/count 跳过重建。Recovery 按事务旧快照整体恢复；失败保留 journal。已批准的结构化 winner HTML 失败语义不变；纯文本审核页生成失败也阻断该文档发布，机器原因与普通文件写入失败区分。

验收至少覆盖：纯文本含空白页、有非文本块、换行与空白规范化、跨多个文本块、页内重叠分块、HTML 特殊字符；结构化零/多 winner；两类快照任一必需文件缺失或损坏；HTML 与 JSON/向量正文不一致；全量 force 中一个快照失败时旧 collection、manifest 和所有旧快照保持不变。固定同一 PDF 和构建参数重复运行，除构建 ID/时间戳外，解析事实、正文、ID、来源和文件角色集合应可逐项对照。

## 5. 待审核的具体问题与同步范围

请重点审核两点：（1）纯文本五个文件是否足以让你定位提取、清洗、组装、分块问题；（2）`chunk-review.html` 是否需要额外展示原 PDF 页面图像。**本提案建议不存页图**：当前纯文本解析未做视觉处理，页图会引入额外大小和渲染依赖；必要时仍可从原 PDF 与页码核对。

确认后需同时更新：总/子需求，产物、处理、装配、存储、健康与运行架构，BuildProjection/manifest/artifact profile，审核图及验收矩阵；删除旧 C4 条件，改为 ArtifactBuilder/ArtifactStore 必接，更新相关边为无条件。既有 V2.0 产物只读保留，V3.0 尚未编码或生成产物。未完成这些同步前，架构仍不通过编码就绪审查。
