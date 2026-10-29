"""Controlled experiments: retrieve hand-made texts once each and report every information item's
completion rank and its evidence units' ranks, the same way the comparison page does for one retrieval
text (rag/eval/query_compare.py). Used to test a suspected cause by changing one thing in a text.

    from probe import Probe
    p = Probe(["RM-T", "LM-C"])
    p.ranks("RM-T", "CONTRACT-105-Q003", "texto …")   # → {group_id: completion rank}, evidence detail
"""

from __future__ import annotations

from pathlib import Path

from rag.cli import _assembly
from rag.eval.judge import judge_case
from rag.eval.metrics import completion_ranks, first_ranks
from rag.eval.query_compare import COMBOS
from rag.eval.runner import prepare
from rag.paths import Workspace

ROOT = Path(__file__).resolve().parents[3]


class Probe:
    def __init__(self, combos=None, retriever: str = "hybrid"):
        """`retriever`: hybrid (as evaluated), or semantic / bm25 to see one side of the hybrid."""
        self.workspace = Workspace(ROOT)
        self.ctx = {}
        for combo in COMBOS:
            if combos and combo.label not in combos:
                continue
            ctx = prepare(self.workspace, _assembly(self.workspace, combo.config, retriever, combo.table_return_unit),
                          cache_retrievals=True)
            ctx.warm_up()
            self.ctx[combo.label] = ctx
        self.cases = {c.case_id: c for c in next(iter(self.ctx.values())).dataset.cases}

    def ranking(self, combo: str, case_id: str, text: str):
        ctx = self.ctx[combo]
        scope = ctx.scope(self.cases[case_id].question)
        return ctx.searcher.rank(text, scope.document_ids).units

    def ranks(self, combo: str, case_id: str, text: str) -> dict:
        """Per information item: completion rank, first-unit rank and the evidence units' ranks
        (unit key tail → rank)."""
        ctx, case = self.ctx[combo], self.cases[case_id]
        units = self.ranking(combo, case_id, text)
        evidence = judge_case(case, ctx.chunks)
        chunk_lists = [u.chunk_ids for u in units]
        needed = completion_ranks(chunk_lists, evidence.groups)
        first = first_ranks(chunk_lists, evidence.groups)
        out = {}
        for g in evidence.groups:
            chunks = {c for s in g.acceptable_sets for c in s}
            ev = {u.key.split("-")[-1]: n for n, u in enumerate(units, start=1) if chunks & set(u.chunk_ids)}
            out[g.group_id] = {"n": needed.get(g.group_id), "f": first.get(g.group_id), "units": ev}
        out["_top"] = [u.key.split("-")[-1] for u in units[:6]]
        return out
