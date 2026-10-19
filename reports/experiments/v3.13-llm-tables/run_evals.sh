#!/usr/bin/env bash
# V3.13：13 种组合的评估（7 种入库方式 × 返回命中 chunk / 整表，摘要只评整表），混合检索，K=3。
# 用法（仓库根目录，Git Bash）：bash reports/experiments/v3.13-llm-tables/run_evals.sh
set -e
export HF_HUB_OFFLINE=1 PYTHONIOENCODING=utf-8
for config in structured_markdown_rows structured structured_llm_md_gemini38f structured_llm_labeled_gemini38f \
              structured_llm_md_lead_gemini38f structured_llm_labeled_lead_gemini38f; do
  for unit in chunk table; do
    .venv/Scripts/python.exe -m rag eval --config "$config" --retriever hybrid --table-return-unit "$unit"
  done
done
.venv/Scripts/python.exe -m rag eval --config structured_summary_gemini38f --retriever hybrid --table-return-unit table
.venv/Scripts/python.exe -m rag eval --config plain_text
