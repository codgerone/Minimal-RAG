"""False-positive diagnosis: why do wrong chunks rank above the needed ones (structured, hybrid)?

Two controls on the existing index, no system change:
1. Language: each question vs a hand translation into the document language (same content,
   no identifier code). Vector ranking of all chunks in scope; reports the needed-chunk ranks,
   the wrong chunks above them, and the similarity gap (top-1 minus median over the document).
2. Unit scoring: every chunk is scored by its best line (paragraph, list item or table row,
   see units()) instead of its whole text; vector only, compared with whole-chunk vectors
   embedded the same way, and the RRF fusion (k = 60) of the two rankings.
3. Identifier code in BM25: hybrid ranking with the scope codes removed from the BM25 query only
   (vector side unchanged), K = 3, 10 and 200 into fp/eval/.
Writes diagnose_fp.json. Run from the repository root:
.venv/Scripts/python.exe reports/experiments/v3.10-topk/diagnose_fp.py
"""

from __future__ import annotations

import glob
import json
import statistics
import sys
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = next(p for p in HERE.parents if (p / "pyproject.toml").is_file())
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import rag.eval.runner as runner
import rag.query.retriever as retriever
import numpy as np

from rag.config import resolve_config
from rag.index.builder import open_store
from rag.paths import Workspace
from rag.registry import assemble
from run_full_rank import summarize as full_rank_summary
from run_nocode import strip_codes
from run_sweep import summarize as sweep_summary

OUT = HERE / "fp"
RESULT = HERE / "full-rank/eval/2026-10-12_structured_k200_filter-on_hybrid/result.json"

# Hand translations into the document language, identifier code removed.
TRANSLATED = {
    "CONTRACT-105-Q001": "¿Quiénes son las partes que firman el contrato y quiénes son sus representantes legales?",
    "CONTRACT-105-Q002": "¿Qué ítems incluye el contrato? ¿Cuál es la cantidad, el precio unitario y el monto total con impuestos de cada uno? ¿Cuál es el monto total del contrato?",
    "CONTRACT-105-Q003": "¿Cuál es el plazo total de ejecución del contrato? ¿Dónde es el lugar de entrega? ¿Se aceptan entregas anticipadas?",
    "CONTRACT-105-Q004": "¿Cuál es el monto de la garantía de fiel cumplimiento de cada ítem y qué entidad la emite?",
    "CONTRACT-105-Q005": "¿Cuál es el plazo de la garantía técnico-comercial? ¿Cuáles son los plazos de respuesta y reparación del contratista ante una falla durante la garantía?",
    "CONTRACT-105-Q006": "¿Cuál es el criterio para considerar una falla repetitiva? ¿Qué responsabilidad asume el contratista ante una falla repetitiva?",
    "CONTRACT-105-Q007": "¿Qué condiciones previas exige la empresa para tramitar el pago? ¿Cuáles son los plazos para emitir la conformidad y para pagar?",
    "CONTRACT-105-Q008": "Si el contratista incurre en retraso injustificado en la entrega, ¿cuál es la fórmula de la penalidad por mora y cuál es su monto máximo?",
    "CONTRACT-105-Q009": "En la primera entrega (1era ENTREGA, 170 días después de la firma del contrato), ¿qué productos y qué cantidades se entregan?",
    "CONTRACT-105-Q010": "¿El contrato establece entregas parciales? ¿Cuál es la fecha de entrega de cada entrega?",
    "E001-602-Q001": "¿Cuál es la fecha de vencimiento del pago de la factura, cuánto se debe pagar y cuánto corresponde a impuestos?",
    "E001-602-Q002": "¿Cuál es el precio unitario y la cantidad de HXE34K-S1 en la factura?",
    "PO-24-12-Q001": "Which products does the purchase order include? What are the unit price and quantity of each? What is the total amount of the order?",
    "PO-24-12-Q002": "Describe the delivery schedule of the purchase order.",
    "PO-24-12-Q003": "In how many batches is the order delivered? What is the delivery date of each batch?",
    "PO-24-12-Q004": "Does the total amount of the order include the cost of MDC ORCA? If not, how much more must be paid, and what is the payment deadline?",
    "PO-24-12-Q005": "How are the operation and maintenance services charged?",
    "PO-24-12-Q006": "What are the payment terms of the purchase order?",
    "PO-25-15-Q001": "What is the date of the 4th delivery, and what is the total amount of the products delivered?",
    "PO-25-15-Q002": "What is the date of the 6th delivery, and what is the total amount of the products delivered?",
    "PO-25-19-Q001": "Which products did ELCTO order? What are the unit price and quantity of each product? What is the total amount ordered? What is the delivery plan: the date of each delivery and the products and quantities delivered in each?",
    "PO-25-19-Q002": "How many HXE34K-S1 did ELUC order, and what is the total amount?",
    "PO-25-19-Q003": "Which products did ENSA order? What are the unit price and quantity of each product? What is the total amount ordered? What is the delivery plan: the date of each delivery and the products and quantities delivered in each?",
    "PO-25-19-Q004": "What are the customer's payment terms?",
}


class ExpWorkspace(Workspace):
    @property
    def reports(self) -> Path:
        return OUT


def needed_rank(group: dict, rank: dict[str, int]) -> int | None:
    best = [max(rank.get(c, 10**6) for c in s) for s in group["acceptable_sets"]]
    return min(best) if best and min(best) < 10**6 else None


def chunk_texts(document_id: str) -> dict[str, str]:
    for f in glob.glob(str(ROOT / ".rag/indexes/structured/documents/*/chunks.json")):
        data = json.loads(Path(f).read_text(encoding="utf-8"))
        if data["document_id"] == document_id:
            return {c["chunk_id"]: c["text"] for c in data["chunks"]}
    raise KeyError(document_id)


def units(text: str) -> list[str]:
    """Lines of the chunk text (paragraphs, list items, table rows); a line under 20 characters
    is joined to the next one so that headings and labels stay with what follows."""
    result, pending = [], ""
    for line in (l.strip() for l in text.split("\n")):
        if not line:
            continue
        pending = f"{pending}\n{line}" if pending else line
        if len(pending) >= 20:
            result.append(pending)
            pending = ""
    if pending:
        result.append(pending)
    return result


class LexicalNoCode:
    def __init__(self, inner):
        self.inner = inner

    def rank_all(self, question, document_ids=None):
        return self.inner.rank_all(strip_codes(question), document_ids)


def main() -> None:
    workspace = Workspace(ROOT)
    base = resolve_config(ROOT, "structured")
    assembly = assemble(replace(base, retriever=replace(base.retriever, use="hybrid")))
    semantic = retriever.SemanticRetriever(assembly.embedder, open_store(workspace, assembly.name))
    cases = json.loads(RESULT.read_text(encoding="utf-8"))["cases"]
    out: dict = {"language": []}
    for case in cases:
        row = {"case_id": case["case_id"]}
        for label, question in (("original", case["question"]), ("translated", TRANSLATED[case["case_id"]])):
            ranked = semantic.rank_all(question, case["scope"]["document_ids"])
            rank = {chunk.chunk_id: i for i, (chunk, _) in enumerate(ranked, start=1)}
            sims = [s for _, s in ranked]
            needed = [needed_rank(g, rank) for g in case["groups"]]
            relevant = {c for g in case["groups"] for s in g["acceptable_sets"] for c in s}
            last = max(n or 999 for n in needed)
            row[label] = {"question": question, "needed": needed,
                          "wrong_above": [chunk.chunk_id[-5:] for chunk, _ in ranked[:min(last, 11) - 1]
                                          if chunk.chunk_id not in relevant],
                          "gap": round(sims[0] - statistics.median(sims), 4)}
        out["language"].append(row)
        print(row["case_id"], {k: (v["needed"], v["gap"], v["wrong_above"]) for k, v in row.items()
                               if isinstance(v, dict)}, flush=True)

    cache: dict[str, np.ndarray] = {}

    def vec(text: str) -> np.ndarray:
        if text not in cache:
            cache[text] = np.array(assembly.embedder.encode_passages([text])[0])
        return cache[text]

    out["units"] = []
    texts = {c: t for case in cases for c, t in chunk_texts(case["document_id"]).items()}
    for case in cases:
        scope = [c for c in texts if c.startswith(case["document_id"])]
        relevant = {c for g in case["groups"] for s in g["acceptable_sets"] for c in s}
        q = np.array(assembly.embedder.encode_query(case["question"]))
        row = {"case_id": case["case_id"]}
        ranks = {}
        for label, score in (("chunk", lambda c: float(vec(texts[c]) @ q)),
                             ("unit_max", lambda c: max(float(vec(u) @ q) for u in units(texts[c]))),
                             ("fused", lambda c: 1 / (60 + ranks["chunk"][c]) + 1 / (60 + ranks["unit_max"][c]))):
            order = sorted(scope, key=lambda c: (-score(c), c))
            rank = ranks[label] = {c: i for i, c in enumerate(order, start=1)}
            needed = [needed_rank(g, rank) for g in case["groups"]]
            last = max(n or 999 for n in needed)
            row[label] = {"needed": needed, "wrong_above": [c[-5:] for c in order[:min(last, 11) - 1]
                                                            if c not in relevant]}
        out["units"].append(row)
        print(row["case_id"], {k: (v["needed"], v["wrong_above"]) for k, v in row.items() if isinstance(v, dict)},
              flush=True)

    original = retriever.HybridRetriever.__init__

    def patched(self, semantic, lexical):
        original(self, semantic, LexicalNoCode(lexical))

    retriever.HybridRetriever.__init__ = patched
    hybrid = assembly
    workspace = ExpWorkspace(ROOT)
    out["bm25_no_code"] = {f"k{k}": sweep_summary(runner.run_evaluation(workspace, hybrid, k)) for k in (3, 10)}
    out["bm25_no_code"]["full_rank"] = full_rank_summary(runner.run_evaluation(workspace, hybrid, 200))
    print({k: {m: v[m]["numerator"] for m in ("hit_rate", "complete_coverage")} | {"mrr": round(v["mrr"]["value"], 3)}
           for k, v in out["bm25_no_code"].items() if k != "full_rank"},
          [(r["case_id"], r["needed_k"]) for r in out["bm25_no_code"]["full_rank"]])
    (HERE / "diagnose_fp.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
