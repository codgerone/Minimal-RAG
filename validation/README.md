# 验证与效果证据

本目录保存系统在特定版本、配置和数据集上的实际运行证据，与 `docs/` 中定义系统行为的需求和架构规格分离。

- `retrieval/comparison.json`：跨运行比较的机器可读事实。
- `retrieval/comparison.md`：跨运行比较的人类可读视图。
- `retrieval/system-v2.0/summary.md`：RAG 系统 V2.0 的配置与效果汇总。
- `retrieval/system-v2.0/runs/`：不可原地覆盖的正式运行记录；每个目录包含 `run.json`、`aggregate.json`，以及五份 PDF 各自的原始 JSON 和人工审核 HTML。

`eval/` 是经审核的评估输入，`validation/` 是运行输出；后者不得反向作为 ground truth 或 test set 的数据源。
