# 四工具表格提取与选优实验

本实验对数字型 PDF 使用 PyMuPDF、Camelot、Unstructured、Docling 提取表格，保留原始事实并统一候选结构。以 Docling 表格位置为唯一卡位，执行双向覆盖准入、按卡位分组、四项指标组内相对评分，最终为每个可评分组选择一个候选。

本阶段截至选优及审计产物；不修改 `rag/`，不实现最终文档融合、表格文本化、chunking 或 embedding。多工具只改善 Docling 已发现的表格，不补漏表，也不保证 winner 已达到绝对质量标准。

## 阅读入口

| 文档 | 内容 |
| --- | --- |
| [需求规格](docs/requirements.md) | 完整链路的目标、非目标、规则、边界、例外和输出要求 |
| [架构](docs/architecture.md) | 模块地图、模型、状态原因、数据流、文件契约与命令接口 |
| [工具调研](docs/tool-findings.md) | 版本依据、原生跨度、bbox 含义与坐标事实；核心适配见架构 |
| [验收与辅助功能](docs/validation.md) | 规则追踪、边界验收、人工 group/score 视图和校准使用 |
| [当前限制](docs/limitations.md) | 当前支持范围、复现条件、兼容与发布保证的边界 |
| [校准证据](docs/calibration/admission-thresholds.md) | 当前阈值的样本、取舍与详细推导 |

现行规格以上表为准。页面越界容差统一为 1e-6 pt，已确认 Camelot 合并格内重复文本只计一次，策略页失败后继续处理后续页并保留成功页。

历史调查、决策和阶段验收统一归档到 [history](docs/history/README.md)，供复盘使用，不作为另一套有效规格。

## 链路

```text
PDF → 四工具九策略 → raw + normalized TableCandidate
Docling raw TableItem → TableSlot
TableCandidate + TableSlot → 准入 → GroupingReport
ready group + PDF word 参照 → 四指标 → 相对分 → 唯一 winner
```

区别：raw 是工具事实；normalized 是公共候选；grouping 决定哪些候选可以比较；scoring 决定同组谁相对更好；HTML 是辅助检查。candidate 几何 deferred 表示无法判断，不等于证明它不是表格；unresolved group 未进入评分，不是评分淘汰。

## 运行

从仓库根目录执行，环境依赖见根目录 [pyproject.toml](../pyproject.toml)。首次运行重型工具可能需要本地模型资源；Unstructured 本地环境还涉及 Tesseract 等系统依赖。工具失败应产生明确运行记录。

```powershell
uv run python -m experiments.table_extraction extract-all --pdf "documents/文件名.pdf"
uv run python -m experiments.table_extraction grouping --pdf "documents/文件名.pdf"
uv run python -m experiments.table_extraction scoring --pdf "documents/文件名.pdf"
```

单工具诊断可将 `extract-all` 替换为 `pymupdf`、`camelot`、`unstructured` 或 `docling`。grouping/scoring 缺上游时会询问是否补齐；同一 PDF 重跑覆盖对应结果。所有单阶段命令的 `--output-dir` 均表示父目录，实际输出再追加 PDF stem；默认目录支持完整串联。

## 目录与当前状态

```text
experiments/
  README.md
  table_extraction/                  能力包内四层 + bootstrap
  docs/                             需求、架构、调研、验收
    calibration/                    校准说明与证据
    history/                        历史审查与固定验收记录
  data/calibration/                  人工标签
  output/
    extracting/<tool>/<PDF stem>/    四工具提取产物
    grouping/                       准入、分组及审核
    scoring/                        评分、选优及审核
    calibration/                    派生图
```

上述布局已落地。提取、分组、评分的默认路径集中定义在 infrastructure/artifacts/paths.py；人工标签只保留 data/calibration 中的一份。旧扁平模块和旧默认路径不提供隐式回退，详细模块职责见架构 §9。

已有六份 PDF 的 grouping/scoring 产物共 14 个 ready group；阈值校准是其中四份 PDF、109 个标签的独立子集。现有产物不是固定测试真值，完整性、来源和正确性要分别核验。

## 文档维护约定

修改规则时同步需求、所属模型/架构与验收场景；纯目录调整只改模块地图、链接和相关限制说明。新旧规则不能同时处于有效入口；不确定的版本归属先确认再定稿。第三阶段的 RAG 设计不提前混入实验规格。

每次行为变更的审阅材料应包含：规则 ID、受影响模型/原因码、验证场景与实际验证结果。需求回答“应该怎样”，架构回答“如何实现和存储”，工具调研保存外部事实依据，limitations 记录当前支持范围和限制；历史审查与校准报告不能覆盖现行规格。提交前检查 Markdown 链接、章节引用和枚举一致性；工具版本或配置变化同时更新调研依据。
