"""对照实验：表格证据组排不上来，是表格入库的问题，还是问题本身的问题？

同一索引、同一证据组，只把原问题换成只问这一个信息项的子问题（用词照原问题，保留标识编码），
比较该组表格证据的指标 1（覆盖其任一词的 chunk 的最好名次）与指标 2（覆盖其表格 excerpt 所需的返回单元数）。
子问题在各入库方式下都排得上来、原问题排不上来，说明原因在问题（复合问题 C4、跨表连接 C5），与表格入库无关。

用法（仓库根目录）：python reports/experiments/v3.13-llm-tables/unrelated/subquestions.py
结果写入同目录 subquestions.json。
"""
import json
from pathlib import Path

from rag.config import resolve_config
from rag.eval import table_metrics as tm
from rag.eval.dataset import load_dataset
from rag.eval.judge import judge_case
from rag.eval.runner import _judge_chunks, _table_boxes
from rag.index.builder import open_lexical, open_store, open_tables
from rag.ingest.sources import discover
from rag.paths import Workspace
from rag.query.retriever import build_retriever
from rag.query.scope import load_catalog, resolve_scope
from rag.query.units import UnitSearch
from rag.registry import assemble

HERE = Path(__file__).parent
CONFIGS = {"RM": "structured_markdown_rows", "RL": "structured", "LM": "structured_llm_md_gemini38f",
           "LL": "structured_llm_labeled_gemini38f", "LM+": "structured_llm_md_lead_gemini38f",
           "LL+": "structured_llm_labeled_lead_gemini38f", "S": "structured_summary_gemini38f"}
PROBES = [  # 题, 证据组, 子问题, 假设的原因
    ("CONTRACT-105-Q003", "e1", "采购合同 CONT2025001 规定的总履约期限是多久？", "C4 复合问题"),
    ("CONTRACT-105-Q003", "e2", "采购合同 CONT2025001 规定的具体交货地点在哪里？", "C4 复合问题"),
    ("PO-24-12-Q004", "e1", "订单 ORDER2025001 的总金额是否涵盖MDC ORCA的费用？", "C4 复合问题"),
    ("PO-25-19-Q001", "e2", "订单 ORDER2025003 中，每款产品的单价是多少？", "C5 跨表连接（去掉公司名）"),
    ("PO-25-19-Q001", "e4", "订单 ORDER2025003 中，ELCTO公司订购的总金额是多少？", "C4 复合问题（仍需跨表，C5）"),
    ("PO-25-19-Q001", "e5", "订单 ORDER2025003 中，每批次交付的具体日期是什么？", "C4 复合问题"),
    ("PO-25-19-Q003", "e2", "订单 ORDER2025003 中，每款产品的单价是多少？", "C5 跨表连接（去掉公司名）"),
    ("PO-25-19-Q003", "e4", "订单 ORDER2025003 中，ENSA公司订购的总金额是多少？", "C4 复合问题（仍需跨表，C5）"),
    ("PO-25-19-Q003", "e5", "订单 ORDER2025003 中，每批次交付的具体日期是什么？", "C4 复合问题"),
    ("PO-25-19-Q001", "e2", "订单 ORDER2025003 中，ELCTO公司订购的产品单价分别是多少？", "对照：保留公司名的单价子问题"),
]


def main() -> None:
    workspace = Workspace(Path("."))
    dataset = load_dataset(workspace.ground_truth)
    cases = {c.case_id: c for c in dataset.cases}
    out = []
    for code, name in CONFIGS.items():
        assembly = assemble(resolve_config(workspace.root, name))
        store = open_store(workspace, name)
        chunks = _judge_chunks(workspace, name, {c.chunk_id for c in store.list_chunks()})
        boxes = _table_boxes(workspace, name)
        catalog = load_catalog(workspace.documents, discover(workspace.documents))
        searcher = UnitSearch(build_retriever(assembly, store, open_lexical(workspace, assembly)), store,
                              open_tables(workspace, name), "table")
        for case_id, group_id, sub, reason in PROBES:
            case = cases[case_id]
            evidence = judge_case(case, chunks)
            own = {x.excerpt_id for x in case.excerpts if tm.is_table_excerpt(x, boxes.get(x.document_id, []))}
            group = next(g for g in tm.table_groups(case, evidence, own) if g.group_id == group_id)
            row = {"config": code, "case_id": case_id, "group_id": group_id, "subquestion": sub, "reason": reason}
            for label, question in (("original", case.question), ("sub", sub)):
                scope = resolve_scope(question, catalog, True)
                hits = searcher.rank_hits(question, scope.document_ids)
                units = searcher.units_for(hits)
                _, k = tm.required_units([u.chunk_ids for u in units], [group])
                row[label] = {"chunk_rank": tm.chunk_rank([h.chunk.chunk_id for h in hits], group.any_word_chunks),
                              "required_units": k}
            out.append(row)
            print(code, case_id, group_id, row["original"], "->", row["sub"], flush=True)
    (HERE / "subquestions.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
