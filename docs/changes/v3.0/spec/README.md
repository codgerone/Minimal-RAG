# V3.0 完整规格导航

状态：[V3.0 需求](requirements.md)与[架构](architecture.md)已获用户审核，现为运行系统的权威规格；两份内置装配的真实等价、审计快照、发布恢复和正式检索评估已按[实施与验收记录](../implementation-plan.md)通过。[规格级编码就绪复审](architecture-review.md)保留编码前结论，[技术验证](technical-validation.md)记录当时的技术门。旧[按解析路线列文件的讨论稿](../archive/artifact-snapshot-review.md)仅作讨论记录。设计来源包括[总体接口结构](../system-design.md)和[限定纠偏决策](../discussion-checklist.md)。

| 文档 | 回答的问题 | 权威内容 | 建议读者 |
| --- | --- | --- | --- |
| [总需求](requirements.md) | 现行 V3.0 系统必须做什么？ | 范围、输入输出、实际处理链、阶段摘要、跨阶段规则及系统级验收 | 用户、产品、架构、测试；首先阅读 |
| [装配需求](requirements/assembly.md) | 怎样选择合法插件组合？ | 接入条件、数量、预设、默认与交互式选择及构建影响行为 | 使用与集成人员 |
| [文档处理需求](requirements/document-processing.md) | 主解析与局部补充怎样形成完整文档？ | 纯文本/Docling 解析、汇合、回退、失败边界 | 文档处理人员 |
| [表格提取与选优](requirements/table-selection.md) | 多工具候选如何确定 winner？ | 四工具九策略、准入、分组、评分和原因 | 表格规则及测试人员 |
| [表头判定](requirements/table-header.md) | 何时可认定表头？ | 判定顺序、类型词法、阈值与原因 | 表头与文本化人员 |
| [表格文本化](requirements/table-text.md) | 结构表格如何成为检索正文？ | 文本语法、合并格、空白/缺失与行来源 | 文本化和分块人员 |
| [分块需求](requirements/chunking.md) | 两种能力怎样生成 chunk？ | 字符分块、结构化文本、超限、overlap 与来源 | 分块人员 |
| [处理产物](requirements/processing-artifacts.md) | 什么必须保存，审核 HTML 展示什么？ | 阶段快照、按 slot/逐块审核与失败阻断 | 审核与存储人员 |
| [索引生命周期](requirements/index-lifecycle.md) | 如何增量构建、发布、回滚和 prune？ | 编码、跳过/重建、发布生效、故障恢复与 D-04 | 索引与故障测试人员 |
| [运行操作](requirements/operations.md) | 命令、健康、修复、凭据如何表现？ | 参数、状态、修复优先级、错误/debug 与 D-03 | CLI 与恢复人员 |
| [检索效果评估](requirements/retrieval-evaluation.md) | 如何标注标准证据和比较效果？ | 数据集、可接受集合、指标公式、报告及 D-05 | 用户、评估与审核人员 |
| [总架构](architecture.md) | V3.0 的数据、接口与模块怎样协作？ | 系统边界、全链数据流、大接口、依赖与持久化约束 | 开发、架构、评审人员 |
| [装配架构契约](architecture/assembly-contracts.md) | 接口、插槽、插件、配置和绑定如何分离？ | 装配模型、校验顺序、唯一连接、参数与身份 | 装配和配置实现人员 |
| [公共文档契约](architecture/document-contracts.md) | 源、主解析、完整文档及 chunk 如何表达？ | 身份、节点、来源、正文、稳定 ID | 所有数据链实现人员 |
| [文档处理契约](architecture/document-processing.md) | 解析、组装和分块怎样经接口协作？ | 内部 DAG、资源共享、适配与失败边界 | 解析与分块实现人员 |
| [检索问答契约](architecture/retrieval-answering.md) | 已发布 chunk 怎样进入检索和回答？ | 健康门、编码身份、同分排序、消息与 LLM 边界 | 检索与问答实现人员 |
| [处理产物契约](architecture/processing-artifacts.md) | 审核产物如何构建且不反向影响正文？ | 文件角色、快照清单、暂存/回读与发布前失败 | 审核与产物实现人员 |
| [表格契约](architecture/table-contracts.md) | 多工具候选如何进入统一表格事实和选择结论？ | 候选、slot、选优证据、表头/文本化接口与坐标转换 | 表格插件实现人员 |
| [存储发布契约](architecture/storage-publication.md) | 多存储如何一致发布和恢复？ | 端口、manifest、journal、提交点和回滚 | 索引与恢复实现人员 |
| [运行契约](architecture/runtime-contracts.md) | 命令如何选择配置、判断健康和取得修复授权？ | CLI 资源图、状态、修复与凭据边界 | CLI 与运行实现人员 |
| [评估契约](architecture/evaluation-contracts.md) | 标准证据如何通过同一检索器生成不可变报告？ | 评估服务链、模型层次、发布状态 | 评估实现人员 |
| [架构自查](architecture-review.md) | 设计是否足以指导编码，实施边界是什么？ | AGENTS.md 编码就绪逐项结论；不是字段权威 | 评审与实施规划人员 |
| [技术验证门](technical-validation.md) | 哪些第三方行为已实测，哪些须随实现验收？ | 环境、命令、实测证据、结论与组件完成门 | 适配器和存储实现人员 |

需求子文档均回链总需求阶段；总需求逐阶段摘要并链接子文档。架构子文档已建立字段、封闭原因码、序列化、外部适配、发布恢复与实现验收契约。实现不得从旧架构文档隐式继承规则；发现契约缺口时先修订规格。
