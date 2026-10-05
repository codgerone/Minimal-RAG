# 索引存储、发布与恢复契约

状态：**架构已审核，通过规格级编码就绪复审**。回链[总架构](../architecture.md#2-入库主线从源事实到-active-索引)；行为与故障承诺见[索引生命周期需求](../requirements/index-lifecycle.md)。本文是 V3.0 新存储格式与事务的唯一架构位置；旧索引不直接作为运行数据。

## 1. 权威来源和端口

Manifest 是 active 构建及产物引用的唯一权威指针；Chroma 保存可查询 chunk 和向量，ArtifactStore 保存必需审核产物，RecoveryStore 保存足以撤销或确认事务的 journal/快照。四者不得以目录修改时间、collection 存在性或 chunk 数量互相替代。IndexHealth 只读四者并派生健康状态；Publisher 是唯一会更新 active manifest 的协调器。

| 端口 | 操作边界 | 实现 |
| --- | --- | --- |
| `VectorReader` | 按构建身份/文档计数、读取记录、限定范围查询和快照回读 | Chroma 只读视图；browse/chunks 不读浮点 embedding |
| `VectorWriter` | 显式写入已验证向量、替换文档或整个 collection、删除与恢复快照 | Chroma；绝不调用默认 embedding 函数 |
| `ManifestStore` | 读取校验、同目录暂存并原子替换 active manifest | 本地 JSON；替换成功为生效点 |
| `ArtifactStore` | 暂存、隔离、发布、回读校验与提交后清理 | 本地文件系统；active 仅从 manifest 引用 |
| `RecoveryStore` | 持久化 journal、旧 manifest、向量/产物快照及恢复证明 | 本地文件系统；未完成证据不得由 force 覆盖 |

端口最小方法契约：`VectorReader.count(index,document_id?) -> int`、`list_records(index,document_id?) -> tuple[VectorRecord, ...]`、`list_text_metadata(index,document_id?) -> tuple[BrowseRecord, ...]`、`query(index,query_vector,k,document_id?) -> tuple[VectorQueryHit, ...]`、`snapshot(index,document_ids?) -> VectorSnapshot`；`VectorWriter.replace_document(index,document_id,records)`、`replace_collection(index,records)`、`delete_document(index,document_id)`、`restore(index,snapshot)`；`ManifestStore.load(index) -> Manifest?`、`save_atomic(index,manifest,expected_old_sha256?)`；`ArtifactStore.stage/verify/publish/quarantine/restore` 绑定本次事务 ID 与受限相对路径，`cleanup_obsolete(index,active_records)` 只接收当前 manifest 的完整文档记录集，另有事务私有的 staging 清理；`RecoveryStore.list_pending(index) -> tuple[RecoveryJournal, ...]` 按创建时间、transaction_id 稳定排序，`prepare/read/update/clear` 均绑定 IndexIdentity 和 transaction_id。写方法只接收已校验的公共记录，不能接受任意 SDK 句柄；`save_atomic` 必须校验预期旧摘要以防并发覆盖。查询口永不隐式创建 collection，写口只在发布协调器授权后创建。

`BrowseRecord` 恰为 `chunk_id: str, text: str, metadata: ChunkMetadata`，只用于 `chunks|browse` 的只读展示；`list_text_metadata` 向 Chroma 仅请求 documents/metadatas，严格验证两者及来源身份，不请求 embedding。需要完整向量与发布回读的 Health/Recovery 仍调用 `list_records/snapshot`，不得用 BrowseRecord 代替向量证明。

上述端口的方法结果和失败语义固定如下。所有方法只接受已解析并验证位于对应 namespace 的路径；所有失败抛出带[运行契约](runtime-contracts.md)机器码的端口错误，不返回 `False` 表示失败。

| 方法族 | 成功结果与前置/后置条件 | 失败分类 |
| --- | --- | --- |
| `VectorReader.count/list_records/query/snapshot` | 不创建 collection；目标不存在分别返回 `0/()/()/VectorSnapshot(collection_existed=false)`；已存在时严格解码并验证记录，query 将 SDK 命中转换为公共 `VectorQueryHit`，保留未舍入原始距离但不负责最终稳定排序 | SDK 打不开为 `vector_store_failed`；记录 schema/身份/有限向量不符为 `vector_record_invalid` 健康问题 |
| `VectorWriter.replace_document/replace_collection/delete_document/restore` | 调用方已持有 IndexIdentity 写锁；替换后回读 ID 全集、正文、metadata 和向量，成功无返回值；restore 严格还原 snapshot 的“存在/不存在”区别 | 写入/删除/回读不一致为 `vector_store_failed`；不允许部分成功被当作返回值 |
| `ManifestStore.load` | 文件不存在返回 null；存在则先验证 UTF-8 JSON、schema、完整指纹及规范快照 | 非法文件为 `manifest_failed`，不得降级成不存在 |
| `ManifestStore.save_atomic` | 比较当前原始字节 SHA（不存在用 null），相符才同目录暂存、flush+fsync、replace、回读精确新字节；返回新 SHA | 摘要冲突为 `manifest_conflict`，I/O/回读为 `manifest_failed` |
| `ArtifactStore.stage` | 接收 `transaction_id,document_id,build_id,files`，将已生成文件复制/移动到事务私有 staging 并返回 `ArtifactSnapshot`；不改变 active | 路径越界为 `unsafe_path`，写入为 `artifact_failed` |
| `ArtifactStore.verify` | 接收 snapshot，回读路径、SHA、身份及必需文件全集；成功返回同一不可变 snapshot | 任一不一致为 `artifact_failed` |
| `ArtifactStore.publish` | 调用方持锁；从已验证 staging 发布到不可变 build 目录，已有同路径仅在字节完全一致时幂等成功；返回 active 引用和 SHA | 冲突或部分发布为 `publication_failed` |
| `ArtifactStore.quarantine/restore` | quarantine 原子改名到事务恢复目录并返回原/隔离路径；restore 幂等恢复精确旧目录 | 隔离/恢复失败为相应 `artifact_restore_failed`/`publication_failed` |
| `ArtifactStore.cleanup_obsolete` | 调用方持锁且已验证新 manifest、向量、产物；根据完整 active 记录集删除同一 V3 namespace 内所有未引用的旧构建及空文档目录；可重复执行 | 路径不安全或清理失败保留 journal 并阻断该索引，恢复器重试 |
| `ArtifactStore.cleanup_staging` | 只接受已提交或已回滚事务的私有 staging 路径，成功无返回值 | 收尾失败记录 warning，pending journal 由恢复器完成收尾 |
| `RecoveryStore.list_pending` | 只枚举本 IndexIdentity 受限 journal 根；逐份验证 schema、身份与文件名 transaction_id 一致后按创建时间、transaction_id 排序；无记录返回空元组，不清理或跳过非法项 | 任一文件非法为 `journal_unreadable`，越界为 `unsafe_path`；不得把坏 journal 当作无 pending |
| `RecoveryStore.prepare/read/update/clear` | prepare 以目标不存在才创建 journal 并回读；update 比较旧 journal SHA 后原子替换；read 不存在返回 null、非法则错误；clear 仅在提交或回滚完整验证后移除 journal，快照清理由后续 cleanup | 并发冲突为 `manifest_conflict`，非法 journal 为 `journal_unreadable`，越界为 `unsafe_path` |

```python
@dataclass(frozen=True)
class ArtifactSnapshot:
    schema_version: Literal["artifact_snapshot_v3"]
    transaction_id: str
    document_id: str
    build_id: str
    existed: bool
    active_path: str | None
    snapshot_path: str | None
    files: dict[str, str]
    sha256: str
```

ArtifactStore 为本次发布可能触碰的每个旧 active 路径和新目标路径创建恢复证据；路径由对应 ManifestDocumentRecord 推出，旧 manifest 损坏而不可解析时仅允许全量 force，并以新目标记录确定本次会触碰的路径。`document_id/build_id` 取该目标记录。目录不存在时 `active_path` 仍保存该目标相对路径，`snapshot_path` 为空且 `files` 为空，以便跨进程回滚时删除本次新建目录；目录存在时两个路径均非空、受限于当前 namespace。`files` 以旧目录中实际存在的相对文件名为键，值为该文件**当时实际字节**的 SHA-256；允许必需文件缺失、哈希与 manifest 引用不同或出现额外普通文件，故不把“旧 active 健康”作为重建前置条件。只接受该目录直属的普通文件，不跟随符号链接或子目录；目录项不安全则阻断。快照复制后逐文件回读名称、字节哈希与目录全集。恢复器只按经摘要验证的快照路径恢复原有目录或删除原本不存在的目标，并回读原始字节；旧 manifest 不可解析时仍可恢复其精确原始字节，健康检查随后继续报告 `manifest_invalid`。新提交仍须通过 Manifest 引用的全部必需角色和跨文件一致性校验。对象 sha256 按除自身外的规范 JSON 计算。新 staging 用 `StagedArtifacts` 表达，不与旧快照混用。

## 2. 持久化对象

`IndexIdentity` 含完整构建指纹、存储 schema、collection 名和持久化作用域；名称/默认指向不作为兼容性依据。`Manifest` 含 schema_version、index_identity、构建配置规范快照、embedding 身份、文档映射、updated_at；文档映射按 document_id 索引，每项含 relative_path、document_name、file_hash、build_id、页/字符/chunk 数、indexed_at、必需产物引用与其校验摘要。`VectorRecord` 含 chunk_id、正文、完整可还原的 document/chunk/source metadata 和显式 embedding；metadata codec 不得丢跨页来源、重复上下文或 chunk kind。所有记录的构建/文档身份须与 manifest 相同，chunk ID 在目标物理索引内唯一。

Indexer 在获取写锁前建立请求；Publisher 在锁内重验并交付结果：

```python
@dataclass(frozen=True)
class PublicationRequest:
    transaction_id: str
    operation: Literal["replace_document", "replace_collection", "prune_document"]
    index_identity: IndexIdentity
    document_id: str | None
    records: tuple[VectorRecord, ...]
    staged_artifacts: tuple[StagedArtifacts, ...]
    new_manifest: Manifest
    expected_old_manifest_sha256: str | None

@dataclass(frozen=True)
class PublicationResult:
    transaction_id: str
    operation: Literal["replace_document", "replace_collection", "prune_document"]
    index_identity: IndexIdentity
    document_id: str | None
    outcome: Literal["committed", "rolled_back", "failed"]
    commit_point_reached: bool
    old_manifest_sha256: str | None
    new_manifest_sha256: str | None
    published_document_ids: tuple[str, ...]
    warnings: tuple[ProcessingWarning, ...]
    recovery_result: RecoveryResult | None
```

replace_document 要求 document_id 非空、records 全属该文档，且 `staged_artifacts` **恰一份**，身份与该文档及本次 build 一致；不再因纯文本装配而允许零产物。replace_collection 的 document_id 为空、records/产物覆盖 new_manifest 全部文档且每份文档恰一份已验证产物；prune 的 document_id 非空、records/产物为空且 new_manifest 不含该文档。旧 manifest 不存在时 expected 摘要为空。committed 当且仅当到达生效点、新摘要非空、recovery_result 为空；rolled_back 时未到生效点、published IDs 为空且 recovery_result 为 rolled_back；failed 必有 failed 恢复结果。prune 的 committed published IDs 为空，其他 committed 与本次生效文档集合相同。warning 只允许提交后清理类，不掩盖恢复失败。

`RecoveryJournal` 保存事务 ID、操作、作用域、预期旧/新 manifest 摘要、旧向量及产物快照引用、staging 引用、已到达步骤及校验摘要；完整字段见下表。journal 阶段是封闭枚举，不用任意 message 判断提交。

下列模型分别由装配根、Publisher 和存储适配器创建，Health、Retriever 与 Recovery 只能读取已发布或受控事务事实。每个代码块列出完整字段，类型别名 `sha256` 均指 64 位小写 SHA-256，`relative_path` 指受限 POSIX 相对路径。

```python
@dataclass(frozen=True)
class IndexIdentity:
    build_fingerprint: str
    storage_schema_version: Literal["index_store_v3"]
    collection_name: str
    namespace_path: str

@dataclass(frozen=True)
class ManifestDocumentRecord:
    document_id: str
    document_name: str
    relative_path: str
    file_hash: str
    build_id: str
    page_count: int
    character_count: int
    chunk_count: int
    artifact_path: str
    artifact_files: dict[str, str]
    indexed_at: str  # RFC3339

@dataclass(frozen=True)
class Manifest:
    schema_version: Literal["index_manifest_v3"]
    index_identity: IndexIdentity
    build_configuration: BuildProjection
    embedding_identity: EmbeddingIdentity
    documents: dict[str, ManifestDocumentRecord]
    created_at: str  # RFC3339
    updated_at: str  # RFC3339
```

IndexIdentity 的 collection 与 namespace 从完整 fingerprint 决定，NAME 不参与；namespace 不得绝对或含 `..`。Manifest 的 map key 等于记录 document_id，构建身份一致，BuildProjection 按[装配指纹算法](assembly-contracts.md#8-构建身份算法)重算并匹配。文档页数/chunk 数正、字符数非负；每条已发布文档的 artifact_path 与 artifact_files 均非空，前者受限于当前 namespace，后者恰以 role 为键覆盖 `snapshot_manifest` 及[产物契约](processing-artifacts.md#2-目录角色与条件)要求的全部适用文件，不得按预设名称硬编码旧五文件。每个 SHA 为 64 位小写十六进制，回读快照清单和文件内容后必须逐项匹配。

```python
@dataclass(frozen=True)
class VectorRecord:
    chunk_id: str
    document_id: str
    build_id: str
    file_hash: str
    text: str
    metadata: ChunkMetadata
    embedding: tuple[float, ...]

@dataclass(frozen=True)
class VectorQueryHit:
    record: VectorRecord
    distance: float

@dataclass(frozen=True)
class ChunkMetadata:
    schema_version: Literal["chunk_metadata_v3"]
    document_id: str
    document_name: str
    relative_path: str
    build_id: str
    file_hash: str
    build_fingerprint: str
    text_sha256: str
    chunk_index: int
    kind: Literal["text", "list", "table"]
    token_count: int | None
    page_numbers: tuple[int, ...]
    sources: tuple[ChunkSource, ...]
    parent_unit_id: str | None
    fragment_index: int
    fragment_count: int
```

向量有限、维度等于 EmbeddingIdentity；text 与 metadata 的 UTF-8 正文摘要一致，写入时显式提供向量。锁定 Chroma/HNSW 环境实测对写入的浮点向量有约 `3e-8` 的逐分量表示尾差；写入回读及快照恢复验证逐分量使用**绝对容差 1e-6、相对容差 0**，同时要求维度、全部分量有限且 ID/正文/metadata 完全一致。恢复快照保存当时实际回读的数值及其精确 SHA，不用容差放宽快照文件的完整性校验；不同分量超差仍失败。`VectorQueryHit.distance` 是 SDK 返回的未舍入有限距离，record 必须在本次查询的 IndexIdentity、可选 document_id 和已发布 manifest 范围内；Reader 不重排同分候选，Retriever 负责边界扩展及稳定排序。metadata 身份与记录/manifest 相同；物化的 page_numbers 必须等于 sources 派生页集合，token_count 仅在 token 分块时为正，片段条件遵守[公共 Chunk 契约](document-contracts.md#4-chunk-与来源)。browse 只取 text/metadata，不加载 embedding。

```python
@dataclass(frozen=True)
class RecoveryJournal:
    schema_version: Literal["index_recovery_v3"]
    transaction_id: str
    index_identity: IndexIdentity
    operation: Literal["replace_document", "replace_collection", "prune_document"]
    document_id: str | None
    state: Literal["prepared", "mutating", "restoring", "recovery_failed"]
    current_step: PublicationStep
    old_manifest_sha256: str | None
    old_manifest_snapshot_path: str
    old_vector_snapshot_path: str
    artifact_quarantine_path: str | None
    new_staging_path: str | None
    expected_new_manifest_sha256: str
    created_at: str  # RFC3339
    error_code: RecoveryFailureCode | None
    error_message: str | None
```

整 collection 的 document_id 为空，文档级非空；仅 prune 有 quarantine。`recovery_failed` 必有错误码/消息，其他 state 两者均空。路径限于当前事务的恢复或 staging 目录。journal 步骤只表示动作可能已尝试，不能据此独自判定提交；须回读 manifest、向量和产物。

`PublicationStep` 封闭为 `none|vector_replace|vector_verify|artifact_publish|artifact_verify|manifest_publish|vector_restore|manifest_restore|artifact_restore|artifact_cleanup`。prepared 时为 none；动作开始前先持久化相应 current_step，故该字段只记录**可能已尝试**动作，不能单凭它推断已提交；提交仍以新 manifest 摘要及向量/产物回读共同证明。`RecoveryFailureCode` 封闭为 `snapshot_unreadable|journal_unreadable|manifest_conflict|vector_restore_failed|artifact_restore_failed|manifest_restore_failed|verification_failed|unsafe_path|lock_unavailable`，与 `error_message` 分离；恢复失败保留 journal，不自动 force。旧 manifest 不存在时 old_manifest_sha256 为空，但 snapshot 必须保存可验证的“原本不存在”证据。所有相对路径在读写前解析并验证位于所属 namespace 内，禁止跨 namespace 清理。

恢复读取的旧状态必须显式区分“不存在”与“存在但为空”：

```python
@dataclass(frozen=True)
class VectorSnapshot:
    schema_version: Literal["vector_snapshot_v3"]
    index_identity: IndexIdentity
    collection_existed: bool
    document_scope: tuple[str, ...]
    ids: tuple[str, ...]
    documents: tuple[str, ...]
    embeddings: tuple[tuple[float, ...], ...]
    metadatas: tuple[ChunkMetadata, ...]
    sha256: str

@dataclass(frozen=True)
class ManifestSnapshot:
    existed: bool
    bytes_sha256: str | None
    snapshot_path: str | None
```

VectorSnapshot 四个记录数组等长、ID 唯一，向量有限且每条身份属于作用域；sha256 按除自身外规范 JSON 计算。不存在的 collection 为 false 且四数组全空，已存在的空 collection 为 true；全量 force 覆盖整个旧 collection，单文档替换/prune 只覆盖目标文档。ManifestSnapshot 不存在时两可空项均空，存在时两者均填且回读一致。产物快照的完整字段由本节 `ArtifactSnapshot` 定义。恢复材料限于事务私有 `recovery/<transaction_id>/` 或本次 staging，禁止跨 namespace 引用。

Chroma metadata codec 只写本环境已验证支持的 `str|int|float|bool` 标量，字段全集为：`schema_version="vector_metadata_v3",document_id,document_name,relative_path,file_hash,build_id,build_fingerprint,text_sha256,chunk_index,chunk_kind,token_count_or_minus_one,parent_unit_id_or_empty,fragment_index,fragment_count,page_numbers_json,sources_json`。`page_numbers_json` 和 `sources_json` 使用与构建投影相同的确定性 UTF-8 JSON 规则转字符串；null token_count 以 -1 存储、null parent 以空字符串存储，解码后必须恢复原语义并拒绝其他负数或非法空值。`sources_json` 完整编码 ChunkSource/PageSpan/BoundingBox，不能只保存首个页或 node ID；`text_sha256` 必须等于 Chroma document UTF-8 字节摘要。读取时必须校验 metadata schema、字段全集、SDK 标量类型（整数拒绝 bool）、document/file/build 身份、来源页物化值、片段范围、正文非空、正文摘要及 chunk ID；不支持旧 `vector_metadata_v2` 作为运行回退。Chroma `document` 字段保存与 ChunkBatch 完全相同的正文，id 为 chunk_id，embedding 显式传入。

存储根按构建身份隔离；两个完整配置若构建身份相同可引用同一物理索引，但任何配置级命令须解析成准确的 IndexIdentity。不同构建身份的写入、prune 或恢复不能越界。两份首批预设的构建身份不同且 active collection、manifest、产物与恢复证据相互隔离。旧 V2.0 存储保持历史只读，不作为 V3.0 ManifestStore 的 fallback。

V3.0 物理根为 `.rag/system-v3/indexes/<完整64位小写构建指纹>/`，其中 `manifest.json`、`artifacts/`、`staging/`、`recovery/` 皆在该 namespace 内；Chroma 数据根为 `.rag/system-v3/chroma/`，collection 名为 `rag_v3_` 加构建指纹前 32 位。打开 collection 必须回读 manifest 中的完整指纹，若短名碰撞则拒绝而非复用。评估数据单独置于 `eval/` 的 V3.0 子空间，不与索引恢复事务共用目录。`artifact_path` 只允许本 namespace 下 `artifacts/documents/<安全文档目录>/<build_id>/`；新目录取原 PDF stem 经 Unicode 字母数字/点/下划线/连字符保留、其余字符替为连字符并压缩连续连字符后的前 8 字符（去除末尾空格、点、连字符、下划线），再加 `--<完整 document_id>`；前缀为空时用 `document`。这样在当前 Windows 工作区中三种审核页均可直接打开。历史 V3 构建采用相同规则但前缀最多 56 字符，回读与恢复仍接受其精确旧路径；新发布一律使用短路径。active 只能从 ManifestDocumentRecord 引用。共享构建身份时配置只保存索引引用，不复制向量；prune/force 作用于该物理构建身份，并在 CLI 明示其他引用它的配置，不能暗中删掉另一配置唯一可用的索引。提交后按当前 manifest 的 artifact_path 白名单清理此构建身份中所有旧 V3 文档构建目录及空文档目录，不因历史评估运行保留旧目录；评估报告有独立只读结果。每份 PDF 最终恰有一份当前产物，prune 后为零份。清理须在持有索引锁、完整验证新状态且保留恢复快照时执行；失败保留 journal 并阻断索引，恢复器在判定新状态已提交后幂等重试清理，完成后才移除 journal。V2 目录不在清理作用域。

## 3. 发布顺序与生效点

每个 IndexIdentity 的写入与恢复都须先取得同一 `namespace_path/index.lock` 的跨进程排他锁，持有到提交后回读/回滚验证及 journal 清理结束；锁文件内容不作为状态，进程退出由 OS 自动释放锁。Windows 实现采用对预先存在的单字节文件执行非阻塞独占锁，竞争者返回 `lock_unavailable`，不得绕过锁重试写入。取得锁后重新读取 manifest、journal、collection 和本次期望旧摘要；若与锁前预检不同，拒绝本次写入并重新健康检查。所有只读查询在读取 manifest 前检查 pending journal，读取命中后再校验 manifest 摘要未变且仍无 pending journal；发现变化则丢弃本次结果并报告未就绪，不交付混合快照。该读门只防进程中断和并发写造成的混合可见性，不承诺断电后的目录项持久化。

一个事务的写序固定为：① 在 staging 完成新 payload 且全部回读；② 在锁内保存旧 manifest/目标向量/必需产物快照，校验各哈希、数量和身份；③ 以 `prepared/none` 原子发布 journal 并回读；④ 每个破坏性动作前将 current_step 持久化为对应步骤，随后执行动作并回读；⑤ 新向量和必需产物全部验证通过后，原子替换 manifest；⑥ 对新 manifest、向量和产物再次回读；⑦ 将步骤记为 `artifact_cleanup`，按新 manifest 清理旧 V3 构建并确认每份 PDF 只剩当前产物；⑧ 清理 journal 与快照。第⑦步失败保留 journal 并阻断索引，由恢复器重试；第⑧步失败记录 warning，下次读取仍按 pending journal 先判定已提交并完成收尾。journal 每次更新均先写同目录暂存、flush+fsync 文件，再 `os.replace`，不允许原地改 JSON。manifest 也同样处理，并在持锁状态比较旧文件摘要；目录项原子替换已在本机进程中断测试，断电耐久性不在已批准承诺内。

`expected_new_manifest_sha256` 由准备阶段对最终精确 JSON 字节计算，不得用不同序列化规则得到的摘要替代。active manifest 写盘使用 UTF-8、键排序、两空格缩进和末尾换行；准备阶段与 ManifestStore 共用该序列化规则，写盘后按实际字节回读并核对摘要。回读新 manifest 时同时验证 schema、完整 IndexIdentity、文档条目、向量 ID/正文/metadata/embedding 维度及所有必需产物摘要。仅 SHA 一致但向量/产物不一致不是已提交。旧 manifest 快照保存原始字节；若原本不存在，保存 `ManifestSnapshot(existed=false,...)` 作为有签名的快照记录。Chroma 恢复使用 `VectorSnapshot` 的显式向量和 metadata，不通过默认 embedding 函数重新编码。全量恢复先清掉本 namespace 本次写出的记录再回填整个旧 collection；文档级恢复只清目标 document_id，不改其他文档。所有删除/回填后均按快照的 ID 集、数量、正文、metadata 和向量逐项回读，不以 count 单独证明成功。

单文档：先完成解析、chunk、必需产物与向量的新 payload；验证身份、数量、有限向量、来源和产物引用；再持久化可回读的旧 manifest、旧向量/产物快照与 journal；之后替换并回读新向量、发布并回读新产物；最后原子保存新 manifest。manifest 成功前发生的错误必须恢复旧 manifest、向量和 active 产物；回滚失败保留 journal 与快照并阻断该索引。

全量 force：全部目标文档先预构建成功，否则清理本次 staging 且旧索引不变；若可解析旧 manifest 尚登记缺失源文档，先阻断本次整 collection 替换，不得把缺失条目从新 manifest 隐式移除；待显式 prune 后再继续。旧 manifest 存在但 schema/JSON 不可解析时，不伪装为不存在：保存原始字节 SHA 与快照，仅允许显式全量 force；新目标可能覆盖的每个产物目录均先保存存在性和原始字节证据，原 collection 完整快照照旧保存。随后替换整个目标 collection 和必需产物；已存在但校验失败的相同构建目录仅在事务证据准备并回读后替换；只原子保存一次新 manifest，使全部新构建同时生效。collection 缺失修复也要求所有仍有源文件的目标构建完整，且缺失的旧条目已由显式 prune 解决，不能先发布成功的子集。`prune` 在恢复证据可回读后隔离旧产物、删除目标向量、原子保存移除条目的 manifest；提交后陈旧产物清理失败保留 journal 并阻断索引，恢复器重试。

## 4. 中断判定、健康与清理

重启/显式恢复在同一排他锁内先读取 journal 和两份 manifest 摘要，再验证新 manifest 与新向量/产物。如果三者全部匹配 `expected_new_manifest_sha256`，结果为 `committed`，只执行提交后清理；包括 journal 停在 `manifest_publish` 或 `artifact_cleanup` 的情况。否则只能按旧快照恢复：先恢复向量，再恢复被隔离的旧产物，最后恢复旧 manifest 原始字节（旧 manifest 原本不存在则移除本次新 manifest），然后再次完整回读旧三者，结果才是 `rolled_back`。旧产物若原本损坏或缺失，回读验收以 ArtifactSnapshot 记录的**原始目录存在性和每个实际文件字节**为准，不把旧 Manifest 的健康一致性当作回滚成功条件；回滚后 Health 仍可报告该文档 `invalid`，以便再次重建。任何一步失败设置 `recovery_failed` 与封闭原因码，保留 journal、快照和 staging，Health 阻断该 IndexIdentity；不得因旧 manifest 看似存在而忽略新向量残留。恢复再次调用必须幂等，遇到相同旧状态可继续完成校验；不得先清除证据再验证。

故障注入点至少覆盖 prepared 之前、journal 发布之后、向量替换前/后、向量验证前/后、产物发布/隔离前后、manifest 替换前/后、提交后清理前后及每一个恢复动作前/后。每个点独立启动新进程回读并断言：要么三份新事实一致且 committed，要么三份旧事实一致且 rolled_back；无法证明时 recovery_failed 并禁止查询。单文档、全量 force、prune 和旧 collection 不存在四类作用域都要覆盖。该矩阵是[技术验证 T-05/T-06](../technical-validation.md)的剩余准入证据，不能用单文件 `os.replace` 测试替代。

Recovery 对 pending journal 先验证 manifest 是否已变成预期新摘要、且新向量与必需产物均可回读：可以证明已提交时只做提交后清理；否则按旧快照回滚，并完整回读验证。无法证明任一方向时保留证据和阻断状态，绝不猜成功或以 force 覆盖。只读命令仅报告，不修改 journal；显式 ingest 可在处理源文件前进入恢复，交互只读命令仅经用户确认才调用 Recovery。

Manifest 存在而 collection 缺失、向量/产物与 manifest 不一致、未完成 journal、构建配置不匹配均是组合级或文档级健康问题，优先级和封闭原因码由运行契约定义。无 manifest/空 collection 的初始状态与损坏状态不同。孤儿目录不成为 active；可报告，但只读流程不自动清除。

## 5. 实现一致性门

Manifest、journal、三类 snapshot、VectorRecord/metadata、哈希输入、Windows 锁与替换边界、恢复判定和共享构建身份作用域已由本文固定。实现必须把第 4 节故障矩阵、Chroma 完整记录快照/恢复、应用层同分排序及多配置引用清理写成自动化测试；在这些测试通过前不得把 Publisher/Recovery 组件标为完成，但编码者不需要也不得另行发明事务规则。[技术验证 T-05/T-06](../technical-validation.md)已证明所选第三方机制在本环境可行。
