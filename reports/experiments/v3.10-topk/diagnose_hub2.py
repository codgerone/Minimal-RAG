"""Follow-up to diagnose_hub.py.

A. Ablation of contract c0004 (why it ranks high for unrelated Chinese questions): each variant
   replaces c0004 among the 33 contract chunks; rank over the contract questions it is not
   evidence for, Chinese and Spanish. Question side: the Chinese questions with the leading
   "采购合同 CONT2025001" (or "采购合同 CONT2025001 中，") removed.
B. Partial Spearman correlation of centroid similarity and mean normalized rank, controlling for
   length (rank-residual method), on the real chunks of diagnose_hub.json.
Writes diagnose_hub2.json. Run from the repository root:
HF_HUB_OFFLINE=1 .venv/Scripts/python.exe reports/experiments/v3.10-topk/diagnose_hub2.py
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
from scipy.stats import rankdata, spearmanr

from diagnose_fp import TRANSLATED
from diagnose_hub import CONTRACT, RESULT, load_chunks
from rag.config import resolve_config
from rag.registry import assemble

VARIANTS = {
    "as_indexed": None,
    "line1_only": "2.4 EL CONTRATISTA ha cumplido con presentar la siguiente documentación en fecha 02/09/2025 y su subsanación en fecha 10/09/2025:",
    "line2_only": "· Garantía de fiel cumplimiento del contrato:",
    "no_dates": "2.4 EL CONTRATISTA ha cumplido con presentar la siguiente documentación:\n· Garantía de fiel cumplimiento del contrato:",
    "no_number": "EL CONTRATISTA ha cumplido con presentar la siguiente documentación en fecha 02/09/2025 y su subsanación en fecha 10/09/2025:\n· Garantía de fiel cumplimiento del contrato:",
    "no_guarantee": "2.4 EL CONTRATISTA ha cumplido con presentar la siguiente documentación en fecha 02/09/2025 y su subsanación en fecha 10/09/2025:",
    "dates_only": "en fecha 02/09/2025 y su subsanación en fecha 10/09/2025",
    "contrato_word_only": "del contrato:",
}
PREFIX = re.compile(r"^采购合同 CONT2025001( 中，| 中|，)?\s*")


def partial_spearman(x, y, z) -> float:
    rx, ry, rz = (rankdata(v) for v in (x, y, z))
    res = [r - np.polyval(np.polyfit(rz, r, 1), rz) for r in (rx, ry)]
    return float(np.corrcoef(*res)[0, 1])


def main() -> None:
    embedder = assemble(resolve_config(ROOT, "structured")).embedder
    contract = load_chunks()[CONTRACT]
    vecs = {k: np.array(v) for k, v in zip(contract, embedder.encode_passages(list(contract.values())))}
    cases = [c for c in json.loads(RESULT.read_text(encoding="utf-8"))["cases"] if c["document_id"] == CONTRACT]
    unrelated = [c for c in cases if "d359c37e0bc3c87a-c0004" not in
                 {x for g in c["groups"] for s in g["acceptable_sets"] for x in s}]
    qs = {"zh": {c["case_id"]: c["question"] for c in unrelated},
          "zh_no_prefix": {c["case_id"]: PREFIX.sub("", c["question"]) for c in unrelated},
          "es": {c["case_id"]: TRANSLATED[c["case_id"]] for c in unrelated}}
    qv = {lang: {k: np.array(embedder.encode_query(q)) for k, q in d.items()} for lang, d in qs.items()}
    out = {"questions": qs, "ablation": {}}
    for name, text in VARIANTS.items():
        v = vecs["c0004"] if text is None else np.array(embedder.encode_passages([text])[0])
        row = {"text": text or contract["c0004"]}
        for lang, d in qv.items():
            ranks = [1 + sum(float(vecs[k] @ q) > float(v @ q) for k in vecs if k != "c0004") for q in d.values()]
            row[lang] = {"ranks": ranks, "median": statistics.median(ranks), "top3": sum(r <= 3 for r in ranks)}
        out["ablation"][name] = row
        print(f"{name:20s}", {lang: (row[lang]["median"], row[lang]["top3"]) for lang in qv})
    rows = json.loads((HERE / "diagnose_hub.json").read_text(encoding="utf-8"))["real_chunks"]["rows"]
    out["partial"] = {}
    for lang in ("zh", "es"):
        sub = [r for r in rows if r["lang"] == lang]
        x, y, z = ([r[f] for r in sub] for f in ("centroid_similarity", "mean_norm_rank", "chars"))
        out["partial"][lang] = {"centroid_given_length": round(partial_spearman(x, y, z), 3),
                                "length_given_centroid": round(partial_spearman(z, y, x), 3),
                                "n": len(sub)}
    print(out["partial"])
    (HERE / "diagnose_hub2.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
