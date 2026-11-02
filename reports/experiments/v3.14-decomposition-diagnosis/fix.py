from pathlib import Path
from rag.cli import _assembly
from rag.eval.judge import judge_case
from rag.eval.metrics import routed_completion_rank
from rag.eval.query_compare import COMBOS
from rag.eval.runner import prepare
from rag.paths import Workspace
TESTS = {  # case, item: [(label, text)]
 ("PO-24-12-Q004", "e3"): [("原问题", None), ("子问题（v9 第 0 次）", "订单 ORDER2025001 付款的最后期限是什么？"),
                          ("补回所指", "订单 ORDER2025001 中 MDC ORCA 费用的付款最后期限是什么？")],
 ("PO-25-19-Q002", "e2"): [("原问题", None), ("子问题（v9 第 0 次）", "订单 ORDER2025003 中，ELUC公司订购的总金额是多少？"),
                          ("补回限定", "订单 ORDER2025003 中，ELUC公司订购HXE34K-S1的总金额是多少？"),
                          ("改问所依据的事实", "订单 ORDER2025003 中，HXE34K-S1 的单价是多少？")],
 ("CONTRACT-105-Q002", "e5"): [("原问题", None), ("子问题（v9 第 0 次）", "采购合同 CONT2025001 的合同总金额是多少？"),
                              ("改问所依据的事实", "采购合同 CONT2025001 中各 Ítem 的总价分别是多少？")],
 ("CONTRACT-105-Q010", "e1"): [("原问题", None), ("子问题（v9 第 0 次）", "采购合同 CONT2025001 是否规定分批交货？")],
}
ws = Workspace(Path("."))
rows = {}
for combo in COMBOS:
    ctx = prepare(ws, _assembly(ws, combo.config, "hybrid", combo.table_return_unit), cache_retrievals=True)
    cases = {c.case_id: c for c in ctx.dataset.cases}
    for (cid, gid), variants in TESTS.items():
        case = cases[cid]; ev = judge_case(case, ctx.chunks)
        group = next(g for g in case.groups if g.group_id == gid)
        schemes = [[ev.excerpts[x].acceptable_sets for x in s.excerpt_ids] for s in group.schemes]
        docs = ctx.scope(case.question).document_ids
        for label, text in variants:
            units = [u.chunk_ids for u in ctx.searcher.rank(text or case.question, docs).units]
            rows.setdefault((cid, gid, label), []).append(routed_completion_rank([units], schemes))
    print(combo.label, flush=True)
print("组合", [c.label for c in COMBOS])
for (cid, gid, label), r in rows.items():
    print(f"{cid} {gid} {label:<10} {r}")
