"""Second diagnosis pass: isolate, per missing chunk, which factor keeps it out of the ranking.

Each probe replaces one or more chunks of a document by text variants (the other chunks keep
their indexed text), embeds them with the configured encoder (bge-m3) and reports the vector
rank of the probed chunk among all chunks of the document, its similarity, and the chunks that
outrank it. Probes:

- delivery place table (CONTRACT c0020): header-labelled form, lead-in sentence, address only;
- signing date (CONTRACT c0032): the closing sentence as its own chunk;
- unit price table (PO 25-19 c0001): generic vs company-specific sub-questions;
- guarantee clause (CONTRACT c0021–c0026): merged into one chunk as if lists were not split off.

Also prints hybrid ranks (index unchanged) for the sub-questions. Writes diagnose2.json.
Run from the repository root: .venv/Scripts/python.exe reports/experiments/v3.10-topk/diagnose2.py
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
from rag.ingest.chunkers.tokens import E5TokenCounter
from rag.paths import Workspace
from rag.query.retriever import build_retriever
from rag.registry import assemble

CONTRACT, PO19 = "d359c37e0bc3c87a", "5a4c36e0022f4917"
Q003 = "采购合同 CONT2025001 规定的总履约期限是多久？具体的交货地点在哪里？是否允许提前交货？"
Q004 = "采购合同 CONT2025001 中，关于各 Ítem 的履约保证金（Garantía de fiel cumplimiento）金额及出具机构是如何规定的？"
Q010 = "采购合同 CONT2025001 是否规定分批交货？每个批次的具体交货日期分别是什么？"
PO_Q = "订单 ORDER2025003 中，{c}公司订购了哪些产品？每款产品的单价和数量是多少？订购的总金额是多少？交货的计划是怎么样的？请详细说明每批次交付的具体日期，及每批次交付的产品和数量明细。"

LEAD_IN = "Lugar de entrega:\n\nLa entrega de los bienes, será en los almacenes de LA EMPRESA, según el siguiente detalle:"
ADDRESS = "Almacén Central ELSE - Av. Mariscal Sucre Nº 400, Urb. Bancopata – Santiago, Cusco, Cusco"
CLOSING = ("De acuerdo con las bases integradas, la oferta y las disposiciones del presente contrato, las partes "
           "lo firman por duplicado en señal de conformidad en la ciudad del Cusco a los 23 días del mes de "
           "setiembre de 2025.")


def chunks_of(document_id: str) -> dict[str, str]:
    for f in glob.glob(str(ROOT / ".rag/indexes/structured/documents/*/chunks.json")):
        data = json.loads(Path(f).read_text(encoding="utf-8"))
        if data["document_id"] == document_id:
            return {c["chunk_id"].rsplit("-", 1)[1]: c["text"] for c in data["chunks"]}
    raise KeyError(document_id)


class Probe:
    def __init__(self, embedder):
        self.embedder, self.cache = embedder, {}

    def vec(self, text: str) -> np.ndarray:
        if text not in self.cache:
            self.cache[text] = np.array(self.embedder.encode_passages([text])[0])
        return self.cache[text]

    def rank(self, texts: dict[str, str], question: str, target: str, show: int = 3) -> dict:
        q = np.array(self.embedder.encode_query(question))
        sims = {k: float(self.vec(t) @ q) for k, t in texts.items()}
        order = sorted(sims, key=lambda k: (-sims[k], k))
        r = order.index(target) + 1
        return {"rank": r, "of": len(order), "similarity": round(sims[target], 4),
                "above": [(k, round(sims[k], 4)) for k in order[:min(show, r - 1)]]}


def main() -> None:
    workspace = Workspace(ROOT)
    base = resolve_config(ROOT, "structured")
    assembly = assemble(replace(base, retriever=replace(base.retriever, use="hybrid")))
    hybrid = build_retriever(assembly, open_store(workspace, assembly.name), open_lexical(workspace, assembly))
    probe, counter = Probe(assembly.embedder), E5TokenCounter()
    contract, po = chunks_of(CONTRACT), chunks_of(PO19)
    out: dict = {}

    def hybrid_rank(question: str, document: str, chunk: str) -> int | None:
        hits = hybrid.retrieve(question, 200, [document])
        ids = [h.chunk.chunk_id.rsplit("-", 1)[1] for h in hits]
        return ids.index(chunk) + 1 if chunk in ids else None

    # 1. delivery place table
    table_variants = {
        "indexed": contract["c0020"],
        "header_labelled": "| ELSE | LUGAR DE ENTREGA: " + ADDRESS + " |",
        "lead_in+indexed": LEAD_IN + "\n" + contract["c0020"],
        "lead_in+header_labelled": LEAD_IN + "\n| ELSE | LUGAR DE ENTREGA: " + ADDRESS + " |",
        "address_only": ADDRESS,
    }
    questions = {"original": Q003, "sub_with_code": "采购合同 CONT2025001 的交货地点在哪里？",
                 "sub_no_code": "合同规定的交货地点在哪里？", "spanish": "¿Cuál es el lugar de entrega de los bienes?"}
    out["c0020"] = {"hybrid_indexed": {q: hybrid_rank(t, CONTRACT, "c0020") for q, t in questions.items()},
                    "vector": {v: {q: probe.rank({**contract, "c0020": text}, t, "c0020")
                                   for q, t in questions.items()} for v, text in table_variants.items()}}
    # address shared with other chunks
    out["address_in"] = [k for k, t in contract.items() if "Mariscal Sucre" in t]

    # 2. signing date
    rest = contract["c0032"].replace(CLOSING, "").strip()
    split = {**contract, "c0032": rest, "c0032b": CLOSING}
    questions = {"original": Q010, "sub_dates": "采购合同 CONT2025001 中每个批次的具体交货日期分别是什么？",
                 "sub_signing": "采购合同 CONT2025001 的签署日期是哪一天？"}
    out["c0032"] = {"hybrid_indexed": {q: hybrid_rank(t, CONTRACT, "c0032") for q, t in questions.items()},
                    "vector_indexed": {q: probe.rank(contract, t, "c0032") for q, t in questions.items()},
                    "vector_closing_split": {q: probe.rank(split, t, "c0032b") for q, t in questions.items()}}

    # 3. unit price table
    questions = {"Q001_original": PO_Q.format(c="ELCTO"), "Q003_original": PO_Q.format(c="ENSA"),
                 "sub_generic": "订单 ORDER2025003 中每款产品的单价是多少？",
                 "sub_ELCTO": "订单 ORDER2025003 中，ELCTO公司订购的产品单价分别是多少？",
                 "sub_ENSA": "订单 ORDER2025003 中，ENSA公司订购的产品单价分别是多少？",
                 "sub_total_ENSA": "订单 ORDER2025003 中，ENSA公司订购的总金额是多少？"}
    out["c0001"] = {"hybrid_indexed": {q: hybrid_rank(t, PO19, "c0001") for q, t in questions.items()},
                    "vector_indexed": {q: probe.rank(po, t, "c0001", show=12) for q, t in questions.items()}}

    # 4. guarantee clause merged (lists not split off), joined like adjacent text nodes
    parts = ["c0021", "c0022", "c0023", "c0024", "c0025", "c0026"]
    merged = "\n\n".join(contract[p] for p in parts)
    merged_texts = {k: t for k, t in contract.items() if k not in parts} | {"m0021": merged}
    questions = {"original": Q004, "sub_issuer": "采购合同 CONT2025001 中各 Ítem 的履约保证金由哪家机构出具？"}
    out["guarantee_merged"] = {"tokens": counter.count_passage(merged),
                               "vector": {q: probe.rank(merged_texts, t, "m0021") for q, t in questions.items()}}

    (HERE / "diagnose2.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
