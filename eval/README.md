# 评估数据

- `ground-truth/`：人工标注的问题、参考答案和证据原文（每份 PDF 一个文件）。这是评估的唯一标准来源。
- `mappings.json`：已确认的"证据组 → chunk 组合"对应关系，用 chunk 正文哈希标识，与 chunk ID 和构建配置无关。由 `python -m rag eval --confirm-auto` 追加。

规则见 [docs/rules/evaluation.md](../docs/rules/evaluation.md)；评估报告输出到 `reports/eval/`。
