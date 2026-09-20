# 检索评估数据

- `ground-truth/`：与 pipeline 无关、经人工审核的证据事实，是问题、参考答案和证据组的唯一权威来源。
- `test-sets/system-<version>__pipeline-<id>__cfg-<fingerprint12>/`：把同一 ground truth 映射到某个系统版本、pipeline 和 BuildConfig 对应的 chunk ID。

正式评估只读取 `review_status=approved` 的 ground truth 和唯一匹配当前索引身份的 test set。运行结果不写回本目录；原始 JSON、人工审核 HTML、汇总和对比位于 `validation/retrieval/`。

修改问题、参考答案或证据原文时，应先更新并重新审核 ground truth；索引构建配置或 chunk 发生变化时，应创建并审核新的 test set，不能覆盖历史配置目录。
