# 现有能力、证据与 V3.0 承接清单

日期：2026-09-22。返回 [审核入口](README.md)；设计见 [总体方案](system-design.md)，文档归属键见 [文档地图](document-map.md)。

本表是现行事实与规格来源的盘点，不把代码或历史输出自动升级为正式规则。范围为当前系统入口及关键实现、全部现行子规格的职责与关键规则；不是逐行代码审计或全量测试结论。`R-*`、`A-*` 是拟建文档的定位键，不是现有文件或新业务状态。

## 1. 能力与规则对应

| 编号 | 必须承接的能力 / 规则 | 现行权威来源 | 实现 / 验证线索 | V3.0 责任与规格位置 |
| --- | --- | --- | --- | --- |
| B01 | 数字 PDF、本地终端、单用户；无 OCR、图片理解、记忆及新检索机制 | [总需求 §1](../../requirements.md#1-目标与非目标) | [CLI](../../../rag/cli.py) | 总需求 R0 的范围与非目标 |
| B02 | 递归发现、路径身份、hash、文档选择和重名歧义 | [总需求 §2](../../requirements.md#2-文档来源与身份) | [Registry](../../../rag/document_registry.py)、[测试](../../../tests/test_document_registry.py) | Registry；R0 来源阶段、A-data；冲突 D-02 |
| B03 | 命令范围、默认选择、目标隔离、force/file/prune 参数约束 | [总需求 P-01/P-02](../../requirements.md#3-链路选择) | [CLI](../../../rag/cli.py)、[装配](../../../rag/bootstrap.py) | V3.0 配置与兼容映射；R-assembly、R-operations、A-runtime |
| B04 | 按物理页 blocks 排序取文本、原空白处理、无可用文本拒绝 | [总需求 P-03](../../requirements.md#4-v1-纯文本基线) | [PDF parser](../../../rag/pdf_parser.py)、[测试](../../../tests/test_pdf_parser.py) | MainParser 的纯文本插件；R-processing、A-processing |
| B05 | 按页 700/100 默认字符分块、原分隔顺序、相邻重复去除、原 ID | [总需求 P-04](../../requirements.md#4-v1-纯文本基线) | [Chunker](../../../rag/chunker.py)、[测试](../../../tests/test_chunker.py) | 字符 Chunker；R-chunking、A-data、A-processing |
| B06 | Docling 版面、列表、图注等保留，图片排除，原生跨页和来源不补造 | [总需求 F-01](../../requirements.md#5-v2-结构化解析)、[映射规格](../../architecture/v2.0/docling-mapping.md) | [Mapper](../../../rag/v2/docling_mapper.py)、[测试](../../../tests/test_v2_docling_mapper.py) | MainParser；R-processing、A-processing 中的工具映射 |
| B07 | 四工具九策略、跨度恢复、统一坐标及缺失、页级继续 | [表格需求 §2](../../requirements/v2.0/table-extraction-selection.md#2-四工具提取与结构恢复) | [Extraction](../../../rag/v2/table_extraction.py)、[提取测试](../../../tests/test_v2_table_extraction.py) | TableExtractor；R-tables、A-tables |
| B08 | slot 准入、双向覆盖、唯一归组、四指标、权重及同分规则 | [表格需求 §3—5](../../requirements/v2.0/table-extraction-selection.md#3-slot-与候选准入) | [Selection](../../../rag/v2/table_selection.py)、[Scoring](../../../rag/v2/table_scoring.py) | TableSelector；R-tables、A-tables |
| B09 | winner 替换、原生回退、不重复表格、完整不可变语义文档 | [总需求 F-01](../../requirements.md#5-v2-结构化解析) | [Assembler](../../../rag/v2/parsed_document.py)、[测试](../../../tests/test_v2_parsed_document.py) | Selector 决策与 Assembler 组装分工；R-processing、A-processing |
| B10 | 表头判定顺序、类型语法、阈值、唯一支持、原原因码 | [完整表头需求](../../requirements/v2.0/table-header-detection.md) | [Header](../../../rag/v2/table_header.py)、[测试](../../../tests/test_v2_table_header.py) | HeaderDetector；R-header、A-tables，不重新发明结论枚举 |
| B11 | 字段路径、合并格、空白与缺失区分、行列兜底及带来源正文 | [完整文本化需求](../../requirements/v2.0/table-text-serialization.md) | [Serialization](../../../rag/v2/table_serialization.py)、[测试](../../../tests/test_v2_table_serialization.py) | TableSerializer；R-table-text、A-tables |
| B12 | 普通文本、List 正文、组合顺序、结构切分、overlap、来源与终止 | [完整分块需求](../../requirements/v2.0/document-text-and-chunking.md) | [结构分块](../../../rag/v2/chunking.py)、[测试](../../../tests/test_v2_chunking.py) | 结构 Chunker；R-chunking、A-processing |
| B13 | 固定模型 revision、passage/query 前缀、归一化、实际 tokenizer | [总需求 C-01/I-01](../../requirements.md#7-embedding向量存储与检索) | [Embeddings](../../../rag/embeddings.py)、[技术验证 §4](../../architecture/v2.0/technical-validation.md#4-e5-tokenizer-与硬上限) | Embedder 与结构化 Chunker 内部计数；R0 编码阶段、A-retrieval；字符方案不悄悄重切 |
| B14 | 一块一记录、来源回读、显式 embedding、不使用默认编码函数 | [总需求 I-02](../../requirements.md#7-embedding向量存储与检索)、[数据契约](../../architecture/v2.0/data-contracts.md) | [VectorStore](../../../rag/vector_store.py)、[测试](../../../tests/test_vector_store.py) | Store/Publisher；R-index、A-data、A-storage |
| B15 | K 校验、显式文档限定、自适应候选池、未舍入距离和 ID 决胜 | [总需求 I-03](../../requirements.md#7-embedding向量存储与检索) | [Retriever](../../../rag/retriever.py)、[测试](../../../tests/test_retrieval.py) | Retriever；R0 检索阶段、A-retrieval |
| B16 | 增量跳过、文件变化重建、全量 force、prune 作用域、配置不兼容 | [总需求 I-04/I-05](../../requirements.md#8-manifest增量索引与发布) | [Indexer](../../../rag/pipeline_indexer.py)、[测试](../../../tests/test_pipeline_indexer.py) | Indexer；R-index、A-storage；失败差异见 D-04 |
| B17 | V2.0 结构化构建的 raw/parsed/chunks/selection/winner HTML；零 winner 页面；必需审核失败不发布 | [现行总需求 I-06](../../requirements.md#8-manifest增量索引与发布) | [现行 Artifacts](../../../rag/v2/artifacts.py)、[测试](../../../tests/test_v2_artifacts.py) | V3.0 ArtifactBuilder 必接，按重要接口阶段成果生成两类装配均有的可审计快照；文件与页面见[新产物需求](spec/requirements/processing-artifacts.md) |
| B18 | 快照、journal、manifest 生效点、提交收尾、回滚失败保留、prune quarantine | [发布契约](../../architecture/v2.0/index-publication.md) | [Publication](../../../rag/publication.py)、[测试](../../../tests/test_publication.py) | Publisher/Recovery/Store；R-index、A-storage |
| B19 | 七种文档状态、只读预检、问题优先级、交互恢复、非交互不写 | [总需求 H-01/H-02](../../requirements.md#9-索引健康与引导恢复)、[运行契约](../../architecture/v2.0/runtime-contracts.md) | [Readiness](../../../rag/cli_readiness.py)、[测试](../../../tests/test_cli_readiness.py) | Health/SourceProbe/恢复交互；R-operations、A-runtime；冲突 D-03 |
| B20 | 证据 Prompt、真实来源、独立 ask/chat、debug、key/认证恢复和服务错误 | [总需求 H-03/§10](../../requirements.md#10-问答聊天与-prompt) | [Prompt](../../../rag/prompt.py)、[LLM](../../../rag/llm.py)、[问答](../../../rag/pipeline.py) | Chatbot/PromptBuilder/LanguageModel；R0 问答阶段、R-operations、A-retrieval |
| B21 | browse 稳定排序分页且不读向量；chunks 来源页筛选；只读检查 | [总需求 P-02/I-03](../../requirements.md#3-链路选择) | [CLI](../../../rag/cli.py)、[只读测试](../../../tests/test_read_only_store.py) | 记录读取与展示；R-operations、A-runtime、A-storage |
| B22 | ground truth/test set 分层、审核、最小可接受集合、unmappable、身份检查 | [评估需求 §3—7](../../requirements/v2.0/retrieval-evaluation.md) | [Repository](../../../rag/evaluation_repository.py)、[模型](../../../rag/evaluation_models.py) | EvaluationRepository；R-evaluation、A-evaluation |
| B23 | 逐题/Macro/Micro、首次相关 chunk 与完整组不同、严格可比协议 | [评估需求 §8](../../requirements/v2.0/retrieval-evaluation.md#8-指标定义) | [Metrics](../../../rag/evaluation_metrics.py)、[测试](../../../tests/test_evaluation_metrics.py) | MetricCalculator；R-evaluation、A-evaluation，完整搬迁公式而非复制简写 |
| B24 | 不可变运行、失败事实、HTML 派生、summary/comparison、live 独立 | [评估需求 §7/9/10](../../requirements/v2.0/retrieval-evaluation.md) | [Evaluator](../../../rag/evaluator.py)、[Reports](../../../rag/evaluation_reports.py) | Evaluator/RunStore/Reporter；R-evaluation、A-evaluation；差异 D-05 |
| B25 | 错误阶段、退出码、日志秘密、调试与清理 | [总需求 §11](../../requirements.md#11-错误退出与安全)、[运行契约 §4](../../architecture/v2.0/runtime-contracts.md#4-ragerror) | [Errors](../../../rag/errors.py)、[Processor](../../../rag/document_processor.py) | R-operations、A-runtime；差异 D-03/D-06 |

上述对应覆盖现行总需求 §1—13 和全部五份需求子文档的职责。后续迁写必须下钻到子规则及验收场景；此表不声称已经完成逐公式、逐原因码搬迁。

## 2. 实际基线与检查限度

只读检查发现 `.rag/system-v2/pipelines/v1/manifest.json` 与 `v2/manifest.json` 均为 `pipeline_manifest_v2`，各登记五份文档；样例 document_id 为 16 位。旧目录仅核对身份与数量，不修改或触发 Chroma 写入。

以下名称只引用历史运行身份：

| 历史配置 | 构建指纹前 12 位 | completed 运行 | K / 文档数 |
| --- | --- | --- | --- |
| v1 | `87059c0fd057` | `20260920T052848Z-v1-87059c0f-k3` | 3 / 5 |
| v2 | `029ccb26ca98` | `20260920T052923Z-v2-029ccb26-k3` | 3 / 5 |

运行 JSON 均记录 `retrieval_evaluation_v2`，存在 aggregate 文件；[历史 summary](../../../validation/retrieval/system-v2.0/summary.md) 和 [comparison](../../../validation/retrieval/comparison.md) 已存在，题数合计 24。没有重跑检索或据展示百分比反算完整原始指标。不能因为已有报告存在就宣称所有失败路径实现符合规格。

后续等价验收应固定源 PDF/hash、规则及模型配置，比较阶段事实和正文、chunks、来源、排序与指标；Prompt 比较完整消息构造；失败恢复用受控故障验证。新 test set 先核对映射再审核，不能把旧报告当成正确答案来源。

## 3. 冲突与处置

这些发现不修改现行规则。建议是待审核的处置方向，不能在实现中默默选择“代码优先”或“文档优先”。

| 编号 | 证据与差异 | 影响 | 建议处置 / 决策状态 |
| --- | --- | --- | --- |
| D-01 | 总需求首页称评估基线待完成，同文 §13、架构及实际 completed 报告表明已完成 | 文档状态陈旧 | 按运行证据修正未来规格状态；不改变算法，无需业务选择 |
| D-02 | 总需求 §2、数据契约 §2 写完整 SHA-256；`make_document_id` 实际截前 16 位，manifest/报告沿用该 ID | 若按全文改写将改变稳定 ID、chunk ID 和评估映射 | 根据已确认“保留既有稳定 ID”，建议明确保留实际 16 位并修正文档；这是落实既有决策，不暗改为 64 位 |
| D-03 | 运行契约规定全局阻断时文档为 unassessed；`_inspect_selected_pipeline` 仍逐文档判定。书面 IndexIssue 为封闭代码，实际存在 `global_config_mismatch`、`manifest_invalid` 及拼接文档 ID 的 code；`RagError` 没有文档声明的 stage 等字段 | 状态、机器原因码和错误归属不能同时等价于文档和实现 | 待确认重要取舍：建议 V3.0 落实现行书面失败语义并记录为纠偏例外，不声称严格复现已有缺陷；接口结构可先审 |
| D-04 | 发布/运行契约要求 force 任一构建失败清理所有本次 staging、collection 缺失恢复不发布部分；`RuntimeIndexer.ingest_all` 的 force 失败路径直接返回，普通增量逐文档发布且无 collection 缺失专用全有或全无路径；跳过条件只看 hash/count，可能跳过 artifact invalid 文档 | 失败留存、恢复完整性和修复能否生效 | 待确认同 D-03 的纠偏原则；先建立故障复现用例，不把静态路径观察夸大成已完成实测 |
| D-05 | 评估要求 invalid/failed 事实可留存、所有必需报告成功才 completed；`run_formal_evaluation` 没有统一失败记录包装，在 `write_summary` 前已发布 completed run；run ID 是秒级组合而非架构的微秒+随机 | 失败事实、完成时机与同秒重跑行为不完全一致 | 待确认同 D-03 的原则；沿用指标和已成功基线，失败留存按批准的规则补齐，不覆写历史记录 |
| D-06 | 总需求允许 debug 展示最终消息；运行契约 §4 又说 debug 不显示完整 prompt；`_print_answer` 实际输出最终消息 | 调试行为定义矛盾 | 建议保留用户显式 debug 的现有消息展示，默认日志不输出，任何路径不得输出 key；把异常 debug 摘要与显式问答 debug 分清，在 R-operations 一处定义 |
| D-07 | 总架构目录示意为 `rag/document_processing/`，当前实现主要在 `rag/v2/`；技术验证 §6 重号及链接锚点过时 | 文档与落地组织不一致，不直接改变结果 | V3.0 自主设计代码分层，在最终规格按实际结构校验；本轮不移动代码或旧文档 |

**已确认的纠偏原则**：用户同意 D-03—D-05 作为 V3.0 限定纠偏例外。保持正常业务结果等价；上述失败路径落实现行书面承诺，单列验收与旧实现差异，不改写历史运行证据。D-06 的 debug 冲突在需求中明确显式展示范围。

## 4. 设计中已处理的隐含依赖

- Docling 版面与原生表格来自一次转换；由适配器族共享本次文档资源，具体技术门见总体方案。
- 评分不只消费 layout 和候选，还读取原 PDF words；明确 PdfEvidenceReader，避免漏依赖。
- 表头/文本化当前由 assembler 调用；拆出责任后仍使用相同最终 node ID，保留 winner 与原生回退一致的处理次序。
- Prompt 用对象是否有 `page_numbers` 区分 `page`/`pages`；统一模型后改为明确模板配置，保留消息格式。
- 纯文本 chunk_index 是页内序号，结构化序号是文档级；统一模型要区分排序身份与历史编号，不能直接要求所有 chunk_index 全局唯一。
- 构建产物中某些 warning 来源于分块；V3.0 单独承接处理证据与分块诊断，不回写已完成 ParsedDocument，也不丢失历史诊断事实。

## 5. 尚未完成的验证

本轮未执行系统测试、SDK 探针、真实 PDF 重建及故障注入。后续技术门包括 Docling 共享资源的单次性/异常传播、统一模型无损转换、旧 ID 与来源排序、Prompt 字节对照、统一存储 codec、发布故障及正式评估映射验证。历史技术验证是依据，不能替代新装配方式的验证。
