# 正式检索评估契约

状态：**架构已审核，通过规格级编码就绪复审**。回链[总架构](../architecture.md#4-运维恢复与正式评估)；标准证据、公式、聚合、报告和完成状态的唯一业务权威是[检索评估需求](../requirements/retrieval-evaluation.md)。本文只定义模型与服务依赖，不重写指标公式。

## 1. 独立评估主链

Evaluator 必接 one，串接 `EvaluationRepository → PreflightValidator → 同一个 Retriever → MetricCalculator → EvaluationReporter → EvaluationRunStore`，各 one。输入为审核通过的 PDF 独立 ground truth、与本次 IndexIdentity 对应的 test set 和查询配置。Repository 只读标注和构建映射；Preflight 先验证问题、证据组、可接受 chunk 组合、来源正文及目标索引身份；Retriever 只返回真实排序命中，不知道正确答案；Calculator 仅在预检通过后产生逐题指标；Reporter 从本次运行事实派生 JSON/HTML/summary/comparison；RunStore 原子发布并使同配置仅保留一个当前 V3 正式运行。默认路径不调用 LLM 或修改索引；`--live` 是独立冒烟，不计正式指标。

`--live` 由 CLI 调用已绑定 Chatbot，题目仅从已审核 ground truth Repository 只读取得并按文档/题目顺序执行；它不调用正式评估 Reporter/RunStore，不产生 `EvaluationRun`。答案与失败仅打印本次终端，按[评估需求第 1 节](../requirements/retrieval-evaluation.md#1-目标范围与非目标)判定命令成功或失败。

接口交接按下列方法族固定。每个方法的参数和返回类型对该接口的所有插件实现相同；`None` 是“预检通过”的守卫结果，不是未执行，也不生成一条伪数据边。Evaluator 是唯一编排 owner：它创建本次请求，按问题构造 `RetrievalRequest`，从同次 ground truth/test set 关联题目与映射，不能让 Retriever 接触答案或正确 chunk ID。Repository/Store 的物理路径从已验证数据集身份、run_id 和限定根推导，不接受任意路径注入。

| 接口方法 | 输入 → 输出 | 失败或不变量 |
| --- | --- | --- |
| `EvaluationRepository.load` | `GroundTruthIdentity, TestSetIdentity → LoadedEvaluationData` | 只读回读两类 manifest 与逐文档 JSON；缺失、hash/schema/身份不一致报预检错误，不补造文件 |
| `PreflightValidator.validate` | `EvaluationRunRequest, LoadedEvaluationData, Manifest, tuple[VectorRecord, ...] → None` | 按[评估需求第 7 节](../requirements/retrieval-evaluation.md#7-运行完整性与失败规则)检查审核、源、构建及可接受集合；失败为 invalid，绝不调用 Retriever |
| `Retriever.retrieve` | `RetrievalRequest → RetrievalResult` | 与 search/ask 共用同一已绑定实现及健康门；题目和 K 来自已验证请求 |
| `MetricCalculator.score_case` | `GroundTruthDocument, GroundTruthCase, TestCaseMapping, RetrievalResult → CaseEvaluationFact` | 只读可接受集合匹配，失败为 failed；所属文档身份用于不可回答题的跨文档诊断，不改变 ground truth 或命中 |
| `MetricCalculator.aggregate` | `run_id, tuple[EvaluationDocumentResult, ...] → EvaluationAggregate` | 只从已完成逐题事实及需求公式派生，不从 HTML 或展示数值计算 |
| `EvaluationReporter.render` | `EvaluationRunRequest, tuple[EvaluationDocumentResult, ...], EvaluationAggregate, tuple[ComparisonRunSnapshot, ...], BaselineSelection → RenderedEvaluation` | 跨版本历史仅含已完整回读的 completed；基线仅从显式审核登记读取；从机器事实生成运行文件与版本级 summary/comparison，不得重新检索或评分；缺任一文件为 failed |
| `EvaluationRunStore.list_completed` | `ground_truth: GroundTruthIdentity → tuple[EvaluationRun, ...]` | 只回读完整 `run.json`、aggregate 和文档文件且 status=completed 的运行；按 run_id 排序，非法 completed 不跳过而报错；不以目录时间选基线 |
| `EvaluationRunStore.list_comparison_runs` | `() → tuple[ComparisonRunSnapshot, ...]` | 只读 V2 历史与 V3 已完成报告，验证来源文件及哈希后转换为同一比较投影；V2 实现和索引绝不作为 V3 运行依赖；损坏的 completed 记录不能静默跳过 |
| `EvaluationRunStore.load_baselines` | `() → BaselineSelection` | 只读回读显式审核登记；文件不存在返回空 entries，非法或引用非 completed 运行则阻断汇总，不能猜最新 |
| `EvaluationRunStore.publish` | `EvaluationRunRequest, RenderedEvaluation → EvaluationRun` | 唯一 run_id 私有暂存、逐文件回读、原子发布后清理同配置旧 V3 运行才返回 completed；失败记录 invalid/failed，不替代旧 completed 结果 |
| `EvaluationRunStore.record_failure` | `EvaluationRunRequest, EvaluationError, tuple[EvaluationDocumentResult, ...] → EvaluationRun` | 预检失败记 invalid，其后失败记 failed；保留已产生的逐文档事实为诊断，不触碰三个版本级文件，也不进入 completed 列表 |

```python
@dataclass(frozen=True)
class EvaluationRunRequest:
    run_id: str
    configuration_name: str
    index_identity: IndexIdentity
    build_config: BuildProjection
    query_config: EvaluationQueryConfig
    ground_truth: GroundTruthIdentity
    test_set: TestSetIdentity
    evaluation_protocol_version: str
    started_at: str  # RFC3339

@dataclass(frozen=True)
class LoadedEvaluationData:
    ground_truth_manifest: GroundTruthManifest
    ground_truth_documents: tuple[GroundTruthDocument, ...]
    test_set_manifest: TestSetManifest
    test_set_documents: tuple[TestSetDocument, ...]

@dataclass(frozen=True)
class RenderedEvaluationFile:
    location: Literal["run", "system_version", "comparison_root"]
    relative_path: str
    media_type: Literal["application/json", "text/html", "text/markdown"]
    content: bytes
    sha256: str

@dataclass(frozen=True)
class RenderedEvaluation:
    run_id: str
    documents: tuple[EvaluationDocumentResult, ...]
    aggregate: EvaluationAggregate
    files: tuple[RenderedEvaluationFile, ...]

@dataclass(frozen=True)
class BaselineSelectionEntry:
    ground_truth_fingerprint: str
    evaluation_protocol_version: str
    query_config: EvaluationQueryConfig
    annotation_rule_version: str
    system_version: Literal["2.0", "3.0"]
    run_id: str
    reviewed_at: str  # RFC3339

@dataclass(frozen=True)
class BaselineSelection:
    schema_version: Literal["evaluation_baselines_v3"]
    entries: tuple[BaselineSelectionEntry, ...]
    updated_at: str | None  # RFC3339

@dataclass(frozen=True)
class ComparisonDocumentIdentity:
    document_id: str
    file_hash: str
    cases: tuple[tuple[str, str], ...]  # 按 case_id 排序的 (case_id, 原始问题)

@dataclass(frozen=True)
class ComparisonQueryConfig:
    top_k: int
    query_prefix: str
    document_filter: None
    question_normalization: Literal["strip_v1"]
    distance_order: Literal["ascending"]
    tie_break_order: Literal["chunk_id_ascending"]
    strict_top_k: Literal[True]

@dataclass(frozen=True)
class ComparisonRunSnapshot:
    system_version: Literal["2.0", "3.0"]
    run_id: str
    source_run_path: str
    configuration_label: str
    collection_name: str
    build_config_schema: Literal["legacy_build_config_v2", "build_projection_v3"]
    build_config_json: bytes
    build_config_fingerprint: str
    ground_truth: GroundTruthIdentity
    documents: tuple[ComparisonDocumentIdentity, ...]
    query_config: ComparisonQueryConfig
    evaluation_protocol_version: str
    annotation_rule_version: str
    document_summaries: tuple[AggregateScope, ...]
    overall_metrics: AggregateMetrics
    summary_path: str

ComparisonDifferenceCode = Literal[
    "dataset_version", "ground_truth_fingerprint", "pdf_hash", "question_text",
    "top_k", "query_prefix", "document_filter", "question_normalization",
    "distance_order", "tie_break_order", "strict_top_k",
    "evaluation_protocol_version", "annotation_rule_version",
]
ComparisonMetricName = Literal[
    "question_hit_rate_at_k", "macro_evidence_group_recall_at_k",
    "complete_coverage_rate_at_k", "macro_chunk_precision_at_k",
    "mean_reciprocal_rank_at_k", "macro_cross_document_contamination_at_k",
]

@dataclass(frozen=True)
class ComparisonRunReference:
    system_version: Literal["2.0", "3.0"]
    run_id: str
    configuration_label: str
    build_config_fingerprint: str
    summary_path: str

@dataclass(frozen=True)
class ComparisonMetricDelta:
    metric_name: ComparisonMetricName
    left_value: float | None
    right_value: float | None
    absolute_delta: float | None

@dataclass(frozen=True)
class ComparisonPair:
    left: ComparisonRunReference
    right: ComparisonRunReference
    status: Literal["strictly_comparable", "protocol_changed", "dataset_changed"]
    difference_codes: tuple[ComparisonDifferenceCode, ...]
    metric_deltas: tuple[ComparisonMetricDelta, ...]

@dataclass(frozen=True)
class EvaluationComparisonReport:
    schema_version: Literal["evaluation_comparison_v3"]
    generated_at: str  # RFC3339
    runs: tuple[ComparisonRunReference, ...]
    comparisons: tuple[ComparisonPair, ...]

# comparison.json 序列化 EvaluationComparisonReport；comparison.md 仅由它渲染。

@dataclass(frozen=True)
class EvaluationVersionRestoreEntry:
    location: Literal["system_version", "comparison_root"]
    relative_path: str
    old_existed: bool
    old_sha256: str | None
    old_snapshot_path: str | None
    expected_new_sha256: str

@dataclass(frozen=True)
class EvaluationPublicationJournal:
    schema_version: Literal["evaluation_publication_journal_v3"]
    run_id: str
    index_identity: IndexIdentity
    state: Literal["prepared", "publishing", "restoring", "recovery_failed"]
    current_step: Literal["none", "run_publish", "version_publish", "run_marker", "restore"]
    staging_path: str
    final_run_path: str
    version_files: tuple[EvaluationVersionRestoreEntry, ...]
    created_at: str  # RFC3339
    error: EvaluationError | None
```

以上交接模型只携带本次已有事实或派生文件，不另造业务判断。`run_id`、各身份和 started_at 非空；`build_config` 的规范哈希等于 `index_identity.build_fingerprint`。LoadedEvaluationData 两组文档分别与各自 manifest.files 按 document_key 一一对应、顺序一致；Preflight 成功后才视作可用于正式运行。RenderedEvaluation 的 run_id 与请求及每份 document/aggregate 相同，files 中 `run` 限于本次 run 根（`aggregate.json` 与每文档 JSON/HTML），`system_version` 只允许 `summary.md`，`comparison_root` 只允许 `comparison.json|comparison.md`；同一 location+path 唯一，SHA 为内容的完整小写摘要。这些派生文件恰覆盖[需求规定的必需视图](../requirements/retrieval-evaluation.md#9-报告与留存)；`run.json` 由 RunStore 在全部文件发布、回读成功时根据本次请求和产物引用生成，Reporter 不预称 completed。Reporter 的输入须按 run_id 唯一且均为已回读 completed；V3 同配置只呈现当前最新一次结果，未获人工明确指定前标示“正式基线未选定”，不得把最新结果自动当作正式基线。预检/执行失败由 Evaluator 与 RunStore 按已有 `EvaluationError` 记录 invalid/failed，未生成的指标保持空，不伪造 RenderedEvaluation。Journal 的 version_files 恰为三个固定版本级目标、路径唯一；old_existed=false 时两旧值均空，true 时两者均非空且快照回读匹配，所有 SHA 为 64 位小写十六进制。journal/staging/快照路径都必须受限于本次评估根；未完成 journal 不能被新运行覆盖。

`ComparisonRunSnapshot` 是只读比较投影，不是第三种运行模式。V2 来源固定为 `validation/retrieval/system-v2.0/runs/*/run.json`，V3 来源固定为 `validation/retrieval/system-v3.0/runs/*/run.json`，每个配置仅有一个当前 completed 结果；两者仅接受 `status=completed`，并回读对应 aggregate、每文档 JSON、文件 hash、问题 ID/原始问题/PDF hash。V2 的 `pipeline_id` 只转换为展示标签 `pipeline-v1|pipeline-v2`，不进入 V3 插件装配或路由；V3 标签为已保存 configuration_name。V2 `BuildConfig` 和 V3 `BuildProjection` 都保留各自规范 JSON 与完整 fingerprint 以展示差异，不强行合并字段。V2 `evaluation_run_v1`/`evaluation_aggregate_v1` 只在此只读适配，V3 按 `evaluation_run_v3`/`evaluation_aggregate_v3` 回读；任一缺失或校验失败阻断 comparison 发布。`source_run_path` 与 `summary_path` 均限定在对应系统版本的验证目录，文档身份按 document_id、case ID 排序且唯一，所有 hash 为完整小写 SHA-256；`build_config_json` 的规范摘要必须等于声明的 fingerprint。两个版本相同语义的十项 `AggregateMetrics` 逐字段转换为公共指标，不重算旧逐题分数。Reporter 比较时以 `(system_version,run_id)` 唯一识别运行；只要数据集版本/fingerprint、PDF hash 或原始问题集合不同，即 `dataset_changed`；否则 K、前缀、过滤、排序/同分、evaluation_protocol_version 或 annotation_rule_version 不同即 `protocol_changed`；全同才 `strictly_comparable` 并允许六项主指标的原始有限值相减。V2 中尚未审核为正式基线的运行仍可列为历史运行，不能被假定为 V3 的正式基线。

Reporter 在生成本次版本级视图时，用已通过预检的请求、`EvaluationDocumentResult` 和 aggregate 暂构本次 V3 比较投影，与 `list_comparison_runs` 的 V2 历史及其他 V3 配置当前结果一起计算；该投影在 `run.json(status=completed)` 发布前只存在于事务私有暂存，不得对外列为 completed。RunStore 回读全部文件后才使它成为该配置当前可发现的正式结果；提交后清理同配置旧 V3 运行，失败恢复保留旧 completed 结果并恢复旧版本级文件。`ComparisonQueryConfig` 是两个版本共同可读取的查询事实，不会把 V2 的构建配置或运行入口引入 V3。

`comparison.json` 恰序列化 `EvaluationComparisonReport`，不把旧 `evaluation_comparison_v1` 原地改写。`runs` 按 `(system_version,run_id)` 升序且唯一；`comparisons` 覆盖这些运行的所有无序二元组各一次，以同一键排序后前者为 left、后者为 right。`left/right` 只是稳定展示顺序，不冒称人工选定的正式基线。`difference_codes` 从上方封闭集合按声明顺序去重输出：数据集身份差异先列 dataset_version/ground_truth_fingerprint，再列 PDF hash、问题原文；其余逐字段列查询与评估协议差异。dataset 类差异优先使状态为 `dataset_changed`，否则有协议差异为 `protocol_changed`，否则 `strictly_comparable` 且 codes 必空。非严格可比时 `metric_deltas=()`；严格可比时六个主指标按 `ComparisonMetricName` 声明顺序各一条，只用两边 `AggregateMetrics` 未舍入值，若任一侧 `MetricValue.status=not_applicable` 则两值保留各自可得值而 `absolute_delta=None`，其余 `right_value-left_value` 为有限数。comparison.md 只从该 JSON 渲染，不读取聚合重新算差值。`summary_path` 限定对应版本目录；引用的每个 run 已完整回读且在 `runs` 中，不能以同名 run_id 混淆不同系统版本。

`BaselineSelection` 唯一存于 `validation/retrieval/baselines.json`，只由人工审核后显式登记或修改；评估运行本身不自动增添条目。空 entries 时 updated_at 为空，非空时非空。比较组键是 `(ground_truth_fingerprint,evaluation_protocol_version,query_config,annotation_rule_version)`，同一键最多一条；`(system_version,run_id)` 必须指向该组内已完成且完整回读的运行，reviewed_at 非空。索引构建配置可不同，故不进入比较组键，但被选 run 的构建差异仍在 summary 展示。无匹配登记时页面明确“正式基线未选定”，仍可以生成运行和成对可比性事实，但不得称任何一份为正式基线。登记文件非法、重复或悬空时停止版本级报告生成；不得静默回退为最新运行。基线登记只是人工审核选择，不改历史 run.json 或检索指标。

`ComparisonRunSnapshot.document_summaries` 来自同一已验证 aggregate 的逐 PDF scope，按 document_id 排序，与 `documents` 身份一一对应；这使 Reporter 能展示历史运行的逐 PDF 指标、不可回答和 unmappable 数。`overall_metrics` 取自该 aggregate 的 overall scope，不从展示文件重算。

## 2. 模型分层

`GroundTruthManifest` 持有数据集 schema、审核状态、PDF 身份和题目集合；题目记录语言、答案可得性、标准证据组与最小可接受片段组合，不持有实际检索排名。`TestSetManifest` 持有目标 IndexIdentity、构建指纹、当前 chunk 映射、正文及来源校验摘要和审核记录；旧 V2.0 test set 只能作为比对证据，不能因 chunk ID 字符相同直接作为 V3.0 当前集合。`EvaluationCaseFact` 保存问题身份、命中列表、各组覆盖与污染事实；`CaseMetrics` 与 `AggregateMetrics` 只从事实派生；`EvaluationRun` 保存唯一 run_id、完整配置和数据集身份、开始/结束时刻、状态、失败阶段/原因、逐题事实及产物引用。

### 2.1 标准证据、实际命中与逐题指标

为防止统一模型丢失现有指标与审核证据，V3.0 使用以下完整字段级结构；旧 `pipeline_id` 替换为 `configuration_name` 与 `index_identity`，而不再参与运行路由。

评估的模型按“标准证据 → 映射 → 实际命中 → 逐题事实 → 指标”阅读；不能让标准答案进入 Retriever。

```python
@dataclass(frozen=True)
class EvidenceExcerpt:
    excerpt_id: str
    document_id: str
    document_name: str
    relative_path: str
    page_numbers: tuple[int, ...]
    text: str

@dataclass(frozen=True)
class EvidenceGroup:
    evidence_group_id: str
    excerpts: tuple[EvidenceExcerpt, ...]

@dataclass(frozen=True)
class GroundTruthCase:
    case_id: str
    question: str
    reference_answer: str
    answerable: bool
    evidence_groups: tuple[EvidenceGroup, ...]

@dataclass(frozen=True)
class ExcerptChunkMapping:
    excerpt_id: str
    status: Literal["mapped", "unmappable"]
    chunk_sets: tuple[tuple[str, ...], ...]

@dataclass(frozen=True)
class EvidenceGroupMapping:
    evidence_group_id: str
    status: Literal["mapped", "unmappable"]
    acceptable_chunk_sets: tuple[tuple[str, ...], ...]
    excerpt_mappings: tuple[ExcerptChunkMapping, ...]

@dataclass(frozen=True)
class MetricValue:
    status: Literal["evaluated", "not_applicable"]
    numerator: float | None
    denominator: int | None
    value: float | None
```

证据页码升序唯一且非空、文本非空；group 至少一条 excerpt；answerable 当且仅当证据组非空。每组 `excerpt_mappings` 按 excerpt_id 升序唯一，必须与 ground truth 的 excerpt ID 一一对应；单条映射 `mapped` 当且仅当 `chunk_sets` 非空。组级 `mapped` 当且仅当 `acceptable_chunk_sets` 非空。两类组合的内层 ID 均升序唯一、外层组合唯一且仅保留最小组合。Preflight 对每条 excerpt 映射的所有 chunk 回读目标索引身份、正文非空、文档与该 excerpt 一致且来源页相交；组级组合仍按整组完整事实单独人工审核，不能由 excerpt 映射并集自动推断。`excerpt_mappings` 是审核事实，不参与正式指标。MetricValue 不适用时三数全空，已计算时三数均有限、分母非负；分母零的结果只由[评估需求](../requirements/retrieval-evaluation.md)决定。

```python
@dataclass(frozen=True)
class QuestionMetrics:
    returned_count: int
    relevant_chunk_count: int
    required_group_count: int
    covered_group_count: int
    cross_document_count: int
    first_relevant_rank: int | None
    chunk_precision_at_k: MetricValue
    evidence_group_recall_at_k: MetricValue
    question_hit_at_k: MetricValue
    complete_coverage_at_k: MetricValue
    reciprocal_rank_at_k: MetricValue
    evidence_group_reciprocal_rank_at_k: MetricValue
    cross_document_contamination_at_k: MetricValue

@dataclass(frozen=True)
class RetrievedChunkSnapshot:
    rank: int
    chunk_id: str
    document_id: str
    document_name: str
    relative_path: str
    page_numbers: tuple[int, ...]
    chunk_index: int
    chunk_kind: Literal["text", "list", "table"]
    text: str
    distance: float
    similarity: float
    matched_evidence_group_ids: tuple[str, ...]
    relevant: bool
    cross_document: bool

@dataclass(frozen=True)
class CaseEvaluationFact:
    case_id: str
    status: Literal["completed", "failed", "not_run"]
    question: str
    answerable: bool
    reference_answer: str
    expected_evidence: tuple[ExpectedEvidenceSnapshot, ...]
    retrieved_chunks: tuple[RetrievedChunkSnapshot, ...]
    unmappable_evidence_group_ids: tuple[str, ...]
    metrics: QuestionMetrics | None
    error: EvaluationError | None

@dataclass(frozen=True)
class AggregateMetrics:
    included_question_count: int
    returned_chunk_count: int
    relevant_chunk_count: int
    required_group_count: int
    covered_group_count: int
    cross_document_count: int
    macro_chunk_precision_at_k: MetricValue
    micro_chunk_precision_at_k: MetricValue
    macro_evidence_group_recall_at_k: MetricValue
    micro_evidence_group_recall_at_k: MetricValue
    question_hit_rate_at_k: MetricValue
    complete_coverage_rate_at_k: MetricValue
    mean_reciprocal_rank_at_k: MetricValue
    evidence_group_mean_reciprocal_rank_at_k: MetricValue
    macro_cross_document_contamination_at_k: MetricValue
    micro_cross_document_contamination_at_k: MetricValue
```

排名一基，distance 有限、similarity 从实际 distance 派生；来源从实际 hit 复制。completed case 才有 metrics 且无 error，failed 有 error，not_run 不伪造零指标。AggregateMetrics 的六个数量非负、十个指标只从逐题事实派生，具体公式与不可计算条件见[评估需求](../requirements/retrieval-evaluation.md)。

### 2.2 最终运行

评估运行是 RunStore 交付的最终结果，不与逐题事实或派生 HTML 混用：

```python
@dataclass(frozen=True)
class EvaluationRun:
    run_id: str
    status: Literal["invalid", "failed", "completed"]
    configuration_name: str
    index_identity: IndexIdentity
    build_config: BuildProjection
    build_config_fingerprint: str
    query_config: EvaluationQueryConfig
    evaluation_protocol_version: str
    ground_truth: GroundTruthIdentity
    test_set: TestSetIdentity
    started_at: str  # RFC3339
    completed_at: str | None  # RFC3339
    documents: tuple[RunDocumentEntry, ...]
    aggregate_path: str | None
    aggregate_sha256: str | None
    error: EvaluationError | None
    schema_version: Literal["evaluation_run_v3"]
```

build_config 与索引 manifest/test set 的规范快照逐字节相同，fingerprint 重算匹配。只有 completed 要求全部文档产物、聚合与完成时间可回读且 error 为空；invalid/failed 必有 error，不进入正式基线。

`BuildProjection` 的字段与规范字节唯一见[装配契约第 8 节](assembly-contracts.md#8-构建身份算法)，不得在评估中另造一套投影。每份文件记录 document_key、相对 JSON 路径、内容 SHA-256 与 case_count；文件路径不得越过数据集根。`EvaluationQueryConfig` 的字段全集见本节代码块，正式评估固定无文档过滤；查询侧参数不进入构建指纹。

### 2.3 数据集清单与报告文件

其余数据集和报告对象依照创建顺序列出，不能用 `dict[str, Any]` 掩盖审核状态：

```python
@dataclass(frozen=True)
class GroundTruthFileEntry:
    document_key: str
    json_path: str
    json_sha256: str
    case_count: int
    document_id: str
    file_hash: str

@dataclass(frozen=True)
class GroundTruthDocument:
    schema_version: Literal["ground_truth_document_v1"]
    document_key: str
    document_id: str
    document_name: str
    relative_path: str
    file_hash: str
    cases: tuple[GroundTruthCase, ...]

@dataclass(frozen=True)
class TestSetFileEntry:
    document_key: str
    json_path: str
    json_sha256: str
    case_count: int
    document_id: str
    file_hash: str
    chunk_count: int

@dataclass(frozen=True)
class TestSetDocument:
    schema_version: Literal["test_set_document_v3"]
    document_key: str
    document_id: str
    document_name: str
    relative_path: str
    file_hash: str
    cases: tuple[TestCaseMapping, ...]

@dataclass(frozen=True)
class TestCaseMapping:
    case_id: str
    groups: tuple[EvidenceGroupMapping, ...]
```

两类文件路径分别限于 ground-truth/test-set 根内，条目 hash、身份、case 数和实际文件回读一致；document_key 全局唯一，计数非负。GroundTruthDocument 身份与 PDF 一致、case ID 唯一。TestSetDocument 的题 ID 与对应 ground truth 一一映射，同题 group ID 集合完全相同，不复制问题和参考答案；PDF hash、chunk 数与目标索引一致。

现存已审核 `ground_truth_manifest_v1` 的磁盘条目仅含 `document_key/json_path/content_sha256/case_count`；V3 Repository 必须先以该 SHA 回读对应 `GroundTruthDocument`，再从已验证文档补出公共 `GroundTruthFileEntry` 的 `document_id/file_hash`，且不得改写旧清单或重算为新的数据集版本。新写入的 V3 ground truth 清单使用上文完整字段；两种读取形式形成同一完整内存模型，并按各自原始清单声明的 fingerprint 规则核验文档集合。此兼容只作用于独立标注数据，不引入 V2 运行路径。

`test_set_fingerprint` 对规范 JSON 对象 `{ground_truth_fingerprint,annotation_rule_version,build_config_fingerprint,documents}` 计算完整 SHA-256，其中 `documents` 是按 manifest.files 顺序回读的完整 `TestSetDocument` 规范对象数组；键按 UTF-8 升序、无空格、保留 Unicode、拒绝非有限数。V3 新 test set 和 Repository 回读均使用此算法。它不包含展示目录名、审核时间或运行 K。

```python
@dataclass(frozen=True)
class ExpectedEvidenceSnapshot:
    evidence_group_id: str
    mapping_status: Literal["mapped", "unmappable"]
    excerpts: tuple[EvidenceExcerpt, ...]
    acceptable_chunk_sets: tuple[tuple[str, ...], ...]
    excerpt_mappings: tuple[ExcerptChunkMapping, ...]

@dataclass(frozen=True)
class EvaluationDocumentResult:
    schema_version: Literal["evaluation_document_result_v3"]
    run_id: str
    document_key: str
    document_id: str
    document_name: str
    relative_path: str
    file_hash: str
    cases: tuple[CaseEvaluationFact, ...]

@dataclass(frozen=True)
class AggregateScope:
    scope: Literal["document", "overall"]
    document_id: str | None
    answerable_count: int
    unanswerable_count: int
    unmappable_group_count: int
    metrics: AggregateMetrics

@dataclass(frozen=True)
class EvaluationAggregate:
    schema_version: Literal["evaluation_aggregate_v3"]
    run_id: str
    documents: tuple[AggregateScope, ...]
    overall: AggregateScope

@dataclass(frozen=True)
class EvaluationQueryConfig:
    top_k: int
    query_prefix: Literal["query: "]
    document_filter: None
    question_normalization: Literal["strip_v1"]
    distance_order: Literal["ascending"]
    tie_break_order: Literal["chunk_id_ascending"]
    strict_top_k: Literal[True]

@dataclass(frozen=True)
class GroundTruthIdentity:
    dataset_version: str
    ground_truth_fingerprint: str

@dataclass(frozen=True)
class TestSetIdentity:
    test_set_version: str
    test_set_fingerprint: str
    annotation_rule_version: str

@dataclass(frozen=True)
class RunDocumentEntry:
    document_key: str
    json_path: str
    json_sha256: str
    html_path: str
    html_sha256: str
```

ExpectedEvidenceSnapshot 只用于审核结果，不反向修改 ground truth；document Scope 必有 document_id，overall 无 ID；三个计数非负。正式评估 top_k 正、无文档过滤；身份与各产物 hash 均为完整小写 SHA-256。RunDocumentEntry 路径限于本次 run 根，hash 回读一致。

评估输入的两个审核清单分别承载“业务标准答案”和“目标构建下的 chunk 映射”，不能混用：

```python
@dataclass(frozen=True)
class GroundTruthManifest:
    schema_version: Literal["ground_truth_manifest_v1"]
    dataset_version: str
    ground_truth_fingerprint: str
    review_status: Literal["draft", "approved", "superseded"]
    files: tuple[GroundTruthFileEntry, ...]
    created_at: str  # RFC3339
    reviewed_at: str | None  # RFC3339
    superseded_by: str | None

@dataclass(frozen=True)
class TestSetManifest:
    schema_version: Literal["test_set_manifest_v3"]
    test_set_version: str
    test_set_fingerprint: str
    review_status: Literal["draft", "approved", "superseded"]
    system_version: Literal["3.0"]
    configuration_name: str
    index_identity: IndexIdentity
    build_config: BuildProjection
    build_config_fingerprint: str
    ground_truth_version: str
    ground_truth_fingerprint: str
    annotation_rule_version: str
    files: tuple[TestSetFileEntry, ...]
    created_at: str  # RFC3339
    reviewed_at: str | None  # RFC3339
    superseded_by: str | None
```

draft 的 reviewed_at/superseded_by 均空，approved 只有 reviewed_at，superseded 两者均有。TestSet 的 build_config 与索引 manifest 快照一致，fingerprint 重算匹配；旧 V2.0 test set 不能因 chunk ID 相同直接作为 V3.0 当前集合。

### 2.4 失败状态

```python
@dataclass(frozen=True)
class EvaluationError:
    stage: Literal["preflight", "retrieval", "metric_calculation", "json_rendering", "html_rendering", "aggregation", "publication"]
    code: EvaluationErrorCode
    scope: Literal["run", "document", "case"]
    document_id: str | None
    case_id: str | None
    message: str
```

`EvaluationErrorCode` 封闭为 `ground_truth_incomplete|test_set_incomplete|dataset_identity_mismatch|index_identity_mismatch|mapping_invalid|source_mismatch|retrieval_failed|metric_failed|json_render_failed|html_render_failed|aggregate_failed|publication_failed`。document/case 作用域需对应 ID，run 作用域两 ID 均空；message 不参与分支，SDK 异常类型仅作诊断。

运行状态封闭为 `invalid|failed|completed`：预检完整性失败为 invalid；预检通过后执行、计算或产物失败为 failed；全部必需输出生成且回读成功后才是 completed。运行 ID 唯一；新 completed 结果完整发布后清理同配置旧 V3 运行，failed/invalid 不进入正式基线且不替代旧 completed 结果。可回答题、不可回答题、空相关组、无命中、未计算指标的空值按需求分别表达，不能用 0 代替不可计算。指标公式、阈值、宏微汇总和排名同分规则完全引用评估需求。

## 3. 报告发布与比较

Reporter 生成每 PDF JSON 与 HTML、整体 summary、可比较的配置结果；HTML 只从机器事实派生，不能成为评分输入。RunStore 在本次评估根持排他锁，先暂存并回读本次运行文件及三个版本级文件，同时保存三个版本级目标的旧字节或“不存在”证据；再写入含 run_id、三个目标路径/旧新 SHA、当前步骤和本次 staging/最终路径的事务 journal。运行目录只以“目标不存在才创建”的原子改名发布，随后逐个原子替换版本级文件，全部回读成功才原子写入 `run.json(status="completed")` 作为本次正式生效标记，最后清理 journal。任一步骤失败或进程中断后，下一次评估/summary 读取必须先按 journal 校验：若 completed marker 与全部新文件可证明一致，则收尾；否则恢复三个旧版本级文件（原先不存在则删除本事务创建的精确目标），把唯一 run 目录保留为 failed 诊断或隔离，不能让半份 run 进入正式列表。恢复失败保留 journal 并阻断正式汇总，不猜 committed。单个文件的原子替换不被误说成跨四个目录的原子事务。比较只允许按需求验证可比数据集、构建身份及审核状态后进行，不因两个配置显示名称不同就自动视为可比较。评估路径与文件名的精确格式遵守评估需求，不改写旧报告。

目录按需求固定：测试集位于 `eval/test-sets/system-v3.0__assembly-<NAME>__cfg-<fingerprint-short>/`，正式报告位于 `validation/retrieval/system-v3.0/runs/assembly-<NAME>__cfg-<fingerprint-short>__k-<K>__<run-id>/`，其下必有 `run.json,aggregate.json,documents/<document-key>.json,documents/<document-key>.html`；比较与汇总位于需求指定同级位置。目录名只用短 fingerprint 便于阅读，进入运行前必须回读完整 fingerprint 与配置快照；若短名冲突则拒绝并采用更长前缀，不覆盖已存在目录。`document-key` 使用文档文件 stem，经不安全字符替换为 `-`、连续 `-` 合并、去首尾点/空格/连字符/下划线、最多 56 字符，再拼 `--<document_id>`；若 stem 清空用 `document`。此规则只决定人类可读文件名，不代替 document_id。

run_id 采用 UTC 时间戳加不可预测随机后缀，创建目录时以不存在才创建的原子操作保证唯一；同参数重跑也使用新 run_id。运行暂存失败保留可诊断的 `invalid|failed` 记录而不替代旧 completed 结果；发布 completed 仅在所有必需文件 hash 与引用回读通过后写入，并在同一发布锁内按配置名清理旧 V3 运行。清理失败保留发布 journal，恢复幂等重试。summary/comparison 只引用每个 V3 配置当前 completed 结果及只读 V2 历史，不扫描目录时间选择正式基线。若旧 V3 运行已被人工登记为正式基线，先撤销该登记再替换，不把未审核的新运行自动选为基线。

## 4. 实现一致性门

字段、空值、状态与机器错误码以上文及[评估需求](../requirements/retrieval-evaluation.md)为权威；指标公式仅在需求中定义，不在架构复制。实现测试必须验证评估真实调用同一个 Retriever、run_id 不冲突、报告部分失败不留下 completed，并与现有已审核标准证据逐题核对；这些是组件完成条件，不要求编码者补充规则。
