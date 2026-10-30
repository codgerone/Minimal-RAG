"""Experiment (proposal v3.14, question 6; decision 017): the hybrid retriever's BM25 weight α.

Six retrieval texts — original question and sub-questions (unmerged), each as the code-stripped
original text, the translation alone and both fused with RRF — under α = 1.0, 0.9, …, 0, on the 13
combinations and generations 0–3. Every retrieval comes from the retrieval cache (the rankings keep
both channels' ranks and are fused again with α), so nothing is re-encoded and no LLM is called.

Writes ranks.json next to this script: α → generation → configuration key → combination → case →
completion ranks. Run: python reports/experiments/v3.14-bm25-weight/run.py
"""

from __future__ import annotations

import json
from pathlib import Path

import rag.eval.query_compare as qc
from rag.eval.query_compare import COMBOS, QueryConfig, collect
from rag.paths import Workspace

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
ALPHAS = tuple(round(1 - i / 10, 1) for i in range(11))
GENERATIONS = (0, 1, 2, 3)
CONFIGS = [QueryConfig.parse(k) for k in ("P3.2.0", "P3.2.0_P3.2.2", "P3.2.0_P3.2.2@rrf",
                                          "P3.1_P3.2.0", "P3.1_P3.2.0_P3.2.2", "P3.1_P3.2.0_P3.2.2@rrf")]

_prepare = qc.prepare
_held: dict = {}


def prepare_once(workspace, assembly, **kw):
    """One context per combination, reused across α and generations; α is set on its retriever."""
    key = (assembly.name, assembly.config.table_return_unit)
    if key not in _held:
        _held.clear()
        _held[key] = _prepare(workspace, assembly, **kw)
    ctx = _held[key]
    ctx.searcher.retriever.bm25_weight = assembly.config.bm25_weight
    return ctx


def main() -> None:
    qc.prepare = prepare_once
    workspace = Workspace(ROOT)
    out: dict = {str(a): {str(g): {c.key: {} for c in CONFIGS} for g in GENERATIONS} for a in ALPHAS}
    for combo in COMBOS:
        for g in GENERATIONS:
            for a in ALPHAS:
                data = collect(workspace, "bm25w", (combo,), generation=g, ranks_only=True, configs=CONFIGS,
                               bm25_weight=a)
                assert not data["unavailable"], data["unavailable"]
                for key, by_combo in data["configs"].items():
                    out[str(a)][str(g)][key][combo.label] = {case: f["n"] for case, f in by_combo[combo.label].items()}
        print(combo.label, flush=True)
    (HERE / "ranks.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
