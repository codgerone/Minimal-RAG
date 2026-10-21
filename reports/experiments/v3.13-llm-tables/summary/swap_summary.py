"""对照实验：摘要或引导句换成提示词 v4 的版本，表格证据的名次怎样变（返回整表，混合检索）。

summary：在 structured_summary 当前索引（提示词 v5）上，只替换摘要 chunk 的文字，其余 chunk 不变，
在内存中重算向量相似度与 BM25（统计量取整个索引，规则同 rag/index/lexical.py），按 RRF 融合；
对每道表格题的每个表格证据组，记录表格 chunk 名次（指标 1）与所需返回单元数（指标 2）。三种摘要：
v5（实际入库）、v4（summaries.json）、v4 去掉订单号（只有 3 张订单表的 v4 摘要写了订单号，其余与 v4 相同）。
lead：在 structured_llm_lead 当前索引上，只替换每个表格 chunk 第一行的引导句（v5 → v4，v4 引导句都不含编号）。
先确认 v5 的结果与评估 result.json 一致，复现无误后再比较。

用法（仓库根目录）：python reports/experiments/v3.13-llm-tables/summary/swap_summary.py summary|lead
需先运行 recover_v4.py / recover_v4_leads.py。结果写入同目录 swap_<summary|lead>.json。
"""
import json
import math
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import numpy as np

from rag.config import resolve_config
from rag.eval import table_metrics as tm
from rag.eval.dataset import load_dataset
from rag.eval.judge import judge_case
from rag.eval.runner import _judge_chunks, _table_boxes
from rag.index.builder import open_store, open_tables
from rag.index.lexical import B, K1, idf
from rag.ingest.sources import discover
from rag.paths import Workspace
from rag.query.retriever import RRF_K
from rag.query.scope import load_catalog, resolve_scope
from rag.registry import assemble

HERE = Path(__file__).parent
RUNS = {"summary": ("structured_summary", "2026-10-20_structured_summary_k3_filter-on_hybrid_table"),
        "lead": ("structured_llm_lead", "2026-10-20_structured_llm_lead_k3_filter-on_hybrid_table")}
ORDER_NUMBERS = (" for PURCHASE ORDER N° 24-12-HPE", " for Purchase Order N° 25-15-HPE",
                 " for Purchase Order N° 25-19-HPE")


def strip_numbers(text: str) -> str:
    for phrase in ORDER_NUMBERS:
        text = text.replace(phrase, "")
    return text


def strip_parts(text: str, code: bool) -> str:
    """Remove only the order code (keep "for Purchase Order") or only the words (keep "for N° <code>")."""
    for phrase in ORDER_NUMBERS:
        words, _, number = phrase.partition(" N° ")
        text = text.replace(phrase, words if code else " for N° " + number)
    return text


def main(mode: str) -> None:
    CONFIG, run = RUNS[mode]
    RESULT = f"reports/eval/{run}/result.json"
    workspace = Workspace(Path("."))
    assembly = assemble(resolve_config(workspace.root, CONFIG))
    embedder, analyzer = assembly.embedder, assembly.lexical
    dataset = load_dataset(workspace.ground_truth)
    catalog = load_catalog(workspace.documents, discover(workspace.documents))
    store = open_store(workspace, CONFIG)
    chunks = store.list_chunks()
    ids = [c.chunk_id for c in chunks]
    doc_of = {c.chunk_id: c.document_id for c in chunks}
    with sqlite3.connect(workspace.index_dir(CONFIG) / "bm25.sqlite") as db:
        language = dict(db.execute("SELECT chunk_id, language FROM chunks"))
    owners = {cid: t.table_id for cid, t in open_tables(workspace, CONFIG).tables_for_chunks(ids).items()}
    judged = _judge_chunks(workspace, CONFIG, set(ids))
    boxes = _table_boxes(workspace, CONFIG)
    expected = {c["case_id"]: c["table"] for c in json.loads(Path(RESULT).read_text(encoding="utf-8"))["cases"]
                if c.get("table")}

    if mode == "summary":
        summaries = json.loads((HERE / "summaries.json").read_text(encoding="utf-8"))
        by_text = {s["v5"]: s for s in summaries}
        summary_chunks = {c.chunk_id: by_text[c.text] for c in chunks if c.kind == "table_summary"}
        assert len(summary_chunks) == len(summaries), "every summary chunk must match a v5 summary"
        variants = {"v5": lambda s: s["v5"], "v4": lambda s: s["v4"],
                    "v4_no_number": lambda s: strip_numbers(s["v4"]),
                    # the two parts of the removed phrase, separately: the code, and the words "Purchase Order"
                    "v4_no_code": lambda s: strip_parts(s["v4"], code=True),
                    "v4_no_words": lambda s: strip_parts(s["v4"], code=False)}
    else:
        leads = {s["v5"]: s["v4"] for s in json.loads((HERE / "leads.json").read_text(encoding="utf-8"))}
        summary_chunks = {}
        for c in chunks:
            first, _, rest = c.text.partition("\n")
            if c.kind == "table" and first in leads:
                summary_chunks[c.chunk_id] = {"v5": c.text, "v4": leads[first] + "\n" + rest}
        print("table chunks with a lead:", len(summary_chunks))
        variants = {"v5": lambda s: s["v5"], "v4": lambda s: s["v4"]}

    base = {c.chunk_id: c.text for c in chunks}
    base_vectors = dict(zip(ids, np.array(embedder.encode_passages([base[i] for i in ids]))))
    out = []
    for name, pick in variants.items():
        text = dict(base) | {cid: pick(s) for cid, s in summary_chunks.items()}
        changed = [cid for cid in summary_chunks if text[cid] != base[cid]]
        vectors = dict(base_vectors) | dict(zip(changed, np.array(embedder.encode_passages([text[c] for c in changed]))))
        terms = {cid: analyzer.terms(text[cid], language[cid]) for cid in ids}
        lengths = {cid: sum(t.values()) for cid, t in terms.items()}
        average = sum(lengths.values()) / len(lengths)
        for case in dataset.cases:
            own = {x.excerpt_id for x in case.excerpts if tm.is_table_excerpt(x, boxes.get(x.document_id, []))}
            if not own:
                continue
            scope = set(resolve_scope(case.question, catalog, True).document_ids or doc_of.values())
            in_scope = [cid for cid in ids if doc_of[cid] in scope]
            q = np.array(embedder.encode_query(case.question))
            dense = sorted(in_scope, key=lambda c: (-float(vectors[c] @ q), c))
            wanted = analyzer.query_terms(case.question)
            frequency = Counter(t for cid in ids for t in terms[cid] if t in set().union(*wanted.values()))
            bm25 = {}
            for cid in in_scope:
                score = 0.0
                for t in wanted[language[cid]]:
                    tf = terms[cid].get(t, 0)
                    if tf:
                        norm = tf + K1 * (1 - B + B * lengths[cid] / average)
                        score += idf(len(ids), frequency[t]) * tf * (K1 + 1) / norm
                if score > 0:
                    bm25[cid] = score
            sparse = {cid: r for r, cid in enumerate(sorted(bm25, key=lambda c: (-bm25[c], c)), start=1)}
            fused = {cid: 1 / (RRF_K + r) + (1 / (RRF_K + sparse[cid]) if cid in sparse else 0.0)
                     for r, cid in enumerate(dense, start=1)}
            ranking = sorted(fused, key=lambda c: (-fused[c], c))
            unit_order = list(dict.fromkeys(owners.get(c, c) for c in ranking))
            members = {}
            for cid in ranking:
                members.setdefault(owners.get(cid, cid), []).append(cid)
            units = [tuple(members[u]) for u in unit_order]
            groups = tm.table_groups(case, judge_case(case, judged), own)
            _, k = tm.required_units(units, groups)
            row = {"variant": name, "case_id": case.case_id, "required_k": k,
                   "chunk_rank": {g.group_id: tm.chunk_rank(ranking, g.any_word_chunks) for g in groups}}
            out.append(row)
            if name == "v5":
                want = {g["group_id"]: g["chunk_rank"] for g in expected[case.case_id]["groups"]}
                assert row["chunk_rank"] == want and k == expected[case.case_id]["required_k"], (case.case_id, row, want)
    print("v5 reproduces the evaluation")
    cases = list(dict.fromkeys(r["case_id"] for r in out))
    for cid in cases:
        rows = {r["variant"]: r for r in out if r["case_id"] == cid}
        if len({json.dumps(r["chunk_rank"]) + str(r["required_k"]) for r in rows.values()}) > 1:
            print(cid, " | ".join(f"{v}: k={r['required_k']} {r['chunk_rank']}" for v, r in rows.items()))
    (HERE / f"swap_{mode}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1])
