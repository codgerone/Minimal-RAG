# 表格候选准入人工标注表

## 填写目的

本表保存双向覆盖率阈值校准所使用的人工真值和逐候选计算结果。最终阈值及其推导、冲突样本和取舍记录在 [阈值报告](../../docs/calibration/admission-thresholds.md)；本表不承担阈值决策说明。

候选顺序严格来自当前 `group_views_for_manual_review`：先按 `group_001 → group_002 → …`，再保持每个 HTML 内 candidate 的展示顺序。`当前审核组` 只用于帮助定位旧版人工审核视图，不代表新版 Table Slot 分组真值。

## Docling Table Slot 对照

下表按 Docling `document.json.tables` 的原生顺序记录校准样本的 slot 映射；实际运行同样按此规则生成 ID：

请为每个卡位填写 `卡位真实性`：`table`、`not_table` 或 `uncertain`。这是在校验“Docling 识别出的卡位本身是否真是表格”；候选的双向覆盖率无法纠正错误卡位。

| PDF | slot_id | 对应 Docling candidate_id | 卡位真实性 | 卡位备注 |
|---|---|---|---|---|
| E001-602.pdf | slot_001 | docling_default_p01_t01 | table |  |
| ORDER PERU 24-12 ANDET.pdf | slot_001 | docling_default_p01_t01 | table |  |
| ORDER PERU 25-15 ANDET VF.pdf | slot_001 | docling_default_p01_t01 | table |  |
| ORDER PERU 25-15 ANDET VF.pdf | slot_002 | docling_default_p02_t02 | table |  |
| 签字-ORDER PERU 25-19 FONAFE.pdf | slot_001 | docling_default_p01_t01 | table |  |
| 签字-ORDER PERU 25-19 FONAFE.pdf | slot_002 | docling_default_p02_t02 | table |  |
| 签字-ORDER PERU 25-19 FONAFE.pdf | slot_003 | docling_default_p03_t03 | table |  |
| 签字-ORDER PERU 25-19 FONAFE.pdf | slot_004 | docling_default_p03_t04 | table |  |

## 填写规则

候选只保留两个真值字段：

- `准入标签`：`admit` 表示候选范围足以代表一个完整 Table Slot；`reject` 表示不应进入任何 slot group。
- `比较/目标 slot_id`：`admit` 时是人工指定的唯一目标 slot；`reject` 时由校准工具写入同页双向重叠最强的比较 slot；没有同页 slot 时为 `none`。它不改变人工 reject 结论。

这里不评价单元格恢复质量。候选只要完整覆盖目标表格，即使列切分、文本或合并单元格恢复很差，仍标为 `admit`，质量问题留给 scoring 处理。

## 覆盖率与阈值统计说明

- `candidate_coverage = 交集面积 / candidate bbox 面积`：候选自身有多大比例落在比较的 Docling Table Slot 内。
- `slot_coverage = 交集面积 / slot bbox 面积`：Docling Table Slot 有多大比例被候选覆盖。
- `N/A` 表示该 candidate 所在页面没有 Docling Table Slot，因此不存在可比较的 bbox，覆盖率在数学上未定义，并不等于 `0`。这类候选直接按 `no_eligible_table_slot_on_page` 拒绝，不参与覆盖率阈值的 TP/FP/FN/TN 统计。
- `TP`（真正例）：人工标注为 `admit`，且覆盖率阈值判断为准入。
- `FP`（假正例）：人工标注为 `reject`，但覆盖率阈值错误地将其准入。
- `FN`（假负例）：人工标注为 `admit`，但覆盖率阈值错误地将其拒绝。
- `TN`（真负例）：人工标注为 `reject`，且覆盖率阈值判断为拒绝。

[打开交互式二维覆盖率图](../../output/calibration/admission-coverage-chart.html)

[打开交互式二维覆盖率图](../../output/calibration/admission-coverage-chart.html)

## 标注表

`admit` 行与人工指定的目标 slot 比较；`reject` 行与同页双向重叠最强的 slot 比较。没有同页 slot 时覆盖率为 `N/A`。

| 序号 | PDF | 当前审核组 | candidate_id | 准入标签 | 比较/目标 slot_id | candidate_coverage | slot_coverage |
|---:|---|---|---|---|---|---:|---:|
| 1 | E001-602.pdf | group_001 | camelot_hybrid_p01_t01 | reject | slot_001 | 0.255399 | 1.000000 |
| 2 | E001-602.pdf | group_001 | camelot_stream_p01_t03 | reject | slot_001 | 0.069512 | 0.065442 |
| 3 | E001-602.pdf | group_001 | pymupdf_text_p01_t01 | reject | slot_001 | 0.276192 | 0.981444 |
| 4 | E001-602.pdf | group_001 | unstructured_hi_res_p01_t01 | reject | slot_001 | 0.399044 | 0.985552 |
| 5 | E001-602.pdf | group_002 | camelot_network_p01_t01 | reject | slot_001 | 0.473664 | 0.735053 |
| 6 | E001-602.pdf | group_002 | camelot_stream_p01_t02 | admit | slot_001 | 0.768290 | 0.920793 |
| 7 | E001-602.pdf | group_002 | docling_default_p01_t01 | admit | slot_001 | 1.000000 | 1.000000 |
| 8 | E001-602.pdf | group_003 | camelot_stream_p01_t01 | reject | slot_001 | 0.054520 | 0.067029 |
| 9 | E001-602.pdf | group_004 | pymupdf_lines_p01_t01 | reject | slot_001 | 0.000000 | 0.000000 |
| 10 | E001-602.pdf | group_004 | pymupdf_lines_strict_p01_t01 | reject | slot_001 | 0.000000 | 0.000000 |
| 11 | ORDER PERU 24-12 ANDET.pdf | group_001 | camelot_hybrid_p01_t01 | admit | slot_001 | 1.000000 | 0.981133 |
| 12 | ORDER PERU 24-12 ANDET.pdf | group_001 | camelot_lattice_p01_t01 | admit | slot_001 | 1.000000 | 0.981133 |
| 13 | ORDER PERU 24-12 ANDET.pdf | group_001 | camelot_stream_p01_t01 | admit | slot_001 | 0.791302 | 0.717766 |
| 14 | ORDER PERU 24-12 ANDET.pdf | group_001 | docling_default_p01_t01 | admit | slot_001 | 1.000000 | 1.000000 |
| 15 | ORDER PERU 24-12 ANDET.pdf | group_001 | pymupdf_lines_p01_t01 | admit | slot_001 | 1.000000 | 0.973893 |
| 16 | ORDER PERU 24-12 ANDET.pdf | group_001 | pymupdf_lines_strict_p01_t01 | admit | slot_001 | 1.000000 | 0.977916 |
| 17 | ORDER PERU 24-12 ANDET.pdf | group_001 | unstructured_hi_res_p01_t01 | admit | slot_001 | 1.000000 | 0.980710 |
| 18 | ORDER PERU 24-12 ANDET.pdf | group_002 | camelot_hybrid_p01_t02 | reject | slot_001 | 0.192356 | 0.996225 |
| 19 | ORDER PERU 24-12 ANDET.pdf | group_003 | camelot_hybrid_p01_t03 | reject | slot_001 | 0.133064 | 0.919083 |
| 20 | ORDER PERU 24-12 ANDET.pdf | group_003 | camelot_network_p01_t01 | reject | slot_001 | 0.133064 | 0.919083 |
| 21 | ORDER PERU 24-12 ANDET.pdf | group_003 | pymupdf_text_p01_t01 | reject | slot_001 | 0.129589 | 0.985402 |
| 22 | ORDER PERU 24-12 ANDET.pdf | group_004 | camelot_hybrid_p02_t04 | reject | none | N/A | N/A |
| 23 | ORDER PERU 24-12 ANDET.pdf | group_004 | camelot_network_p02_t02 | reject | none | N/A | N/A |
| 24 | ORDER PERU 24-12 ANDET.pdf | group_004 | camelot_stream_p02_t05 | reject | none | N/A | N/A |
| 25 | ORDER PERU 24-12 ANDET.pdf | group_004 | pymupdf_text_p02_t01 | reject | none | N/A | N/A |
| 26 | ORDER PERU 24-12 ANDET.pdf | group_005 | camelot_stream_p01_t02 | reject | slot_001 | 0.000000 | 0.000000 |
| 27 | ORDER PERU 24-12 ANDET.pdf | group_006 | camelot_stream_p01_t03 | reject | slot_001 | 0.000000 | 0.000000 |
| 28 | ORDER PERU 24-12 ANDET.pdf | group_007 | camelot_stream_p01_t04 | reject | slot_001 | 0.000000 | 0.000000 |
| 29 | ORDER PERU 24-12 ANDET.pdf | group_008 | pymupdf_lines_p01_t02 | reject | slot_001 | 0.000000 | 0.000000 |
| 30 | ORDER PERU 24-12 ANDET.pdf | group_009 | pymupdf_lines_p01_t03 | reject | slot_001 | 0.000000 | 0.000000 |
| 31 | ORDER PERU 25-15 ANDET VF.pdf | group_001 | camelot_hybrid_p01_t01 | admit | slot_001 | 1.000000 | 0.989576 |
| 32 | ORDER PERU 25-15 ANDET VF.pdf | group_001 | camelot_lattice_p01_t01 | admit | slot_001 | 1.000000 | 0.989576 |
| 33 | ORDER PERU 25-15 ANDET VF.pdf | group_001 | camelot_network_p01_t01 | admit | slot_001 | 0.717735 | 0.839650 |
| 34 | ORDER PERU 25-15 ANDET VF.pdf | group_001 | camelot_stream_p01_t02 | admit | slot_001 | 1.000000 | 0.822049 |
| 35 | ORDER PERU 25-15 ANDET VF.pdf | group_001 | docling_default_p01_t01 | admit | slot_001 | 1.000000 | 1.000000 |
| 36 | ORDER PERU 25-15 ANDET VF.pdf | group_001 | pymupdf_lines_p01_t01 | admit | slot_001 | 1.000000 | 0.984945 |
| 37 | ORDER PERU 25-15 ANDET VF.pdf | group_001 | pymupdf_lines_strict_p01_t01 | admit | slot_001 | 1.000000 | 0.988852 |
| 38 | ORDER PERU 25-15 ANDET VF.pdf | group_001 | unstructured_hi_res_p01_t01 | admit | slot_001 | 0.997257 | 0.994329 |
| 39 | ORDER PERU 25-15 ANDET VF.pdf | group_002 | camelot_hybrid_p02_t02 | admit | slot_002 | 1.000000 | 0.985948 |
| 40 | ORDER PERU 25-15 ANDET VF.pdf | group_002 | camelot_lattice_p02_t02 | admit | slot_002 | 1.000000 | 0.985948 |
| 41 | ORDER PERU 25-15 ANDET VF.pdf | group_002 | camelot_network_p02_t03 | admit | slot_002 | 0.787131 | 0.973994 |
| 42 | ORDER PERU 25-15 ANDET VF.pdf | group_002 | camelot_stream_p02_t04 | admit | slot_002 | 0.949103 | 0.971244 |
| 43 | ORDER PERU 25-15 ANDET VF.pdf | group_002 | docling_default_p02_t02 | admit | slot_002 | 1.000000 | 1.000000 |
| 44 | ORDER PERU 25-15 ANDET VF.pdf | group_002 | pymupdf_lines_p02_t01 | admit | slot_002 | 1.000000 | 0.985324 |
| 45 | ORDER PERU 25-15 ANDET VF.pdf | group_002 | pymupdf_lines_strict_p02_t01 | admit | slot_002 | 1.000000 | 0.986094 |
| 46 | ORDER PERU 25-15 ANDET VF.pdf | group_002 | unstructured_hi_res_p02_t02 | admit | slot_002 | 0.998798 | 0.992765 |
| 47 | ORDER PERU 25-15 ANDET VF.pdf | group_003 | camelot_hybrid_p02_t03 | reject | slot_002 | 0.000000 | 0.000000 |
| 48 | ORDER PERU 25-15 ANDET VF.pdf | group_003 | camelot_network_p02_t04 | reject | slot_002 | 0.000000 | 0.000000 |
| 49 | ORDER PERU 25-15 ANDET VF.pdf | group_003 | camelot_stream_p02_t05 | reject | slot_002 | 0.000000 | 0.000000 |
| 50 | ORDER PERU 25-15 ANDET VF.pdf | group_004 | camelot_hybrid_p02_t04 | reject | slot_002 | 0.359872 | 0.527967 |
| 51 | ORDER PERU 25-15 ANDET VF.pdf | group_004 | camelot_network_p02_t05 | reject | slot_002 | 0.359872 | 0.527967 |
| 52 | ORDER PERU 25-15 ANDET VF.pdf | group_005 | camelot_network_p01_t02 | reject | slot_001 | 0.090414 | 0.057986 |
| 53 | ORDER PERU 25-15 ANDET VF.pdf | group_005 | camelot_stream_p01_t03 | reject | slot_001 | 0.000000 | 0.000000 |
| 54 | ORDER PERU 25-15 ANDET VF.pdf | group_006 | camelot_stream_p01_t01 | reject | slot_001 | 0.000000 | 0.000000 |
| 55 | ORDER PERU 25-15 ANDET VF.pdf | group_007 | pymupdf_lines_p02_t02 | reject | slot_002 | 0.000000 | 0.000000 |
| 56 | ORDER PERU 25-15 ANDET VF.pdf | group_008 | pymupdf_text_p01_t01 | reject | slot_001 | 0.333041 | 0.962859 |
| 57 | ORDER PERU 25-15 ANDET VF.pdf | group_009 | pymupdf_text_p02_t01 | reject | slot_002 | 0.248343 | 0.998960 |
| 58 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_001 | camelot_hybrid_p01_t01 | admit | slot_001 | 0.999578 | 0.988539 |
| 59 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_001 | camelot_lattice_p01_t01 | admit | slot_001 | 0.999578 | 0.988539 |
| 60 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_001 | camelot_network_p01_t01 | reject | slot_001 | 0.588718 | 0.834659 |
| 61 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_001 | camelot_stream_p01_t03 | admit | slot_001 | 1.000000 | 0.809951 |
| 62 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_001 | docling_default_p01_t01 | admit | slot_001 | 1.000000 | 1.000000 |
| 63 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_001 | pymupdf_lines_p01_t01 | admit | slot_001 | 1.000000 | 0.983454 |
| 64 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_001 | pymupdf_lines_strict_p01_t01 | admit | slot_001 | 1.000000 | 0.987441 |
| 65 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_001 | unstructured_hi_res_p01_t01 | admit | slot_001 | 0.999181 | 0.956996 |
| 66 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_002 | camelot_hybrid_p02_t02 | admit | slot_002 | 1.000000 | 0.992653 |
| 67 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_002 | camelot_lattice_p02_t02 | admit | slot_002 | 1.000000 | 0.992653 |
| 68 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_002 | camelot_network_p02_t03 | admit | slot_002 | 0.934051 | 0.977084 |
| 69 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_002 | camelot_stream_p02_t05 | admit | slot_002 | 1.000000 | 0.889146 |
| 70 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_002 | docling_default_p02_t02 | admit | slot_002 | 1.000000 | 1.000000 |
| 71 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_002 | pymupdf_lines_p02_t01 | admit | slot_002 | 1.000000 | 0.985335 |
| 72 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_002 | pymupdf_lines_strict_p02_t01 | admit | slot_002 | 1.000000 | 0.992192 |
| 73 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_002 | pymupdf_text_p02_t01 | admit | slot_002 | 0.860434 | 0.984015 |
| 74 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_002 | unstructured_hi_res_p02_t02 | reject | slot_002 | 1.000000 | 0.723789 |
| 75 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_003 | camelot_hybrid_p03_t03 | admit | slot_003 | 1.000000 | 0.981805 |
| 76 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_003 | camelot_lattice_p03_t03 | admit | slot_003 | 1.000000 | 0.981805 |
| 77 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_003 | camelot_network_p03_t04 | admit | slot_003 | 0.883106 | 0.950536 |
| 78 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_003 | camelot_stream_p03_t07 | admit | slot_003 | 0.825458 | 0.972908 |
| 79 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_003 | docling_default_p03_t03 | admit | slot_003 | 1.000000 | 1.000000 |
| 80 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_003 | pymupdf_lines_p03_t01 | admit | slot_003 | 1.000000 | 0.970477 |
| 81 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_003 | pymupdf_lines_strict_p03_t01 | admit | slot_003 | 1.000000 | 0.978555 |
| 82 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_003 | unstructured_hi_res_p03_t04 | admit | slot_003 | 0.980729 | 0.992170 |
| 83 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_004 | camelot_hybrid_p03_t04 | admit | slot_004 | 1.000000 | 0.978699 |
| 84 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_004 | camelot_lattice_p03_t04 | admit | slot_004 | 1.000000 | 0.978699 |
| 85 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_004 | camelot_network_p03_t05 | admit | slot_004 | 0.746508 | 0.927557 |
| 86 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_004 | camelot_stream_p03_t08 | admit | slot_004 | 0.832247 | 0.927557 |
| 87 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_004 | docling_default_p03_t04 | admit | slot_004 | 1.000000 | 1.000000 |
| 88 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_004 | pymupdf_lines_p03_t02 | admit | slot_004 | 1.000000 | 0.972646 |
| 89 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_004 | pymupdf_lines_strict_p03_t02 | admit | slot_004 | 1.000000 | 0.978793 |
| 90 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_004 | unstructured_hi_res_p03_t05 | admit | slot_004 | 0.989012 | 0.979677 |
| 91 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_005 | camelot_hybrid_p05_t05 | reject | none | N/A | N/A |
| 92 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_005 | camelot_network_p05_t06 | reject | none | N/A | N/A |
| 93 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_006 | camelot_network_p01_t02 | reject | slot_001 | 0.057653 | 0.079042 |
| 94 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_006 | camelot_stream_p01_t04 | reject | slot_001 | 0.000000 | 0.000000 |
| 95 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_007 | camelot_stream_p01_t01 | reject | slot_001 | 0.000000 | 0.000000 |
| 96 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_008 | camelot_stream_p01_t02 | reject | slot_001 | 0.000000 | 0.000000 |
| 97 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_009 | camelot_stream_p02_t06 | reject | slot_002 | 1.000000 | 0.231969 |
| 98 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_009 | unstructured_hi_res_p02_t03 | reject | slot_002 | 0.989526 | 0.194801 |
| 99 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_010 | camelot_stream_p03_t09 | reject | slot_003 | 0.000000 | 0.000000 |
| 100 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_011 | camelot_stream_p04_t10 | reject | none | N/A | N/A |
| 101 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_012 | camelot_stream_p05_t11 | reject | none | N/A | N/A |
| 102 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_013 | camelot_stream_p05_t12 | reject | none | N/A | N/A |
| 103 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_014 | camelot_stream_p06_t13 | reject | none | N/A | N/A |
| 104 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_015 | camelot_stream_p06_t14 | reject | none | N/A | N/A |
| 105 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_015 | pymupdf_text_p06_t01 | reject | none | N/A | N/A |
| 106 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_016 | pymupdf_text_p01_t01 | reject | slot_001 | 0.249711 | 0.998135 |
| 107 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_017 | pymupdf_text_p03_t01 | reject | slot_003 | 0.246450 | 0.997344 |
| 108 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_018 | pymupdf_text_p04_t01 | reject | none | N/A | N/A |
| 109 | 签字-ORDER PERU 25-19 FONAFE.pdf | group_019 | pymupdf_text_p05_t01 | reject | none | N/A | N/A |
