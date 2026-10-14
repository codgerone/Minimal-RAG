"""Follow-up to diagnose_hub2.py: does the digit string in the identifier code (CONT2025001)
attract chunks that contain dates of 2025?

A. c0004 (dates in 2025 / the same dates moved to 2019 / no dates) against Chinese questions whose
   code is: kept (CONT2025001), removed (只留"采购合同"), replaced by a code without digits
   (CONTABCDEFG), replaced by a code with another year (CONT2019001).
B. All 33 contract chunks: how often each one is a wrong top-3 chunk, by question variant, and
   whether a chunk contains a 2025 date.
Writes diagnose_hub3.json. Run from the repository root:
HF_HUB_OFFLINE=1 .venv/Scripts/python.exe reports/experiments/v3.10-topk/diagnose_hub3.py
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = next(p for p in HERE.parents if (p / "pyproject.toml").is_file())
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import numpy as np

from diagnose_hub import CONTRACT, RESULT, load_chunks
from rag.config import resolve_config
from rag.registry import assemble

CHUNK = {
    "dates_2025": None,
    "dates_2019": "2.4 EL CONTRATISTA ha cumplido con presentar la siguiente documentación en fecha 02/09/2019 y su subsanación en fecha 10/09/2019:\n· Garantía de fiel cumplimiento del contrato:",
    "no_dates": "2.4 EL CONTRATISTA ha cumplido con presentar la siguiente documentación:\n· Garantía de fiel cumplimiento del contrato:",
}
QUESTION = {"code_kept": "CONT2025001", "code_removed": "", "code_letters": "CONTABCDEFG", "code_2019": "CONT2019001"}
DATE_2025 = re.compile(r"(?:\b\d{1,2}/\d{1,2}/2025\b|de 2025\b|\b2025\b)")


def main() -> None:
    embedder = assemble(resolve_config(ROOT, "structured")).embedder
    contract = load_chunks()[CONTRACT]
    vecs = {k: np.array(v) for k, v in zip(contract, embedder.encode_passages(list(contract.values())))}
    cases = [c for c in json.loads(RESULT.read_text(encoding="utf-8"))["cases"] if c["document_id"] == CONTRACT]
    relevant = {c["case_id"]: {x.rsplit("-", 1)[1] for g in c["groups"] for s in g["acceptable_sets"] for x in s}
                for c in cases}

    def question(case: dict, code: str) -> str:
        return re.sub(r"\s+", " ", case["question"].replace("CONT2025001", code)).replace("合同 的", "合同的")

    qv = {name: {c["case_id"]: np.array(embedder.encode_query(question(c, code))) for c in cases}
          for name, code in QUESTION.items()}
    out: dict = {"c0004": {}, "top3_wrong": {}}
    for cname, text in CHUNK.items():
        v = vecs["c0004"] if text is None else np.array(embedder.encode_passages([text])[0])
        row = {}
        for qname, d in qv.items():
            ranks = [1 + sum(float(vecs[k] @ q) > float(v @ q) for k in vecs if k != "c0004")
                     for case, q in d.items() if "c0004" not in relevant[case]]
            row[qname] = {"median": statistics.median(ranks), "top3": sum(r <= 3 for r in ranks), "ranks": ranks}
        out["c0004"][cname] = row
        print(f"{cname:12s}", {q: (r["median"], r["top3"]) for q, r in row.items()})
    has_2025 = {k: bool(DATE_2025.search(t)) for k, t in contract.items()}
    for qname, d in qv.items():
        counts: dict[str, int] = {}
        for case, q in d.items():
            order = sorted(vecs, key=lambda k: -float(vecs[k] @ q))
            for k in order[:3]:
                if k not in relevant[case]:
                    counts[k] = counts.get(k, 0) + 1
        out["top3_wrong"][qname] = counts
        with_date = sum(n for k, n in counts.items() if has_2025[k])
        print(f"{qname:12s} wrong top-3 total={sum(counts.values())} in 2025-dated chunks={with_date}",
              sorted(counts.items(), key=lambda kv: -kv[1]))
    out["has_2025_date"] = has_2025
    print("chunks with a 2025 date:", sum(has_2025.values()), "of", len(has_2025),
          [k for k, v in has_2025.items() if v])
    (HERE / "diagnose_hub3.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
