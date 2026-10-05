# V3.0 编码就绪复审

状态（2026-09-25）：**规格级编码就绪，通过。** 用户已审核并同意当前 V3.0 需求与架构；本记录检查的是能否按规格开始编码，不宣称 V3.0 已实现或已通过等价验收。字段和规则仍以[总需求](requirements.md)、[总架构](architecture.md)及各子契约为权威，本记录不另定义模型。V2.0 运行入口在切换交付前仍是现行代码事实；V3.0 交付后不得并存 V2 运行路径，但已产出的 V2 文件与评估报告只读保留。

## 1. 本轮已关闭的规格缺口

| 检查点 | 关闭证据 |
| --- | --- |
| 阶段快照与人工审核页 | [产物需求](requirements/processing-artifacts.md)与[产物契约](architecture/processing-artifacts.md)按接口阶段而非预设路线定义文件、缺席条件、slot 分区和逐 chunk 页面；旧讨论稿移入[归档](../archive/artifact-snapshot-review.md)。 |
| 大接口、内部槽和连接 | [装配契约第 2 节](architecture/assembly-contracts.md#2-接入与单份连接关系)给出首批槽 ID 全集、required/optional/conditional 与 one/multi、C1/C2、完整业务处理边、端口模型及边界映射/owner 构造；Store/Reader 方法族不伪造为处理阶段。`PdfEvidenceReader.read_page_words` 的事实模型与缺失语义见[表格契约](architecture/table-contracts.md#1-内部接口与依赖)。 |
| 跨插件兼容 | [A01—A09](architecture/assembly-contracts.md#3-跨插件兼容性)集中登记版本、参与槽、参数路径、首批允许对、失败原因和完整/部分配置的校验顺序；内置通过、交叉解析—分块失败及 A02—A09 负例列为固定矩阵，CLI、保存、加载与批量入库不得分叉。 |
| 关键数据模型 | [文档](architecture/document-contracts.md)、[处理](architecture/document-processing.md)、[表格](architecture/table-contracts.md)、[检索问答](architecture/retrieval-answering.md)、[产物](architecture/processing-artifacts.md)、[存储](architecture/storage-publication.md)、[运行](architecture/runtime-contracts.md)、[评估](architecture/evaluation-contracts.md)均按创建/消费顺序给出字段、可空条件、状态和失败影响。表格 `StrategyExecution` 五态与现有 `rag/v2/table_models.py` 对照；评估的十项聚合指标、历史运行字段与 `rag/evaluation_models.py` 对照。新公共 schema 不要求沿用旧 `pipeline_id` 字段，但必须保留两种旧能力的用户可见效果。 |
| 评估历史与发布 | [评估契约](architecture/evaluation-contracts.md)增加跨版本只读 `ComparisonRunSnapshot`、完整 `comparison.json` 模型、显式基线选择和版本级文件的恢复 journal；V2 报告只能作为比较输入，不调用 V2 检索实现。两份现存 completed V2 运行的 aggregate、逐文档 JSON/HTML 哈希已在本工作区只读回算通过。报告路径已按需求统一为 `validation/retrieval/`。 |
| 审核图与导航 | [审核图](../system-architecture-review.html)是正式契约的交互式摘要，删除虚构阶段模型并补齐评估比较输入；脚本语法检查通过。V3.0 的 32 份 Markdown（含入口）本地链接目标、章节锚点和代码围栏检查无误；[规格地图](README.md)区分唯一权威、审核图、技术验证和归档。 |

## 2. AGENTS.md 编码就绪清单

| 问题 | 结论 |
| --- | --- |
| 编码者是否仍需发明影响结果的业务规则？ | **否。** 主链、表格阈值、分块、检索排序、发布恢复、快照和评估公式各有唯一需求/架构位置；新发现的业务缺口仍必须先修订规格。 |
| 是否有仅有名称而无字段/不变量的关键模型，或普通字符串承担状态、策略、原因？ | **否。** 关键持久化/交接模型和封闭枚举已落在各子契约；原生 SDK 事实经适配器转换，`instance_handle: object` 仅限不序列化的内存运行绑定。 |
| 可空字段、状态触发及下游行为是否确定？ | **是。** 未接槽、执行失败、正常空结果、拒绝与降级分开；存储与评估完成标记必须经回读，错误不靠人类 message 分支。 |
| 输入事实—判断—结果—持久化因果链是否闭合？ | **是。** 入库业务边与 owner 构造分离；表格选择证据、段落/向量身份、manifest/产物生效点、检索健康门及评估派生报告均有上下游与失败影响。 |
| 是否存在双份权威、改名摘要或与历史效果的已知冲突？ | **未发现。** HTML 只用于审核；正式字段由子契约定义。解析—分块只允许两组已批准能力，V3 架构不保留 V2 运行分支；真实等价仍须 T-04 证明。 |
| 关键规则能否直接形成正常、边界与失败测试？ | **是。** A01—A09、条件 C1/C2、快照角色、表格状态、索引故障注入及评估 invalid/failed/completed 都有可判定输入和禁止结果。 |
| 链接、术语、代码围栏、历史引用和导航是否已检查？ | **是。** 本地机械检查 32 份 Markdown 无缺失目标、无失效章节锚点、无未闭合代码围栏；旧快照讨论稿在 archive，不再列为待审规格。 |

## 3. 编码后的完成门，不是编码前阻断

[技术验证](technical-validation.md)的 T-01—T-03、T-05—T-07、T-09 已在隔离环境实测，给出 SDK、向量、文件系统和原生证据的可行适配边界。T-04 真实 V3 两份内置能力的解析、chunk、metadata、命中与 Prompt 等价，T-08 标注映射独立性与评估失败注入，必须在组件实现后执行；它们不是要求在不存在 V3 代码时预先宣称通过。T-02 的合并格/跨页/缺坐标 fixture、Docling 单次转换生命周期、真实 Publisher 全故障矩阵、历史报告只读适配及 `comparison.json` 回读，均是对应组件的交付测试。任一实测若要求改变用户可见语义，先修订已审核需求/架构，不能让代码成为新规则。

实施边界：本次先按规格重塑 V2 完整能力，不引入元数据过滤、关键词检索或图片解析等新功能；切换 V3 运行入口须在两份内置装配的真实等价、恢复与审核产物验收通过后进行。
