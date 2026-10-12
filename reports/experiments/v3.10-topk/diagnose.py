"""Diagnosis of the structured questions not fully covered at K=10 (hybrid retrieval).

Tests two hypotheses on the existing structured index, without changing the system:

1. Compound question: each uncovered evidence group is queried with its own sub-question
   (written by hand from the information item, same wording style as the dataset).
2. Missing context: each chunk is re-embedded with the context it loses when cut out of the
   document, by generic rules (no per-chunk editing):
   - heading: the nearest preceding `title` node not already in the chunk;
   - lead-in: for list / table chunks, the preceding paragraph when it ends with ':';
   - row group: in a table chunk, every data row gets the nearest full-width section row
     above it (a row whose other cells are all '←'), also across chunk boundaries.
   Ranking for hypothesis 2 is vector-only (BM25 would need a rebuilt index); it is compared
   with the vector ranking of the original texts embedded the same way.

Run from the repository root: .venv/Scripts/python.exe reports/experiments/v3.10-topk/diagnose.py
Writes diagnose.json next to this script.
"""

from __future__ import annotations

import glob
import json
import sys
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = next(p for p in HERE.parents if (p / "pyproject.toml").is_file())
sys.path.insert(0, str(ROOT))

import numpy as np

from rag.config import resolve_config
from rag.index.builder import open_lexical, open_store
from rag.paths import Workspace
from rag.query.retriever import build_retriever
from rag.registry import assemble

CONTRACT, PO19 = "d359c37e0bc3c87a", "5a4c36e0022f4917"

# (case, group) -> (document, needed chunks of the best acceptable set at K=200, sub-questions)
CASES = {
    ("CONTRACT-105-Q003", "e2"): (CONTRACT, ["c0020"], ["采购合同 CONT2025001 的交货地点在哪里？"]),
    ("CONTRACT-105-Q003", "e3"): (CONTRACT, ["c0019"], ["采购合同 CONT2025001 是否允许提前交货？"]),
    ("CONTRACT-105-Q004", "e2"): (CONTRACT, ["c0021", "c0022", "c0023", "c0024", "c0025", "c0026"],
                                  ["采购合同 CONT2025001 中各 Ítem 的履约保证金由哪家机构出具？"]),
    ("CONTRACT-105-Q010", "e2"): (CONTRACT, ["c0017", "c0032"],
                                  ["采购合同 CONT2025001 中每个批次的具体交货日期分别是什么？",
                                   "采购合同 CONT2025001 的签署日期是哪一天？"]),
    ("PO-25-19-Q001", "e1"): (PO19, ["c0009", "c0010", "c0014"], ["订单 ORDER2025003 中，ELCTO公司订购了哪些产品？数量分别是多少？"]),
    ("PO-25-19-Q001", "e2"): (PO19, ["c0001"], ["订单 ORDER2025003 中每款产品的单价是多少？"]),
    ("PO-25-19-Q001", "e5"): (PO19, ["c0021"], ["订单 ORDER2025003 中每批次交付的具体日期是什么？"]),
    ("PO-25-19-Q003", "e1"): (PO19, ["c0009", "c0011", "c0014", "c0015", "c0017", "c0019"],
                              ["订单 ORDER2025003 中，ENSA公司订购了哪些产品？数量分别是多少？"]),
    ("PO-25-19-Q003", "e2"): (PO19, ["c0001"], ["订单 ORDER2025003 中每款产品的单价是多少？"]),
    ("PO-25-19-Q003", "e5"): (PO19, ["c0021"], ["订单 ORDER2025003 中每批次交付的具体日期是什么？"]),
}
QUESTIONS = {
    "CONTRACT-105-Q003": "采购合同 CONT2025001 规定的总履约期限是多久？具体的交货地点在哪里？是否允许提前交货？",
    "CONTRACT-105-Q004": "采购合同 CONT2025001 中，关于各 Ítem 的履约保证金（Garantía de fiel cumplimiento）金额及出具机构是如何规定的？",
    "CONTRACT-105-Q010": "采购合同 CONT2025001 是否规定分批交货？每个批次的具体交货日期分别是什么？",
    "PO-25-19-Q001": "订单 ORDER2025003 中，ELCTO公司订购了哪些产品？每款产品的单价和数量是多少？订购的总金额是多少？交货的计划是怎么样的？请详细说明每批次交付的具体日期，及每批次交付的产品和数量明细。",
    "PO-25-19-Q003": "订单 ORDER2025003 中，ENSA公司订购了哪些产品？每款产品的单价和数量是多少？订购的总金额是多少？交货的计划是怎么样的？请详细说明每批次交付的具体日期，及每批次交付的产品和数量明细。",
}


def load(document_id: str) -> tuple[list[dict], list[dict]]:
    folder = next(Path(f).parent for f in glob.glob(str(ROOT / ".rag/indexes/structured/documents/*/chunks.json"))
                  if json.loads(Path(f).read_text(encoding="utf-8"))["document_id"] == document_id)
    chunks = json.loads((folder / "chunks.json").read_text(encoding="utf-8"))["chunks"]
    nodes = json.loads((folder / "parsed.json").read_text(encoding="utf-8"))["nodes"]
    return chunks, nodes


def is_section_row(line: str) -> bool:
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return len(cells) > 2 and cells[0] not in ("", "←") and all(c == "←" for c in cells[1:])


def enrich(chunks: list[dict], nodes: list[dict]) -> dict[str, str]:
    order = {n["node_id"]: i for i, n in enumerate(nodes)}
    out, section = {}, {}
    for chunk in chunks:
        first = order[chunk["sources"][0]["node_id"].split("_item_")[0]]
        context = []
        title = next((n["text"] for n in reversed(nodes[:first]) if n.get("kind") == "title"), None)
        if title and title not in chunk["text"]:
            context.append(title)
        if chunk["kind"] in ("list", "table"):
            before = next((n for n in reversed(nodes[:first]) if n.get("kind") in ("paragraph", "title")), None)
            if before and before["kind"] == "paragraph" and before["text"].rstrip().endswith(":") \
                    and before["text"] not in chunk["text"]:
                context.append(before["text"].strip())
        text = chunk["text"]
        if chunk["kind"] == "table":
            unit = chunk["parent_unit_id"] or chunk["chunk_id"]
            current, lines = section.get(unit), []
            for line in text.split("\n"):
                if is_section_row(line):
                    current = line.strip().strip("|").split("|")[0].strip()
                    lines.append(line)
                else:
                    lines.append(f"| {current} {line.strip()}" if current else line)
            section[unit] = current
            text = "\n".join(lines)
        out[chunk["chunk_id"]] = "\n".join(context + [text])
    return out


def vector_ranks(embedder, texts: dict[str, str], question: str) -> dict[str, int]:
    ids = sorted(texts)
    matrix = np.array(embedder.encode_passages([texts[i] for i in ids]))
    sims = matrix @ np.array(embedder.encode_query(question))
    order = sorted(range(len(ids)), key=lambda j: (-sims[j], ids[j]))
    return {ids[j]: rank for rank, j in enumerate(order, start=1)}


def main() -> None:
    workspace = Workspace(ROOT)
    base = resolve_config(ROOT, "structured")
    assembly = assemble(replace(base, retriever=replace(base.retriever, use="hybrid")))
    store = open_store(workspace, assembly.name)
    hybrid = build_retriever(assembly, store, open_lexical(workspace, assembly))
    embedder = assembly.embedder
    docs = {d: load(d) for d in (CONTRACT, PO19)}
    original = {d: {c["chunk_id"]: c["text"] for c in docs[d][0]} for d in docs}
    enriched = {d: enrich(*docs[d]) for d in docs}

    def hybrid_ranks(question: str, document: str) -> dict[str, int]:
        hits = hybrid.retrieve(question, 200, [document])
        return {h.chunk.chunk_id: h_rank for h_rank, h in enumerate(hits, start=1)}

    rows = []
    for (case, group), (document, needed, subs) in CASES.items():
        ids = [f"{document}-{c}" for c in needed]
        row = {"case_id": case, "group_id": group, "chunks": needed, "scope_chunks": len(original[document])}
        for label, question in [("original", QUESTIONS[case])] + [(f"sub{i + 1}", q) for i, q in enumerate(subs)]:
            h = hybrid_ranks(question, document)
            v = vector_ranks(embedder, original[document], question)
            e = vector_ranks(embedder, enriched[document], question)
            row[label] = {"question": question,
                          "hybrid": [h.get(i) for i in ids],
                          "vector": [v[i] for i in ids],
                          "vector_enriched": [e[i] for i in ids]}
        rows.append(row)
        print(case, group, {k: (v["hybrid"], v["vector"], v["vector_enriched"]) for k, v in row.items()
                            if isinstance(v, dict)}, flush=True)
    samples = {d: {c: enriched[d][f"{d}-{c}"] for c in ("c0009", "c0010", "c0019", "c0020", "c0023", "c0001")
                   if f"{d}-{c}" in enriched[d]} for d in docs}
    (HERE / "diagnose.json").write_text(json.dumps({"rows": rows, "enriched_samples": samples},
                                                   ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
