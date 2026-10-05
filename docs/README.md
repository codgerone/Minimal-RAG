# 文档导航

当前代码运行 V3.0 接口—插件—装配系统，默认选择 `structured` 装配；V2.0 文件和评估记录仅作只读历史证据。本页区分现行 V3.0 权威规格与旧版记录。

## 当前有效需求与架构：V3.0

[V3.0 规格地图](changes/v3.0/spec/README.md)是现行文档入口：其中的[总需求](changes/v3.0/spec/requirements.md)、[总架构](changes/v3.0/spec/architecture.md)及各子契约唯一地定义当前系统。该地图逐份说明回答的问题、权威内容和建议读者。[交互式架构审核图](changes/v3.0/system-architecture-review.html)仅辅助理解，不能替代正式契约。[实施与验收记录](changes/v3.0/implementation-plan.md)和[正式 V3.0 评估汇总](../validation/retrieval/system-v3.0/summary.md)给出两份内置装配的真实验证结果；[编码就绪复审](changes/v3.0/spec/architecture-review.md)记录编码前结论，不代替交付验收。

## V2.0 历史需求

| 文档 | 回答的问题 | 权威内容 | 建议读者 |
| --- | --- | --- | --- |
| [完整系统需求](requirements.md) | V2.0 完成后整个系统必须做什么？ | 系统范围、端到端主链、跨阶段规则、状态与系统级验收 | 产品、架构、实现与测试人员；阅读入口 |
| [表格提取、准入、分组与选优](requirements/v2.0/table-extraction-selection.md) | 多工具候选如何产生并确定 winner？ | 策略、坐标、准入、分组、评分公式、阈值和决胜 | 表格领域实现与测试人员 |
| [表头判定](requirements/v2.0/table-header-detection.md) | 何时可以认定表头？ | 判定顺序、类型词法、原因码、参数和已知限制 | 表头与表格文本化实现人员 |
| [结构表格文本化](requirements/v2.0/table-text-serialization.md) | 结构表如何变成唯一检索正文？ | 字段路径、合并格、空白/缺失和确定性文本语法 | 文本化、分块与审核视图实现人员 |
| [普通文本、List 文本化与 V2 分块](requirements/v2.0/document-text-and-chunking.md) | 非表格节点及超限结构如何分块？ | 正文格式、组合边界、overlap、退化和终止规则 | Chunker、tokenizer 与测试人员 |
| [检索效果评估](requirements/v2.0/retrieval-evaluation.md) | 如何建立标准证据并量化、审核和跨版本比较检索效果？ | ground truth、配置 test set、证据组映射、指标公式、报告和变更控制 | 产品、检索实现、评估与审核人员 |

以下文档保存 V2.0 的系统和领域规则，不定义现行 V3.0 行为。

## 更早版本基线

- [V1.0 需求](history/requirements/v1.0-minimal-rag-requirements.md)与[V1.0 架构](history/architecture/v1.0-minimal-rag-architecture.md)。
- [V1.1 索引健康与引导恢复需求](history/requirements/v1.1-index-health-guided-recovery.md)与[V1.1 架构补充](history/architecture/v1.1-index-health-guided-recovery-architecture.md)。

这些文件记录更早版本；V1.0、V1.1 和 V2.0 的文档、索引及已发布评估运行均保留为历史事实，不参与 V3.0 命令装配。

## V2.0 历史架构

| 文档 | 回答的问题 | 权威内容 | 建议读者 |
| --- | --- | --- | --- |
| [V2.0 当前系统架构](architecture.md) | 模块如何协作完成全部需求？ | 系统边界、数据主链、模块职责、依赖方向和子契约导航 | 全体实现与评审人员；架构入口 |
| [V2.0 数据、标识与配置契约](architecture/v2.0/data-contracts.md) | 文档处理数据怎样表示和持久化？ | 公共来源、Layout/Parsed/Chunk 模型、ID、JSON、向量 metadata | 领域模型、序列化和向量适配人员 |
| [V2.0 运行与持久化模型契约](architecture/v2.0/runtime-persistence-models.md) | 构建与运行状态怎样表示？ | Settings、BuildConfig、manifest、artifact envelope、snapshot、journal、DocumentState | runtime、存储和健康检查人员 |
| [V2.0 表格领域模型契约](architecture/v2.0/table-domain-models.md) | 表格处理证据怎样跨阶段传递？ | 提取、准入、分组、评分报告的完整字段与不变量 | 表格领域实现与测试人员 |
| [Docling 2.121.0 映射规格](architecture/v2.0/docling-mapping.md) | Docling SDK 事实如何进入自有模型？ | 遍历、label/list/table/source 映射和适配失败边界 | Docling adapter 实现人员 |
| [索引、产物发布与恢复契约](architecture/v2.0/index-publication.md) | 多存储怎样原子生效并恢复？ | 发布顺序、生效点、持久化恢复、prune 和健康验证 | Indexer、Chroma 和 artifact store 实现人员 |
| [V2.0 运行时、健康检查与错误契约](architecture/v2.0/runtime-contracts.md) | CLI 怎样装配、判断可用性和传播错误？ | Registry、IndexIssue、恢复动作、异常阶段及退出码 | CLI、runtime 与集成测试人员 |
| [V2.0 检索效果评估架构契约](architecture/v2.0/retrieval-evaluation.md) | ground truth、test set、运行事实、指标和报告怎样协作？ | 完整模型、fingerprint、运行编排、聚合、比较、发布与失败语义 | 评估实现、测试和审核人员 |
| [V2.0 编码前技术验证记录](architecture/v2.0/technical-validation.md) | 第三方行为能否支持当前架构？ | 锁定环境的实测证据、适配结论和剩余实现验证 | 架构评审、adapter 与测试人员 |
| [V2.0 正式检索效果汇总](../validation/retrieval/system-v2.0/summary.md) | 当前两条链路的正式检索效果怎样？ | 各配置及五份 PDF 的指标汇总，明细链接到不可变运行目录 | 产品、开发与效果审核人员 |
| [正式检索效果对比](../validation/retrieval/comparison.md) | 可严格比较的运行之间有何变化？ | 比较状态、主指标基线值、候选值和 delta | 版本决策与迭代复盘人员 |

这些架构文档保存 V2.0 数据流与模型；五份 PDF 的旧 Top-3 运行由 V3.0 评估比较服务只读回读。现行字段、状态、发布和失败语义以本页上方 V3.0 规格地图为准。

## V2.0 历史变更与交付记录

1. [增量需求](changes/v2.0/proposal.md)：已确认从 V1.1 到 V2.0 的行为变化及明确后置事项。
2. [实施清单](changes/v2.0/checklist.md)：设计与代码交付顺序、验收场景和进度。
3. [升级指南](upgrade-guide.md)：默认链路变化、显式 V1、系统版本存储迁移、恢复方式和已知限制。

现行实现不得从历史文档隐式继承规则；业务语义以 V3.0 已审核规格为准。
