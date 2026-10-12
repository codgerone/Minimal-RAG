"""Experiment: cross-encoder reranking (bge-reranker-v2-m3) of the hybrid candidates.

Does not change the system: rag.eval.runner is run with a patched retriever that takes the
hybrid top-N (N = 30) in scope and reorders them by the reranker score (ties by chunk_id).
Runs for K = 1..10 and K = 30 (every candidate, to read the rank of each needed chunk) are
written to rerank/eval/, the summary to rerank/summary.json. Reranker scores are cached per
(question, chunk_id) so the K sweep scores each pair once.
Run from the repository root: .venv/Scripts/python.exe reports/experiments/v3.10-topk/run_rerank.py
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "rerank"
ROOT = next(p for p in HERE.parents if (p / "pyproject.toml").is_file())
sys.path.insert(0, str(ROOT))

import torch
from sentence_transformers import CrossEncoder

import rag.eval.runner as runner
from rag.config import resolve_config
from rag.paths import Workspace
from rag.query.retriever import Hit, build_retriever
from rag.registry import assemble

sys.path.insert(0, str(HERE))
from run_full_rank import summarize as full_rank_summary
from run_sweep import summarize as sweep_summary

MODEL, REVISION = "BAAI/bge-reranker-v2-m3", "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
CANDIDATES = 30


class ExpWorkspace(Workspace):
    @property
    def reports(self) -> Path:
        return OUT


class RerankRetriever:
    def __init__(self, first_stage, model: CrossEncoder, cache: dict):
        self.first_stage, self.model, self.cache = first_stage, model, cache

    def retrieve(self, question, top_k, document_ids=None):
        hits = self.first_stage.retrieve(question, CANDIDATES, document_ids)
        todo = [h for h in hits if (question, h.chunk.chunk_id) not in self.cache]
        if todo:
            scores = self.model.predict([(question, h.chunk.text) for h in todo], batch_size=8)
            for h, s in zip(todo, scores):
                self.cache[(question, h.chunk.chunk_id)] = float(s)
        ranked = sorted(hits, key=lambda h: (-self.cache[(question, h.chunk.chunk_id)], h.chunk.chunk_id))
        return [Hit(h.chunk, self.cache[(question, h.chunk.chunk_id)], h.semantic_rank, h.similarity,
                    h.lexical_rank, h.bm25) for h in ranked[:top_k]]


def main() -> None:
    model = CrossEncoder(MODEL, revision=REVISION, max_length=1024, model_kwargs={"dtype": torch.float32})
    cache: dict = {}
    original = runner.build_retriever
    runner.build_retriever = lambda assembly, store, lexical: RerankRetriever(
        original(assembly, store, lexical), model, cache)
    workspace = ExpWorkspace(ROOT)
    summary: dict = {}
    for config_name in ("structured", "plain_text"):
        base = resolve_config(ROOT, config_name)
        assembly = assemble(replace(base, retriever=replace(base.retriever, use="hybrid")))
        full = full_rank_summary(runner.run_evaluation(workspace, assembly, CANDIDATES))
        rows = []
        for k in range(1, 11):
            row = sweep_summary(runner.run_evaluation(workspace, assembly, k))
            row["k"] = k
            rows.append(row)
            print(config_name, f"K={k}", row["returned_chars"],
                  {m: row[m]["numerator"] for m in ("hit_rate", "complete_coverage")}, flush=True)
        summary[config_name] = {"sweep": rows, "full_rank": full}
        print(config_name, [(r["case_id"], r["needed_k"]) for r in full], flush=True)
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
