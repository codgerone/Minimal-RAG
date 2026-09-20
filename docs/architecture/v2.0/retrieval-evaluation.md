# V2.0 检索效果评估架构契约

本文是[当前架构第 10 节](../../architecture.md#10-检索健康检查和-cli)的技术子契约，实现[检索效果评估需求](../../requirements/v2.0/retrieval-evaluation.md)。需求文档是状态含义、指标公式、可比性和报告行为的唯一业务权威；本文只定义模型、接口、持久化和模块协作。

## 1. 边界与数据流

评估复用已发布索引、`PipelineRuntime`、`PipelineManifest`、`BuildConfig`、`Retriever` 和 `RetrievalHit`，不重新解析 PDF、不写 Chroma、不修改 pipeline manifest，也不调用 LLM。

```text
ground truth JSON ─┐
                   ├─> EvaluationRepository ─> EvaluationRunner
test set JSON ─────┘            │                    │
                                │                    ├─> Retriever
pipeline manifest/index ────────┘                    ├─> MetricsCalculator
                                                     └─> staged run JSON
                                                               │
                                                               v
                                                        ReportRenderer
                                                               │
                                                               v
                                                immutable run directory
                                                               │
                                      completed runs ─> Summary/Comparison builder
```

权威关系：

- PDF 和 ground truth 是语义事实来源；
- test set 是 ground truth 到一个构建配置 chunks 的人工映射；
- collection 是实际检索来源，pipeline manifest 是构建身份来源；
- 每 PDF 运行 JSON 是实际结果与指标的权威运行事实；
- HTML、`summary.md` 和 `comparison.md` 只从机器 JSON 生成，不被运行逻辑读取；
- `comparison.json` 是跨运行可比性和差值的机器事实。

旧 `EvaluationCase`、`EvaluationResult` 和页面相交判定只用于旧验证记录，不得适配或混入正式评估模型。

| 模型/产物 | 创建者 | 主要消费者 | 持久化时机 | 失败影响 |
| --- | --- | --- | --- | --- |
| GroundTruthManifest/Document | 人工标注工具与审核流程 | repository、test-set 标注、runner | 审核输入形成时 | preflight invalid，不执行检索 |
| TestSetManifest/Document | 人工映射工具与审核流程 | repository、runner | 指定构建映射形成时 | preflight invalid，不执行检索 |
| EvaluationRunRecord | runner | 报告索引、人工追溯 | 每次运行最终发布 | 状态记录 invalid/failed/completed |
| EvaluationDocumentResult | runner | calculator、HTML renderer | 每 PDF 完成或失败时 | 失败事实保留，不进入正式聚合 |
| EvaluationAggregate | calculator | summary、comparison | completed run 发布前 | 缺失则运行不能 completed |
| ComparisonIndex | comparison builder | comparison.md、跨版本审核 | completed run 发布后重建 | 不破坏已发布 run，可重建 |

## 2. 公共类型、序列化与标识

```python
DatasetReviewStatus = Literal["draft", "approved", "superseded"]
EvidenceMappingStatus = Literal["mapped", "unmappable"]
EvaluationRunStatus = Literal["invalid", "failed", "completed"]
ComparisonStatus = Literal[
    "strictly_comparable", "protocol_changed", "dataset_changed"
]

EvaluationFailureStage = Literal[
    "preflight", "retrieval", "metric_calculation",
    "json_rendering", "html_rendering", "aggregation", "publication",
]

CaseExecutionStatus = Literal["completed", "failed", "not_run"]
MetricStatus = Literal["evaluated", "not_applicable"]
```

四个业务状态枚举的触发和下游行为以需求第 3.4 节为准。`EvaluationFailureStage` 和 `CaseExecutionStatus` 是技术诊断状态：第一个失败问题为 `failed`，其后尚未执行的问题为 `not_run`；不可回答问题正常执行后仍为 `completed`，只是正式指标为 `not_applicable`。

所有 JSON 使用 UTF-8、无 BOM、两个空格缩进和末尾换行。字段按模型声明顺序输出；计算 fingerprint 或文件 hash 时使用 UTF-8 canonical JSON：对象 key 字典序、数组保持模型规定顺序、无额外空白。读取时拒绝未知 schema、缺字段、额外字段、错误类型、重复 ID 和不满足不变量的数据。

时间均为带 `Z` 的 UTC RFC 3339。SHA-256 使用 64 位小写十六进制。路径在 JSON 中均为相对项目根的 POSIX 路径，禁止绝对路径和 `..`。

`document_key` 由 `Path(relative_path).stem` 进行文件系统安全化后，加 `--` 和 `document_id[:16]` 组成；安全化规则与 artifact 可读目录名复用同一函数。`run_id` 为 `YYYYMMDDTHHMMSSffffffZ-<uuid4前8位>`，时间按运行开始时生成。短 fingerprint 固定为完整 fingerprint 前 12 位，只用于目录名。

## 3. Ground truth 契约

### 3.1 模型

```python
class GroundTruthFileEntry:
    document_key: str
    json_path: str
    content_sha256: str
    case_count: int

class GroundTruthManifest:
    schema_version: Literal["ground_truth_manifest_v1"]
    dataset_version: str
    ground_truth_fingerprint: str
    review_status: DatasetReviewStatus
    files: tuple[GroundTruthFileEntry, ...]
    created_at: str
    reviewed_at: str | None
    superseded_by: str | None

class EvidenceExcerpt:
    excerpt_id: str
    document_id: str
    document_name: str
    relative_path: str
    page_numbers: tuple[int, ...]
    text: str

class EvidenceGroup:
    evidence_group_id: str
    excerpts: tuple[EvidenceExcerpt, ...]

class GroundTruthCase:
    case_id: str
    question: str
    answerable: bool
    reference_answer: str
    evidence_groups: tuple[EvidenceGroup, ...]

class GroundTruthDocument:
    schema_version: Literal["ground_truth_document_v1"]
    document_key: str
    document_id: str
    document_name: str
    relative_path: str
    file_hash: str
    cases: tuple[GroundTruthCase, ...]

class GroundTruthDataset:
    manifest: GroundTruthManifest
    documents: tuple[GroundTruthDocument, ...]
```

### 3.2 不变量与 fingerprint

- `dataset_version` 非空，由人工在语义内容变化时提升；实现不解析其版本大小。
- `files` 按 `json_path.casefold(), json_path` 排序，路径和 document_key 唯一；entry 的 hash 和 case_count 必须与文件实值一致。json_path 必须位于 `eval/ground-truth/`。
- `ground_truth_fingerprint` 对按 `files` 顺序读取的完整 `GroundTruthDocument` canonical JSON 数组计算 SHA-256；manifest 的状态和时间不参与。
- `review_status=approved` 时 `reviewed_at` 非空且 `superseded_by=null`；`draft` 时 `reviewed_at=null`、`superseded_by=null`；`superseded` 时二者均非空。
- `case_id` 在整个数据集全局唯一且非空；问题和参考答案去除首尾空白后非空。
- 每份文件内的 case 按原始人工顺序保存；group 使用 `e1`、`e2` 连续编号，excerpt 使用组内 `x1`、`x2` 连续编号。
- `answerable=true` 时 evidence_groups 非空；`false` 时必须为空。
- 每个 evidence group 至少有一个 excerpt；group ID 和 excerpt ID 在各自作用域唯一。
- excerpt 的 document_id、relative_path 和 file hash 所属文档必须存在于本数据集；页码升序去重且均大于 0；text 只允许需求规定的换行和首尾空白规范化。

Ground-truth 内容和 manifest 由人工标注命令通过共享 model/codec 写出并经用户审核；运行时 repository 只读和验证。批准或替代只创建新 manifest 内容，不修改已被运行引用的运行快照；旧版本由 Git 历史和运行 JSON 快照共同追溯。

GroundTruthDataset 是只存在内存的已验证组合，documents 顺序必须与 manifest.files 一致，不单独序列化。

## 4. Test set 契约

### 4.1 模型

```python
class TestSetFileEntry:
    document_key: str
    json_path: str
    content_sha256: str
    case_count: int

class TestSetManifest:
    schema_version: Literal["test_set_manifest_v1"]
    test_set_version: str
    test_set_fingerprint: str
    review_status: DatasetReviewStatus
    system_version: Literal["2.0"]
    pipeline_id: PipelineId
    collection_name: str
    ground_truth_version: str
    ground_truth_fingerprint: str
    annotation_rule_version: Literal["evidence_chunk_mapping_v2"]
    build_config: BuildConfig
    build_config_fingerprint: str
    files: tuple[TestSetFileEntry, ...]
    created_at: str
    reviewed_at: str | None
    superseded_by: str | None

class EvidenceGroupMapping:
    evidence_group_id: str
    status: EvidenceMappingStatus
    acceptable_chunk_sets: tuple[tuple[str, ...], ...]

class TestCaseMapping:
    case_id: str
    evidence_groups: tuple[EvidenceGroupMapping, ...]

class TestSetDocument:
    schema_version: Literal["test_set_document_v1"]
    document_key: str
    document_id: str
    document_name: str
    relative_path: str
    file_hash: str
    cases: tuple[TestCaseMapping, ...]

class TestSetDataset:
    manifest: TestSetManifest
    documents: tuple[TestSetDocument, ...]
```

### 4.2 不变量

- `files` 的 json_path 必须位于该 test-set 目录，并按 `json_path.casefold(), json_path` 排序。
- `test_set_fingerprint` 对 `ground_truth_fingerprint`、annotation_rule_version、build_config_fingerprint 和按 manifest `files` 顺序读取的完整 `TestSetDocument` canonical JSON 数组组成的对象计算 SHA-256；review 状态、版本标签和时间不参与。
- BuildConfig 的类型、canonical JSON 和 fingerprint 复用[运行与持久化模型契约](runtime-persistence-models.md#1-构建配置)，不复制字段或另算 hash。
- system、pipeline、collection、BuildConfig 和 fingerprint 必须与目标 pipeline manifest 完全一致。
- ground truth version/fingerprint 必须与所映射的 approved ground-truth manifest 一致。
- 每份 TestSetDocument 的文档身份、case 集合及顺序与对应 GroundTruthDocument 完全一致；每题 group 集合及顺序完全一致。
- `mapped` 时 `acceptable_chunk_sets` 非空；`unmappable` 时必须为空。每个内层集合表示共同完整覆盖该组的最小 chunk 集合，成员是 AND；多个内层集合互为 OR。
- 每个内层集合非空，ID 按字符串升序且唯一；外层按内层 tuple 的字典序升序且组合唯一。若一个组合是另一个组合的真子集，较大组合不是最小集合，validator 必须拒绝。
- 每个可接受 ID 在目标 collection 中恰好存在一次，其 document_id 必须属于该 group 的 ground-truth excerpt 文档集合。
- review 状态与时间、superseded_by 的不变量与 GroundTruthManifest 相同。
- 同一 ground-truth fingerprint、pipeline 和 build fingerprint 最多存在一个 approved test set；否则选择歧义，正式运行 preflight 为 invalid。

test set 标注命令只导出候选 chunks 和空的 draft 映射，不根据相似度或检索结果自动填充 acceptable IDs。人工映射完成后，同一命令通过共享 validator 执行全量校验并生成 manifest/fingerprint；运行时 repository 仍保持只读。

TestSetDataset 是只存在内存的已验证组合，documents 顺序必须与 manifest.files 一致，不单独序列化。

## 5. 运行配置与 run.json

```python
class EvaluationQueryConfig:
    top_k: int
    query_prefix: str
    document_filter: None
    question_normalization: Literal["strip_v1"]
    primary_order: Literal["distance_ascending"]
    tie_break_order: Literal["chunk_id_ascending"]
    strict_top_k: Literal[True]

class GroundTruthIdentity:
    dataset_version: str
    ground_truth_fingerprint: str

class TestSetIdentity:
    test_set_version: str
    test_set_fingerprint: str
    annotation_rule_version: str

class RunDocumentEntry:
    document_key: str
    json_path: str
    html_path: str | None
    json_sha256: str
    html_sha256: str | None

class EvaluationError:
    stage: EvaluationFailureStage
    case_id: str | None
    message: str

class EvaluationRunRecord:
    schema_version: Literal["evaluation_run_v1"]
    run_id: str
    system_version: Literal["2.0"]
    pipeline_id: PipelineId
    collection_name: str
    status: EvaluationRunStatus
    evaluation_protocol_version: Literal["retrieval_evaluation_v2"]
    ground_truth: GroundTruthIdentity | None
    test_set: TestSetIdentity | None
    build_config: BuildConfig
    build_config_fingerprint: str
    query_config: EvaluationQueryConfig
    started_at: str
    finalized_at: str
    error: EvaluationError | None
    documents: tuple[RunDocumentEntry, ...]
    aggregate_path: str | None
    aggregate_sha256: str | None
```

`top_k>0`；query_prefix 必须等于 BuildConfig tokenizer 的 query_prefix；正式评估禁止 document filter，因此固定为 null。question_normalization 只去除查询首尾空白，不改写文本。

`invalid` 的 error.stage 必须为 preflight、documents 为空、aggregate_path/sha256 为空；ground_truth 或 test_set 在加载前失败时相应 identity 允许为空。`failed` 的 stage 不得为 preflight，两个 identity 必须非空，允许 documents 保存已产生事实但 aggregate_path/sha256 为空；`completed` 时两个 identity 非空、error 为空，五份 document JSON/HTML 均存在且 aggregate_path/sha256 非空。html_path 与 html_sha256 必须同时为空或同时存在。所有路径必须位于本 run 目录，hash 与文件内容一致。

run 记录完整 BuildConfig 是审计快照；其 fingerprint 必须与该快照复算值、test set 和 pipeline manifest 三方一致。运行 JSON 不保存 API Key、绝对路径、embedding 浮点向量、LLM 或 prompt。

## 6. 每 PDF 原始评估事实

```python
class ExpectedEvidenceSnapshot:
    evidence_group_id: str
    mapping_status: EvidenceMappingStatus
    excerpts: tuple[EvidenceExcerpt, ...]
    acceptable_chunk_sets: tuple[tuple[str, ...], ...]

class RetrievedChunkSnapshot:
    rank: int
    chunk_id: str
    document_id: str
    document_name: str
    relative_path: str
    page_numbers: tuple[int, ...]
    chunk_index: int
    chunk_kind: Literal["text", "list", "table"] | None
    text: str
    distance: float
    similarity: float
    matched_evidence_group_ids: tuple[str, ...]
    relevant: bool
    cross_document: bool

class MetricValue:
    status: MetricStatus
    numerator: float | None
    denominator: int | None
    value: float | None

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

class CaseEvaluationFact:
    case_id: str
    status: CaseExecutionStatus
    question: str
    answerable: bool
    reference_answer: str
    expected_evidence: tuple[ExpectedEvidenceSnapshot, ...]
    retrieved_chunks: tuple[RetrievedChunkSnapshot, ...]
    unmappable_evidence_group_ids: tuple[str, ...]
    metrics: QuestionMetrics | None
    error: EvaluationError | None

class EvaluationDocumentResult:
    schema_version: Literal["evaluation_document_result_v1"]
    run_id: str
    document_key: str
    document_id: str
    document_name: str
    relative_path: str
    file_hash: str
    cases: tuple[CaseEvaluationFact, ...]
```

不变量：

- cases 与 ground truth 文件的 case 集合及顺序相同；失败点之后的 case 仍写出为 not_run，保证问题不静默丢失。
- expected evidence 的 mapping_status 和 acceptable chunk sets 必须与 test set 完全一致并保持规范顺序；运行用该快照完成匹配，使历史 JSON 可独立复算。
- completed case 的 error 为空；failed 必须有匹配 case_id 的 error；not_run 的 error 为空、hits 为空、metrics 为空。
- answerable=false 且 completed 时 metrics 为空；answerable=true 且 completed 时 metrics 完整。
- retrieved_chunks 的 rank 从 1 连续到实际返回数，最多 K 条，按距离和 chunk ID 稳定排序；chunk ID 唯一。
- V1 chunk_kind 为空且 page_numbers 由 page_number 转为单元素；V2 chunk_kind 非空，page_numbers 原样升序去重，允许为空。
- similarity 精确使用 `1.0-distance`；不得从显示值反算。distance 和 similarity 必须为有限数。
- matched group IDs 按 ground-truth group 顺序、唯一，表示该 hit 出现在这些组的至少一个可接受组合中；relevant 等价于该集合非空，但单个 hit 参与某组不等于该组已完整覆盖。
- 对每组，calculator 在返回 ID 集合包含某个完整 `acceptable_chunk_set` 时才判该组 covered。组合完成排名取其成员最大 rank，同组多个已完成组合取最小完成排名；EGRR 使用该排名。`unmappable` 没有可完成组合，始终未覆盖并继续进入组指标分母。
- answerable 题的目标文档集合取全部 excerpt 的 document_id；不可回答题取其所属 GroundTruthDocument.document_id。cross_document 表示 hit.document_id 不在该集合中，与 relevant 独立。
- MetricValue 为 evaluated 时三项均按指标定义存在；not_applicable 时三项均为空。Chunk Precision 或污染率在零返回时 denominator=0、value=0，仍是 evaluated。
- 所有浮点指标从整数计数和未舍入排名计算；保存 IEEE 754 双精度值，展示时才四舍五入。

运行 JSON 中的 ground-truth excerpt 是不可变快照，用于让历史报告脱离后续数据集变更仍可审核；它不是新的 ground-truth 权威。

## 7. 聚合与比较模型

```python
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

class AggregateScope:
    scope: Literal["document", "overall"]
    document_id: str | None
    answerable_count: int
    unanswerable_count: int
    unmappable_group_count: int
    metrics: AggregateMetrics

class EvaluationAggregate:
    schema_version: Literal["evaluation_aggregate_v1"]
    run_id: str
    documents: tuple[AggregateScope, ...]
    overall: AggregateScope
```

document scope 必须有 document_id，overall 必须为空。documents 按 ground-truth manifest 顺序。只有 completed run 生成 aggregate；所有值必须能从五份 document JSON 重新计算并严格匹配整数、以绝对误差不超过 `1e-12` 匹配浮点值。无纳入问题时所有比率 MetricValue 为 not_applicable。

```python
ComparisonDifferenceCode = Literal[
    "dataset_version", "ground_truth_fingerprint", "pdf_hash",
    "question_text", "top_k", "evaluation_protocol_version",
    "annotation_rule_version", "query_prefix", "document_filter",
    "distance_order", "tie_break_order",
]

PrimaryMetricName = Literal[
    "question_hit_rate_at_k", "macro_evidence_group_recall_at_k",
    "complete_coverage_rate_at_k", "macro_chunk_precision_at_k",
    "mean_reciprocal_rank_at_k",
    "macro_cross_document_contamination_at_k",
]

class MetricDelta:
    metric_name: PrimaryMetricName
    baseline_value: float | None
    candidate_value: float | None
    absolute_delta: float | None

class RunComparison:
    baseline_run_id: str
    candidate_run_id: str
    status: ComparisonStatus
    difference_codes: tuple[ComparisonDifferenceCode, ...]
    metric_deltas: tuple[MetricDelta, ...]

class ComparisonIndex:
    schema_version: Literal["evaluation_comparison_v1"]
    generated_at: str
    comparisons: tuple[RunComparison, ...]
```

ComparisonStatus 按需求规则从两个 completed run 计算，不写入单个 run。strictly_comparable 时 difference_codes 为空且生成全部六项 MetricDelta；某项任一侧为 not_applicable 时三个值均为空，否则 `absolute_delta = candidate_value - baseline_value`。另外两种状态至少一个 difference code 且 metric_deltas 为空。污染率下降表现为负数，报告不得统一把正数解释为改善。

同一 dataset/test set/build fingerprint/K/protocol 的多次 completed 运行中，按 started_at、run_id 升序的第一条作为该配置的正式基线；后续重跑保留但不替代。comparison builder 对全部正式基线按 system version、pipeline、build fingerprint、K、started_at、run_id 排序，并为每一无序运行对生成一条 RunComparison，顺序靠前者为 baseline。Markdown 按 strictly comparable cohort 汇总，protocol/dataset changed 只列差异组而不展开无意义的指标表。该规则是确定性的，不选择“最新一次”。

## 8. 模块和接口

```python
class EvaluationRepository(Protocol):
    def load_approved_ground_truth(self) -> GroundTruthDataset: ...
    def load_matching_test_set(
        self, runtime: PipelineRuntime, manifest: PipelineManifest,
        ground_truth: GroundTruthDataset,
    ) -> TestSetDataset: ...
    def validate_chunk_mappings(
        self, test_set: TestSetDataset, vector_store: ChromaVectorStore,
    ) -> None: ...

class EvaluationRunner:
    def run(self, runtime: PipelineRuntime, top_k: int) -> EvaluationRunRecord: ...

class MetricsCalculator:
    def question_metrics(
        self, case: GroundTruthCase, mappings: tuple[EvidenceGroupMapping, ...],
        hits: tuple[RetrievedChunkSnapshot, ...],
    ) -> QuestionMetrics | None: ...
    def aggregate(
        self, documents: tuple[EvaluationDocumentResult, ...],
    ) -> EvaluationAggregate: ...

class ReportRenderer:
    def render_document(self, result: EvaluationDocumentResult) -> str: ...
    def rebuild_indexes(self, runs: tuple[EvaluationRunRecord, ...]) -> None: ...
```

职责边界：

- repository 只读取、严格反序列化、复算 hash/fingerprint 和验证关联；不判定检索相关性。
- runner 是唯一编排者，调用 Retriever、建立 hit snapshot、查 test set 映射并调用 calculator；不内嵌公式或 HTML。
- calculator 是纯函数层，不访问文件、Chroma 或时间。
- renderer 只读取已写出的机器 JSON，使用 HTML 转义模板生成视图；不重新检索或计算业务指标。
- summary builder 读取全部已发布 run 以显示 invalid/failed/completed；comparison builder 只从 completed run/aggregate JSON 计算 comparison JSON，再由 JSON 生成 Markdown。

正式评估必须通过 Retriever，以保持索引健康检查、query embedding、collection 隔离和总需求 I-03 的边界同分扩展；不得直接调用 Chroma 绕过 runtime。runner 只验证返回值已经按 `(distance, chunk_id)` 稳定排序、ID 唯一且数量不超过 K，不再实现第二套候选扩展或排序逻辑。

## 9. 运行、失败与发布

正式 `eval --pipeline v1|v2 [--top-k N]` 流程：

1. Registry 创建 runtime，既有 readiness 协调器尝试恢复后把最终健康结果交给 runner；仍不可用时 runner 记录 invalid，不调用 Retriever；
2. repository 加载唯一 approved ground truth；
3. 按 system、pipeline、ground-truth fingerprint 和 build fingerprint 选择唯一 approved test set；
4. 对 BuildConfig、PDF/file hash、问题/group 集合和全部 chunk mapping 做 preflight；
5. 为 run_id 创建 `.work/<run_id>/`；
6. 按 manifest 文件顺序、case 原始顺序逐题调用 Retriever；
7. 每完成一份 PDF，原子写其 JSON，再从该 JSON 生成 HTML；
8. 全部完成后写 aggregate.json 和 completed run.json；
9. 校验路径、hash 和可复算指标后，将 work 目录同卷原子 rename 到最终 runs 目录；
10. 从全部 completed runs 原子重建 system summary、comparison.json 和 comparison.md。

`--top-k` 省略时使用 Settings.top_k；显式值只进入 EvaluationQueryConfig，不改变 BuildConfig。`--live` 保留为非正式 LLM 冒烟入口，只读取 ground-truth 问题并输出到终端，不生成本契约的 run、指标或报告，也不得被写入 comparison。

preflight 失败仍生成只含 invalid run.json 的最终 run 目录。执行失败时 runner 为当前 case 写 failed、后续 case 写 not_run，尽可能完成该 PDF JSON，最终发布 failed run.json 和已有诊断文件；不生成 aggregate、summary 或 comparison 更新。错误 message 供人阅读，程序只依据 status 和 stage 分支。

进程在 rename 前崩溃只留下 `.work`。下一次 eval 启动时仅清理超过 24 小时且目录名满足 run_id 格式的 work 目录；不得触碰已发布 run。评估不修改 collection/manifest，因此不复用索引 publication journal。summary/comparison 重建失败不删除已发布 completed run，但本次命令返回失败；下次可从机器 JSON 确定性重建。

## 10. 存储路径

```text
eval/
├── README.md
├── ground-truth/
│   ├── manifest.json
│   └── <document-key>.json
└── test-sets/
    └── system-v2.0__pipeline-<id>__cfg-<fingerprint12>/
        ├── manifest.json
        └── <document-key>.json

validation/retrieval/
├── comparison.json
├── comparison.md
└── system-v2.0/
    ├── summary.md
    ├── .work/<run-id>/
    └── runs/
        └── pipeline-<id>__cfg-<fingerprint12>__k-<K>__<run-id>/
            ├── run.json
            ├── aggregate.json                 # 仅 completed
            └── documents/
                ├── <document-key>.json
                └── <document-key>.html
```

ground truth 和 test sets 是审核输入，提交 Git。正式 JSON、HTML 和 Markdown 是版本效果证据，也提交 Git；`.work` 加入 `.gitignore`。运行目录不可原地覆盖。报告包含业务 PDF 原文，仓库公开前必须按项目数据发布政策单独审核；本契约不自动脱敏或改写证据。

## 11. 代码组织与依赖方向

```text
rag/
├── evaluation_models.py       # 本文不可变模型、严格 JSON codec、fingerprint
├── evaluation_repository.py   # ground truth/test set 读取与 preflight
├── evaluation_metrics.py      # 纯指标与聚合
├── evaluation_reports.py      # JSON→HTML/Markdown、comparison
└── evaluator.py               # EvaluationRunner；保留 CLI 导入位置

scripts/
└── prepare_evaluation_data.py # 导出、校验和定稿人工标注输入；不运行检索
```

依赖方向固定为 models ← repository/metrics ← evaluator ← CLI；reports 依赖 models/metrics 的读取接口，不依赖 Retriever。现有通用 `rag.models.EvaluationCase/EvaluationResult` 在迁移完成后删除；不得同时维护新旧两套正式评估模型。旧比较脚本若需保留，只能标为历史工具且不得读取新的 approved 数据集生成正式结论。

不引入数据库、Web UI、后台任务、LLM judge、插件系统或通用工作流框架。

## 12. 验证要求

- 每个模型覆盖严格反序列化、额外字段拒绝、可空条件和全部不变量。
- ground-truth/test-set fingerprint 对键顺序和格式化空白稳定，对任何语义字段变化敏感。
- preflight 覆盖零个/多个 approved test set、BuildConfig 三方错配、PDF hash 变化、缺失/未知 chunk、问题/group 不一致。
- 指标覆盖零返回、少于 K、一个 chunk 参与多组、单 chunk 组合、多 chunk AND 组合、多个 OR 组合、组合未完整返回、unmappable、跨文档污染和不可回答题。
- 排序测试覆盖距离同分、K 边界、重复 chunk ID 拒绝和非有限距离。
- 聚合测试从整数分子分母复算 Macro/Micro、MRR 和 not_applicable。
- 故障注入覆盖 retrieval、JSON、HTML、aggregation、rename 和 summary rebuild；验证 failed run 不进入正式汇总且 completed run 不被删除。
- HTML 测试验证正文不截断、HTML 转义、无 embedding、默认不以 chunk ID 代替正文。
- comparison 覆盖全部 DifferenceCode、dataset_changed、污染率 delta 符号和同配置首次 completed 基线选择。
- 集成测试使用隔离临时目录和 fake embedder/vector store；默认 pytest 不加载模型、不访问网络。

## 13. 技术验证门

Chroma 1.5.9 已验证无法在距离完全相同且跨越 K 边界时稳定返回同一候选集合。已确认的适配方式是：初始请求 `min(available, K+1)` 条；若排序后第 K 条与候选池末条距离完全相同，则按 `min(available, max(current+1, current*2))` 扩大并重查，直到末条距离越过边界或覆盖全部作用域；最后按 `(distance, chunk_id)` 排序并严格取 K。实现验证必须证明该算法在同进程和独立进程中稳定，证据记录在[技术验证](technical-validation.md#6-正式检索评估同距离-top-k-边界未关闭)。
