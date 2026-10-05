# 装配、配置与接口连接契约

状态：**架构已审核，通过规格级编码就绪复审**。本文是[总架构](../architecture.md#5-跨线约束)所述装配的唯一权威：先读第 2 节的插槽与连接，再读第 3 节的兼容约束，最后读内置绑定和模型。可观察接入与命名规则以[装配需求](../requirements/assembly.md)为准，命令行为见[运行操作需求](../requirements/operations.md)。

## 1. 四类定义不可合并

`InterfaceDefinition` 描述稳定业务契约与可接入插件约束；`SlotDefinition` 描述一个大接口内部或系统主链对接口的一处使用；`PluginDefinition` 描述一种实现、能力和参数 schema；`SavedConfiguration` 只保存对插槽的插件选择和参数。装配配置不得修改接口输入输出、接入条件、数量或连接方向。运行时 `Binding` 是校验后解析出的本次对象实例与资源作用域，不写回已保存配置。

| 模型 | 必须独立表达的内容 | 禁止承担的内容 |
| --- | --- | --- |
| InterfaceDefinition | 接口 ID/契约版本、统一输入输出类型、能力要求、允许插件约束 | 某用户选择的插件参数、系统执行顺序副本 |
| SlotDefinition | 所属大接口、槽 ID、引用接口、接入规则、one/multi、条件及能力约束 | 插件实现细节、反方向连接副本 |
| PluginDefinition | 插件 ID/实现版本、所实现接口及契约版本、能力声明、参数 schema、工厂与资源需求 | 改写公共字段或私自定义上下游 |
| SavedConfiguration | 稳定 NAME、绑定插件 ID 与参数、配置格式版本、默认指向之外的装配选择 | 接口定义、已发布索引本体、自动生成的健康状态 |
| Binding | 本次配置解析结果、已验证连接、实例句柄与生命周期 | 跨命令可变全局单例、向持久化反写隐式默认值 |

字段级记录见第 7 节；角色表只解释边界，不作为第二份 schema。

## 2. 接入与单份连接关系

槽的接入规则封闭为 `required`、`optional`、`conditional`，数量封闭为 `one`、`multi`。`required` 恰有一个或至少一个绑定；`optional` 可空；`conditional` 的条件在已选配置上求值，明确决定必须、允许或禁止接入。`multi` 各绑定需独立身份和确定收集顺序。未接入是配置事实，不能伪装为运行成功且结果为空。

系统只保存一份有向**处理依赖边**，每条边只能从生产者的输出端口指向消费者的输入端口；下游所需上游、界面展示的反向关系及拓扑执行顺序均由此推导。大接口的外部输入如何交给内部子接口、内部结果如何作为大接口输出，是单独的**边界映射**，不得伪装成 `input → input` 或 `output → output` 的处理依赖边。插槽接入只决定哪些接口实例存在，也不等于数据边。MainParser 与 TableExtractor 共享源 PDF 但彼此无边，可以并接；并接不承诺物理同时执行。

本版插槽目录如下；相同接口在不同 owner 下是不同插槽，不复制实现。`required` 均相对于所属大接口被本命令启用而言。

| owner | slot/interface | 接入、数量、条件 |
| --- | --- | --- |
| 系统入库 | Registry、Indexer、ArtifactBuilder | 各 required one |
| 系统检索/问答/运维/评估 | IndexHealth、Retriever、Chatbot、Recovery、Evaluator | 各在对应命令启用时 required one；不相关命令不实例化 |
| Indexer | DocumentProcessor、Chunker、Embedder、Publisher | 各 required one |
| DocumentProcessor | MainParser | required one |
| DocumentProcessor | TableExtractor | optional multi |
| DocumentProcessor | TableSelector | conditional one，C1 |
| TableSelector | PdfEvidenceReader | conditional one，C1 |
| DocumentProcessor | DocumentAssembler | required one |
| DocumentAssembler | TableContentPreparation | conditional one，C2 |
| DocumentAssembler | DocumentComposer | required one |
| TableContentPreparation | HeaderDetector、TableSerializer | 各 required one |
| Publisher | VectorStore、ManifestStore、RecoveryStore | 各 required one |
| Publisher | ArtifactStore | required one |
| IndexHealth | SourceProbe、VectorReader、ManifestStore、RecoveryStore、ArtifactStore | 各 required one |
| Retriever | IndexHealth、query Embedder、VectorReader | 各 required one |
| Chatbot | Retriever、PromptBuilder、LanguageModel | 各 required one |
| Evaluator | EvaluationRepository、PreflightValidator、Retriever、MetricCalculator、EvaluationReporter、EvaluationRunStore | 各 required one |
| Recovery | VectorStore、ManifestStore、RecoveryStore、ArtifactStore | 各 required one |

C1 是至少一个 TableExtractor 已接入；C2 是 Assembler 的有效参数 `table_content_enabled=true` 且其插件声明表格消费能力。`ConditionId` 封闭为 `table_extractor_present_v1|assembler_consumes_table_v1`，分别表示 C1、C2；每个条件只读取已经校验的绑定/能力/有效参数，求值一次后不可变。槽的 conditional 在条件真时必须恰接 one，条件假时禁止接；不存在“条件真可选”的隐式分支。边的 `active_when_all` 是合取，不允许任意表达式。条件在静态装配时求值；C1/C2 不由预设名称推断。主解析器可能产生表格 slot 时必须有 C2，C1 成立也要求 C2；若 C2 为 false 则主解析器须声明不产生表格结构。ArtifactBuilder/ArtifactStore 在所有入库装配中必接；只由 C1/C2 决定适用的表格阶段文件。PdfEvidenceReader 是 TableSelector 使用时的 required one 支撑依赖，DocumentRegistry 是显式文档选择和 Health 的共享源事实依赖。结构化 Chunker 的 passage 计数是插件内部能力，不再作为可独立绑定的插槽。

机器槽 ID 的规范形式为 `<owner_id>.<role_id>`，owner_id 对应上表大接口小写蛇形名（顶层为 `system`），role_id 对应表中接口名小写蛇形名；例如 `document_processor.main_parser`、`document_processor.table_extractor`、`document_processor.table_selector`、`document_processor.document_assembler`、`document_assembler.document_composer`、`system.artifact_builder`、`retriever.embedder`、`chatbot.retriever`。同 owner 下不得复用 role_id。C1、C2 的 condition_id 分别固定为 `table_extractor_present_v1`、`assembler_consumes_table_v1`；版本变化触发装配重新校验。接口契约版本采用 `major.minor`，插件声明所支持的明确版本集合，不做隐式“大致兼容”；任何公共输入输出字段或空值语义变化须新契约版本并显式适配。

首批机器槽 ID 全集如下。顶层同一接口的不同消费位置可以复用 PluginDefinition，但每个槽仍有自己的绑定身份；`system.registry` 指向接口 `DocumentRegistry`。表内列举是对上方角色表的 ID 展开，不另定义接入规则；新增接口/槽须在两表同次审核。

| owner | slot_id 全集 |
| --- | --- |
| system | `system.registry`, `system.indexer`, `system.artifact_builder`, `system.index_health`, `system.retriever`, `system.chatbot`, `system.recovery`, `system.evaluator` |
| indexer | `indexer.document_processor`, `indexer.chunker`, `indexer.embedder`, `indexer.publisher` |
| document_processor | `document_processor.main_parser`, `document_processor.table_extractor`, `document_processor.table_selector`, `document_processor.document_assembler` |
| table_selector | `table_selector.pdf_evidence_reader` |
| document_assembler | `document_assembler.table_content_preparation`, `document_assembler.document_composer` |
| table_content_preparation | `table_content_preparation.header_detector`, `table_content_preparation.table_serializer` |
| publisher | `publisher.vector_store`, `publisher.manifest_store`, `publisher.recovery_store`, `publisher.artifact_store` |
| index_health | `index_health.source_probe`, `index_health.vector_reader`, `index_health.manifest_store`, `index_health.recovery_store`, `index_health.artifact_store` |
| retriever | `retriever.index_health`, `retriever.embedder`, `retriever.vector_reader` |
| chatbot | `chatbot.retriever`, `chatbot.prompt_builder`, `chatbot.language_model` |
| evaluator | `evaluator.evaluation_repository`, `evaluator.preflight_validator`, `evaluator.retriever`, `evaluator.metric_calculator`, `evaluator.evaluation_reporter`, `evaluator.evaluation_run_store` |
| recovery | `recovery.vector_store`, `recovery.manifest_store`, `recovery.recovery_store`, `recovery.artifact_store` |

`system.index_health|retriever.index_health`、`system.retriever|chatbot.retriever|evaluator.retriever` 等是不同使用位置，不表示复制业务算法；同一命令内同一 IndexIdentity 的共享资源由装配根统一建立且不得跨身份复用。顶层命令只激活本命令所需的 system 槽；未激活槽不要求实例化。`system.artifact_builder` 仅入库激活，`system.evaluator` 仅正式评估激活，`system.recovery` 仅获准恢复时激活；条件 C1/C2 只控制表格子槽，不改变顶层命令资源图。

以下是首批有向**业务结果依赖边**的唯一注册表。`slot_id.port_id` 已按上表展开，`[C]` 为条件边；每行是一条 `DependencyEdge`，不得再维护反向副本。`ordered_collect` 只在 multi 槽报告收集时使用；其余均为 `direct`。大接口输入分发、owner 创建新请求和支撑方法调用不混入本表。

| 生产者输出端口 → 消费者输入端口 | 条件与负载 |
| --- | --- |
| `document_processor.main_parser.primary` → `document_processor.table_selector.primary` | C1；direct `PrimaryDocument` |
| `document_processor.table_extractor.report` → `document_processor.table_selector.reports` | C1；ordered_collect，各绑定输出按 binding.order 汇集为 `tuple[TableExtractionReport, ...]` |
| `document_processor.main_parser.primary` → `document_processor.document_assembler.primary` | 始终；direct `PrimaryDocument` |
| `document_processor.table_extractor.report` → `document_processor.document_assembler.reports` | C1；ordered_collect，同次绑定报告只作已指认候选的结构查找 |
| `document_processor.table_selector.resolutions` → `document_processor.document_assembler.resolutions` | C1；direct `tuple[ContentResolution, ...]` |
| `table_content_preparation.header_detector.decision` → `table_content_preparation.table_serializer.header` | C2；direct `HeaderDecision`，同一 slot |
| `document_assembler.table_content_preparation.prepared` → `document_assembler.document_composer.prepared_tables` | C2；direct `tuple[PreparedTableContent, ...]` |
| `document_processor.document_assembler.document` → `indexer.chunker.document` | 始终；direct `ParsedDocument` |
| `indexer.document_processor.result` → `system.artifact_builder.processing` | 始终；direct `ProcessingResult`，含同次阶段证据 |
| `indexer.chunker.chunks` → `indexer.embedder.chunks` | 始终；direct `ChunkBatch` |
| `indexer.chunker.chunks` → `system.artifact_builder.chunks` | 始终；direct 同一 `ChunkBatch` |
| `chatbot.retriever.result` → `chatbot.prompt_builder.retrieval` | 问答激活；direct `RetrievalResult` |
| `evaluator.evaluation_repository.loaded` → `evaluator.preflight_validator.loaded` | 评估激活；direct `LoadedEvaluationData` |
| `evaluator.retriever.result` → `evaluator.metric_calculator.case_retrieval` | 评估逐题；direct `RetrievalResult`，其余 case/mapping 输入由 Evaluator 关联构造 |
| `evaluator.evaluation_reporter.rendered` → `evaluator.evaluation_run_store.rendered` | 评估发布；direct `RenderedEvaluation` |

DocumentProcessor 的 ingress 边界映射为：其外部 `source` 输入供 MainParser 和每个已接入的 TableExtractor 使用；MainParser 的 `primary` 同时送给 Selector（C1）和 Assembler。DocumentProcessor 插件在全部子接口完成后按[处理结果模型](document-processing.md#2-documentprocessor-的内部-dag)构造自己的 `result: ProcessingResult`，其中 `parsed_document` 字段来自 Assembler 输出；这不是把一个 `ParsedDocument` 用 egress 映射冒充为 `ProcessingResult`。DocumentAssembler 内部的 `primary` 与条件 `resolutions` 分别供 TableContentPreparation 和 DocumentComposer 所需输入；Composer 的 `document` 是 Assembler 对外 `ParsedDocument` 的同型 egress。边界映射只说明容器如何传递**同型**数据，不创建第二份处理依赖或新的业务结果。PdfEvidenceReader 是 Selector 内部只读支撑依赖，其证据作为 Selector 调用输入但不在主处理边中伪装成独立阶段。ArtifactBuilder 从 `ProcessingResult` 和 `ChunkBatch` 观察已完成结果，全部适用文件校验成功后 Indexer 才允许 passage 编码；“校验成功”是编排门，不伪装成 ArtifactBuilder 向 Embedder 发送正文的边。Indexer 汇集 ChunkBatch、PassageEmbeddingBatch、StagedArtifacts 和构建身份，按[发布请求模型](storage-publication.md#2-持久化对象)构造 `PublicationRequest` 后调用 `Publisher.request`；这些不同类型的结果不是伪造为 Publisher 的同型端口依赖边。Publisher/Health/Recovery 的各 Store 是资源依赖而非处理结果边，仍由插槽表与各自领域端口契约约束。运行外壳、CLI、纯领域函数及预设名称不在插槽图中伪装成插件。

上段简写的“DocumentProcessor 外部 `source`”准确指 `DocumentProcessor.request: DocumentRequest` 的 `source: SourceDocument` 字段；它以 `BoundaryMapping.field_path="source"` 映射到 MainParser 与 TableExtractor 的各 `source` 输入端口，不是 `DocumentRequest` 对 `SourceDocument` 的同型直连。Indexer 的 DocumentRequest 与 PublicationRequest 由自身编排规则构造，不从不存在的上游同型输出画箭头。

首批入库处理链的端口 ID 与模型引用如下；表中 `[C1]`/`[C2]` 对应 `InterfacePort.active_when_all`，未注明者为空条件。字段全集以各模型链接的子契约为准；这些端口是调用值的方向定义，插件选择仍只写在 Slot/Plugin 定义中。

| 接口 | 输入端口：模型 | 输出端口：模型 |
| --- | --- | --- |
| DocumentRegistry | `request: RegistryRequest` | `sources: tuple[SourceDocument, ...]` |
| Indexer | `request: IndexerInput` | `result: IngestResult` |
| DocumentProcessor | `request: DocumentRequest` | `result: ProcessingResult` |
| MainParser | `source: SourceDocument` | `primary: PrimaryDocument`；`native: NativeParserEvidence` |
| TableExtractor | `source: SourceDocument` | `report: TableExtractionReport`（每个绑定各一份） |
| TableSelector | `primary: PrimaryDocument`；`reports: tuple[TableExtractionReport, ...]` | `resolutions: tuple[ContentResolution, ...]`；`grouping: GroupingReport`；`scoring: ScoringReport` |
| DocumentAssembler | `primary: PrimaryDocument`；`reports: tuple[TableExtractionReport, ...] [C1]`；`resolutions: tuple[ContentResolution, ...] [C1]` | `document: ParsedDocument`；`prepared_tables: tuple[PreparedTableContent, ...] [C2]` |
| TableContentPreparation | `primary: PrimaryDocument`；`reports: tuple[TableExtractionReport, ...] [C1]`；`resolutions: tuple[ContentResolution, ...] [C1]` | `prepared: tuple[PreparedTableContent, ...]` |
| HeaderDetector | `table: StructuredTable` | `decision: HeaderDecision` |
| TableSerializer | `table: StructuredTable`；`header: HeaderDecision` | `serialized: SerializedTable` |
| DocumentComposer | `primary: PrimaryDocument`；`resolutions: tuple[ContentResolution, ...] [C1]`；`prepared_tables: tuple[PreparedTableContent, ...] [C2]` | `document: ParsedDocument` |
| Chunker | `document: ParsedDocument`；`context: ChunkingContext` | `chunks: ChunkBatch` |
| ArtifactBuilder | `processing: ProcessingResult`；`chunks: ChunkBatch`；`build_projection: BuildProjection` | `staged: StagedArtifacts` |
| Embedder | `chunks: ChunkBatch`（passage 操作）或 `query_request: RetrievalRequest`（query 操作） | `passages: PassageEmbeddingBatch` 或 `query_vector: QueryEmbedding`，由调用操作唯一决定 |
| Publisher | `request: PublicationRequest` | `result: PublicationResult` |

检索与问答线的业务端口按[检索问答字段契约](retrieval-answering.md)登记，表中只列直接参与这两条工作线的数据端口；IndexHealth 与 VectorReader 的读方法、模型服务调用是已接入支撑接口的方法族，不被压成虚构的单一处理阶段结果。`messages` 的模型是有序元组，不是单条消息。

| 接口 | 输入端口：模型 | 输出端口：模型 |
| --- | --- | --- |
| Retriever | `request: RetrievalRequest` | `result: RetrievalResult` |
| Embedder（query 操作） | `query_request: RetrievalRequest` | `query_vector: QueryEmbedding` |
| Chatbot | `request: AnswerRequest` | `result: AnswerResult` |
| PromptBuilder | `retrieval: RetrievalResult`；`request: AnswerRequest` | `messages: tuple[PromptMessage, ...]` |
| LanguageModel | `request: LanguageModelRequest` | `answer: str`（非空） |

Retriever 内 `request` 同型 ingress 给 query Embedder；其 `query_vector` 供本次只读 VectorReader 查询，健康检查先于编码和向量读取，查询结果按[检索契约](retrieval-answering.md#2-编码与排序)构造成 `RetrievalResult`。Chatbot 按 `AnswerRequest` 中同次 question、document_id、top_k、configuration_name、index_identity 构造 `RetrievalRequest`，调用其 Retriever 槽；`Retriever.result → PromptBuilder.retrieval` 是直接处理依赖，`Chatbot.request → PromptBuilder.request` 是同型 ingress。Chatbot 将 `PromptBuilder.messages` 与已验证模型名、temperature=0 组装为 `LanguageModelRequest` 再调 LanguageModel；其返回文本与实际 RetrievalResult、消息、模型名共同构造 `AnswerResult`。这些不同类型的请求创建都属于 owner 构造，不绘成 `AnswerRequest → RetrievalRequest` 或 `tuple[PromptMessage] → LanguageModelRequest` 的同型直连边。检索/问答业务链不把 Store、健康检查或 API 密钥伪装成用户可选处理步骤。

运维与评估不复用入库端口作为伪阶段。它们的大接口及支撑方法族采用以下真实签名；`None` 返回只用于守卫方法，不表示执行缺席。字段级定义及失败条件分别在[运行](runtime-contracts.md)、[存储](storage-publication.md)与[评估](evaluation-contracts.md)契约中唯一维护。

| 接口 | 入口 → 结果 | 内部供数及连接 |
| --- | --- | --- |
| IndexHealth | `check(IndexIdentity, tuple[SourceDocument, ...]) → HealthReport` | Registry 源集合由调用 owner 传入；SourceProbe 对每个源只读，VectorReader/ManifestStore/RecoveryStore/ArtifactStore 从同一 IndexIdentity 支撑绑定读取；Health 自行构造报告 |
| SourceProbe | `probe(SourceDocument) → SourceProbeResult` | 接收 Health 对已发现源逐项分发；不运行主解析 |
| Recovery | `recover(IndexIdentity, transaction_id) → RecoveryResult` | transaction_id 仅从本 namespace 的 `RecoveryStore.list_pending` 得到；其余 Store 是同一事务的支撑依赖，结果交给 CLI 后必须复查 Health |
| Evaluator | `evaluate(EvaluationRunRequest) → EvaluationRun` | owner 创建同次 run_id/身份，按[评估交接契约](evaluation-contracts.md#1-独立评估主链)编排六个必接接口；Retriever 与查询入口共用实现，按题目构造请求 |
| EvaluationRepository | `load(GroundTruthIdentity, TestSetIdentity) → LoadedEvaluationData` | 只读已审核数据集，不由检索结果创建 ground truth |
| PreflightValidator | `validate(EvaluationRunRequest, LoadedEvaluationData, Manifest, tuple[VectorRecord, ...]) → None` | 实际 manifest/向量记录由同一 IndexIdentity 的 Store 读出；成功是 Retriever 的执行门 |
| MetricCalculator | `score_case(GroundTruthCase, TestCaseMapping, RetrievalResult) → CaseEvaluationFact`；`aggregate(run_id, tuple[EvaluationDocumentResult, ...]) → EvaluationAggregate` | Retriever 只给真实有序命中；正确答案和映射只在 Calculator 侧参与派生 |
| EvaluationReporter | `render(EvaluationRunRequest, tuple[EvaluationDocumentResult, ...], EvaluationAggregate, tuple[ComparisonRunSnapshot, ...], BaselineSelection) → RenderedEvaluation` | V2 只读历史及 V3 completed 运行转换为同一比较投影；HTML/Markdown 不回流评分 |
| EvaluationRunStore | `list_completed(GroundTruthIdentity) → tuple[EvaluationRun, ...]`；`list_comparison_runs() → tuple[ComparisonRunSnapshot, ...]`；`load_baselines() → BaselineSelection`；`publish(EvaluationRunRequest, RenderedEvaluation) → EvaluationRun` | 读取历史与明确基线供版本视图；写入使用私有暂存、版本级文件恢复证据与最终 run.json 完成标记 |

Resource/Store 接口是每个操作固定输入输出的方法族，不要求一个虚构的 `StoreOperation` 万能模型；它们的读写方法已在[存储端口表](storage-publication.md#1-权威来源和端口)逐项定义。Health/Recovery/Evaluator 的 owner 参数构造与资源读取不记为 `DependencyEdge`；评估的直接阶段边只包含上表的 Repository.loaded→Preflight、逐题 Retriever.result→MetricCalculator 和 Reporter.rendered→RunStore。Calculator 的逐题结果由 Evaluator 按文档组装，再传给聚合和 Reporter，属于 owner 构造；成功守卫只决定是否执行下游，不伪造 `None` 到业务输入的边。

评估复用 `Retriever.request/result` 公共端口；另有跨槽处理边所需的机器端口：`EvaluationRepository.loaded: LoadedEvaluationData`（output）、`PreflightValidator.loaded: LoadedEvaluationData`（input）、`MetricCalculator.case_retrieval: RetrievalResult`（input）、`EvaluationReporter.rendered: RenderedEvaluation`（output）、`EvaluationRunStore.rendered: RenderedEvaluation`（input）。其余方法参数由 Evaluator 从 `EvaluationRunRequest`、Repository 已加载题目/映射、同一索引的已发布 manifest/向量事实和 Calculator 结果显式构造；不存在另一个隐含上游业务边。预检 `None` 是顺序守卫；逐题 `CaseEvaluationFact` 先由 Evaluator 按文档顺序组成 `EvaluationDocumentResult`，Calculator 再以这些完成事实生成 Aggregate，Reporter 读取两者，不能从 HTML 反向计算。对每次方法调用，输入模型由本段及[评估契约](evaluation-contracts.md#1-独立评估主链)共同限定，不能把签名省略为 `EvaluationStage`。

主解析、提取、选优、组装和内容准备的调用还使用已验证 RuntimeBinding 与单文档资源上下文；它们是调用上下文/支撑依赖，不是从上游业务输出凭空出现的第二套边。`PdfEvidenceReader` 只在 Selector 内提供同源 PDF 原文/几何只读事实；`read_page_words`、`PdfPageWord` 与缺失语义由[表格契约](table-contracts.md#1-内部接口与依赖)定义。

入库链中不属于 `DependencyEdge` 的跨层供数按下表登记。`ingress` 是已有输入的同型透传或字段投影；owner 构造产生新的调用模型，必须由链接契约决定字段和值；子接口结果组装成 owner 输出也是 owner 的结果构造，不虚构一个 owner 输入端口。表格路径的条件以 C1/C2 为准，未接入时相关映射和构造均不激活。

| owner | 目标子接口端口或 owner 输出 | 供数方式与唯一规则 |
| --- | --- | --- |
| DocumentProcessor | `MainParser.source`、每个 `TableExtractor.source` | `request.source` 字段投影；同一份 `SourceDocument`，不是解析器之间的数据边 |
| DocumentProcessor | `result: ProcessingResult` | owner 按[处理结果契约](document-processing.md#2-documentprocessor-的内部-dag)汇集同次 source、identity、原生证据、主解析、条件表格证据及 Assembler 文档；不能用 Assembler 的 `ParsedDocument` 冒充同型 egress |
| DocumentAssembler | `TableContentPreparation.primary/reports/resolutions`、`DocumentComposer.primary/resolutions` | 已收到的同型 owner 输入按相同端口名 ingress；`reports/resolutions` 仅 C1，内容准备的全部映射仅 C2 |
| DocumentAssembler | `document: ParsedDocument` | `DocumentComposer.document` 同型 egress；`prepared_tables` 则从已执行的 `TableContentPreparation.prepared` 同型 egress，仅 C2 |
| TableContentPreparation | `HeaderDetector.table`、`TableSerializer.table` | owner 严格查找被采用候选或主解析原生表，构造同一 `StructuredTable`；`TableSerializer.header` 单独由 `HeaderDetector.decision` 的处理依赖边供给 |
| TableContentPreparation | `prepared: tuple[PreparedTableContent, ...]` | owner 按 slot 顺序汇集采用结构、`HeaderDecision`、`SerializedTable`；不伪造 HeaderDetector/Serializer 指向 owner 输入的边 |
| Indexer | `DocumentProcessor.request`、`Chunker.context`、`Publisher.request` | 分别按[处理交接](document-processing.md)与[发布请求](storage-publication.md#2-持久化对象)构造 `DocumentRequest`、`ChunkingContext`、`PublicationRequest`；使用同次已验证装配和构建身份，不接受用户注入任意 payload |
| Indexer | `ArtifactBuilder.build_projection` | 从本次已验证绑定创建并传入唯一 `BuildProjection`；其规范哈希必须等于处理结果的完整构建指纹，不能由 Builder 猜插件或从报告内容反推绑定 |

上表与第 2 节的方法族表共同限定四条工作线：业务 DAG 只登记生产者输出到消费者输入的同型边；Registry、检索、问答、健康、恢复和评估所需的不同类型请求由各 owner 按表中构造规则创建，Store/Reader 仍是有签名的资源方法，不为了凑一条边而伪造中间阶段。新增直接业务依赖时，须在此处同时登记两个机器端口和一条唯一的有向边。

校验顺序为：配置格式与名称 → 引用的接口/槽/插件存在且契约版本兼容 → 参数 schema → 必接/选接/条件/数量 → 边闭合和无环 → 输入输出契约与能力匹配 → 下游可完成性 → 构建身份及资源需求。前一关失败不进入下一关；静态可判定错误不得打开业务 PDF、向量库或模型。运行期外部资源错误与静态装配错误分开报告。

## 3. 跨插件兼容性

接口的公共输入输出模型相同，只证明形状可连接，不证明算法能够正确消费内容。本节是首批插件**跨槽装配规则的唯一权威**，不从内置配置名称推断。规则只读取已选插件、有效参数和注册能力；CLI 候选过滤、配置保存、配置加载及 `ingest --all-configs` 使用同一校验器。静态失败返回 `invalid_assembly`，指出规则 ID、相关槽与插件以及不满足的条件，不自动替换插件。

| 规则 | 必须判定的关系 | 首批结果 |
| --- | --- | --- |
| A01 解析—分块 | MainParser 经必接 DocumentAssembler 交付的文档语义必须满足 Chunker 的输入前提。当前 `assembler.composed_v1` 和 `assembler.ordered` 保留主解析的逐页或结构化语义，不在中途把一种变成另一种。 | 仅允许下方规则注册表 A01 的两个有序插件对；两种交叉绑定均拒绝，即使公共类型同为 `ParsedDocument`。 |
| A02 解析—组装 | 能产生 table slot 的解析器要求 C2、TableContentPreparation、HeaderDetector、TableSerializer；不能产生 table slot 的解析器不得启用 C2。 | 当前 `parser.docling_layout` 要求 C2；`parser.pymupdf_pages` 要求 C2 为 false。 |
| A03 提取—选优 | 有 TableExtractor 时，主解析必须提供可定位 table slot 和原生回退，TableSelector/PdfEvidenceReader 必须接入；候选统一转换为表格事实契约。 | 四个 extractor 可各自或共同接在 `parser.docling_layout` 后；与 `parser.pymupdf_pages` 组合拒绝。无 extractor 时 Selector/EvidenceReader 禁止接入。 |
| A04 可审计快照 | 所有入库装配必须有 ArtifactBuilder、ArtifactStore；主解析器必须交付可序列化原生证据，Builder 支持该证据格式，并按已接入阶段生成[固定角色集合](processing-artifacts.md#2-目录角色与条件)。 | `parser.pymupdf_pages` 与 `parser.docling_layout` 都须接 `artifacts.stage_snapshot_v3`；前者不伪造表格文件，后者随 C1/C2 生成表格文件。 |
| A05 分块—编码 | 结构化 Chunker 的 passage 计数与 Embedder 的模型名、不可变 revision、`passage: ` 前缀和 special-token 行为一致；两种 Chunker 的正文均须满足 passage 输入。 | 任一身份项不同则拒绝；字符 Chunker 不因此改变分块规则。 |
| A06 编码—存储—查询 | passage/query Embedder 使用同一完整 EmbeddingIdentity；向量维度、归一化与存储 schema 匹配，查询身份与已发布索引匹配。 | 仅维度相同不能替换模型；配置 NAME 相同不能绕过索引身份。 |
| A07 分块—引用展示 | `prompt.grounded(single_page)` 要求构建保证每个 chunk 恰一物理页；`page_set` 可展示多页。 | `page_characters` 可用两种，`structured_tokens` 只能用 `page_set`；不得等到首次多页命中才发现不兼容。 |
| A08 发布—健康—恢复 | Publisher、IndexHealth、Recovery 绑定同一 IndexIdentity、manifest/metadata schema 与必需快照角色集合，所需 Store/Reader 均存在。 | 不能把另一构建或另一装配的存储端口拼进当前运行绑定。 |
| A09 共享转换资源 | 同一文档同时绑定 `parser.docling_layout` 与 `extractor.docling` 时，二者的转换参数、文件身份与生存期必须指向同一次 Docling 转换；其余 extractor 不共享该资源。 | 首批 `extractor.docling` 不独立设置转换参数，继承本次主解析的已验证参数与资源；若将来允许独立设置，参数不一致时静态拒绝。同源 hash 不一致时停止该文档。 |

首批参与这些规则的 `PluginDefinition.capabilities` 取值是封闭的注册词汇，不向用户配置开放任意字符串：

| 插件 | 必须声明的能力 |
| --- | --- |
| `parser.pymupdf_pages` | `no_table_slots_v1`、`provides_native_parser_evidence_v1`；无 table slot 是适配器的输出保证，不由某次 PDF 零结果推断。 |
| `parser.docling_layout` | `produces_table_slots_v1`、`provides_native_table_fallback_v1`、`provides_native_parser_evidence_v1`。 |
| `assembler.composed_v1` | `consumes_table_slots_v1`；是否启用由有效参数 `table_content_enabled` 决定。 |
| `chunker.page_characters` | `guarantees_single_page_chunks_v1`。 |
| `chunker.structured_tokens` | `requires_matching_passage_tokenizer_v1`；具体模型身份从有效绑定及 EmbeddingIdentity 比较，不能由能力名代替。 |
| `artifacts.stage_snapshot_v3` | `consumes_native_parser_evidence_v1`；仅接受本次解析绑定声明的封闭原生格式，不接受任意 JSON 冒充。 |

上述机器能力 ID 的全集为 `no_table_slots_v1|produces_table_slots_v1|provides_native_table_fallback_v1|provides_native_parser_evidence_v1|consumes_table_slots_v1|guarantees_single_page_chunks_v1|requires_matching_passage_tokenizer_v1|consumes_native_parser_evidence_v1`。同一解析器不得同时声明 `no_table_slots_v1` 与 `produces_table_slots_v1`。未在表中出现的首批插件对此集合声明空元组；未来新增能力需审查并版本化注册词汇。适配器必须兑现正反能力声明，不能靠一次运行的零结果猜测。

A01—A09 的参与槽、判定条件和首批允许集合只在本节定义。PluginDefinition 提供真实能力和有效参数，不得以“输入类型都是 `ParsedDocument`”放行，也不得在插件内维护另一份同名白名单。新增插件需证明满足目标规则，并注册已审核的兼容声明；超出公共模型或算法前提时先演进接口。领域事实见[文档处理](document-processing.md)、[表格](table-contracts.md)、[检索问答](retrieval-answering.md)与[存储发布](storage-publication.md)子契约；本节裁决能否装配，不重复字段或算法。

下方注册表 A01 的允许关系须按 `MainParser, Chunker` 顺序保存为有序插件对，不写进任一插件的 `compatible_plugin_ids` 字段。A02—A09 由同一装配校验器的版本化纯规则判定，规则不可从配置文件注入任意表达式；新增插件需在本节审查其对应允许关系后更新注册规则，而非在 Indexer、CLI 和插件内部各写一份名称分支。

首批规则注册使用以下封闭记录；`evaluator_id` 只可指向安装包中已审核的纯校验函数，配置文件不能提供脚本、表达式或修改参与槽。`parameter_paths` 只读按插件 schema 展开后的有效值，缺字段按 schema 失败而非在规则中猜默认。`version` 或允许集合变化都触发已保存配置重新校验；影响构建结果时还须改变构建指纹。

```python
@dataclass(frozen=True)
class CompatibilityRuleDefinition:
    rule_id: Literal["A01", "A02", "A03", "A04", "A05", "A06", "A07", "A08", "A09"]
    version: Literal["1"]
    evaluator_id: Literal[
        "parser_chunk_semantics_v1", "parser_assembler_table_v1",
        "extractor_selector_evidence_v1", "audit_snapshot_roles_v1",
        "passage_tokenizer_identity_v1", "embedding_store_identity_v1",
        "prompt_page_semantics_v1", "publication_scope_v1",
        "shared_docling_conversion_v1",
    ]
    participating_slot_ids: tuple[str, ...]
    parameter_paths: tuple[str, ...]
    allowed_plugin_pairs: tuple[tuple[str, str], ...]
    failure_reason: RuleFailureReason

@dataclass(frozen=True)
class CompatibilityEvaluation:
    rule_id: Literal["A01", "A02", "A03", "A04", "A05", "A06", "A07", "A08", "A09"]
    status: Literal["passed", "failed", "deferred"]
    failure_reason: RuleFailureReason | None
    participating_slot_ids: tuple[str, ...]
    selected_plugin_ids: tuple[str, ...]

RuleFailureReason = Literal[
    "semantic_pair_disallowed", "table_content_mismatch", "extractor_selector_mismatch",
    "audit_snapshot_unavailable", "passage_tokenizer_mismatch", "embedding_store_mismatch",
    "source_label_unsupported", "publication_scope_mismatch", "shared_conversion_mismatch",
]
```

`version` 首批均为 `1`；rule_id/evaluator_id/failure_reason 与参与槽精确对应下表，`allowed_plugin_pairs` 仅 A01 为两个有序元组 `(parser.pymupdf_pages,chunker.page_characters)`、`(parser.docling_layout,chunker.structured_tokens)`，其余为空元组。`parameter_paths` 采用 `<slot_id>/parameters/<schema_field>`，从已选 PluginBinding 经 schema 默认展开后的有效值读取；没有参数依赖填空元组。C1/C2、能力声明、注册 schema/资源身份由 evaluator 直接读取已验证装配，不伪装成用户参数路径。表中“条件读取”写的是封闭 evaluator 的判定输入，不是第二套用户可编辑规则。

| 规则与 evaluator / failure_reason | `participating_slot_ids` | `parameter_paths`；条件读取 |
| --- | --- | --- |
| A01 `parser_chunk_semantics_v1` / `semantic_pair_disallowed` | `document_processor.main_parser`, `document_processor.document_assembler`, `indexer.chunker` | `()`；只接受上方两个有序 parser/chunker 配对，Assembler 须保留对应语义 |
| A02 `parser_assembler_table_v1` / `table_content_mismatch` | `document_processor.main_parser`, `document_processor.document_assembler`, `document_assembler.table_content_preparation`, `table_content_preparation.header_detector`, `table_content_preparation.table_serializer` | `document_processor.document_assembler/parameters/table_content_enabled`；解析器 `no_table_slots_v1|produces_table_slots_v1` 与 Assembler `consumes_table_slots_v1`、C2、条件子槽闭合 |
| A03 `extractor_selector_evidence_v1` / `extractor_selector_mismatch` | `document_processor.main_parser`, `document_processor.table_extractor`, `document_processor.table_selector`, `table_selector.pdf_evidence_reader` | `()`；C1 为真时主解析须同时声明可定位 table slot 和原生回退，Selector/EvidenceReader 必接；C1 假时两槽禁止接 |
| A04 `audit_snapshot_roles_v1` / `audit_snapshot_unavailable` | `document_processor.main_parser`, `document_processor.table_extractor`, `document_processor.table_selector`, `document_assembler.table_content_preparation`, `system.artifact_builder`, `publisher.artifact_store` | `()`；原生证据提供/消费能力与 C1/C2 决定的必需文件角色全集；两个首批 parser 都允许同一 Builder |
| A05 `passage_tokenizer_identity_v1` / `passage_tokenizer_mismatch` | `indexer.chunker`, `indexer.embedder` | `indexer.chunker/parameters/maximum_input_tokens`, `indexer.embedder/parameters/model_name`, `indexer.embedder/parameters/revision`, `indexer.embedder/parameters/passage_prefix`, `indexer.embedder/parameters/add_special_tokens`；仅结构化 Chunker 要求完整 tokenizer 身份相同，字符 Chunker 跳过此比较 |
| A06 `embedding_store_identity_v1` / `embedding_store_mismatch` | `indexer.embedder`, `retriever.embedder`, `publisher.vector_store`, `retriever.vector_reader` | 对 `indexer.embedder` 与 `retriever.embedder` 各自登记完整的 `model_name,revision,vector_dimension,normalize_embeddings,passage_prefix,query_prefix,add_special_tokens` 路径；完整 EmbeddingIdentity 与已发布 manifest、向量 schema 相等，只有维度相同不通过 |
| A07 `prompt_page_semantics_v1` / `source_label_unsupported` | `indexer.chunker`, `chatbot.prompt_builder` | `chatbot.prompt_builder/parameters/source_label_style`；`single_page` 要求 Chunker `guarantees_single_page_chunks_v1`，`page_set` 不要求 |
| A08 `publication_scope_v1` / `publication_scope_mismatch` | `indexer.publisher`, `publisher.vector_store`, `publisher.manifest_store`, `publisher.recovery_store`, `publisher.artifact_store`, `index_health.vector_reader`, `index_health.manifest_store`, `index_health.recovery_store`, `index_health.artifact_store`, `recovery.vector_store`, `recovery.manifest_store`, `recovery.recovery_store`, `recovery.artifact_store` | `()`；各端口同一 IndexIdentity、manifest/metadata/artifact schema 和必需角色，资源与作用域完整 |
| A09 `shared_docling_conversion_v1` / `shared_conversion_mismatch` | `document_processor.main_parser`, `document_processor.table_extractor` | 对 `document_processor.main_parser` 登记 `do_ocr,do_table_structure,table_mode,do_cell_matching,generate_page_images,docling_version` 各字段的完整路径；仅同接 parser.docling_layout/extractor.docling 时校验一次转换资源，运行时同源 hash 不一致由处理阶段阻断 |

参数路径的字段列举按上方语法逐项展开为完整路径并固定词典序；这只是表格排版压缩，不是运行时接收缩写。A06 的完整 `parameter_paths` 是对 `indexer.embedder`、`retriever.embedder` 两个前缀各自生成 `/parameters/model_name`、`/parameters/revision`、`/parameters/vector_dimension`、`/parameters/normalize_embeddings`、`/parameters/passage_prefix`、`/parameters/query_prefix`、`/parameters/add_special_tokens`，合计 14 条；A09 是 `document_processor.main_parser/parameters/` 分别接 `do_ocr|do_table_structure|table_mode|do_cell_matching|generate_page_images|docling_version` 的 6 条。注册记录实际存储完整字符串，按码点序排序，不能存带 `|` 或逗号的缩写。A06 中查询 Embedder 绑定使用与 passage 同一 PluginDefinition，但构建投影只包含构建侧绑定；查询侧身份比较在查询装配/索引加载时执行，不把查询设置写进 fingerprint。A08 对尚无已发布索引的新配置只验证可静态确定的 Store/schema/作用域构造能力；与 active 数据逐字节相等的检查留给运行时 Health，不把“尚未建库”误报为非法装配。

固定静态验收矩阵：`pymupdf_pages + page_characters + table_content_enabled=false + 无提取器` 与 `docling_layout + structured_tokens + table_content_enabled=true + 四提取器/选优/内容准备` 均通过；交叉两种 parser/chunker 组合均 A01 失败；`docling_layout + table_content_enabled=false` 和 `pymupdf_pages + table_content_enabled=true` 均 A02 失败；任一 extractor 接在 `pymupdf_pages` 后 A03 失败；缺 Builder/ArtifactStore 或解析器不交付原生证据 A04 失败；结构化 tokenizer revision/前缀/special-token 任一不同 A05 失败；passage/query EmbeddingIdentity 任一项不同 A06 失败；`structured_tokens + single_page` A07 失败；跨 IndexIdentity 复用 Store A08 失败；同接 Docling 主解析/提取却使用不同转换参数 A09 失败。CLI 部分选择只在存在至少一种完整合法延伸时显示候选；这些用例在保存、加载及 `--all-configs` 必须得到同一规则 ID，不能由各入口实现不同白名单。

校验器按 A01→A09 顺序遍历完整配置，返回首个 failed 规则及其 `rule_id,version,failure_reason,participating_slot_ids,selected_plugin_ids`，不凭人类 message 分支。`CompatibilityEvaluation` 在完整配置上不得为 deferred；passed 时 failure_reason 空，failed 时恰为该规则声明的原因。CLI 向导的部分选择中，仅因参与槽尚未选定而不能下结论时标 deferred，不作为失败；已选部分已确定不可能完成时 failed，随后对剩余必接槽做存在性搜索，只有存在至少一条完整合法延伸时才显示该候选。保存/加载/全配置入库使用同一完整校验器和版本快照；新增插件不自动因公共模型同型进入 A01 允许对。运行时 PDF hash、共享资源或 SDK 异常仍由实际处理阶段报告，不误称静态配置错误。

CLI 每一步校验已选择部分，并检查剩余必接槽至少有一个可完成的候选；保存前再次校验完整配置。依赖实际 PDF 的内容错误在运行阶段报告，不把某份 PDF 没有表格、正常零命中或 SDK 失败伪装成静态不兼容。

## 4. 内置和自定义配置

系统安装两份已保存的内置配置：`plain_text` 与 `structured`，后者是 V3.0 初始默认指向。内置名称不允许被自定义配置占用。新配置由 `--configure` 向导按拓扑顺序选插件，候选须与前序选择兼容且能使后续必接槽可完成；完整装配通过校验后才接受 NAME 并原子保存。NAME 非空，仅含 ASCII 英文字母、数字、`_` 或 `-`，项目内按大小写不敏感规则唯一。重名或非法名不覆盖，取消不留半成品。已用于建索引的配置不得用原名称重写装配；实验变体另存新名称。

`--config NAME` 只引用已有配置；默认指向只保存被选配置的稳定引用，安装插件或保存配置不自动改变它。NAME 不进入构建兼容性判断。查询侧不同而构建侧相同的完整配置可共享物理索引，但命令和评估记录必须保留各自配置身份。配置保存在 `.rag/system-v3/configs/<name.casefold()>.json`，文件内 `name` 保留原始显示拼写；大小写不敏感查找由规范文件名完成，加载后仍校验文件名与内容一致。默认指向放 `.rag/system-v3/default-config.json`。新配置先在同目录独立暂存并回读校验，再以“目标不存在才创建”的原子操作发布；同名并发创建只能一方成功，失败方不覆盖。默认指向同目录临时写入、flush、原子替换并回读。路径中只允许已校验 NAME 字符，禁止目录越界。

## 5. 构建与查询配置隔离

构建身份按会改变持久化输出的已选插件、规则版本及有效参数的规范化值计算；包含解析、表格判定、分块、tokenizer/embedding 身份和持久化 schema。查询 K、Retriever 查询策略参数、Prompt/LLM、日志及 UI 参数不进入构建指纹，仍进入请求或评估记录。两个配置即使名字不同，只有构建身份及存储 schema 兼容时才可复用物理索引；同名但构建参数不同时绝不能凭 NAME 放行。规范化字节与冲突处理由第 8 节唯一规定。

`ingest --all-configs` 枚举本次启动时所有已保存、已校验且构建侧可入库的完整配置快照；向导草稿不纳入。仅查询侧参数不同、但构建侧完整的配置仍纳入枚举，随后按相同 IndexIdentity 去重物理构建。逐配置独立汇总结果；不同 IndexIdentity 的事务独立，一个目标失败不改变其他目标已提交状态，也不阻止其继续。若多个完整配置共享构建身份，物理索引不重复构建，但每个配置的执行结果和绑定关系须明确可追溯；不能用名称枚举顺序决定索引内容。

枚举只产生已校验且可入库的目标；未通过校验的保存配置不进入构建事务。多个配置共享 IndexIdentity 时按 fingerprint 去重构建，全部引用配置共享同一次成功/失败事实，各自的摘要条目指向同一物理结果；一个配置名称不会独占该索引。无有效目标时不执行写入并明确提示，而不伪报已建立索引。

## 6. 资源与失败

装配根仅实例化当前命令需要的插件：只读浏览无需解析器/E5/LLM，search 需要 query Embedder，ask/chat 才需要 LLM。一个文档处理期间可按适配器族共享 Docling 转换资源，但资源必须受本次文档与转换参数约束；不进入公共模型，也不作为跨运行缓存。创建、关闭及失败清理由装配根/作用域管理，插件不能持有无法释放的全局状态。

校验失败返回槽、插件、违反的契约和可执行下一步；运行期失败按发生阶段与作用域回传，不在装配器内悄悄切换插件。构建或查询中途不得更改 Binding；chat 整个会话固定配置。配置保存与默认指向更新须原子化，不能让中断写入产生半份可选配置。

## 7. 字段级定义

配置和注册使用封闭 JSON 对象，未知字段、重复绑定、重复 ID 与不支持的 schema_version 均拒绝。下列代码块**列出完整字段**，采用与[历史架构](../../../../history/architecture/v1.0-minimal-rag-architecture.md#6-领域模型)相同的逐模型展示方式；它们是规格记法，不要求实现逐字采用 dataclass。`None` 仅在标注处允许，其他字段不得缺失或为 null。字符串标识非空且在各自命名空间唯一，运行时 factory/实例句柄不序列化。

接口、插槽、边界映射、处理依赖和插件是注册表中的不同定义：

```python
@dataclass(frozen=True)
class InterfaceDefinition:
    interface_id: str
    contract_version: str
    input_ports: tuple[InterfacePort, ...]
    output_ports: tuple[InterfacePort, ...]
    required_capabilities: tuple[str, ...]
    allowed_plugin_ids: tuple[str, ...]

@dataclass(frozen=True)
class InterfacePort:
    port_id: str
    model_id: str
    active_when_all: tuple[ConditionId, ...]

@dataclass(frozen=True)
class SlotDefinition:
    slot_id: str
    owner_interface_id: str | None
    interface_id: str
    access: Literal["required", "optional", "conditional"]
    cardinality: Literal["one", "multi"]
    condition_id: ConditionId | None
    required_capabilities: tuple[str, ...]

@dataclass(frozen=True)
class DependencyEdge:
    source_slot_id: str
    source_output_port_id: str
    target_slot_id: str
    target_input_port_id: str
    delivery: Literal["direct", "ordered_collect"]
    active_when_all: tuple[ConditionId, ...]

@dataclass(frozen=True)
class BoundaryMapping:
    owner_interface_id: str
    owner_port_id: str
    child_slot_id: str
    child_port_id: str
    kind: Literal["ingress", "egress"]
    field_path: str | None
    active_when_all: tuple[ConditionId, ...]

@dataclass(frozen=True)
class OwnerConstruction:
    owner_interface_id: str
    target_slot_id: str
    target_input_port_id: str
    construction_contract_id: str
    active_when_all: tuple[ConditionId, ...]

@dataclass(frozen=True)
class PluginDefinition:
    plugin_id: str
    implementation_version: str
    interface_id: str
    supported_contract_versions: tuple[str, ...]
    capabilities: tuple[str, ...]
    parameter_schema_id: str
    factory_id: str
    resource_requirements: tuple[str, ...]
    build_effect: bool
```

`interface_id` 和 `plugin_id` 在注册表各自唯一；`InterfacePort.port_id` 在本接口、本方向内唯一，`model_id` 指向公共契约中的完整模型，不用普通字符串临时充当未定义类型。`allowed_plugin_ids=()` 表示按能力开放注册，不表示任意插件均可接。顶层 Slot 的 owner 为 null；仅 conditional Slot 有 condition_id。`DependencyEdge` 两端必须是已登记且实际接入的**输出**与**输入**端口；`direct` 要求同一公共模型及契约版本，`ordered_collect` 只用于 multi 槽中每个绑定输出 `T` 汇集为目标端口 `tuple[T,...]`，顺序取 Binding.order，不按完成时刻。边条件集合为空表示无条件边，非空表示全部成立才激活；激活图无环，反向导航派生。`BoundaryMapping.ingress` 只把 owner 输入或其中已定义的一个字段交给 child 输入；`egress` 只把 child 输出暴露为 owner 输出。`field_path=None` 时两端须同型；非空时只能是 owner 输入模型中存在的只读字段路径，投影后的字段类型须与 child 输入端口同型，不能包含运算或隐式转换；egress 的 field_path 必为空。组合接口通过自身算法构造不同模型（如 `ProcessingResult`）不使用 egress 映射冒充直通。`OwnerConstruction` 表示 owner 根据其已定义领域规则创建新的子接口调用模型（如 Indexer 构造 DocumentRequest），`construction_contract_id` 必须指向唯一字段级创建规则，不允许用它掩盖本可直连的同型数据。每个必需子接口输入须由恰一直接边、一个 ordered_collect 集合、一个 ingress 映射或一个明确的 owner construction 供给；未接槽不得留下激活边/映射/构造。插件能力必须可机器校验，不能只用文字说明；参数 schema 与 factory 由注册表解析，用户配置不提供可执行代码。第 3 节的跨槽规则是装配校验的一部分，不能因单槽 `capabilities` 看似满足而跳过。

`InterfacePort.active_when_all=()` 表示所处 slot 接入后该端口始终存在；非空条件全部成立时端口才存在，条件不成立时禁止供数或出数。例如 DocumentAssembler 的 `resolutions` 输入仅在 C1 成立时存在；没有提取器的正常装配不会为它伪造一次“空选择结果”。端口条件不得由一次 PDF 恰好零表的运行结果反推。

用户保存的是绑定，不是接口或边：

```python
ParameterValue: TypeAlias = "None | bool | str | int | Decimal | tuple[ParameterValue, ...] | dict[str, ParameterValue]"

@dataclass(frozen=True)
class PluginBinding:
    slot_id: str
    binding_id: str
    plugin_id: str
    parameters: dict[str, ParameterValue]
    order: int

@dataclass(frozen=True)
class SavedConfiguration:
    schema_version: Literal["assembly_config_v3"]
    name: str
    bindings: tuple[PluginBinding, ...]
    created_at: str  # RFC3339

@dataclass(frozen=True)
class DefaultConfigurationPointer:
    schema_version: Literal["default_config_v3"]
    name: str
```

`order≥0`，同一 multi 槽内严格唯一且决定结果收集顺序，one 槽固定为 0；`parameters` 必须严格符合所选插件 schema。`bindings` 按槽拓扑与 order 规范排序。`name` 遵循已批准需求的大小写折叠唯一规则；默认指向必须指向已保存且校验通过的完整配置，更新指向不改写该配置。`created_at` 只作审计，不进入指纹。

运行时绑定只在内存中存在：

```python
@dataclass(frozen=True)
class ResolvedPlugin:
    slot_id: str
    binding_id: str
    plugin_id: str
    interface_id: str
    contract_version: str
    instance_handle: object  # 仅内存；实际类型由 interface_id 对应端口校验

@dataclass(frozen=True)
class RuntimeBinding:
    configuration_name: str
    index_identity: IndexIdentity
    resolved_plugins: tuple[ResolvedPlugin, ...]
    resource_scope_id: str
```

`ResolvedPlugin` 由装配根在全部静态校验通过后创建；slot/binding/plugin/interface/contract 与注册表及配置逐项一致，`instance_handle` 必须实现 `interface_id` 的端口协议，不序列化、不暴露给业务模型。`RuntimeBinding` 只在内存中存活，作用域在命令或 chat 会话结束时按创建逆序关闭；单文档 Docling 转换是子作用域资源，在该文档构建结束时释放，不被插件长期持有。若创建中途失败，已创建实例逆序清理并返回原阶段错误，不产生半份 RuntimeBinding。

每个插件 `parameters` 的准确 schema 由对应领域子契约定义；无字段的参数对象为 `{}`。查询 K 是命令级或默认运行设置，不再在 SavedConfiguration 中复制一份 `query_settings`。用户在配置中不能增删 `InterfaceDefinition`、`SlotDefinition`、`DependencyEdge`。内置配置与用户配置用同一 SavedConfiguration schema；内置只额外受不可覆盖保护，不需要 `preset_kind` 参与算法。`created_at` 只作审计，不进入 fingerprint。

首批公共接口契约版本为 `1.0`；所有内置插件注册显式支持 `1.0`，不能仅凭 Python 方法名匹配。首批插件 `parameter_schema_id` 为 `<plugin_id>.params.v1`，无参数插件也注册空对象 schema；每次配置加载按该 schema 拒绝多余字段。`binding_id` 在槽内唯一，单绑定取 `default`，multi 表格绑定取固定工具 ID；更换同槽插件仍保持独立的 binding_id，不能把绑定顺序当插件身份。

两份内置配置的槽绑定作为安装时登记数据，不能由名称分支生成。下表同一格中逗号分隔的是 multi 绑定，顺序为执行收集顺序；未列出的条件槽均不接。

| 插槽 | `plain_text` | `structured` |
| --- | --- | --- |
| Registry | `registry.local_pdf` | 同左 |
| Indexer | `indexer.incremental` | 同左 |
| DocumentProcessor | `processor.composed_v1` | 同左 |
| MainParser | `parser.pymupdf_pages`，规则 `pymupdf_text_v1` | `parser.docling_layout`，规则 `docling_layout_v1`/mapper `docling_mapper_v2`，OCR 关闭、accurate 表格与 cell matching 开启、页图关闭 |
| TableExtractor | 未接 | `extractor.pymupdf` 三策略、`extractor.camelot` 四策略、`extractor.docling` accurate、`extractor.unstructured` hi_res，按该顺序 |
| TableSelector | 禁止接 | `selector.table_v1`，规则 `table_selection_v1` |
| PdfEvidenceReader | 禁止接 | `pdf_evidence.pymupdf`，只读区域原文与页面几何事实 |
| DocumentAssembler | `assembler.composed_v1`，`table_content_enabled=false` | 同插件，`table_content_enabled=true` |
| TableContentPreparation | 禁止接 | `table_content.serial_v1` |
| DocumentComposer | `assembler.ordered` | 同左 |
| HeaderDetector / TableSerializer | 未接 | `header.table_v1` / `serializer.table_text_v1` |
| Chunker | `chunker.page_characters`，size=300、overlap=50、规则 `recursive_character_v1` | `chunker.structured_tokens`，max=512、overlap=32、规则 `structured_chunk_v1`，文本规则 `document_text_v1` |
| ArtifactBuilder / ArtifactStore | `artifacts.stage_snapshot_v3` / `store.local_artifacts` | 同左 |
| Embedder | `embedder.e5_small`，model=`intfloat/multilingual-e5-small`、revision=`614241f622f53c4eeff9890bdc4f31cfecc418b3`、dimension=384 | 同左 |
| Publisher / VectorStore / ManifestStore / RecoveryStore | `publisher.journaled` / `store.chroma_cosine` / `store.json_manifest` / `store.local_recovery` | 同左 |
| IndexHealth / SourceProbe / Recovery | `health.strict` / `probe.pymupdf` / `recovery.journaled` | 同左 |
| Retriever / PromptBuilder / LanguageModel / Chatbot | `retriever.semantic_stable_topk` / `prompt.grounded`（single_page） / `llm.openrouter` / `chatbot.single_turn` | 同左，PromptBuilder 使用 page_set |
| Evaluator / Repository / Preflight / Calculator / Reporter / RunStore | `evaluator.formal` / `eval.local_reviewed` / `eval.preflight_v1` / `eval.evidence_metrics` / `eval.json_html` / `eval.immutable_local` | 同左 |

用户配置可以选择通过第 3 节全部装配规则的插件；首批仅上述两份完整内置装配承诺与历史结果等价。A01 禁止的解析—分块交叉组合不能通过静态检查；其他合法自定义组合是否适用于正式对比，另须满足数据集与索引兼容性要求。环境路径、OpenRouter key 和 query K 不固化在构建投影；通过运行配置提供。为保留既有环境参数能力，内置配置允许 `CHUNK_SIZE`/`CHUNK_OVERLAP`、结构化 token 上限/overlap、embedding 模型名/revision 的显式环境覆盖；该覆盖只形成本次不可变有效配置，不原地改写已保存预设，且必须产生新的构建投影和 IndexIdentity，旧索引不被重解释。内置配置显式 `OPENROUTER_MODEL` 覆盖只改变本次 LanguageModel 绑定与问答运行记录，不改变构建投影或 IndexIdentity。CLI 展示有效构建身份和不兼容重建提示。自定义配置的构建参数与 LanguageModel 参数以保存值为准，不受上述内置默认覆盖暗中改变。

首批插件的参数 schema 由插件定义注册，但以下有效字段是构建身份必须覆盖的全集；未列插件使用空对象 `{}`，查询插件参数另按[检索问答契约](retrieval-answering.md)记录。

| 插件 | 参数字段与约束 |
| --- | --- |
| `parser.pymupdf_pages` | `rule_version="pymupdf_text_v1"` |
| `parser.docling_layout` | `rule_version="docling_layout_v1",mapper_version="docling_mapper_v2",do_ocr=false,do_table_structure=true,table_mode="accurate",do_cell_matching=true,generate_page_images=false,docling_version="2.121.0"` |
| `extractor.pymupdf` | `strategies=(lines,lines_strict,text)`，不可重复或重排；`extractor.camelot` 为 `(lattice,stream,network,hybrid)`；`extractor.docling` 为 `(accurate)`；`extractor.unstructured` 为 `(hi_res)` |
| `selector.table_v1` | `rule_version="table_selection_v1"`，四指标权重 `(0.25,0.25,0.25,0.25)`，工具同分顺序 `pymupdf,camelot,docling,unstructured`，页面容差 `0.000001pt`、页尺寸容差 `1.0pt`、边界聚类容差 `2.0pt`、比较 epsilon `1e-12`、候选覆盖 `0.65`、slot 覆盖 `0.77`；字段命名分别为 `metric_weights,tool_tiebreak_order,page_bounds_tolerance_pt,page_size_tolerance_pt,boundary_cluster_tolerance_pt,comparison_epsilon,candidate_coverage_minimum,slot_coverage_minimum` |
| `assembler.composed_v1` | `table_content_enabled: bool`；true 时接入表格内容准备并验证每 slot 结构，false 时禁止接表格内容准备且不接受产生表格 slot 的主解析器 |
| `assembler.ordered` | 无参数 `{}`；按主文档阅读顺序和 slot 关联结果组装唯一的 ParsedDocument |
| `header.table_v1` / `serializer.table_text_v1` | 规则版本分别为 `table_header_v1`、`table_text_v1`；前者 `sample_row_budget=8,minimum_independent_observations=2` |
| `chunker.page_characters` | `chunk_size_characters: positive int,chunk_overlap_characters: int [0,size),rule_version="recursive_character_v1",separator_version="legacy_default_v1"` |
| `chunker.structured_tokens` | `maximum_input_tokens: positive int,text_overlap_tokens: int [0,max),rule_version="structured_chunk_v1",separator_version="recursive_boundaries_v1",document_text_rule_version="document_text_v1"` |
| `artifacts.stage_snapshot_v3` | 无用户参数 `{}`；注册的 `snapshot_rule_version="stage_snapshot_v3"` 固定[产物契约](processing-artifacts.md#2-目录角色与条件)的角色集合、文件名、JSON/HTML 生成和回读规则，参与构建投影 |
| `embedder.e5_small` | `model_name,revision: nonempty str,vector_dimension: positive int,normalize_embeddings=true,passage_prefix="passage: ",query_prefix="query: ",add_special_tokens=true`；模型本身 512-token 截断行为不得伪装为 `truncation=false` 参数：字符分块按既有路径保留该事实，结构化分块在编码前保证完整 passage 不超限。结构化 Chunker 内部 tokenizer 的模型名、revision、passage 前缀、special-token 行为必须与其一致 |

首批 `selector.table_v1` 的九项规则参数与 `header.table_v1` 的两个观察参数只接受表中明确列出的值；它们保存于有效配置和构建投影以便审计，但当前版本不允许通过自定义配置改变[表格选优](../requirements/table-selection.md)或[表头](../requirements/table-header.md)的固定业务阈值。若需调整，应先版本化规则与需求，不能让已保存参数虽通过 schema 却在运行中被忽略。

插件实现版本和会影响输出的第三方依赖版本由注册表提供而非用户随意填入；同一已保存配置在注册版本变化且构建结果可能改变时必须获得新指纹并提示重建。不能通过仅修改显示名称绕过参数校验。

## 8. 构建身份算法

先静态校验和展开全部插件默认参数，再按 slot ID、binding order、plugin ID 的确定顺序构成 build projection。每条参与构建的绑定包含 `slot_id,binding_id,order,plugin_id,implementation_version,interface contract_version,parameter_schema_id,effective build parameters,build_rule_versions`；再包含公共文档、快照角色集合、向量 metadata schema、E5/tokenizer 不可变模型身份及持久化格式版本。ArtifactBuilder 必接绑定、阶段快照 schema/文件角色规则与会改变必需 HTML 字节或校验结果的渲染规则版本属于构建投影，不能拿旧索引冒充具备新快照。只包含能改变解析正文、来源、判定、chunk、向量或必需持久化输出的有效参数；不包含 NAME、创建时间、目录绝对路径、日志、可选 diagnostics、K、Prompt 或 LLM。依赖包版本仅在其变化会改变输出或存储格式且经过注册声明时参与，不把普通代码搬移或每次安装时间纳入。

投影序列化为 UTF-8 JSON：对象键按 Unicode 码点序，数组保留声明顺序，禁用 NaN/Infinity，整数与浮点参数按 schema 中的规范类型序列化，字符串不做隐式 trim，尾部无额外空白。计算 `sha256(canonical_json_bytes).hexdigest()` 作为完整构建 fingerprint；短显示值只能用于界面和路径，可碰撞时以完整值判别。配置文件中若显式保存了期望 fingerprint，加载时重算不一致即拒绝。相同投影必须得到相同指纹，包含查询侧差异的两配置可共用索引；默认指向变化不改变投影。

投影是构建配置的规范化结果，而非运行实例：

```python
@dataclass(frozen=True)
class BuildProjectionBinding:
    slot_id: str
    binding_id: str
    order: int
    plugin_id: str
    implementation_version: str
    contract_version: str
    parameter_schema_id: str
    parameters: dict[str, ParameterValue]
    rule_versions: dict[str, str]

@dataclass(frozen=True)
class BuildProjection:
    projection_schema: Literal["build_projection_v3"]
    interface_contract_major: Literal[1]
    document_schema_version: str
    chunk_schema_version: str
    artifact_schema_version: str
    vector_metadata_schema_version: str
    manifest_schema_version: str
    bindings: tuple[BuildProjectionBinding, ...]
```

五个领域 schema 版本字段非空；`artifact_schema_version` 首批固定为 `snapshot_manifest_v3`，ArtifactBuilder 的 `rule_versions` 固定第 2 节文件角色与审核页规则。bindings 只含构建侧有效绑定，按 slot_id 码点序、order、binding_id 排序；order 非负并保留 multi 槽的有效执行顺序。每项标识非空，parameters 只含插件 schema 标为 build 的有效字段，rule_versions 的键按码点序排列且值为非空版本字符串。created_at、配置 NAME、查询/Prompt/LLM 绑定、绝对路径及评估设置不进入投影。存储/发布插件改变持久化字节布局时也属构建侧；纯运行实现版本不进入投影。字段的 build/query 分类由第 7 节参数表和对应子契约固定，注册表不得自行改动已发布 schema。

规范化先从 JSON 用十进制精确数读取参数，再按参数 schema 校验类型和范围；布尔不视作整数。整数用十进制 JSON integer（禁止 `-0`），小数在投影中编码为**十进制字符串**：有限值，去尾随零和无意义小数点，不用指数形式，负零归零，例如输入 `0.6500` 归为 `"0.65"`。不得经过二进制 float 后再格式化；不接受 NaN/Infinity 或小数 schema 中的字符串伪装值。其他字符串原样保留，枚举大小写精确匹配；对象键按 Unicode 码点序，JSON 转义按 `ensure_ascii=false` 的 UTF-8 输出，紧凑分隔符 `,`/`:`，不输出 BOM 和末尾换行。参数 schema 的默认值在投影前显式展开；默认值变化因此改变指纹。读取已保存配置时重算完整指纹，若与 manifest 不同，不复用旧索引；查询侧变化不改变这个比较。短 collection 名碰撞由[存储契约](storage-publication.md)按完整指纹拒绝。

## 9. 实现一致性门

- 将机器槽 ID、C1/C2 条件谓词、A01—A09 跨槽规则、`1.0` 契约支持集合及两份预设绑定表转成可校验注册数据；上方端口依赖表须成为唯一方向权威，不允许复制另一份。
- 将上文参数 schema 与 build/query 分类、共享索引引用与清理语义转成可执行校验，并对浮点规范化做跨 Python 版本验证。
- 验证配置与默认指向原子保存、Windows 文件替换与 Chroma 快照行为。
- 以固定输入执行两份预设绑定，逐项对照既有输出基线。

上述项目均已由本契约给出确定目标，是实现测试清单而非留给编码者的设计选择；若结果与本契约矛盾，先修订架构和受影响需求，不让代码成为另一份权威。
