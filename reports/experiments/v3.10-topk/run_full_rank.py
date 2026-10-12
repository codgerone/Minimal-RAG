"""Experiment: rank every chunk in scope (K = 200) to find, per question, the smallest K at which
all evidence groups are covered, and each needed chunk's rank in the vector, BM25 and fused lists.

Writes runs to full-rank/eval/ and the per-question summary to full-rank/summary.json.
Run from the repository root: .venv/Scripts/python.exe reports/experiments/v3.10-topk/run_full_rank.py
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "full-rank"
ROOT = next(p for p in HERE.parents if (p / "pyproject.toml").is_file())
sys.path.insert(0, str(ROOT))

import rag.eval.runner as runner
from rag.config import resolve_config
from rag.paths import Workspace
from rag.registry import assemble

K_ALL = 200   # larger than any document's chunk count, so every chunk in scope is ranked


class ExpWorkspace(Workspace):
    @property
    def reports(self) -> Path:
        return OUT


def needed_k(group: dict, rank: dict[str, int]) -> int | None:
    """Smallest K covering the group: min over acceptable sets of the worst rank in the set."""
    options = [max(rank[c] for c in s) for s in group["acceptable_sets"] if all(c in rank for c in s)]
    return min(options) if options else None


def summarize(folder: Path) -> list[dict]:
    result = json.loads((folder / "result.json").read_text(encoding="utf-8"))
    rows = []
    for case in result["cases"]:
        if not case["answerable"]:
            continue
        hits = {h["chunk_id"]: h for h in case["hits"]}
        rank = {cid: h["rank"] for cid, h in hits.items()}
        groups = []
        for g in case["groups"]:
            k = needed_k(g, rank)
            best = min(g["acceptable_sets"], key=lambda s: max(rank.get(c, 10**6) for c in s))
            groups.append({"group_id": g["group_id"], "information_item": g["information_item"], "needed_k": k,
                           "chunks": [{"chunk_id": c, "rank": rank.get(c),
                                       "semantic_rank": hits.get(c, {}).get("semantic_rank"),
                                       "lexical_rank": hits.get(c, {}).get("lexical_rank")} for c in best]})
        ks = [g["needed_k"] for g in groups]
        rows.append({"case_id": case["case_id"], "scope_chunks": len(case["hits"]),
                     "needed_k": None if None in ks else max(ks), "groups": groups})
    return rows


def main() -> None:
    workspace = ExpWorkspace(ROOT)
    summary: dict[str, dict[str, list[dict]]] = {}
    for config_name in ("structured", "plain_text"):
        base = resolve_config(ROOT, config_name)
        for use in ("hybrid", "semantic"):
            assembly = assemble(replace(base, retriever=replace(base.retriever, use=use)))
            rows = summarize(runner.run_evaluation(workspace, assembly, K_ALL))
            summary.setdefault(config_name, {})[use] = rows
            print(config_name, use, [(r["case_id"], r["needed_k"]) for r in rows], flush=True)
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
