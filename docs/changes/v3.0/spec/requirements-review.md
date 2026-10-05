# V3.0 需求一致性审查

日期：2026-09-22，2026-09-25 增补。状态：**需求已获用户审核；逐阶段快照增补已完成一致性复审。** 本文保留作者自查记录，不等于架构设计或实现验证。阅读入口见[规格导航](README.md)，现行事实对应见[能力盘点](../capability-inventory.md)。

## 1. 范围与阶段覆盖

| 来源能力 | 需求落点 | 自查结果 |
| --- | --- | --- |
| B01—B03 范围、源身份、配置及命令 | [总需求 §1—3](requirements.md)、[装配](requirements/assembly.md)、[操作](requirements/operations.md) | 两份等价预设、默认装配指向、配置选择/创建及批量建索引有确定行为；旧参数和旧运行实现均不进入 V3.0 |
| B04—B06 纯文本与 Docling 主解析 | [总需求 §4](requirements.md#4-文档解析与组装)、[处理](requirements/document-processing.md) | 纯文本页边界、Docling 阅读序、列表及原生来源分别保留 |
| B07—B09 四工具、选优及回退 | [表格选择](requirements/table-selection.md)、[处理](requirements/document-processing.md) | 工具事实、候选决策与最终语义分离；原生回退不伪装 winner |
| B10—B12 表头、文本化与两种分块 | [表头](requirements/table-header.md)、[表格正文](requirements/table-text.md)、[分块](requirements/chunking.md) | 阈值、词法、表格正文与切分规则各有单一完整定义；字符分块新增第 10 节 |
| B13—B18 向量、产物、发布及恢复 | [总需求 §6—7](requirements.md#7-向量化与索引发布)、[产物](requirements/processing-artifacts.md)、[索引](requirements/index-lifecycle.md) | 旧 active 保留、生效点、全量 force、prune 与中断恢复均有失败影响 |
| B19—B21 健康、问答及只读浏览 | [总需求 §8—9](requirements.md#8-索引健康与查询入口)、[操作](requirements/operations.md) | 状态、问题优先级、凭据/debug 与所有八类命令有去向 |
| B22—B24 数据集、指标及报告 | [检索评估](requirements/retrieval-evaluation.md) | ground truth、映射、公式、空值、完成状态与不可变报告集中定义 |
| B25 错误与安全 | [总需求 §11](requirements.md#11-错误安全系统级验收与限制)、[操作](requirements/operations.md) | 机器阶段归属与错误显示边界已要求；字段全集仍属于架构 |

## 2. 明确的历史差异

| 编号 | 需求草案如何处理 | 禁止的误解 |
| --- | --- | --- |
| D-01 | 总需求声明历史正式评估已存在，当前 V3.0 需独立验证 | 不把旧基线报告当作 V3.0 已通过的测试 |
| D-02 | 文档 ID 采用已发布的 SHA-256 前 16 位，保留 chunk 稳定 ID 格式 | 不按旧书面“全文 hash”改写已有身份 |
| D-03 | [操作需求 §2、5](requirements/operations.md#2-健康状态)：组合级阻断时 `unassessed`，机器问题码与阶段封闭 | 不把旧实现逐文档误判写成新需求 |
| D-04 | [索引需求 §3、6](requirements/index-lifecycle.md#3-增量force-与-prune)：force 预构建全有或全无、collection 缺失不部分发布、invalid 不按 hash/count 跳过 | 不以单个成功文档代表恢复完成 |
| D-05 | [评估需求 §7](requirements/retrieval-evaluation.md#7-运行完整性与失败规则)：invalid/failed/completed 分明，必需输出完成后才算成功，run ID 唯一 | 不覆写旧 completed 运行或把失败报告放进正式基线 |
| D-06 | [操作需求 §4](requirements/operations.md#4-llm-凭据与输出)：显式终端 debug 可显示最终消息，异常摘要与默认日志仍限制 Prompt 和秘密 | 不把显式 debug 的展示要求误写成默认日志行为 |

D-03—D-05 是用户批准的有限纠偏，需在实现与验证中单列；其他行为仍按确定性阶段结果等价核对。D-07 属后续架构代码组织与旧锚点修订，不在需求中发明行为。

## 3. 权威边界与已检查事项

- 总需求沿实际阶段顺序给出目的、输入、行为、输出及主要失败，十份子需求仅按稳定职责展开；表格评分公式只在 `table-selection.md`，指标公式只在 `retrieval-evaluation.md`，超长表格切分只在 `chunking.md`，四类可审计 HTML 的完整规则只在 `processing-artifacts.md`。2026-09-25 的阶段快照增补已核对总需求第 6 节、装配条件、产物角色和失败阻断，不沿用旧的按特定解析路线列文件方式。
- 主解析事实、提取候选、选择结论、完整语义文档、chunk、已发布事实、健康状态、评估派生事实分层描述；只读 HTML 与评估报告不能反向变成业务来源。
- `identified|undetermined`、七种文档健康状态和三种评估运行状态均沿用已有语义，没有以近义新名字替代。字段类型、全部错误/原因码及空值不变量由已完成的架构子契约定义；需求文档不复制它们。
- 子文档均回链总需求阶段；本草案的本地 Markdown 链接与标题锚点、围栏经过机械检查。没有把历史需求当作 V3.0 规则的必读依赖。

## 4. 已交付架构的技术契约

完整接口与插件字段、连接的唯一存储形式、配置文件 schema、身份与 fingerprint 公式、统一节点/来源/Chunk/Hit 模型、产物 JSON 与向量 metadata、状态和问题码全集、事务 journal、查询/Prompt 的准确序列化、评估模型与 run 发布顺序已由[总架构与子契约](architecture.md)定义。它们不能由编码者临时改写；如技术验证改变用户可见行为，先修订需求。

Docling、E5、Chroma、文件发布和 PyMuPDF 原生事实的编码前可行性证据见[技术验证](technical-validation.md)；真实 V3 统一模型等价与评估独立性只能随实现测试。验证只决定适配方式，不默默新增失败或降级政策。

## 5. 审核建议

用户已确认本版需求与架构，包括默认装配、交互式配置、批量建索引、配置命名和逐阶段审计快照；[编码就绪复审](architecture-review.md)已完成。编码中发现新的语义缺口时，仍须先修订需求并重新审核受影响部分。
