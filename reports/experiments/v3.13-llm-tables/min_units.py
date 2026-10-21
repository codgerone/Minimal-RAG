"""每题完整覆盖所需的最少返回单元数（K 的下限），只依据标注与分块，与检索排名无关。

从每个证据组的可接受组合中各选一个，使并集最小。按 chunk 计；按单元计时同一张表的 chunk 合为一个。
用法：python reports/experiments/v3.13-llm-tables/min_units.py <评估目录名> <配置名>
"""
import itertools
import json
import sqlite3
import sys
from pathlib import Path

run, config = sys.argv[1], sys.argv[2]
out = Path(__file__).parent
result = json.loads(Path(f"reports/eval/{run}/result.json").read_text(encoding="utf-8"))
table_of = dict(sqlite3.connect(f".rag/indexes/{config}/tables.sqlite").execute(
    "select chunk_id, table_id from table_chunks"))


def fewest(groups, unit):
    options = [[frozenset(unit(c) for c in s) for s in g["acceptable_sets"]] for g in groups]
    if any(not o for o in options):
        return None
    return min(len(frozenset().union(*combo)) for combo in itertools.product(*options))


rows = [{"case_id": c["case_id"], "groups": len(c["groups"]),
         "chunks": fewest(c["groups"], lambda x: x),
         "units": fewest(c["groups"], lambda x: table_of.get(x, x))} for c in result["cases"]]
(out / "min-units.json").write_text(json.dumps({"run": run, "config": config, "cases": rows},
                                               ensure_ascii=False, indent=2), encoding="utf-8")
lines = [f"# 每题完整覆盖所需的最少单元数（{config}，评估 {run}）", "",
         "只依据标注与分块：从每个证据组的可接受组合中各选一个，使并集最小。"
         "按 chunk 计为只返回命中 chunk 时 K 的下限；按单元计为返回整表时 K 的下限（一张整表算 1）。", "",
         "| 题目 | 证据组 | 按 chunk 计 | 按单元计 |", "|---|---:|---:|---:|"]
lines += [f"| {r['case_id']} | {r['groups']} | {r['chunks']} | {r['units']} |" for r in rows]
for key, name in (("chunks", "按 chunk 计"), ("units", "按单元计")):
    counts = {}
    for r in rows:
        counts[r[key]] = counts.get(r[key], 0) + 1
    lines.append("")
    lines.append(f"{name}：" + "，".join(f"{k} 个 {v} 题" for k, v in sorted(counts.items())))
(out / "min-units.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
