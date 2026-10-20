"""对照实验：引导句本身对向量相似度的影响（块边界不变）。

在带引导句的索引（LM+、LL+）上，把每个表格 chunk 开头的引导句去掉、其余文字不变，重新编码；
对每道表格题的每个表格证据组，比较三种文本下目标 chunk（覆盖其任一词的 chunk）与问题的相似度及向量名次：
带引导句（实际入库）、去掉引导句、只有引导句。非表格 chunk 不变，向量名次在同一范围内重排。

用法（仓库根目录）：python reports/experiments/v3.13-llm-tables/lead/strip_lead.py
结果写入同目录 strip_lead.json。
"""
import json
from pathlib import Path

import numpy as np

from rag.config import resolve_config
from rag.eval import table_metrics as tm
from rag.eval.dataset import load_dataset
from rag.eval.judge import judge_case
from rag.eval.runner import _judge_chunks, _table_boxes
from rag.index.builder import open_store
from rag.ingest.sources import discover
from rag.paths import Workspace
from rag.query.scope import load_catalog, resolve_scope
from rag.registry import assemble

HERE = Path(__file__).parent
CONFIGS = {"LM+": "structured_llm_md_lead_gemini38f", "LL+": "structured_llm_labeled_lead_gemini38f"}


def split_lead(text: str) -> tuple[str, str] | None:
    first, _, rest = text.partition("\n")
    return None if first.startswith("|") or not rest else (first, rest)


def main() -> None:
    workspace = Workspace(Path("."))
    dataset = load_dataset(workspace.ground_truth)
    catalog = load_catalog(workspace.documents, discover(workspace.documents))
    out = []
    for code, name in CONFIGS.items():
        embedder = assemble(resolve_config(workspace.root, name)).embedder
        store = open_store(workspace, name)
        all_chunks = store.list_chunks()
        judged = _judge_chunks(workspace, name, {c.chunk_id for c in all_chunks})
        boxes = _table_boxes(workspace, name)
        table = [c for c in all_chunks if c.kind == "table"]
        parts = {c.chunk_id: split_lead(c.text) for c in table}
        missing = [cid for cid, p in parts.items() if p is None]
        print(code, "table chunks", len(table), "without lead", len(missing), flush=True)
        ids = [c.chunk_id for c in all_chunks]
        full = dict(zip(ids, np.array(embedder.encode_passages([c.text for c in all_chunks]))))
        pieces = [cid for cid, p in parts.items() if p]
        body = dict(zip(pieces, np.array(embedder.encode_passages([parts[c][1] for c in pieces]))))
        lead = dict(zip(pieces, np.array(embedder.encode_passages([parts[c][0] for c in pieces]))))
        doc_of = {c.chunk_id: c.document_id for c in all_chunks}
        for case in dataset.cases:
            own = {x.excerpt_id for x in case.excerpts if tm.is_table_excerpt(x, boxes.get(x.document_id, []))}
            if not own:
                continue
            evidence = judge_case(case, judged)
            scope = set(resolve_scope(case.question, catalog, True).document_ids or doc_of.values())
            q = np.array(embedder.encode_query(case.question))
            in_scope = [cid for cid in ids if doc_of[cid] in scope]
            with_lead = {cid: float(full[cid] @ q) for cid in in_scope}
            stripped = {cid: float(body[cid] @ q) if cid in body else with_lead[cid] for cid in in_scope}

            def rank(sims, targets):
                order = sorted(sims, key=lambda c: (-sims[c], c))
                return next(i for i, c in enumerate(order, 1) if c in targets)
            for group in tm.table_groups(case, evidence, own):
                targets = group.any_word_chunks
                best = min(targets, key=lambda c: (-with_lead[c], c))
                best_s = min(targets, key=lambda c: (-stripped[c], c))
                out.append({"config": code, "case_id": case.case_id, "group_id": group.group_id,
                            "rank_with_lead": rank(with_lead, targets), "rank_without_lead": rank(stripped, targets),
                            "best_chunk": best[-5:], "sim_with_lead": round(with_lead[best], 4),
                            "sim_without_lead": round(stripped[best], 4),
                            "sim_lead_only": round(float(lead[best] @ q), 4) if best in lead else None,
                            "lead": parts[best][0] if parts.get(best) else None})
                r = out[-1]
                print(code, case.case_id, group.group_id, r["rank_without_lead"], "->", r["rank_with_lead"],
                      r["sim_without_lead"], "->", r["sim_with_lead"], "lead only", r["sim_lead_only"], flush=True)
            # every table chunk in scope: does the lead move it up or down?
            for cid in in_scope:
                if cid in body:
                    out.append({"config": code, "case_id": case.case_id, "chunk": cid[-5:], "kind": "all_table_chunks",
                                "sim_without_lead": round(stripped[cid], 4), "sim_with_lead": round(with_lead[cid], 4),
                                "sim_lead_only": round(float(lead[cid] @ q), 4),
                                "target": any(cid in g.any_word_chunks for g in tm.table_groups(case, evidence, own))})
    (HERE / "strip_lead.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
