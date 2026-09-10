# 第二阶段重构验收记录

历史验收日期：2026-09-10。本文是该次重构的固定记录，不作为持续更新的当前测试数量或有效规格。范围：experiments 的代码、目录、文档与相关测试；正式 RAG parser、chunking、embedding 尚未迁移。

## 结果与证据范围

| 验证 | 结果 | 能证明什么 |
| --- | --- | --- |
| 原实验基线 | 44 项通过 | 既有提取适配、准入、分组、评分、CLI、校准行为 |
| 新增重构测试 | 19 项通过 | 四层依赖方向；四工具页失败继续；Docling 部分恢复及原页码；规范化隔离；零表；限定去重；发布回滚；内容失效；请求页范围与文档链接 |
| 全项目回归 | 111 项通过 | 实验重构与现有 RAG 自动测试兼容；不包含外部 live 服务测试 |
| 真实 E001-602.pdf | 四工具九策略成功，后续分组与评分成功 | 新提取格式能实际接通完整实验链路 |
| 六份历史 PDF 迁移 | 483 个提取文件 SHA-256 不变；14 个 ready group | 新路径重建的 slots、candidate views、准入、比较、groups 及完整组内评分事实与旧快照相等 |
| 校准 | 109 条标签、95 条可比较 | 迁移后的默认路径可重新计算既有样本 |
| 文档与人工数据 | 11 份 Markdown 及审核 HTML 本地引用无断链；人工标签与备份一致 | 导航有效，校准没有改写人工判断 |

真实重跑中，PyMuPDF 产生 3 个候选，Camelot 产生 5 个，Docling 与 Unstructured 各 1 个。Camelot lattice/hybrid 显式传 copy_text=None、shift_text=['l','t']；stream/network 不接受这两个参数，按策略分别传参。这项问题通过真实调用发现并修正。

六份历史样本依次包含 1、5、1、1、2、4 个 ready group；比较排除了耗时、路径及新增身份字段，直接比较计算事实。历史提取的 legacy warning 明确保留，迁移没有伪装新格式，也没有重新评价全部识别质量。

## 复验入口

从仓库根目录运行：

```powershell
uv run python -m pytest -q tests/test_table_scoring.py tests/test_table_grouping.py tests/test_admission_calibration.py tests/test_camelot_normalizer.py tests/test_docling_normalizer.py tests/test_pymupdf_normalizer.py tests/test_table_extraction_cli.py tests/test_experiment_refactor.py
uv run python -m experiments.table_extraction extract-all --pdf "documents/E001-602.pdf"
uv run python -m experiments.table_extraction grouping --pdf "documents/E001-602.pdf"
uv run python -m experiments.table_extraction scoring --pdf "documents/E001-602.pdf"
uv run python -m experiments.table_extraction admission-calibration
```

单工具和分组/评分命令的 --output-dir 表示父目录；完整 CLI 串联使用默认目录。复验更新对应实验输出；校准保留人工判断。Windows 沙箱临时目录 ACL 曾阻止 pytest/Unstructured 写入，本次成功验证使用已获批准的本地进程权限。

当时用于核验的重构前 ZIP 备份、逐文件哈希基线、回归 JSON 与真实运行日志曾保存在根目录 tmp/，现已在提交前清理，未纳入版本管理。上表保留当时的验证结论；当前仓库不能直接重放与该旧快照的逐文件比较。上面的命令可重新验证现有链路，但需要自行提供相应 PDF、工具依赖和模型，且不等于恢复原始对照证据。

## 解释边界

本次验证证明实验链路可运行及迁移计算事实保持一致，不能证明全部 winner 人工质量或最终 RAG 效果提升。旋转/裁切映射、跨页拼接、所有真实失败类型、断电事务不在已验证支持范围，详见[当前限制](../limitations.md)。

## 修复对照记录

以下保留该次重构的决策和修复编号，供追溯使用；当前行为以需求和架构为准，仍存在的边界见[当前限制](../limitations.md)。

### 已确认决策

| 编号 | 有效规则 | 实现 |
| --- | --- | --- |
| Q-01 | 页面越界容差 0.000001 pt | 转换与准入统一使用 1e-6 pt；span 聚类容差用途不同，不混用 |
| Q-02 | 同一已确认 Camelot 合并格内相同完整文本只输出一次 | 矩形连通分量内去重，独立格同值及单格内部重复词保留；source_refs 追踪全部组成格 |
| Q-03 | 任一页失败继续同策略后续页，保留成功页 | 四工具及规范化按页隔离；完整成功退出与下游可用性分开判断 |

### 代码与目录修复对照

| 原编号 | 状态 | 实现与验证 |
| --- | --- | --- |
| F-01、D-03 | 已修复 | Docling/Camelot 成功零表保存 raw、空 normalized 及有效路径；删除 CamelotTableRecord |
| F-02、F-15 | 已修复 | 页级模型、Docling 完整/部分返回恢复及原页码还原；四工具故障注入与单份 PDF 实测 |
| F-03 | 已实现内容失效检查 | 提取绑定 PDF/JSON 哈希及工具配置；分组绑定提取集合，评分拒绝陈旧输入；不是多版本缓存 |
| F-04 | 已修复 | 统一越界容差，边界簇中位数不额外三位舍入；阈值边界测试 |
| F-05 | 保留支持范围限制 | Docling 页尺寸差异告警；旋转/CropBox 与 MediaBox 不一致时保留 raw、延后几何处理，未实现通用仿射映射 |
| F-06 | 已修复 | Docling 矛盾 span/offset 保留可读取原值，标 unavailable 并告警，不作为有效网格覆盖 |
| F-07 | 已修复已定义路径 | Unstructured 异常 cell 保留文本；缺 HTML 用 unplaced_text，不造一格表；table_as_cells 未适配 |
| F-08、F-09 | 已修复 | 合并格限定去重与全部来源；PyMuPDF cell ref 可查回 raw JSON |
| F-10 | 已清理 | 删除旧评分常量、legacy_candidates、无消费方 Docling layout 辅助代码；保留完整 document JSON/HTML |
| F-11 | 已拆分 | 通用写入归 artifacts/writers，自定义 HTML 归 presentation/review_views；移除跨工具借用私有 exporter |
| F-12、PATH-03 | 已统一 | --output-dir 为父目录，再追加 PDF stem；默认路径集中，不隐式改变下游输入 |
| F-13 | 已实现进程内回滚 | 暂存后发布，报告与视图一起回滚；不承诺断电/强杀时跨目录原子性 |
| F-14 | 已修复 | 可选工具身份和策略缺失显式 warning；Docling 权威输入无效仍硬失败 |
| F-16 | 已修复 | 畸形 PixelSpace/非法 bbox 返回不可转换及诊断，保留可用文本结构 |
| D-01 | 已修复 | 聚类消除退化间隔；核验 rows/extract 维度并告警，不改原始矩阵 |
| D-02 | 已修复 | Docling 聚类包含 table/cell 边界；未知行列及页尺寸告警，原生 span 优先 |
| ARCH-01、ARCH-02 | 已迁移并检查 | 四层 + bootstrap；应用注入基础设施；领域无第三方 SDK；导入方向自动回归 |
| PATH-01 | 已迁移并回归 | output/extracting/<tool>/<PDF stem>；六份 PDF 计算事实与旧输出一致 |
| PATH-02 | 已迁移并回归 | docs/calibration 说明、data/calibration 标签、output/calibration 图；只保留一份人工数据 |

