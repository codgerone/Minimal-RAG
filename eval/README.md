# 评估数据

- `ground-truth/`：人工标注的问题、参考答案、证据原文，以及每段证据在 PDF 上的已确认位置（每份 PDF 一个文件）。这是评估的唯一标准来源。位置由 `python -m rag anchors` 生成审核文件，人工核对后用 `--write` 写入。

规则见 [docs/rules/evaluation.md](../docs/rules/evaluation.md)；评估报告输出到 `reports/eval/`。
