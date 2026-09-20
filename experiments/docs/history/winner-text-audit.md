# 现有 winner 的规则文本化调研

状态：讨论依据，不是已确认需求或已实现功能。检查日期：2026-09-10。

## 范围与结论

逐一读取 experiments/output/scoring 中六份 PDF 的全部 14 个 group，按 selected_candidate_id 定位完整 normalized candidate，检查每个物理 cell 的文本、跨度、位置、角色、warning 和未覆盖格；对照原 PDF 中全部相关页面及表格局部。另核对关键异常的 PyMuPDF raw header。

本次没有重跑四工具、改变 winner、修改评分或实现序列化器；没有运行 embedding 或检索评测。结论是规则设计的可行性判断，不是效果实验。

- 14 个 winner：12 个 PyMuPDF、1 个 Camelot、1 个 Docling。
- 14 个均有已定位的 cell，warnings 和 uncovered_grid_positions 均为空；这些字段为空不能证明业务语义正确。
- 13 个候选含 column_header 标签：其中 1 个把数据行误标成表头；另外 1 个 Camelot winner 的全部 31 个 cell 为 unknown，但原表有清晰表头。
- 13 个表格/片段可以在补齐有限且可验证的规则后生成保守、可解释的文本；其中两个存在空白列名，不能凭空命名。剩余 1 个是缺失表头的续表，独立使用本页 winner 无法恢复完整列语义，需要跨片段关联或明确采用较弱表示。

“有结构但表头不可靠”在本样本中有具体例子，不意味着需要 LLM；问题是如何用规则确认角色与关联证据。

## 全部 winner 清单

页码为原 PDF 文件的一基物理页码，形状包含表头行。A–F 只是本报告文件简称，不是管道 ID。特别注意 A 的 PDF 物理第 1 页印刷标注为第 15 页。

| 编号 | PDF | group / 页 | winner | 行×列 | 情况与规则可行性 |
| --- | --- | --- | --- | --- | --- |
| 01 | A | 001 / 1 | camelot_hybrid_p01_t01 | 5×7 | 单层表头＋总计；全部角色 unknown，须识别第一行表头及尾部总计 |
| 02 | B | 001 / 2 | pymupdf_lines_strict_p02_t01 | 4×2 | 普通项目—描述表；可直接按列名与行值组织 |
| 03 | B | 002 / 3 | pymupdf_lines_p03_t01 | 4×3 | 项目—描述—技术资料引用；需保留引用文本，不把文中“页 25–33”当成当前来源页码 |
| 04 | B | 003 / 4 | pymupdf_lines_p04_t01 | 4×5 | 数量、含 IGV 单价和总价；保留列标题中的税与货币限定 |
| 05 | B | 004 / 5 | pymupdf_lines_strict_p05_t01 | 6×10 | 三层交付表头；跨度足以构造交付批次＋相对期限路径，空白保持空白 |
| 06 | B | 005 / 5 | pymupdf_lines_strict_p05_t02 | 2×2 | 左列原表未命名；ELSE 与地址对应，可保留“未命名列 1”，不能仅凭该表补出企业全称 |
| 07 | C | 001 / 1 | docling_default_p01_t01 | 7×5 | 发票商品明细；列名明确，币种 DOLAR AMERICANO 在表格外 |
| 08 | D | 001 / 1 | pymupdf_lines_strict_p01_t01 | 4×5 | 两层表头＋单条明细＋合计；CIF Callao 与 USD 限定不得丢失 |
| 09 | E | 001 / 1 | pymupdf_lines_strict_p01_t01 | 9×5 | 两层表头＋六条明细＋合计；保留原文 UD、金额和型号，不自动纠错 |
| 10 | E | 002 / 2 | pymupdf_lines_strict_p02_t01 | 8×7 | 型号×交付批次及日期；必须绑定第二行日期；空白与 '-' 并存 |
| 11 | F | 001 / 1 | pymupdf_lines_strict_p01_t01 | 7×5 | 两层表头＋四条明细＋合计；保留原始金额分隔符 |
| 12 | F | 002 / 2 | pymupdf_lines_strict_p02_t01 | 27×11 | 三层表头＋3 条全宽产品分段＋21 条公司明细；分段型号必须传递到后续记录 |
| 13 | F | 003 / 3 | pymupdf_lines_strict_p03_t01 | 9×11 | 跨页续表，本页无列标题；首条 ELPU 数据误标 column_header，必须解决前页关联 |
| 14 | F | 004 / 3 | pymupdf_lines_strict_p03_t02 | 7×3 | 交付批次—DDP 日期，首列原表无标题且像序号；表外有影响日期解释的注释 |

## 规则可行性分类

### 一、普通单层明细：02、03、04、07

采用按原列顺序的“列名：值”即可表达主要事实，不需要生成自然语言摘要或翻译。例如 04 的一条记录：

```text
ÍTEM: Ítem 3;
DESCRIPCIÓN: MEDIDOR ELECTRÓNICO TRIFÁSICO DE 3 HILOS;
CANTIDAD: 1,500;
PRECIO UNITARIO CON IGV (US$): 18.0894;
PRECIO TOTAL (US$): 27,134.10.
```

这只是该行的展示示例，不表示必须将每行单独 embedding。整表优先、超限才拆的约定不变。

### 二、明细与合计混合：01、08、09、11

08/09/11 的表头用两行表达：Unit price 下是 CIF Callao，Total Amount 下是 USD，其他三列跨两行。可以依据已确认 span 形成列标题路径，无需猜测。

最后一行是合计，不能套用普通明细规则，输出类似“Item: Total CIF...”会错误归类。01 的合计标签 TOTAL USD 出现在原 Cantidad Total 列的位置，410.529,00 出现在原单价列的位置：这也说明仅机械套用每列标题会生成错误的数量/单价语义。

拟议规则：结合合并布局、明确合计标记与相邻值，把它保留为独立汇总记录，例如 `Total CIF (CALLAO): USD 968,000.00`；不能重新计算后覆盖原总计。模式如何验证仍待确认。

01 的原 PDF 第一行有明确的 item、Matrícula、Descripción、各数量与单价标题，但 Camelot 不提供相应角色，normalized 全部为 unknown。这是角色缺失，不是人眼无法理解表头。可设计顶部行、字段文本与后续行模式的组合规则，或使用有来源的表头证据；不能把“所有表第一行必是表头”作为通用规则。

### 三、带交付维度的多级表头：05、10

05 已有三层：交付计划 → 第几次交付 → 距签署合同多少天。10 有两层：第几次交付 → 日期。跨度与列位置足以构造有序路径。

例如 10 的一项可以写成：

```text
MODEL: HXE13ESX;
2nd Delivery > 10.10.25: 5,000.00.
```

不能只写“第 2 次交付：5,000”，把日期丢掉；也不必把 `10.10.25` 转成推定的标准日期。05 的相对天数是合同条件，不能无依据换算成实际日历日期。原表空白不自动改为零，'-' 不自动改为空白。

当前样本已经包含多级表头，因此参考文档中“第一版只支持单层”的范围过窄。这里需要的是利用已经恢复的跨度，尚不是任意复杂表头自动推断。

### 四、多级表头与表内分段：12

三条产品分段都是 colspan=11 的单元格，内容分别包含 ITEM、产品名称和 MODEL，但 normalized 角色均为 body。它们不是公司明细，也不是整张表的新列标题。

例如第一个分段给出 HXE12ES，而后面的 ELUC 行自身只写 Single Phase Meter 2 wires。若生成或拆分文本时不继承分段上下文，查询“ELUC 的 HXE12ES 交付计划”就缺少型号证据。

拟议规则：满足全宽分段形态且含明确产品标识时建立分段作用域，持续到下一分段。例如：

```text
产品分段：ITEM 1: SINGLE PHASE METER 2 WIRES - MODEL HXE12ES
COMPANY: ELUC; ITEM: 1; UNIT: UNIT; TOTAL REQUIREMENT: 27,000;
QTY AND DELIVERY SCHEDULE > 1st Delivery > (At 170 days of contract signature): 2,000.
```

不能把分段文本填到 11 个独立字段，也不能只保留该表第一条产品分段。此表还有后续页，不能把本页称为该产品或公司清单的全部。

### 五、原始空白列标题：06、14

这两份表格不是工具丢掉了表头，而是原 PDF 的对应标题单元格就为空。

- 06：`[未命名列 1]: ELSE; LUGAR DE ENTREGA: Almacén Central ELSE ...` 可以保留关联。但把 ELSE 自动解释为公司全称或把左列正式命名为 COMPANY，需要额外证据。
- 14：左列 1–6 是序列状内容，可暂按未命名列保留；DELIVERIES 与 DDP DATE 两列本身清楚。是否将左列判为辅助序号、如何表示，仍是待确认规则。

应区分“缺一列业务名称”与“整表不能使用”。这些样本能够生成保守有用的文本，不应只因一个空白标题就整表丢弃。

### 六、缺表头的续表及误标：13

这是本次最明确的表头错误。

- PDF 第 2 页末尾是 ITEM 3 的各公司需求与交付明细，末行是 ELSE。
- PDF 第 3 页开头继续 ITEM 3 的 ELPU 行，随后才出现 ITEM 4 分段。
- 第 3 页这个片段没有重印列标题。
- winner 将 `ELPU / 3 / Three Phase 3 wires / UNIT / 100 / ...` 整行标为 column_header。
- PyMuPDF 原始 header.names 正是这些数据，external=False；误标源自工具提供的 header，而不是本次调研推测。

若盲目信任角色，会删除第一条真实记录，并把 ELPU、3、100 当成后续记录的字段名。只读第 3 页 winner 无法知道后六列分别是哪次交付、什么期限。

原 PDF 视觉上明确延续前页：相邻页、11 列对齐、前页 ITEM 3 持续到本页首行、之后切换 ITEM 4。可设计有证据的表头/分段继承，但这些证据的检查门槛尚未确认，不能仅凭“列数相同”套用。

需要区分两类跨页：本例在现有流水线中是两个单页 slot、两个已选优 winner；并不是一个 provenance 跨页而 deferred 的 slot。因此，排除 cross_page_slot 不会消除本例。

可讨论的方向是“保持两个物理片段，建立前页表头和分段引用”，不一定立即实现完整物理表合并。关联必须保留前页表头来源，不能伪装成本页原生标题。

## 共同但不同于表头的问题

1. **外部限定信息。** 07 的 DOLAR AMERICANO 位于表外；14 的注释说明 DDP 日期包含强制 SGS 检验，且是客户仓库要求的最迟日期。这些不在 winner cells 内，文档融合时需要有证据的上下文关联；不能把任意邻近段落全部拼上。
2. **换行与断词。** 05 的 REQUERIMI/ENTO、ENTREG/A 是行内断词；普通空白折叠未必恢复正确词形。但数值与型号也可能含换行，不能普遍删除所有换行并拼接。原表还有 TEM 字样，不能默认改成 ÍTEM。
3. **源文档自身差异。** 09 原 PDF 就写 UD 218,400.00；10 原 PDF 就写 HXE12ESX/HXE13ESX，而前页明细写 HXE12ES/HXE13ES。序列化不应静默统一币种拼写、型号或重算修正金额。
4. **长度。** 12 的大表和带长说明的表需要在最终文本生成后按实际 tokenizer 计数。尚未选择最终模板，本报告不报告精确 token 数，也没有验证检索收益。

## 建议的后续讨论顺序

1. 先确认物理结构、列标题路径、普通数据行、汇总行、产品分段的规则职责；示例输出保留原语言和原数值。
2. 单独讨论 01 的无角色表头，以及 06/14 的真实空白列名；不要与错误表头混为一谈。
3. 优先讨论 13 的续表关联与第一条数据恢复；它直接决定后续所有列值能否正确解释。
4. 再确认表外币种/注释的关联、空白及符号保留，最后确定 token 预算和超限拆分。

先覆盖这里已经出现的六类情况，再扩展到本样本未出现的复杂矩阵、合并数值或更复杂行层级。本次调研没有发现必须依赖 LLM 才能解决的情形；但用规则实现的正确性仍需要具体规则、反例与检索评测验证。

## 原始资料与复核路径

以下文件为本地样本，PDF 与 output 均未纳入 Git；缺少本地文件时不能仅靠本报告重建它们。

- A：`documents/Contrato 5000000202 - HEXING ELECTRICAL - v1-15.pdf`（当前本地样本缺失；仅保留历史文件名事实）
- B：[Contrato N 105-2025 LP-002-2024-FONAFE Adquisición de medidores ítem 1 3 y 4 - HEXING ELECTRICAL (1)[R][R].pdf](<../../../documents/Contrato N 105-2025 LP-002-2024-FONAFE Adquisición de medidores ítem 1 3 y 4 - HEXING ELECTRICAL %281%29[R][R].pdf>)
- C：[E001-602.pdf](<../../../documents/E001-602.pdf>)
- D：[ORDER PERU 24-12 ANDET.pdf](<../../../documents/ORDER PERU 24-12 ANDET.pdf>)
- E：[ORDER PERU 25-15 ANDET VF.pdf](<../../../documents/ORDER PERU 25-15 ANDET VF.pdf>)
- F：[签字-ORDER PERU 25-19 FONAFE.pdf](<../../../documents/签字-ORDER PERU 25-19 FONAFE.pdf>)

每个编号对应的规范化输入与选优报告：

| 编号 | winner 规范化文件 | 选优记录 |
| --- | --- | --- |
| 01 | [camelot_hybrid_p01_t01](<../../output/extracting/camelot/Contrato 5000000202 - HEXING ELECTRICAL - v1-15/normalized/hybrid/tables.json>) | [group_001](<../../output/scoring/Contrato 5000000202 - HEXING ELECTRICAL - v1-15/scoring.json>) |
| 02 | [pymupdf_lines_strict_p02_t01](<../../output/extracting/pymupdf/Contrato N 105-2025 LP-002-2024-FONAFE Adquisición de medidores ítem 1 3 y 4 - HEXING ELECTRICAL (1)[R][R]/normalized/lines_strict/tables.json>) | [group_001](<../../output/scoring/Contrato N 105-2025 LP-002-2024-FONAFE Adquisición de medidores ítem 1 3 y 4 - HEXING ELECTRICAL (1)[R][R]/scoring.json>) |
| 03 | [pymupdf_lines_p03_t01](<../../output/extracting/pymupdf/Contrato N 105-2025 LP-002-2024-FONAFE Adquisición de medidores ítem 1 3 y 4 - HEXING ELECTRICAL (1)[R][R]/normalized/lines/tables.json>) | [group_002](<../../output/scoring/Contrato N 105-2025 LP-002-2024-FONAFE Adquisición de medidores ítem 1 3 y 4 - HEXING ELECTRICAL (1)[R][R]/scoring.json>) |
| 04 | [pymupdf_lines_p04_t01](<../../output/extracting/pymupdf/Contrato N 105-2025 LP-002-2024-FONAFE Adquisición de medidores ítem 1 3 y 4 - HEXING ELECTRICAL (1)[R][R]/normalized/lines/tables.json>) | [group_003](<../../output/scoring/Contrato N 105-2025 LP-002-2024-FONAFE Adquisición de medidores ítem 1 3 y 4 - HEXING ELECTRICAL (1)[R][R]/scoring.json>) |
| 05 | [pymupdf_lines_strict_p05_t01](<../../output/extracting/pymupdf/Contrato N 105-2025 LP-002-2024-FONAFE Adquisición de medidores ítem 1 3 y 4 - HEXING ELECTRICAL (1)[R][R]/normalized/lines_strict/tables.json>) | [group_004](<../../output/scoring/Contrato N 105-2025 LP-002-2024-FONAFE Adquisición de medidores ítem 1 3 y 4 - HEXING ELECTRICAL (1)[R][R]/scoring.json>) |
| 06 | [pymupdf_lines_strict_p05_t02](<../../output/extracting/pymupdf/Contrato N 105-2025 LP-002-2024-FONAFE Adquisición de medidores ítem 1 3 y 4 - HEXING ELECTRICAL (1)[R][R]/normalized/lines_strict/tables.json>) | [group_005](<../../output/scoring/Contrato N 105-2025 LP-002-2024-FONAFE Adquisición de medidores ítem 1 3 y 4 - HEXING ELECTRICAL (1)[R][R]/scoring.json>) |
| 07 | [docling_default_p01_t01](<../../output/extracting/docling/E001-602/normalized/default/tables.json>) | [group_001](<../../output/scoring/E001-602/scoring.json>) |
| 08 | [pymupdf_lines_strict_p01_t01](<../../output/extracting/pymupdf/ORDER PERU 24-12 ANDET/normalized/lines_strict/tables.json>) | [group_001](<../../output/scoring/ORDER PERU 24-12 ANDET/scoring.json>) |
| 09 | [pymupdf_lines_strict_p01_t01](<../../output/extracting/pymupdf/ORDER PERU 25-15 ANDET VF/normalized/lines_strict/tables.json>) | [group_001](<../../output/scoring/ORDER PERU 25-15 ANDET VF/scoring.json>) |
| 10 | [pymupdf_lines_strict_p02_t01](<../../output/extracting/pymupdf/ORDER PERU 25-15 ANDET VF/normalized/lines_strict/tables.json>) | [group_002](<../../output/scoring/ORDER PERU 25-15 ANDET VF/scoring.json>) |
| 11 | [pymupdf_lines_strict_p01_t01](<../../output/extracting/pymupdf/签字-ORDER PERU 25-19 FONAFE/normalized/lines_strict/tables.json>) | [group_001](<../../output/scoring/签字-ORDER PERU 25-19 FONAFE/scoring.json>) |
| 12 | [pymupdf_lines_strict_p02_t01](<../../output/extracting/pymupdf/签字-ORDER PERU 25-19 FONAFE/normalized/lines_strict/tables.json>) | [group_002](<../../output/scoring/签字-ORDER PERU 25-19 FONAFE/scoring.json>) |
| 13 | [pymupdf_lines_strict_p03_t01](<../../output/extracting/pymupdf/签字-ORDER PERU 25-19 FONAFE/normalized/lines_strict/tables.json>) | [group_003](<../../output/scoring/签字-ORDER PERU 25-19 FONAFE/scoring.json>) |
| 14 | [pymupdf_lines_strict_p03_t02](<../../output/extracting/pymupdf/签字-ORDER PERU 25-19 FONAFE/normalized/lines_strict/tables.json>) | [group_004](<../../output/scoring/签字-ORDER PERU 25-19 FONAFE/scoring.json>) |
