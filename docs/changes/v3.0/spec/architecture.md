# Minimal RAG V3.0 总架构

状态：**V3.0 架构已获用户审核，并通过[规格级编码就绪复审](architecture-review.md)。** 对应已审核的[总需求](requirements.md)。本文按工作线与数据交接解释整个系统；字段级模型和判定规则只在链接的子契约中定义。[审核图](../system-architecture-review.html)用于查看接口拓扑，不代替正式契约。

## 审核阅读顺序

请按下面五个问题审核，不必从九份子契约的第一页顺读到最后一页：

1. **切分是否对？** 先看第 1—4 节的四条工作线与阶段交接，再看[审核图](../system-architecture-review.html)中的大接口、小接口及方向。
2. **装配是否会产生非法组合？** 看[装配契约第 2—3 节](architecture/assembly-contracts.md#2-接入与单份连接关系)的端口边、C1/C2 和 A01—A09；共同数据类型不等于语义兼容。
3. **交接的数据是否说清？** 第 2、3 节列出每阶段交付物；从第 6 节的地图进入唯一字段级契约。审核图的悬停内容是阅读辅助，不能代替字段权威。
4. **失败会留下什么？** 看第 2、4、5 节，再审核[存储发布](architecture/storage-publication.md)和[运行健康](architecture/runtime-contracts.md)的生效点、回滚及恢复作用域。
5. **能否开始编码？** 最后看[架构审查记录](architecture-review.md)的已通过结论，并按[技术验证](technical-validation.md)区分编码前可行性与实现验收门。

## 1. 系统边界与四条工作线

V3.0 只有一套 CLI、接口契约、插件注册、装配校验与存储发布机制。`plain_text`、`structured` 是已保存的装配数据，不是旧运行分支；V2.0 产物只读保留供基线核对，V3 runtime 不调用旧代码。输入为本地数字型 PDF、已保存配置、命令参数、经审核的评估数据及问答凭据。外部依赖包括 PyMuPDF、Docling、Camelot、Unstructured、E5/tokenizer、Chroma、OpenRouter 和本地文件系统；SDK 对象不得成为公共模型。
两份内置装配的显式构建环境覆盖只生成本次有效配置和新构建身份；显式 `OPENROUTER_MODEL` 只改变问答运行模型，不进入构建指纹。字段及覆盖范围由[装配契约](architecture/assembly-contracts.md)和[检索问答契约](architecture/retrieval-answering.md)定义。

| 工作线 | 起点 → 终点 | 写入边界 |
| --- | --- | --- |
| 入库 | PDF 与装配配置 → 已发布 manifest/向量/必要产物 | Indexer 编排，只有 Publisher 使新业务索引生效。 |
| 检索与问答 | 已发布索引与问题 → 命中或回答 | 先过健康门；Retriever/Chatbot 只读索引。 |
| 运维与恢复 | 源、索引及事务证据 → 健康事实或经确认的修复结果 | IndexHealth 只读；Recovery 仅获授权后写入。 |
| 正式评估 | 审核数据、目标索引与装配身份 → 不可变评估运行 | 调用同一个 Retriever；报告不反向成为业务数据源。 |

CLI 装配根解析参数、静态校验、按命令创建资源并管理生命周期。领域接口不依赖具体工具；适配器负责把 SDK 事实转换成公共契约。一次命令或 chat 会话中的装配不可变。插槽和单向连接边只在[装配契约](architecture/assembly-contracts.md)定义。

## 2. 入库主线：从源事实到 active 索引

```text
DocumentRegistry → Indexer → DocumentProcessor → Chunker → Embedder → Publisher
                                  │                 │
                                  └──────→ ArtifactBuilder ←──────┘  [必需派生支路]
```

ArtifactBuilder 是每次入库必接的审核产物派生支路，不替代正文处理；全部适用阶段文件暂存成功是后续编码和发布的前置。图只展示主职责；完整端口、边界映射与依赖边以[装配契约](architecture/assembly-contracts.md#2-接入与单份连接关系)为准。

| 阶段 | 接收 → 交付 | 职责与直接消费者 | 失败及持久化影响 |
| --- | --- | --- | --- |
| DocumentRegistry | 目录/选择请求 → `SourceDocument` 集合 | 安全发现 PDF，确定路径与身份；Indexer、Health 使用。 | 不写索引；源身份无效不得解析。 |
| Indexer | 源集合、目标装配、入库请求 → `IngestResult` | 决定范围、增量、force/prune，编排其余入库接口；不计算表格评分。 | 目标配置独立提交；预构建失败不得破坏既有 active。 |
| DocumentProcessor | 源/构建上下文 → `ProcessingResult`，含 `ParsedDocument` | MainParser 与可选 TableExtractor 读取同源 PDF；条件 TableSelector 作选择；必接 DocumentAssembler 交付完整文档。详见[处理契约](architecture/document-processing.md)。 | 主解析、选择或组装阻断时不交付部分文档。 |
| Chunker | 完整文档/分块上下文 → `ChunkBatch` | 按所选插件生成稳定正文、ID 与来源；供 ArtifactBuilder、Embedder、Publisher。 | 来源覆盖、边界、身份不成立则停止该文档。 |
| ArtifactBuilder | 同次 `ProcessingResult`、`ChunkBatch` 与已验证 `BuildProjection` → `StagedArtifacts` | 必需派生支路按实际接入阶段生成 JSON 与审核 HTML；装配投影只用于核对插件/绑定身份和适用角色，不重做处理；文件契约见[产物架构](architecture/processing-artifacts.md)。 | 任一适用产物失败阻止发布；不改变 active。 |
| Embedder | chunks/编码上下文 → `PassageEmbeddingBatch`；问题/索引上下文 → `QueryEmbedding` | 同一模型身份的 passage/query 两种操作；分别供 Publisher、Retriever。 | 不直接写索引；数量、身份或维度不匹配即阻断。 |
| Publisher | 核验后的正文、向量、暂存产物与事务上下文 → `PublicationResult` | 协调向量、manifest、恢复及必接产物端口；manifest 原子保存是新 active 生效点。 | 保留回滚/恢复证据，不发布部分结果。 |

`PrimaryDocument` 与候选报告是转换后的事实，不被原地写入 winner；`ContentResolution` 是选择结论；`ParsedDocument` 是系统认定的完整文档；`ChunkBatch` 是向量化及持久化输入。模型及稳定 ID 见[公共文档契约](architecture/document-contracts.md)与[表格契约](architecture/table-contracts.md)。统一模型不意味着任意插件可互插：跨阶段语义兼容须在保存配置前静态判定，见[装配兼容规则](architecture/assembly-contracts.md#3-跨插件兼容性)。

## 3. 检索与问答：只消费已发布事实

```text
索引身份 + 问题 → IndexHealth → Retriever〔query Embedder + VectorReader〕
                              ├─ search → 有序命中
                              └─ Chatbot〔PromptBuilder → LanguageModel〕→ ask/chat 回答
```

Retriever 以问题、K、可选显式文档范围及索引身份生成 `RetrievalResult`。它先过健康门，再编码 query、读取同一索引并应用确定性同分排序；正常零命中不是故障。Chatbot 以实际命中构造证据消息并调用 LLM，证据不足拒答与服务失败分开。二者不读取暂存产物、历史 V2.0 数据或未命中正文，不自动切换配置。请求、命中、消息、回答及 embedding 身份见[检索问答契约](architecture/retrieval-answering.md)。

## 4. 运维恢复与正式评估

IndexHealth 从源、manifest、向量、必要产物和 journal 读取事实，形成 `HealthReport`；pending journal 先组合级阻断。Recovery 只在明确授权和可核验事务证据下修复，随后重新健康检查；失败保留证据，不以 force 掩盖。命令的最小资源图、引导和机器状态见[运行契约](architecture/runtime-contracts.md)，事务、生效点与回滚见[存储契约](architecture/storage-publication.md)。

Evaluator 从审核数据与目标构建映射出发，依次经过 Repository → Preflight → 同一 Retriever → Calculator → Reporter → RunStore。Preflight 核对每条 excerpt 的独立映射及组级完整组合；Calculator 只以完整组组合计分。它只写独立、不可变的评估运行，评估判断不能修改索引。字段、原因码与报告发布见[评估契约](architecture/evaluation-contracts.md)。

## 5. 跨线约束

- **装配先于资源使用。** 校验版本、参数、接入条件与数量、图闭合、跨插件兼容性和下游可完成性；静态失败时不打开业务 PDF、向量库或模型。CLI 向导与配置加载调用同一校验器。
- **构建身份不同于配置名称。** fingerprint 只覆盖影响正文、来源、判定、chunk、向量或持久化输出的有效绑定/规则；K、Prompt、LLM、日志和展示不触发重建。不同名称仅在构建身份及存储 schema 兼容时共享物理索引。
- **读写边界清楚。** 只有 Publisher 使新 active 生效；查询和健康只读，修复需授权；批量入库每个目标独立提交和汇总。
- **状态可机器判断。** 未装配、未执行、失败、成功零结果、规则拒绝与降级完成是不同事实；message 只供人阅读。
- **保留旧效果而非旧运行模式。** 两份内置装配分别保留已批准的正文、来源、稳定 ID、检索及问答效果；切换入口前仍须真实端到端等价验证。

这些规则的字段级权威依次是[装配](architecture/assembly-contracts.md)、[文档](architecture/document-contracts.md)、[运行](architecture/runtime-contracts.md)及[存储](architecture/storage-publication.md)。

## 6. 代码依赖与子契约地图

V3 代码统一置于 `rag/v3/`：`contracts/` 放模型、枚举和接口；`application/` 放阶段编排；`adapters/` 实现外部依赖；`plugins/` 放注册声明与 factory；`cli/` 是组合根和引导。依赖方向是 contracts ← application、contracts ← adapters，由 plugins/cli 装配；领域契约不导入 SDK，application 不导入具体适配器。`rag/v2` 不得被 V3 runtime import；纯函数不因插件化强行变成插件。测试置于 `tests/v3/`，覆盖契约、适配器、事务故障注入和端到端等价。

| 想审核的问题 | 唯一权威子契约 |
| --- | --- |
| 插槽、边、条件、插件兼容性、配置及构建指纹 | [装配](architecture/assembly-contracts.md) |
| 源、主解析事实、最终文档、Chunk 与来源 | [公共文档](architecture/document-contracts.md) |
| 解析、组装与分块阶段行为 | [文档处理](architecture/document-processing.md) |
| 表格提取、选优、表头与文本化 | [表格](architecture/table-contracts.md) |
| 审核产物暂存 | [处理产物](architecture/processing-artifacts.md) |
| passage/query 编码、检索、消息及回答 | [检索问答](architecture/retrieval-answering.md) |
| active 存储、发布与恢复 | [存储发布](architecture/storage-publication.md) |
| CLI、健康、交互与错误 | [运行健康](architecture/runtime-contracts.md) |
| 审核数据、评估运行与不可变报告 | [评估](architecture/evaluation-contracts.md) |

当前编码就绪状态以[架构审查记录](architecture-review.md)为准。第三方实测见[技术验证](technical-validation.md)；它只决定适配方式，不代替可见行为审核。
