"""Experiment: retrieval metrics as Top-K grows (K=1..10) on the current indexes.

Does not change the system: it calls rag.eval.runner for every (config, retriever, K) and writes
each run next to this script (a Workspace whose `reports` points here), then summarizes the
metrics plus the returned text volume (characters of the Top-K chunks, summed per question,
averaged over answerable questions) into summary.json.
Run from the repository root: .venv/Scripts/python.exe reports/experiments/v3.10-topk/run_sweep.py
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = next(p for p in OUT.parents if (p / "pyproject.toml").is_file())
sys.path.insert(0, str(ROOT))

import rag.eval.runner as runner
from rag.config import resolve_config
from rag.paths import Workspace
from rag.registry import assemble

KS = range(1, 11)
METRICS = ("hit_rate", "complete_coverage", "group_recall", "mrr", "chunk_precision")


class ExpWorkspace(Workspace):
    @property
    def reports(self) -> Path:
        return OUT


def summarize(folder: Path) -> dict:
    result = json.loads((folder / "result.json").read_text(encoding="utf-8"))
    cases = [c for c in result["cases"] if c["answerable"]]
    chars = sum(len(h["text"]) for c in cases for h in c["hits"])
    row = {"run": folder.name, "returned_chars": round(chars / len(cases), 1)}
    for key in METRICS:
        m = result["metrics"][key]
        row[key] = {"numerator": m["numerator"], "denominator": m["denominator"], "value": m["value"]}
    row["complete_cases"] = sorted(c["case_id"] for c in cases if c["complete"])
    return row


def main() -> None:
    workspace = ExpWorkspace(ROOT)
    summary: dict[str, dict[str, list[dict]]] = {}
    for config_name in ("plain_text", "structured"):
        base = resolve_config(ROOT, config_name)
        for use in ("hybrid", "semantic"):
            assembly = assemble(replace(base, retriever=replace(base.retriever, use=use)))
            for k in KS:
                row = summarize(runner.run_evaluation(workspace, assembly, k))
                row["k"] = k
                summary.setdefault(config_name, {}).setdefault(use, []).append(row)
                print(f"{config_name:11s} {use:8s} K={k:2d} chars={row['returned_chars']:7.1f}  " + "  ".join(
                    f"{key}={row[key]['value']:.1%}" for key in METRICS), flush=True)
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
