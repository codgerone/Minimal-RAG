# 检索效果评估需求

本文是 [Minimal RAG 当前系统需求](../../requirements.md#12-评估与验收) 中检索效果评估阶段的唯一业务规则权威，适用于当前 V2.0 及后续系统版本和 pipeline。总需求只保留阶段摘要，不重复本文的数据集规则、指标公式和报告规则。

## 1. 目标、范围与非目标

系统必须使用人工确认的语义证据标准，量化指定检索配置能否找到回答问题所必需的全部独立事实、将相关片段排在靠前位置，并控制无关片段及相似订单之间的跨文档污染。评估结果用于建立当前基线、定位失败模式和比较后续迭代，不预设 v2 必须优于 v1，也不以单个汇总数字代替逐题证据审核。

本文覆盖五份基准 PDF 的问题与证据、ground truth 到指定构建 chunk 的离线映射、检索运行、指标、人工审核视图、跨版本汇总和变更控制。

本阶段不评估 LLM 最终回答的措辞、事实一致性或拒答质量；不从检索排名、LLM 回答或关键词相似度自动生成 ground truth；不把高分结果反向标注为正确证据；不把报告作为索引或问答的数据源；当前不定义统计显著性或上线最低分。

## 2. 输入、输出与前置条件

输入是已冻结的基准 PDF、人工审核的 pipeline 无关 ground truth、目标配置已发布的索引与最终 chunks、对应 test set，以及固定查询文本、`top_k` 和检索参数。

输出必须同时包含机器可读事实和人类可读视图。每次评估运行的逻辑结构如下；物理路径及文件命名见第 9 节。

```text
一次评估运行
├── run manifest                  # 本次构建、检索配置和运行身份
├── aggregate result             # 按 PDF 及全题集汇总的机器可读指标
└── documents/
    └── 每份 PDF
        ├── evaluation.json       # 全部问题、预期证据、实际有序结果、匹配和指标原始事实
        └── review.html           # 同一批事实的人工审核视图

版本级输出
├── summary.md                    # 同一系统版本内各配置/运行的精简比较
├── comparison.json              # 跨运行可比性和指标差值的机器事实
└── comparison.md                 # comparison.json 的人类视图
```

JSON 是指标复算、自动检查和后续生成其他视图的权威运行事实；HTML 与 Markdown 是由同批 JSON 派生的人类视图，不得反向改写 JSON。输出还必须保留足以复现结果的配置和运行身份。

正式评估前必须确认：PDF 内容 hash 与 ground truth 一致；索引健康且 manifest 与 collection 一致；test set 与目标构建身份一致；全部可接受 chunk ID 存在且可还原文档、页面和正文；数据集通过第 7 节完整性检查。任一条件不满足时该配置状态为 `invalid`，不得输出正式可比指标。

## 3. 术语与评估粒度

### 3.1 Evidence group

`evidence group` 表示回答一个问题所必需的一项独立事实。若完整回答同时需要金额、日期等多个事实，必须分别定义多个组。同一事实在 PDF 中可以跨段、跨表格行或跨页表达；这些证据仍可属于同组，但在配置相关 test set 中必须明确哪些 chunk 需要共同出现才能完整覆盖该事实。

`evidence_group_id` 只在单个问题内唯一，使用 `e1`、`e2` 等稳定编号，由人工建立 ground truth 时创建，不由 RAG 检索生成。

### 3.2 Chunk 相关性与派生匹配

一个 chunk 只有在其 ID 出现在该问题至少一个 evidence group 的 `acceptable_chunk_sets` 中时才相关。仅来自目标 PDF、含相同关键词或语义相近不足以判定相关。

一个 chunk 可以参与多个组；一个组可以由单个 chunk 独立覆盖，也可以由多个 chunk 共同覆盖，还可以存在多种可替代的完整覆盖组合。RAG 只返回 chunk ID、排名、正文、来源、距离和相似度；评估器依据 test set 确定性反查并生成 `matched_evidence_group_ids`，表示该 chunk 参与哪些组的至少一个可接受组合，不代表该 chunk 单独完成组覆盖。该字段是运行派生结果，不能预写进 RAG 返回值。

### 3.3 指标粒度原则

指标名称必须显式说明粒度。chunk 指标衡量返回片段的相关性和噪声，evidence-group 指标衡量独立事实覆盖。只定义有独立解释价值的粒度，不为每项指标机械复制 chunk/group 两套同名变体。

### 3.4 状态

只使用以下封闭状态：

| 状态类型 | 取值 | 触发条件与下游行为 |
| --- | --- | --- |
| DatasetReviewStatus | `draft`、`approved`、`superseded` | 新建或修改后为 draft；用户审核通过为 approved，只有 approved 可生成正式基线；被新版替代后为 superseded，只保留历史追溯 |
| EvidenceMappingStatus | `mapped`、`unmappable` | 至少存在一个可完整覆盖该组的 chunk 集合为 mapped；即使组合当前索引中的 chunks 也无法恢复完整证据时为 unmappable，保留空组合集合并继续进入召回分母 |
| EvaluationRunStatus | `invalid`、`failed`、`completed` | 运行前完整性检查失败为 invalid；检查通过但检索、计算或产物生成失败为 failed；全部问题、JSON、HTML 和聚合成功为 completed，只有 completed 可进入正式汇总 |
| ComparisonStatus | `strictly_comparable`、`protocol_changed`、`dataset_changed` | 数据集与协议均相同为 strictly_comparable；数据集相同但协议不同为 protocol_changed；数据集身份不同为 dataset_changed；只有 strictly_comparable 可计算直接指标差值 |

状态必须保存为机器字段；人类说明不得代替或覆盖状态。

## 4. 数据集分层与目录

```text
eval/
├── README.md
├── ground-truth/
│   ├── manifest.json
│   └── <document-key>.json
└── test-sets/
    ├── system-v2.0__pipeline-v1__cfg-<fingerprint-short>/
    │   ├── manifest.json
    │   └── <document-key>.json
    └── system-v2.0__pipeline-v2__cfg-<fingerprint-short>/
        ├── manifest.json
        └── <document-key>.json
```

每个“系统版本 + pipeline + 构建配置”占用 `test-sets/` 下一个一级目录。短 fingerprint 只用于方便人识别，完整 fingerprint 以 manifest 为准；例如不同 `chunk_size` 必须使用不同目录，因此可以并行留存并比较多个分块配置。后续配置继续使用同一扁平规则，不再建立系统版本/pipeline 多层嵌套。`eval/README.md` 只说明文件用途和操作流程，不另行定义字段或指标。正式运行报告不放入 `eval/`。

## 5. Pipeline 无关 ground truth

ground truth 必须由标注者直接阅读 PDF 后人工定义，并经另一轮人工审核。可以借助 PDF 文本提取定位，但不得查看某次查询排名来决定证据。

每题至少记录稳定问题 ID、原始问题、`answerable`、参考答案、目标 PDF 身份及内容 hash，以及每个 evidence group 的 ID、物理页码和可供人工核对的原文片段。原有 `expected_index_ids` 字段废止；ground truth 不保存随 pipeline 和分块变化的 chunk ID。

页码统一使用从 1 开始的 PDF 物理页序号，不使用页面内印刷页码作为机器字段。一个组可以保存一个或多个 evidence excerpt，每个 excerpt 分别记录文档、一个或多个物理页码和证据正文；不连续证据不得伪装成连续范围。证据正文只允许统一换行和去除首尾空白，不得改写词语、数字或原意。若多个事实缺一不可，必须拆为多个 evidence group，而不是把多个 excerpt 当作可替代事实。

`answerable=true` 时必须至少有一个 evidence group；参考答案中的每项必要事实都须有组支持；每组必须有非空证据文本和页码。`answerable=false` 时不得定义必需组，参考答案须说明 PDF 中证据不足。不可回答问题不进入本阶段 recall、precision、coverage 和排名指标的分母；仍保存实际返回并单列观察。固定返回近邻的向量检索不能仅因返回了 chunk 就被判为不可回答失败。

## 6. 配置相关 test set

test set 必须在正式检索前生成。标注者逐个 evidence group 检查目标配置的全部最终 chunks，把能够共同完整覆盖该组事实的最小 ID 集合写入 `acceptable_chunk_sets`；过程只使用 ground truth、最终 chunks 及来源，不使用实际查询排名。

test set 只把稳定语义标准绑定到具体构建，通过问题 ID 和 evidence group ID 与 ground truth 唯一关联，不重复定义问题、答案或证据原文。

- `acceptable_chunk_sets` 的内层集合是 AND：其中全部 chunk 都返回时，该组合才完整覆盖该组；外层集合是 OR：命中任一内层组合即可。
- 每个内层集合必须是完整覆盖事实的最小集合；若删除其中任一 ID 后仍能完整覆盖，则不得保留该冗余 ID。
- 一个 chunk 独立覆盖该组时表示为单元素集合；存在多个可替代 chunk 时分别表示为多个单元素集合，而不是放入同一个内层集合。
- 一个 chunk 可参与多个组或多个可替代组合；只覆盖部分事实的 chunk 只有与其他 chunk 组成完整最小集合时才可纳入，页面/关键词相同但不承载必要证据的 chunk 不可纳入。
- 只有在解析、文本化或索引过程丢失必要证据，导致组合当前全部 chunks 仍不能恢复该组时，才保留空组合集合并标记 `unmappable`。正常的分块拆分不得标记为 `unmappable`。

每个 test-set manifest 至少绑定系统版本、pipeline、collection、数据集版本、test-set 标注规则版本、PDF hash 集合、完整规范化的 `build_config`、由该快照确定性计算的 `build_config_fingerprint`、生成时间和审核状态。embedding 模型及 revision、解析、文本化、chunk 和其他会改变索引结果的配置必须包含在 `build_config` 中，不得只在 fingerprint 之外零散记录。

`build_config` 是供人解释差异和供系统复现构建的权威配置快照，fingerprint 是对这份快照做快速身份校验的派生值。两者必须同时保存并满足：相同规范化快照生成相同 fingerprint；任一参与构建结果的字段变化都生成不同 fingerprint；保存后不可原地修改。API Key、环境绝对路径、日志、诊断、展示配置不得进入快照。`top_k`、问题文本 hash 和查询时检索参数不属于 test-set manifest，也不得进入索引构建 fingerprint；它们属于一次评估的运行身份。

构建 fingerprint 标识“这套 chunk 映射适用于哪一个索引构建配置”，不是要求历史结果始终等于当前活动配置。当前 collection 切换到新 fingerprint 后：

- 旧 test set 和旧运行结果继续作为对应旧 fingerprint 的有效历史记录，禁止覆盖或宣告失效；
- 旧 test set 不可用于评估当前新索引，新配置必须建立自己的 test set；
- 若要重新运行旧配置，须先恢复或重建该 fingerprint 对应索引；只查看已保存的旧 JSON/HTML/汇总不要求旧配置仍处于活动状态；
- `chunk_size`、chunk overlap、解析/文本化规则、embedding 模型等构建配置变化产生新 fingerprint，可以分别留存效果；
- 仅 `top_k` 改变不重建索引或 test set，但必须生成新的运行身份和结果目录。K=3 与 K=5 是两次结果，后一次不得覆盖前一次。

## 7. 运行、完整性与失败规则

运行顺序固定为：验证数据与构建身份；按原始问题逐题检索；保存前 `K` 个有序结果；用 test set 映射组命中；先计算逐题指标再汇总；生成机器结果、人工视图和版本汇总。`K` 必须为正整数。

正式评估复用总需求 I-03 的确定性 Retriever：候选池覆盖第 K 位完整同距离组后，按距离升序、chunk ID 升序决胜并严格截取前 K 条。不得用显示时的四舍五入值判断同分，chunk ID 在一次返回中必须唯一。评估器验证顺序和数量，但不得另建一套检索或排序语义。

不同 pipeline 的同一比较批次必须使用相同 ground truth、问题文本和 K。每次运行保存 `evaluation_protocol_version`；指标公式、纳入范围、排序、空值或状态规则任一变化都必须提升该版本。test-set 的 chunk 可接受性判定规则变化必须提升标注规则版本。严格可比还要求数据集版本、PDF hash、评估协议版本、test-set 标注规则版本、查询前缀、查询过滤条件、距离与排序协议完全相同。允许 pipeline、解析、分块和 embedding 等被比较的构建配置不同，但必须逐项展示差异。数据集身份不同为 `dataset_changed`；数据集相同但上述评估协议任一项不同为 `protocol_changed`。

正式运行前必须机械检查：问题 ID 全局唯一；test set 与 ground truth 的问题及组集合完全一致；可回答性与组数一致；每个可接受组合非空、内部 ID 唯一且按序排列、不同组合唯一且不存在真子集组合；每个可接受 ID 在目标 collection 恰好存在一次；chunk 的 pipeline、文档、正文及页面与目标构建一致；可接受 ID 不得来自未声明的证据文档；fingerprint、PDF hash 和 chunk 总数与当前索引一致。

完整性失败时状态为 `invalid`，不运行或立即终止，不保留部分指标为正式结果。完整性通过后，单题检索、指标计算、JSON、HTML 或聚合任一阶段异常均使整次运行状态为 `failed`；已有结果只供诊断。所有要求产物成功后状态才为 `completed`。成功但少于 K 条不是失败，chunk 指标按实际返回数计算。

## 8. 指标定义

对一个可回答问题，设必需组集合为 `G`，`g=|G|>0`；前 `K` 个实际返回的有序 chunk 列表为 `R_K`，`n=|R_K|`；其中相关 chunk 集合为 `L_K`。对组 `e`，其可接受最小 chunk 集合族为 `A(e)`；当存在 `S∈A(e)` 满足 `S⊆IDs(R_K)` 时，组 `e` 被完整覆盖。所有被完整覆盖的组组成 `C_K`。排名从 1 开始。

chunk 指标与 group 指标分别计数：一个返回 chunk 只占 `R_K` 中一个位置，因此在 Chunk Precision 的分子中至多算一个相关 chunk，即使它参与多个组；group 只有在某个可接受组合的全部成员均返回后才进入 `C_K`。同组组合中的两个碎片都返回时，Chunk Precision 可计两个相关 chunk，而 `C_K` 中该组仍只计一次；只返回其中一个碎片时该 chunk 仍是相关 chunk，但该组尚未覆盖。

### 8.1 逐题指标

**Chunk Precision@K** 衡量返回噪声：

```text
ChunkPrecision@K = |L_K| / n    当 n > 0
ChunkPrecision@K = 0            当 n = 0
```

分母使用实际返回数，避免把未返回的空位算成无关 chunk。

**Evidence-group Recall@K** 衡量必要事实覆盖：

```text
EvidenceGroupRecall@K = |C_K| / g
```

**Question Hit@K** 衡量前 K 个结果是否至少找回一项所需证据：

```text
QuestionHit@K = 1    当 |C_K| > 0
QuestionHit@K = 0    当 |C_K| = 0
```

它便于直观表达“这道题有没有命中”，但等价于 `EvidenceGroupRecall@K > 0`，不能替代组召回或完整覆盖。

**Complete Coverage@K** 衡量是否覆盖全部必要事实：

```text
CompleteCoverage@K = 1          当 |C_K| = g
CompleteCoverage@K = 0          其他情况
```

**Reciprocal Rank@K（RR@K）** 衡量第一个相关 chunk 的位置：

```text
RR@K = 1 / 最早相关 chunk 的排名    当 L_K 非空
RR@K = 0                           当 L_K 为空
```

多个正确结果仍只取最早者，因此 RR 不能替代多事实覆盖指标。

**Evidence-group Reciprocal Rank@K（EGRR@K）**：一个可接受组合的完成排名是其最后一个必要 chunk 的排名；一个组存在多个可替代组合时取最早完成排名。组未完整覆盖则为 0，再对该题全部必需组取平均。单 chunk 组合退化为该 chunk 的排名。

```text
complete_rank(S) = max(rank(chunk))                 chunk ∈ S
group_rank(e) = min(complete_rank(S))               S∈A(e) 且 S⊆IDs(R_K)
rr(e) = 1 / group_rank(e)                           存在已完成组合时
rr(e) = 0                                           否则
EGRR@K = Σ rr(e) / g                                e ∈ G
```

**Cross-document Contamination@K** 衡量相似订单抢占。设 `X_K` 为来源 PDF 不属于该题 ground truth 证据文档集合的返回 chunk：

```text
CrossDocumentContamination@K = |X_K| / n    当 n > 0
CrossDocumentContamination@K = 0            当 n = 0
```

来自目标 PDF 但无关的 chunk 只降低 Chunk Precision，不算跨文档污染；两项不能互相替代。

每题还须记录返回数、相关 chunk 数、必需组数、命中组数、未命中组、`unmappable` 组数和首个相关排名。它们是指标的可审计分子分母。

不定义 Evidence-group Precision@K：系统预测的是 chunk，并不预测一个包含“错误证据组”的封闭 group 集合；无关 chunk 只映射为零个预期组，因此不存在合理的“预测 group 总数”作为 precision 分母。若用“命中的唯一组数 / 返回 chunk 数”，该值又会在必需组数小于 K 时天然受限，并把重复的正确 chunk 混成 group 层错误。返回噪声已经由 Chunk Precision 表达。

不定义 Chunk Recall@K：其分母只能是所有可接受组合中 chunks 的并集，但替代组合并不要求全部找回；以该并集为分母会错误惩罚已经完整覆盖事实的结果，并使指标随分块数量变化而失去跨配置可比性。事实是否找全由 Evidence-group Recall 表达。不定义统一 F1，因为 chunk precision 与 evidence-group recall 的计数对象不同，调和平均没有清晰业务含义。

### 8.2 汇总

只对 `answerable=true` 且所属运行状态为 `completed` 的问题计算正式指标；按单份 PDF 和全部 PDF 汇总：

- Macro Chunk Precision@K：逐题 Chunk Precision 的算术平均，每题等权；
- Micro Chunk Precision@K：全部相关返回 chunk 数除以全部实际返回数，总返回为零时为 0；
- Macro Evidence-group Recall@K：逐题 Evidence-group Recall 的算术平均，每题等权；
- Micro Evidence-group Recall@K：全部命中组数除以全部必需组数；
- Question Hit Rate@K：`QuestionHit@K=1` 的问题数除以纳入问题数；
- Complete Coverage Rate@K：完整覆盖问题数除以纳入问题数；
- MRR@K：所有纳入问题 RR 的平均；
- Evidence-group MRR@K：所有问题的全部必需组 `rr(e)` 的平均；
- Macro Cross-document Contamination@K：逐题 Cross-document Contamination 的算术平均，每题等权；
- Micro Cross-document Contamination@K：全部跨文档返回数除以全部实际返回数，总返回为零时为 0。

使用未四舍五入值汇总，展示四位小数和百分比两位小数。无纳入问题时为 `not_applicable`，不能写 0；同分不人为决胜。

版本比较主表使用 Question Hit Rate@K、Macro Evidence-group Recall@K、Complete Coverage Rate@K、Macro Chunk Precision@K、MRR@K 和 Macro Cross-document Contamination@K；三个 Micro 指标及 Evidence-group MRR@K 作为总体量级和多事实排名诊断同时保留。任何单项改善都不能表述为系统整体必然改善。

## 9. 报告与留存

```text
validation/retrieval/
├── comparison.json
├── comparison.md
└── system-v2.0/
    ├── summary.md
    └── runs/
        └── pipeline-<id>__cfg-<fingerprint-short>__k-<K>__<run-id>/
            ├── run.json
            ├── aggregate.json
            └── documents/
                ├── <document-key>.json
                └── <document-key>.html
```

`document-key` 是用于报告文件名的人类可读、文件系统安全的稳定键，不替代 `document_id`；精确生成规则由架构定义。逻辑上的 `evaluation.json` 在物理目录中即 `<document-key>.json`，不额外建立每 PDF 子目录。

`run-id` 必须唯一且稳定引用一次不可变运行。构建 fingerprint、K 或其他运行参数不同必须写入不同目录；同一参数重跑也生成新 `run-id`，不得覆盖。`run.json` 保存运行身份、evaluation_protocol_version、完整 `build_config` 快照及其 fingerprint、完整查询时检索配置；`aggregate.json` 保存按 PDF 及全题集汇总。运行开始前必须验证 `run.json`、test-set manifest 和目标索引 manifest 中的规范化 `build_config` 及 fingerprint 完全一致，不能只比较 hash 而忽略快照内容。

每份 PDF 的 JSON 是该次运行的原始评估事实，至少包含全部问题及原始顺序、预期 evidence group 和证据正文、实际有序 chunk 及正文/来源/距离/相似度、派生组匹配、指标分子分母、逐题指标、异常和审核状态。即使该 PDF 某题零返回或运行失败，也须保存能够说明发生阶段的事实；失败运行不得进入正式聚合。

同目录 HTML 必须只由对应 PDF JSON 派生，按问题 ID 稳定排序，并直接展示问题 ID/文本、按组组织的预期证据正文及 PDF/页码、按排名组织的实际 chunk 正文及 PDF/页码、每个结果命中的组、逐题指标及分子分母、未命中和 `unmappable` 组。不得用 chunk ID 代替正文，不展示 embedding；ID、距离和相似度可放入折叠诊断区域。正文不得截断且必须 HTML 转义。

`summary.md` 仍有必要：JSON 负责完整、可复算的事实，HTML 负责单份 PDF 的逐题审核，而 summary 负责让人无需打开十余个文件即可比较同一系统版本内不同 pipeline、chunk 配置和 K。它记录数据集版本和题数、PDF hash、系统版本、pipeline、collection、fingerprint、固定的一组主要 `build_config` 展示字段、K、运行时间和状态、ComparisonStatus 及配置差异，并展示五份测试集和整体的 Macro/Micro 指标、Question Hit Rate、完整覆盖率、MRR、Evidence-group MRR、污染率、unmappable 数及失败数。展示字段集合由架构统一规定，同一类运行不得临时增删；完整 `build_config` 以链接的 `run.json` 为准。汇总不展开检索正文，须链接选用运行的 JSON 与 HTML；同一配置多次运行时必须明确哪次是正式基线，不能静默选择最新一次。

`comparison.json` 保存参与比较的运行身份、ComparisonStatus、差异原因和允许时的指标差值，是 comparison 的机器事实；`comparison.md` 只能由它生成，并追加各系统版本和 pipeline 的关键配置、整体指标及 summary 链接。ComparisonStatus 按第 3.4、7 节判定；非 `strictly_comparable` 的运行分组展示并说明差异，不计算直接指标差值或提升百分比。

机器可读逐题结果与报告同批留存，具体字段契约由架构定义。报告不能反向成为 ground truth 或 test set 的权威来源。

## 10. 审核与变更控制

- ground truth 和每个配置的 test set 由系统协作者完成标注，用户审核后才从 `draft` 变为 `approved`。
- 已发布数据不得原地修改。修改语义事实须建立新 ground truth 版本，旧版本转为 `superseded`；只改 chunk 映射也须建立新 test set 版本，旧版本转为 superseded。
- 新 ground truth 版本产生后，旧版本关联的 test set 转为 superseded；针对新版本重新生成的映射从 draft 开始审核。
- 相同配置重跑不得覆盖旧运行；新建运行身份，并明确哪次是正式基线。
- 人工说明可以解释失败，但不能覆盖机械计算值。

## 11. 已知限制

- 五份 PDF、24 个问题仅构成项目基线，不能证明对未知文档的统计泛化能力。
- chunk 相关性依赖人工映射，须保留证据正文与审核状态。
- 跨文档污染不能揭示同文档内噪声。
- RR/MRR 只关注首次命中，多事实问题必须结合组召回、完整覆盖和组 MRR。
- `unmappable` 可能由解析、文本化或分块阶段丢失必要内容造成，本评估只揭示现象，不自动归因；仅仅把完整证据拆到多个仍可组合的 chunks 不属于 `unmappable`。

## 12. 阶段验收

1. 五份 PDF 的全部问题都有经人工审核的 ground truth，旧 `expected_index_ids` 已移除；
2. system-v2.0 当前正式基线配置的 pipeline v1、v2 各有一套配置身份完整的 test set；
3. 所有 evidence group 映射通过检查，`unmappable` 被显式保留；
4. 双 pipeline 使用相同问题和 `K` 成功运行，逐题指标可由分子分母复算；
5. 每次正式运行按 PDF 生成五份原始评估 JSON 及五份由其派生的人工审核 HTML；
6. 当前版本 summary、机器可读 comparison.json 和跨版本 comparison.md 已生成并明确可比性；
7. 任一实际 chunk 可追溯到 collection、test set、ground truth 证据和 PDF 页码；
8. 不可回答、零返回、少于 `K`、一个 chunk 覆盖多组、`unmappable`、跨文档污染和运行失败均有测试覆盖。
