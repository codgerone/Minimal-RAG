#!/usr/bin/env bash
# 标准对比：configs/ 下 7 种入库方式的 13 种组合（6 种表格 chunk 入库 × 返回命中 chunk（C）/ 整表（T），
# 摘要只评整表），混合检索，K 取各配置 [eval] top_k；评估完生成对比页 reports/compare/<名称>/。
# 用法（仓库根目录，Git Bash）：bash scripts/eval_tables.sh <对比页名称> [表格对照说明 JSON]
# 入库没有变化时直接运行；入库有变化时先 python -m rag ingest --all-configs --force。
set -euo pipefail
name=${1:?需要对比页名称}
spec=${2:-}
export HF_HUB_OFFLINE=1 PYTHONIOENCODING=utf-8
runs=()
evaluate() {   # 配置名 返回单元 显示名
  local folder
  folder=$(.venv/Scripts/python.exe -m rag eval --config "$1" --retriever hybrid --table-return-unit "$2" \
           | sed -n 's|^报告：reports/eval/\(.*\)/index.html$|\1|p')
  [ -n "$folder" ] || { echo "评估 $1（$2）没有生成报告" >&2; exit 1; }
  echo "$3：$folder"
  runs+=("$folder=$3")
}
for pair in structured:RM structured_labeled:RL structured_llm:LM structured_llm_labeled:LL \
            structured_llm_lead:LM+ structured_llm_labeled_lead:LL+; do
  evaluate "${pair%%:*}" chunk "${pair##*:}-C"
  evaluate "${pair%%:*}" table "${pair##*:}-T"
done
evaluate structured_summary table S-T
.venv/Scripts/python.exe -m rag compare --name "$name" ${spec:+--table-spec "$spec"} "${runs[@]}"
