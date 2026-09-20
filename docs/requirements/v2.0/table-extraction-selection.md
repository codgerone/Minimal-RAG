# 表格提取、准入、分组与选优需求

本文是 RAG 的现行独立规格，完整定义表格选择规则。上游输入为数字型 PDF，下游把选定表格写入 ParsedDocument。全链路及审核需求见[系统需求](../../requirements.md)，本次变化见 [V2.0 增量需求](../../changes/v2.0/proposal.md)。当前实现与验收以本规格为准。

## 1. 输入、输出与处理顺序

```text
PDF → 四工具九策略 → cell 级候选
Docling 原生表格 → slot
候选与 slot 几何准入 → 每 slot 一组 → 四指标评分 → 唯一 winner
```

输入必须是同一份可打开的 PDF；记录文件身份、工具版本、有效配置和页级执行结果。不得混用不同 PDF 或不同内容版本的候选。计算使用结构化结果，不读取人工审核 HTML。

每个输入候选保留一份几何判定和准入结论；每次实际比较保留覆盖率；每个 slot 保留一组，包括未评分组。每个可评分组保留全部成员、指标计数和 winner。默认产物及详细诊断的保存范围由增量需求 I-04 规定。

## 2. 四工具提取与结构恢复

### 2.1 策略及配置

| 工具 | 策略及关键配置 | 使用的原始事实 |
| --- | --- | --- |
| PyMuPDF | lines、lines_strict、text | 表格/行/cell 区域、文本矩阵、header 信息 |
| Camelot | lattice、stream、network、hybrid；仅 lattice/hybrid 传 copy_text=None、shift_text=['l','t'] | 原子格、四边状态、文本及解析报告 |
| Docling | do_ocr=False、do_table_structure=True、accurate、do_cell_matching=True、generate_page_images=False | 完整文档及原生 TableItem、cell、provenance；不把未消费的页图嵌入 raw artifact |
| Unstructured | hi_res、infer_table_structure=True | Element 原文、Table 的 text_as_html、metadata |

九策略都执行，不因前一策略已得到表格而跳过其余策略。某策略某页失败后继续下一页；成功页结果保留。初始化未执行、页面执行失败、成功零表、成功有表分别记录。可选工具失败不阻止其他工具运行。

### 2.2 统一结构

TableCandidate 保存候选 ID、工具/策略、原始引用、全部页面区域、行列数、物理 cell、未覆盖位置及诊断。TableCell 保存文字、行列范围、跨度、跨度来源、可用 bbox 及原始引用。

行列范围为零基半开区间，span 为终点减起点。一个合并格是一个物理 cell，不把内容复制到每个覆盖位置。区分已知空格、未知文字、合并覆盖位置及缺失记录。无法恢复结构时保留可读文字和原因，不造一列网格。

### 2.3 工具适配要求

| 工具 | 跨度恢复 | cell bbox 含义与限制 |
| --- | --- | --- |
| PyMuPDF | 没有直接采用的数值 span；格边界按 2.0 pt 聚类并映射到逻辑行列，标 geometry_inferred | 识别/推断的格区域；text 策略不保证存在真实绘制线，不能仅凭空位置推定合并 |
| Camelot | hspan/vspan 为布尔值；相邻原子格双侧均缺共享边时连通，只有矩形连通分量恢复为合并格，标 edge_inferred | 原子网格区域，合并框取组成格联合，不是内部文字框 |
| Docling | 使用原生 span 和 offsets，标 native；相互矛盾时保留原事实并标 unavailable | 可选的模型/匹配后处理区域，可能对齐文字；不得据此重推合法原生跨度 |
| Unstructured | 按 HTML 行序、rowspan/colspan 和占位恢复；缺省合法 span 为 1，标 native | 本版输入契约不使用 cell bbox；整表 Element 区域不能分摊成虚构的格框 |

Camelot 在确认同一合并格后，仅去除重复的完整源格文本：xyz、xyz 输出 xyz；不同格相同文字、单格内部重复词保留。不同片段按原子行列顺序合并并保存组成格引用。

PyMuPDF 的退化区间、重叠和矩阵维度矛盾必须诊断；Camelot 非矩形分量不得硬合并；Docling 缺 cell-to-page 关系时不猜页码；Unstructured 缺 HTML 或非法 span 时保留文字，不伪造结构。表头 role 是源事实，不代表 RAG 已确认它是表头。

### 2.4 坐标转换及支持范围

公共空间为原 PDF 页面左上原点、向右/向下、单位 pt，标识 pymupdf_page_top_left_pt_v1。

| 来源 | 转换要求 |
| --- | --- |
| PyMuPDF | 已验证的无旋转、无裁剪偏移页面采用页面坐标；不得把旋转后 page.rect 与未旋转操作坐标直接混用 |
| Camelot | 左下原点；页面高 H 时，把矩形 (x0,y0,x1,y1) 转为 (x0,H-y1,x1,H-y0) |
| Docling | 逐 bbox 读取 coord_origin；TOPLEFT 保持方向，BOTTOMLEFT 按页面高翻转；图像尺度须先正确还原 |
| Unstructured | 已知 PixelSpace 左上像素；用 PDF宽/layout_width、PDF高/layout_height 分别缩放 x/y，不固定假设 DPI |

来源单位、尺寸或变换未知时标不可转换。旋转或 CropBox/MediaBox 不一致且无可靠变换证据时，不进入几何比较。表级框与 cell 框分开保存，不以文字框冒充格边界。

页面尺寸必须有限且为正，与 PDF 对应页每方向差不超过 1.0 pt。bbox 四值有限且面积为正；仅允许 **1e-6 pt** 页面越界尾差并裁剪，更大越界无效。跨度聚类容差 2.0 pt 与比例比较 epsilon 1e-12 不混用。

## 3. Slot 与候选准入

### 3.1 建立 slot

Docling 每个原生 TableItem 对应一个 slot，按原生表格顺序生成 slot_001 等稳定 ID，保留原生引用。全部 provenance 在同一有效页面、每个 bbox 可转换且有效时为 eligible；同页多个框先各自转换，再取最小外接矩形。

否则为 deferred，不接纳候选、不评分，但原生表格交由 ParsedDocument 保留。TableSlot.deferred_reason 取值：

| 原因 | 条件 |
| --- | --- |
| cross_page_slot | provenance 涉及多个页面 |
| missing_slot_provenance | 无可用 provenance |
| missing_slot_bbox | provenance 缺框 |
| slot_coordinate_conversion_failed | 无可靠转换 |
| invalid_slot_bbox | 转换后非有限、非正面积或超界 |
| invalid_slot_page_geometry | 页码或页面尺寸无效 |

### 3.2 候选几何检查

候选全部 regions 可归到同一有效页面、尺寸与 PDF 一致且框有效时为 comparable。同页多个 region 取外接矩形，不只挑其中一个。否则为 deferred，不创建匹配评价。

CandidateView.deferred_reason：cross_page_candidate（跨页或无法唯一归页）、missing_candidate_region（无区域）、missing_candidate_bbox（缺框）、candidate_coordinate_conversion_failed（转换不可靠）、invalid_candidate_bbox（非有限/非正/越界）、invalid_candidate_page_geometry（页码或尺寸无效/不符）。

### 3.3 同页双向覆盖

只比较 comparable 候选与同页 eligible slot。C 为候选框，S 为 slot 框：

```text
candidate_coverage = area(C ∩ S) / area(C)
slot_coverage = area(C ∩ S) / area(S)
iou = area(C ∩ S) / area(C ∪ S)
```

两项覆盖率分别加 1e-12 后达到 **0.65、0.77**，该对为 accepted，原因 bidirectional_coverage_passed；否则 rejected，逐项记录 candidate_coverage_below_minimum、slot_coverage_below_minimum，可同时出现。

不扩框，不用 IoU 单独准入，不增加文本相似度、中心距离或工具专用阈值。Docling 自身候选也执行相同公式。不同页不创建虚假的 rejected pair。

### 3.4 候选级结论

| 按顺序判断 | 结论及原因 | 下游 |
| --- | --- | --- |
| 候选几何 deferred | processing_status=deferred，沿用几何原因，decision=null | 不比较、不入组 |
| Docling 对应页确实执行失败 | deferred，slot_source_page_failed | 无可靠 slot 来源，不当作成功零表 |
| 无同页 eligible slot | completed/rejected，no_eligible_table_slot_on_page | 不入组 |
| 比较后零个 accepted | completed/rejected，no_table_slot_passed_bidirectional_coverage | 不入组 |
| 恰好一个 accepted | completed/admitted，matched_single_table_slot | 唯一 matched_slot_id |
| 至少两个 accepted | completed/rejected，multiple_table_slots_passed_bidirectional_coverage | 不选最高者、不多组复用 |

保留 evaluated_slot_ids、accepted_slot_ids 和 matched_slot_id。未比较、比较不通过、同时命中多个必须能区分。页失败事实的处理不等于已确定整文档索引发布政策；该政策仍见增量需求 U-03。

## 4. 分组

每个 slot 固定一组，按 matched_slot_id 聚合 admitted 候选，不执行候选之间的相似匹配。同工具、同策略多个候选可以共存，不降权或先去重。一个候选最多一组，不同页不混组。

TableGroup.status 仅为 ready_for_scoring（eligible slot 且至少一个 admitted 成员）或 unresolved。unresolved_reason 为 slot 的延后原因，或 no_admitted_candidate；ready 时原因为 null。单成员组正常评分，空组不伪造 winner。

有效页面/框的组按页码、框 y0、x0、slot_id 稳定排序，无有效位置的按原 slot 顺序排后；group ID 按最终顺序生成。评分不可修改组成员。

## 5. 四指标评分与确定 winner

### 5.1 文本参照和规范化

读取原 PDF word（PyMuPDF get_text 的 words 模式，sort=False），不调用表格识别结果构建标准答案。word 中心点落在 slot bbox 闭区间内即归入，框不扩张；按 block_no、line_no、word_no、y0、x0 稳定排序，规范化后为空的 word 不进入多重集。同组共用同一参照。

参照的一个原生 word 是一个 token；候选遍历物理 cell，每个 text 读一次，规范化后按 Unicode 空白切 token，不跨 cell 拼接。两边使用相同规范化：NFKC、首尾去空白、casefold；仅在两个 Unicode 十进制数字之间，将直/弯撇号、modifier apostrophe、acute 及 NFKC 组合重音统一为 ASCII 撇号，移除该分隔符两侧不含换行的噪声空白。其余标点保留，不交换逗号和小数点。

例如 1´ 131 与 1’131 可等价；5 00 不能拼成 500。参照 R 与候选 C 都是多重集，保留重复次数。评分 token 不等于 embedding tokenizer 的 token。

### 5.2 原始指标

| 指标 | 计算 | 方向与边界 |
| --- | --- | --- |
| text_f1 | M=Σ min(count_R(t),count_C(t))；precision=M/\|C\|，recall=M/\|R\|，F1=2PR/(P+R) | 越高越好；R非空而C空/不可读或P+R=0时为0；R空为 not_evaluable |
| critical_token_integrity | K 为 R 中至少含一个 Unicode 十进制数字的 token 多重集；Σ min(count_K(t),count_C(t))/\|K\| | 越高越好；完整 token 匹配，不拼碎片；K空为 not_applicable |
| shape_support | 同组与本候选 (row_count,column_count) 相同的候选数 / 组成员数 | 越高越好；维度缺失、非整数或非正为0并诊断；各候选独立计数 |
| blank_anomaly | max(0, blank_ratio − reference_blank_ratio) | 越低越好；具体定义如下 |

blank_ratio = 1 − 非空格有效跨度覆盖的位置并集大小 / (row_count × column_count)。非空指 text.strip() 非空；合并延续位置计入非空覆盖，重叠只计一次。跨度只在可验证的有效区间内计数，不猜异常跨度；网格无法建立时为 not_evaluable，保留异常事实。

参照空白率：用空白位置数/总位置数的整数交叉乘积判等，统计所有可计算成员；存在唯一且至少重复两次的众数时取众数，否则取最小值。不得先四舍五入。低于参照者异常为0，不再加分。

### 5.3 相对分与总分

每个指标在同组两两比较：较优得1、相等得0.5、较差得0；双方均不可评价/不适用视为相等，仅一方可评价则该方胜。数值相等的绝对容差为 1e-12。

```text
N > 1：relative_score = (wins + 0.5 × ties) / (N − 1)
N = 1：四项 relative_score 均为 1，但仍计算原始指标
total_score = 四项 relative_score 各乘 0.25 后求和
```

不能静默删成员，不设置单项或总分淘汰线，不跨组比较总分。取最高总分，容差内平分按 pymupdf > camelot > docling > unstructured，再按 candidate_id 升序决定。每个 ready 组恰有一个 winner。

选择原因仅为 highest_total_score、tool_priority_tiebreak、candidate_id_tiebreak，分别表示分数直接决定、工具优先级决定、候选 ID 决定。

### 5.4 可复核输出

保留参照及候选 token 计数、匹配/未匹配、关键 token 匹配、形状计数、网格覆盖计数、空白率频次和参照来源、每项胜平负及相对分、权重、总分、并列集合、winner 和原因。计算不提前舍入，展示可以格式化。

## 6. 接入和验收

winner 经表头判定、文本化和 chunking 后，必须进入[系统需求 R-01 的逐 PDF 审核视图](../../requirements.md)。没有 winner 的原生回退表格不冒充 winner。

验收至少覆盖：九策略配置；坐标翻转/缩放与越界阈值；矩形合并与空格；失败页后续成功；同页零/一/多命中；空组、单成员和同工具多成员；重复词、数字撇号、空参照；形状异常、合并覆盖、众数并列；全部同分的确定性选择。实现不依赖实验代码、文档或产物。
