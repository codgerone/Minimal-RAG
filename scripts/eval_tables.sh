#!/usr/bin/env bash
# 标准对比：configs/ 下 7 种入库方式的 13 种组合（6 种表格 chunk 入库 × 返回命中 chunk（C）/ 整表（T），
# 摘要只评整表），混合检索；评估完生成对比页 reports/compare/<名称>/。
# 用法（仓库根目录，Git Bash）：
#   bash scripts/eval_tables.sh <对比页名称> [表格对照说明 JSON]
#       K 取各配置 [eval] top_k，对比页含表格证据标签页
#   bash scripts/eval_tables.sh --budgets 3,6,9,12 <对比页名称>
#       每题检索一次，每档预算各出一份评估，对比页每档一个标签页，不含表格证据标签页
# 入库没有变化时直接运行；入库有变化时先 python -m rag ingest --all-configs --force。
set -euo pipefail
budgets=
if [ "${1:-}" = "--budgets" ]; then
  budgets=${2:?--budgets 需要预算，如 3,6,9,12}
  shift 2
fi
name=${1:?需要对比页名称}
spec=${2:-}
[ -z "$budgets" ] || [ -z "$spec" ] || { echo "--budgets 与表格对照说明不能同时使用" >&2; exit 1; }
export HF_HUB_OFFLINE=1 PYTHONIOENCODING=utf-8
runs=()
evaluate() {   # 配置名 返回单元 显示名
  local folders folder
  folders=$(.venv/Scripts/python.exe -m rag eval --config "$1" --retriever hybrid --table-return-unit "$2" \
            ${budgets:+--budgets "$budgets"} | sed -n 's|^报告：reports/eval/\(.*\)/index.html$|\1|p')
  [ -n "$folders" ] || { echo "评估 $1（$2）没有生成报告" >&2; exit 1; }
  for folder in $folders; do
    echo "$3：$folder"
    runs+=("$folder=$3")
  done
}
for pair in structured:RM structured_labeled:RL structured_llm:LM structured_llm_labeled:LL \
            structured_llm_lead:LM+ structured_llm_labeled_lead:LL+; do
  evaluate "${pair%%:*}" chunk "${pair##*:}-C"
  evaluate "${pair%%:*}" table "${pair##*:}-T"
done
evaluate structured_summary table S-T
.venv/Scripts/python.exe -m rag compare --name "$name" ${spec:+--table-spec "$spec"} \
  ${budgets:+--no-table-evidence} "${runs[@]}"
