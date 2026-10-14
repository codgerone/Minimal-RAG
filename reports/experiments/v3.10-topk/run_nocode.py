"""Experiment: remove the document identifier codes from the question before retrieval.

The codes (documents/documents.toml) only decide the search scope; they never occur in the
documents. Here the scope is still resolved from the original question, then every registered
code is removed from the text handed to the retriever (vector and BM25). Runs K = 3, 10 and
200 (full ranking) for both configs, hybrid and vector-only, into nocode/eval/, and writes
nocode/summary.json with the K=3 / K=10 metrics and, per question, the K needed for full coverage.
Run from the repository root: .venv/Scripts/python.exe reports/experiments/v3.10-topk/run_nocode.py
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "nocode"
ROOT = next(p for p in HERE.parents if (p / "pyproject.toml").is_file())
sys.path.insert(0, str(ROOT))

import rag.eval.runner as runner
from rag.config import resolve_config
from rag.paths import Workspace
from rag.registry import assemble

sys.path.insert(0, str(HERE))
from run_full_rank import summarize as full_rank_summary
from run_sweep import summarize as sweep_summary

CODES = [c for d in tomllib.loads((ROOT / "documents/documents.toml").read_text(encoding="utf-8"))["document"]
         for c in d["codes"]]
PATTERN = re.compile("|".join(re.escape(c) for c in sorted(CODES, key=len, reverse=True)), re.IGNORECASE)


def strip_codes(question: str) -> str:
    return re.sub(r"\s+", " ", PATTERN.sub(" ", question)).strip()


class ExpWorkspace(Workspace):
    @property
    def reports(self) -> Path:
        return OUT


class NoCode:
    def __init__(self, inner):
        self.inner = inner

    def retrieve(self, question, top_k, document_ids=None):
        return self.inner.retrieve(strip_codes(question), top_k, document_ids)


def main() -> None:
    original = runner.build_retriever
    runner.build_retriever = lambda assembly, store, lexical: NoCode(original(assembly, store, lexical))
    workspace = ExpWorkspace(ROOT)
    summary: dict = {}
    for config_name in ("structured", "plain_text"):
        base = resolve_config(ROOT, config_name)
        for use in ("hybrid", "semantic"):
            assembly = assemble(replace(base, retriever=replace(base.retriever, use=use)))
            entry = {f"k{k}": sweep_summary(runner.run_evaluation(workspace, assembly, k)) for k in (3, 10)}
            entry["full_rank"] = full_rank_summary(runner.run_evaluation(workspace, assembly, 200))
            summary.setdefault(config_name, {})[use] = entry
            print(config_name, use, {k: {m: entry[k][m]["numerator"] for m in ("hit_rate", "complete_coverage")}
                                     | {"mrr": round(entry[k]["mrr"]["value"], 3)} for k in ("k3", "k10")},
                  [(r["case_id"], r["needed_k"]) for r in entry["full_rank"]], flush=True)
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
