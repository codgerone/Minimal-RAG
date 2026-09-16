# 文档导航

当前代码仍使用旧纯文本 RAG 链路，V2.0 需求已经整理但尚未实现。

## 当前有效需求

| 文档 | 回答的问题 | 权威内容 | 建议读者 |
| --- | --- | --- | --- |
| [完整系统需求](requirements.md) | V2.0 完成后整个系统必须做什么？ | 系统范围、端到端主链、跨阶段规则、状态与系统级验收 | 产品、架构、实现与测试人员；阅读入口 |
| [表格提取、准入、分组与选优](requirements/v2.0/table-extraction-selection.md) | 多工具候选如何产生并确定 winner？ | 策略、坐标、准入、分组、评分公式、阈值和决胜 | 表格领域实现与测试人员 |
| [表头判定](requirements/v2.0/table-header-detection.md) | 何时可以认定表头？ | 判定顺序、类型词法、原因码、参数和已知限制 | 表头与表格文本化实现人员 |
| [结构表格文本化](requirements/v2.0/table-text-serialization.md) | 结构表如何变成唯一检索正文？ | 字段路径、合并格、空白/缺失和确定性文本语法 | 文本化、分块与审核视图实现人员 |
| [普通文本、List 文本化与 V2 分块](requirements/v2.0/document-text-and-chunking.md) | 非表格节点及超限结构如何分块？ | 正文格式、组合边界、overlap、退化和终止规则 | Chunker、tokenizer 与测试人员 |

总需求是系统行为入口；四份子需求分别是对应复杂规则的唯一权威。增量提案和历史文档不能补充或改写这些现行规则。

## 当前系统基线

- [V1.0 需求](history/requirements/v1.0-minimal-rag-requirements.md)与[V1.0 架构](history/architecture/v1.0-minimal-rag-architecture.md)。
- [V1.1 索引健康与引导恢复需求](history/requirements/v1.1-index-health-guided-recovery.md)与[V1.1 架构补充](history/architecture/v1.1-index-health-guided-recovery-architecture.md)。

这些文件记录升级前版本，当前有效需求以 `requirements.md` 及其子文档为准。完成当前架构文档后再整体迁入 `history/`，避免在架构尚未完成时破坏旧链接。

## 当前架构

| 文档 | 回答的问题 | 权威内容 | 建议读者 |
| --- | --- | --- | --- |
| [V2.0 当前系统架构](architecture.md) | 模块如何协作完成全部需求？ | 系统边界、数据主链、模块职责、依赖方向和子契约导航 | 全体实现与评审人员；架构入口 |
| [V2.0 数据、标识与配置契约](architecture/v2.0/data-contracts.md) | 文档处理数据怎样表示和持久化？ | 公共来源、Layout/Parsed/Chunk 模型、ID、JSON、向量 metadata | 领域模型、序列化和向量适配人员 |
| [V2.0 运行与持久化模型契约](architecture/v2.0/runtime-persistence-models.md) | 构建与运行状态怎样表示？ | Settings、BuildConfig、manifest、artifact envelope、snapshot、journal、DocumentState | runtime、存储和健康检查人员 |
| [V2.0 表格领域模型契约](architecture/v2.0/table-domain-models.md) | 表格处理证据怎样跨阶段传递？ | 提取、准入、分组、评分报告的完整字段与不变量 | 表格领域实现与测试人员 |
| [Docling 2.121.0 映射规格](architecture/v2.0/docling-mapping.md) | Docling SDK 事实如何进入自有模型？ | 遍历、label/list/table/source 映射和适配失败边界 | Docling adapter 实现人员 |
| [索引、产物发布与恢复契约](architecture/v2.0/index-publication.md) | 多存储怎样原子生效并恢复？ | 发布顺序、生效点、持久化恢复、prune 和健康验证 | Indexer、Chroma 和 artifact store 实现人员 |
| [V2.0 运行时、健康检查与错误契约](architecture/v2.0/runtime-contracts.md) | CLI 怎样装配、判断可用性和传播错误？ | Registry、IndexIssue、恢复动作、异常阶段及退出码 | CLI、runtime 与集成测试人员 |
| [V2.0 编码前技术验证记录](architecture/v2.0/technical-validation.md) | 第三方行为能否支持当前架构？ | 锁定环境的实测证据、适配结论和剩余实现验证 | 架构评审、adapter 与测试人员 |

总架构只说明定位、关系和数据流；字段级定义以上表对应子契约为唯一权威。架构语义审查和四项编码前技术验证已经完成；实现契约测试及业务代码尚未开始。

## V2.0 规划

1. [增量需求](changes/v2.0/proposal.md)：已确认从 V1.1 到 V2.0 的行为变化及明确后置事项。
2. [实施清单](changes/v2.0/checklist.md)：设计与代码交付顺序、验收场景和进度。
3. 升级指南：发布前补充，说明 legacy 索引与 v1/v2 的重建和使用方式。

升级指南尚未交付。实现不得自行补充需求中仍未确定的业务规则。
