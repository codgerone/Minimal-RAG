# 命令、健康与交互恢复契约

状态：**架构已审核，通过规格级编码就绪复审**。回链[总架构](../architecture.md#4-运维恢复与正式评估)，可观察规则见[运行操作需求](../requirements/operations.md)；发布 journal 的完整持久化定义归[存储发布契约](storage-publication.md)。

## 1. CLI 装配根与参数

命令保留 `documents|ingest|chunks|browse|search|ask|chat|eval`。统一配置选择为：无选择参数读取明确的默认配置指向；`--config NAME` 查找已保存配置；`--configure` 交互选择已有或建立新配置。两选项互斥，旧 `--pipeline` 被拒绝；`ingest --all-configs` 又与两种单配置选择互斥。参数冲突、未知名称、配置 schema 无效在打开索引或加载模型前报错。解析得到不可变 `CommandContext`，包含所选配置名称、构建身份、查询设置、命令作用域和交互能力；`chat` 会话固定该上下文。

```python
@dataclass(frozen=True)
class CommandContext:
    command: CommandName
    configuration_name: str | None
    configuration: SavedConfiguration | None
    index_identity: IndexIdentity | None
    interactive: bool
    selection_mode: Literal["default", "named", "wizard", "all_configs"]
    query_k: int | None
    document_selector: str | None
    debug: bool
    force: bool
    prune: bool
    live: bool
```

`CommandName` 封闭为八个命令。`all_configs` 只用于 ingest，此时单配置三字段为空；其他模式必须有名称、配置、索引身份。query_k 若填必须为正且仅 search/ask/chat/eval 使用；force/prune 仅 ingest，live 仅 eval，debug 仅支持的问答命令。解析器拒绝无关参数；document_selector 在 Registry 解析前只是用户输入，业务接口只接 document_id。

无索引且无相关配置的交互场景先询问是否使用最佳装配，拒绝进入逐接口向导；所选配置存在但未建索引时，入库可在确认后构建，查询只引导建立该配置索引，不切换配置。非交互不等待输入、不创建配置或隐式建索引；显式 `ingest` 仍可按参数写入。向导保存配置前校验完整装配、名称规则及原子落盘；详细装配模型见[装配契约](assembly-contracts.md)。

## 2. 健康读取模型

`HealthReport` 含 `usable: bool`、`index_identity`、`document_statuses: tuple<DocumentStatus>`、`issues: tuple<IndexIssue>`；`DocumentStatus` 含 source/manifest 对照身份、`state`、可空 file_hash/记录计数/时间及 issue 引用。`state` 封闭为 `current|new|changed|missing|invalid|unprocessable|unassessed`，触发和优先级完全由[操作需求](../requirements/operations.md#2-健康状态)定义。`IndexIssue` 应使用封闭 code、scope、stage、blocking 和可空 document_id；`message` 与 remediation 文本只作展示，不参与分支。

```python
@dataclass(frozen=True)
class DocumentStatus:
    document_id: str
    relative_path: str
    document_name: str
    state: DocumentState
    source_file_hash: str | None
    recorded_file_hash: str | None
    page_count: int | None
    chunk_count: int | None
    indexed_at: str | None  # RFC3339
    issues: tuple[IndexIssue, ...]

@dataclass(frozen=True)
class IndexIssue:
    code: IndexIssueCode
    scope: Literal["configuration", "document", "artifact", "transaction"]
    stage: StageCode
    document_id: str | None
    blocking: bool
    message: str
    remediation: RemediationAction

@dataclass(frozen=True)
class HealthReport:
    usable: bool
    index_identity: IndexIdentity
    document_statuses: tuple[DocumentStatus, ...]
    issues: tuple[IndexIssue, ...]
    checked_at: str  # RFC3339
    inspection_complete: bool
```

IndexHealth 创建以上模型，CLI、Retriever 和 Evaluator 消费。源缺失时 source hash 空；未登记时 recorded hash、计数及时间空；组合阻断时 state 为 unassessed，已读 manifest 计数只能展示，不能暗示 current。document/artifact issue 必有 document_id，configuration/transaction issue 不得有。组合阻断使 inspection_complete 与 usable 均 false；issues 按已定义优先级和 document_id 稳定排序。

DocumentState 的判定顺序固定为：存在组合级阻断 → 所有目标 `unassessed`；否则源存在且属于 new/changed 候选时才运行 Probe，失败 → `unprocessable`；Probe 成功且源与 manifest 均存在但 source hash 不同 → `changed`；Probe 成功且源存在、manifest 无记录 → `new`；manifest 有记录、源不存在 → `missing`；两者存在且 hash 相同但任一向量/metadata/必需产物/计数/身份校验失败 → `invalid`；全部一致 → `current`。已登记且 hash 未变的文档先保留可核验的 current/invalid 事实，不让 Probe 覆盖；这与[操作需求第 2 节](../requirements/operations.md#2-健康状态)一致。同一文档只取首个命中状态，不同时输出多个 state；issues 可保留所有已实际检查出的同层证据。`new` 的 recorded 字段全空；`missing` 的 source hash/page_count 为空；`unprocessable` 可按 SourceProbeResult 保留已获得页数但不产生 chunk_count；`current|changed|invalid` 有两侧 hash 和 manifest 计数；`unassessed` 只展示在组合阻断前已读取的 manifest 事实，不继续探测源或向量。下游只有全部 current 且无阻断 issue 时可查询。

`IndexIssueCode` 封闭为 `pending_recovery|recovery_failed|manifest_invalid|manifest_missing_with_collection|configuration_mismatch|collection_missing_with_manifest|collection_manifest_count_mismatch|unknown_vector_document|source_new|source_changed|source_missing|source_unprocessable|vector_record_invalid|artifact_invalid`。`RemediationAction` 封闭为 `recover|incremental_ingest|force_rebuild|prune_after_confirmation|repair_source|repair_configuration|none`。`StageCode` 封闭为 `configuration|source_discovery|source_probe|main_parsing|table_extraction|table_selection|assembly|chunking|artifact_staging|embedding|vector_store|publication|recovery|health|retrieval|prompt|llm|evaluation`；更细的 SDK 异常可在只读 detail 中保存，不扩展控制流枚举。优先级为 pending/recovery_failed → manifest/schema/config/collection 结构 → 逐文档 source/vector/artifact；同级按 code 再按 document_id 排序。issue code 不拼接 document ID。

```python
@dataclass(frozen=True)
class OperationError:
    code: OperationErrorCode
    stage: StageCode
    scope: Literal["configuration", "document", "artifact", "transaction", "request"]
    document_id: str | None
    retryable: bool
    message: str
    diagnostic_ref: str | None
```

`OperationErrorCode` 封闭为 `invalid_argument|unknown_configuration|invalid_assembly|missing_source_directory|unsafe_document_selector|ambiguous_document_selector|pdf_unreadable|pdf_no_content|main_parser_failed|optional_extractor_failed|selection_invariant_failed|assembly_invariant_failed|chunking_failed|artifact_failed|embedding_failed|vector_store_failed|manifest_failed|manifest_conflict|publication_failed|recovery_failed|index_not_ready|retrieval_failed|prompt_failed|llm_missing_credentials|llm_auth_failed|llm_rate_limited|llm_timeout|llm_upstream_failed|evaluation_invalid|evaluation_failed`。stage/scope 与 code 匹配，document/artifact 作用域需有 document_id；可继续的提取器页/策略错误只转 warning。仅 LLM 认证在显式交互后可重试一次；message 和 SDK 文本不驱动控制流。

IndexHealth 输入源发现事实、只读 manifest/向量/产物和 pending journal 状态；SourceProbe 仅只读判断 PDF 页和可用内容，不运行完整解析/编码。先判组合级 schema、身份、配置、collection/manifest 结构和 journal；一旦阻断，全部应检查文档为 `unassessed`，不得同时猜测 current/invalid。没有组合级阻断才逐文档判源、file hash、向量和必需 active 产物。每条已发布文档均须有快照目录与清单；Health 按有效构建绑定及[产物角色契约](processing-artifacts.md#2-目录角色与条件)校验适用文件全集、未接入阶段缺席、文件哈希/身份/跨文件引用及 chunks 与向量记录一致，不凭预设名称或仅 hash/count 判定 current。`usable` 当且仅当无阻断问题且所有应检查文档均 current。新配置尚无 manifest/collection 是可引导建库状态，不是假损坏。

错误映射按最内层已知阶段和以下表格确定；同一异常满足多项时取表中靠前的更具体项，不以异常 message 匹配。第三方类名仅记录在受限 diagnostic，适配器必须在边界转换为本表机器码。

| 来源事实 | OperationError code / stage / scope | Health issue 或继续规则 |
| --- | --- | --- |
| CLI 类型、互斥项、K/NAME/schema 参数无效 | `invalid_argument/configuration/request` 或 `invalid_assembly/configuration/configuration` | 写入前终止；不创建 Health issue |
| 配置不存在 | `unknown_configuration/configuration/configuration` | 终止并列出已有 NAME |
| documents 根缺失/不可读 | `missing_source_directory/source_discovery/configuration` | 不把所有已登记文档误判 missing |
| 选择器越界或歧义 | `unsafe_document_selector` 或 `ambiguous_document_selector` / `source_discovery/request` | 不进入 SourceProbe |
| PDF 无法打开/无可处理内容 | `pdf_unreadable` 或 `pdf_no_content` / `source_probe/document` | `source_unprocessable`，修复源文件 |
| 主解析 SDK/映射失败 | `main_parser_failed/main_parsing/document` | 当前文档构建失败；旧 active 保留 |
| 可选提取器单页/单策略异常 | 不创建 OperationError；`optional_tool_page_failed` warning / `table_extraction` | 继续其他页/策略；插件初始化完全失败用 `optional_tool_unavailable` warning；报告状态不得写成零表 |
| 选择或组装不变量破坏 | `selection_invariant_failed/table_selection/document` 或 `assembly_invariant_failed/assembly/document` | 当前文档阻断，不降级换插件 |
| 分块覆盖、上限或 ID 不变量失败 | `chunking_failed/chunking/document` | 不编码、不发布 |
| 产物生成/回读/身份失败 | `artifact_failed/artifact_staging/artifact` | 本次文档不发布；所有入库文档均需快照 |
| 模型加载、编码、维度或有限值失败 | `embedding_failed/embedding/document|request` | passage 为 document，query 为 request；退出码 4 |
| Chroma 打开/读写/回读失败 | `vector_store_failed/vector_store/transaction|request` | 健康读取到非法记录用 `vector_record_invalid`；发布中进入回滚 |
| manifest JSON/schema/I/O 失败 | `manifest_failed/health|publication/configuration|transaction` | 对应 `manifest_invalid`；发布中进入回滚 |
| manifest/journal 预期摘要冲突或写锁被占 | `manifest_conflict/publication/transaction` | 不覆盖，重新健康检查；retryable=true 仅表示调用者可在重新检查后显式重试 |
| 发布任一步骤失败 | `publication_failed/publication/transaction` | 进入恢复；恢复结果决定 pending/recovery_failed |
| 快照不可读或任一恢复动作/验证失败 | `recovery_failed/recovery/transaction` | `recovery_failed`，证据保留并阻断 |
| 健康门不通过 | `index_not_ready/health/request` | 不加载 query Embedder/LLM |
| 查询 SDK 或排序/命中身份不变量失败 | `retrieval_failed/retrieval/request` | 不构建 Prompt |
| Prompt 构造/来源能力不满足 | `prompt_failed/prompt/request` | 不调用 LLM |
| LLM 缺 key、认证、限流、超时、其余上游 | 对应 `llm_* / llm/request` | 仅认证在交互模式允许换 key 后重试一次 |
| 评估预检失败 | `evaluation_invalid/evaluation/request` | run=`invalid`，无正式指标 |
| 预检后检索/计算/报告/发布失败 | `evaluation_failed/evaluation/request` | run=`failed`，不进入基线 |

`StrategyExecution.error_type`、`PageExecution.error_type` 和 OperationError 的 `diagnostic_ref` 只保存诊断分类/引用，不参与任何分支；控制流只读取封闭 status/code/reason。诊断字符串为空条件由各状态不变量决定，因此它们不构成开放状态枚举。

```python
@dataclass(frozen=True)
class SourceProbeResult:
    document_id: str
    file_hash: str
    status: Literal["processable", "unprocessable"]
    page_count: int | None
    reason: Literal["pdf_unreadable", "pdf_no_content"] | None
```

processable 必有正页数且无原因，unprocessable 必有原因且仅在已打开并计数时保留页数。Probe 不保存可入库正文，不覆盖已登记文档的 vector/artifact invalid 事实。`SourceProbe.probe(source: SourceDocument) -> SourceProbeResult` 只读。`IndexHealth.check(index_identity: IndexIdentity, sources: tuple[SourceDocument, ...]) -> HealthReport` 只读且幂等；sources 是同次 Registry 的完整稳定排序结果，不能由 Health 自行按目录重新枚举。ManifestStore、VectorReader、ArtifactStore、RecoveryStore 由已验证的 Health 插槽绑定，Health 在方法内部读取它们，不让调用方构造未定义的 `manifest_view/vector_view/artifact_view/journal_view` 参数。存储读取失败产生已定义的组合级机器 issue，不伪造正常零记录；健康失败的查询不继续编码。

## 3. 修复编排与凭据

`RemediationPlan` 包含目标构建身份、问题证据、建议动作与作用域；动作封闭为恢复 pending journal、增量 ingest、全量 force、显式 prune 或人工修复源/配置，不用命令字符串解析动作。CLI 交互展示原因、影响和是否写入，取得肯定确认后调用 Recovery/Indexer，完整复查再继续原请求。pending journal 的恢复判定优先于 force；非交互只读命令输出下一步并失败，绝不写入。取消确认不修改状态。

```python
@dataclass(frozen=True)
class RemediationPlan:
    index_identity: IndexIdentity
    trigger_issue_codes: tuple[IndexIssueCode, ...]
    action: RemediationAction
    document_ids: tuple[str, ...]
    requires_confirmation: bool
    default_confirm: bool
    resume_original_request: bool

@dataclass(frozen=True)
class RecoveryResult:
    transaction_id: str
    index_identity: IndexIdentity
    outcome: Literal["committed", "rolled_back", "failed"]
    verified_manifest: bool
    verified_vectors: bool
    verified_artifacts: bool
    remaining_issues: tuple[IndexIssueCode, ...]
```

计划作用域由 IndexIdentity 和 document_ids 决定，不允许自由路径；只依据封闭状态和优先级生成，不读 message。failed 恢复结果必有 remaining_issues，三个 verified 字段保存实际完成的验证，可混合；committed/rolled_back 三项均 true 且 remaining_issues 为空。仅后二者且 Health 复查通过才恢复原只读请求。

`ask|chat|eval --live` 仅在索引健康通过且即将调用 LLM 时要求 OpenRouter key。交互输入仅用于当前进程；用户明确同意后才原子更新 `.env`。认证失败可新 key 重试一次原 LLM 请求；限流、超时和上游故障不循环。日志/默认错误不输出 key、全文 Prompt、embedding 或完整文档；显式 debug 只向当前终端展示实际命中和最终消息。各阶段错误需携带机器 code、stage、scope、可恢复性和人类 message；正常零表、零命中、表头 undetermined、拒答不转成错误。

`Recovery.recover(index_identity: IndexIdentity, transaction_id: str) -> RecoveryResult` 只处理同一 namespace 内一份已发现的 pending journal；transaction_id 非空且必须由该 IndexIdentity 的 RecoveryStore 只读枚举取得，调用方不得提供任意路径。若同一 namespace 有多份 pending journal，按 journal 创建顺序逐份处理、每份之后重查状态；任一份不能证明 committed 或 rolled_back 就停止，不跨过失败证据继续后续事务。Recovery 的 Store 插槽是绑定资源，不在主处理图上伪造 `StoreOperation` 业务阶段；恢复成功仍须经 IndexHealth 重新检查才允许原只读请求继续。

## 4. 命令最小资源图

`documents` 需要 Registry/Health；`chunks|browse` 需要 Health 和只读向量 metadata，禁止读取 embedding；`search` 再需要 query Embedder/Retriever；`ask|chat` 再需要 PromptBuilder/LanguageModel；默认 `eval` 需要 Evaluator/Retriever，不加载 LLM；`eval --live` 才需要 LLM；`ingest` 才加载解析、分块、passage Embedder 与 Publisher。静态配置校验在昂贵资源之前，但运行中按命令延迟创建资源。一个命令不得因装配表包含某插件就无谓实例化所有插件。

退出码保持既有分类：`0` 表示请求完整成功（正常零命中、证据不足拒答仍可为 0）；`2` 是预期业务/配置错误及参数解析错误；`4` 是 embedding 或 LLM 服务错误；`3` 是已完成的入库批次含逐文档失败，或既有 `eval --live` 冒烟断言失败；`1` 是未预期内部错误。`ingest --all-configs` 任一已纳入目标的批次失败时整体为 3 且逐目标输出，若操作在批次建立前即发生配置/资源错误则按其错误分类。正式评估 `invalid|failed` 非零、`completed` 为 0。用户取消写入确认停止原命令并按预期未就绪错误返回 2，不报告成功。错误对象机器字段决定退出码，message 不参与判断。

## 5. 实现一致性门

实现须对配置无索引/索引不健康做完整交互测试，并核对旧 CLI 命令参数含义（除已删除选择器）和 D-03 纠偏，用状态机测试覆盖不可混淆事实。SDK 错误映射按第 2 节表执行；不得由适配器新增机器码或以 message 分支。恢复交互只调用存储契约规定的 Recovery，不另造提交判定。
