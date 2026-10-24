#!/usr/bin/env bash
# V3.14 的五套基线：RM-T、LM-T、LM+-T（返回整表）与 LM-C、LM+-C（返回命中 chunk），混合检索，
# 每题用原问题检索一次，预算 B = 3、6、9、12 各出一份评估；评估完生成对比页 reports/compare/<名称>/，
# 每档预算一个标签页，第一列 RM-T 是基线。
# 用法（仓库根目录，Git Bash）：bash scripts/eval_baselines.sh <对比页名称> [预算，默认 3,6,9,12]
set -euo pipefail
name=${1:?需要对比页名称}
budgets=${2:-3,6,9,12}
export HF_HUB_OFFLINE=1 PYTHONIOENCODING=utf-8
runs=()
evaluate() {   # 配置名 返回单元 显示名
  local folders folder
  folders=$(.venv/Scripts/python.exe -m rag eval --config "$1" --retriever hybrid --table-return-unit "$2" \
            --budgets "$budgets" | sed -n 's|^报告：reports/eval/\(.*\)/index.html$|\1|p')
  [ -n "$folders" ] || { echo "评估 $1（$2）没有生成报告" >&2; exit 1; }
  for folder in $folders; do
    echo "$3：$folder"
    runs+=("$folder=$3")
  done
}
evaluate structured table RM-T
evaluate structured_llm table LM-T
evaluate structured_llm_lead table LM+-T
evaluate structured_llm chunk LM-C
evaluate structured_llm_lead chunk LM+-C
.venv/Scripts/python.exe -m rag compare --name "$name" "${runs[@]}"
