"""Controlled tests of why wrong chunks rank high (vector ranking, structured index, bge-m3).

A. Probes: one synthetic chunk at a time is added to the 33 contract chunks and ranked for the
   10 contract questions (Chinese originals and Spanish translations). None of the probes holds
   an answer. Factors: content (generic contract wording / specific facts no question asks for /
   out of domain) x length (1, 2, 4, 8 sentences, each a prefix of the same sentence list).
   Also c0004 merged with the chunk that follows it (c0005), as the P1 change would produce.
B. Real chunks of all 5 documents: per chunk, the mean normalized vector rank (0 = first,
   1 = last) over the questions it is not evidence for, against (1) its similarity to the
   document centroid (mean of the document's chunk vectors), (2) its length. Spearman correlation.
C. Mixed chunks: c0027, c0016, c0011, c0028 against each of their paragraphs substituted alone.

"Generic" is measured, not assumed: similarity to the document centroid.
Writes diagnose_hub.json. Run from the repository root:
HF_HUB_OFFLINE=1 .venv/Scripts/python.exe reports/experiments/v3.10-topk/diagnose_hub.py
"""

from __future__ import annotations

import glob
import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = next(p for p in HERE.parents if (p / "pyproject.toml").is_file())
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import numpy as np
from scipy.stats import spearmanr

from diagnose_fp import TRANSLATED
from rag.config import resolve_config
from rag.registry import assemble

RESULT = HERE / "full-rank/eval/2026-10-12_structured_k200_filter-on_hybrid/result.json"
CONTRACT = "d359c37e0bc3c87a"

GENERIC = [
    "EL CONTRATISTA ha cumplido con presentar la documentación requerida conforme a lo establecido en las bases.",
    "Las partes se obligan a cumplir las disposiciones del presente contrato y la normativa que resulte aplicable.",
    "LA EMPRESA podrá solicitar información adicional cuando lo considere necesario para la ejecución del contrato.",
    "Cualquier modificación al presente contrato se realizará por escrito y de acuerdo con la normativa vigente.",
    "Los documentos que forman parte del contrato se interpretan de manera conjunta y complementaria entre sí.",
    "EL CONTRATISTA declara conocer y aceptar todas las condiciones establecidas en el presente documento.",
    "Las comunicaciones entre las partes se efectuarán por los medios previstos en el presente contrato.",
    "Lo no previsto en el presente contrato se rige por las disposiciones legales que resulten aplicables.",
]
SPECIFIC = [
    "El 14 de octubre de 2025 se capacitó a 12 técnicos de la División de Ventas en el laboratorio de Quillabamba.",
    "El software de lectura MeterView versión 4.2.1 se instalará en 3 computadoras de la sede Cusco con licencia perpetua.",
    "Cada pallet de madera tratada medirá 1.20 m por 1.00 m y llevará como máximo 240 cajas apiladas en 8 niveles.",
    "El informe de ensayos de tipo fue emitido por el laboratorio LCOE de Madrid con el número 25-0417-MT.",
    "La placa de características llevará el logotipo de ELSE en azul Pantone 287 C y el año de fabricación 2025.",
    "El sello de seguridad de policarbonato tendrá numeración correlativa desde ELS-000001 hasta ELS-120000.",
    "La visita a la fábrica de Hangzhou durará 5 días y la harán 2 ingenieros designados por la División de Ventas.",
    "El manual de usuario se entregará en español, en formato PDF, con 48 páginas y un índice de alarmas del medidor.",
]
OUT_OF_DOMAIN = "La receta tradicional del cuy al horno requiere marinar la carne con ajo, comino y huacatay durante doce horas."


def load_chunks() -> dict[str, dict[str, str]]:
    docs = {}
    for f in glob.glob(str(ROOT / ".rag/indexes/structured/documents/*/chunks.json")):
        data = json.loads(Path(f).read_text(encoding="utf-8"))
        docs[data["document_id"]] = {c["chunk_id"].rsplit("-", 1)[1]: c["text"] for c in data["chunks"]}
    return docs


def main() -> None:
    embedder = assemble(resolve_config(ROOT, "structured")).embedder
    cache: dict[str, np.ndarray] = {}

    def vec(text: str) -> np.ndarray:
        if text not in cache:
            cache[text] = np.array(embedder.encode_passages([text])[0])
        return cache[text]

    def unit(v: np.ndarray) -> np.ndarray:
        return v / np.linalg.norm(v)

    docs = load_chunks()
    cases = json.loads(RESULT.read_text(encoding="utf-8"))["cases"]
    relevant = {c["case_id"]: {x.rsplit("-", 1)[1] for g in c["groups"] for s in g["acceptable_sets"] for x in s}
                for c in cases}
    questions = {(c["case_id"], lang): np.array(embedder.encode_query(q))
                 for c in cases for lang, q in (("zh", c["question"]), ("es", TRANSLATED[c["case_id"]]))}
    centroid = {d: unit(np.mean([vec(t) for t in chunks.values()], axis=0)) for d, chunks in docs.items()}
    contract = docs[CONTRACT]
    contract_cases = [c["case_id"] for c in cases if c["document_id"] == CONTRACT]
    out: dict = {}

    # A. probes
    def probe_ranks(text: str, replace: tuple[str, ...] = (), skip: tuple[str, ...] = ()) -> dict:
        base = {k: vec(t) for k, t in contract.items() if k not in replace}
        result = {"chars": len(text), "centroid_similarity": round(float(vec(text) @ centroid[CONTRACT]), 4)}
        for lang in ("zh", "es"):
            ranks = []
            for case in contract_cases:
                if case in skip:
                    continue
                q = questions[(case, lang)]
                s = float(vec(text) @ q)
                ranks.append(1 + sum(float(v @ q) > s for v in base.values()))
            result[lang] = {"ranks": ranks, "median": statistics.median(ranks), "top3": sum(r <= 3 for r in ranks)}
        return result

    probes = {}
    for name, sentences in (("generic", GENERIC), ("specific", SPECIFIC)):
        for n in (1, 2, 4, 8):
            probes[f"{name}_{n}"] = probe_ranks(" ".join(sentences[:n]))
    probes["out_of_domain_1"] = probe_ranks(OUT_OF_DOMAIN)
    probes["c0004_as_indexed"] = probe_ranks(contract["c0004"], replace=("c0004",))
    probes["c0004+c0005"] = probe_ranks(contract["c0004"] + "\n" + contract["c0005"], replace=("c0004", "c0005"),
                                        skip=("CONTRACT-105-Q002", "CONTRACT-105-Q004"))
    for single in range(len(SPECIFIC)):
        probes[f"specific_only_{single + 1}"] = probe_ranks(SPECIFIC[single])
    out["probes"] = probes

    # B. real chunks: centroid similarity and length vs rank over unrelated questions
    rows = []
    for d, chunks in docs.items():
        doc_cases = [c["case_id"] for c in cases if c["document_id"] == d]
        if not doc_cases:
            continue
        for lang in ("zh", "es"):
            norm_ranks: dict[str, list[float]] = {k: [] for k in chunks}
            for case in doc_cases:
                q = questions[(case, lang)]
                order = sorted(chunks, key=lambda k: -float(vec(chunks[k]) @ q))
                for r, k in enumerate(order):
                    if k not in relevant[case]:
                        norm_ranks[k].append(r / (len(order) - 1))
            for k, values in norm_ranks.items():
                if values:
                    rows.append({"document": d, "chunk": k, "lang": lang, "chars": len(chunks[k]),
                                 "centroid_similarity": float(vec(chunks[k]) @ centroid[d]),
                                 "mean_norm_rank": statistics.mean(values), "questions": len(values)})
    corr = {}
    for lang in ("zh", "es"):
        sub = [r for r in rows if r["lang"] == lang]
        for factor in ("centroid_similarity", "chars"):
            rho, p = spearmanr([r[factor] for r in sub], [r["mean_norm_rank"] for r in sub])
            corr[f"{lang}_{factor}"] = {"rho": round(float(rho), 3), "p": float(f"{p:.2g}"), "n": len(sub)}
        rho, p = spearmanr([r["chars"] for r in sub], [r["centroid_similarity"] for r in sub])
        corr[f"{lang}_chars_vs_centroid"] = {"rho": round(float(rho), 3), "p": float(f"{p:.2g}"), "n": len(sub)}
    out["real_chunks"] = {"correlation": corr, "rows": rows}

    # C. mixed chunks vs their paragraphs
    mixed = {}
    for k in ("c0027", "c0016", "c0011", "c0028"):
        skip = tuple(c for c in contract_cases if k in relevant[c])
        parts = [p.strip() for p in contract[k].split("\n\n") if len(p.strip()) >= 40]
        mixed[k] = {"whole": probe_ranks(contract[k], replace=(k,), skip=skip),
                    "parts": [probe_ranks(p, replace=(k,), skip=skip) | {"text": p[:80]} for p in parts]}
    out["mixed"] = mixed

    (HERE / "diagnose_hub.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    for name, p in probes.items():
        print(f"{name:22s} chars={p['chars']:5d} centroid={p['centroid_similarity']:.3f} "
              f"zh median={p['zh']['median']:5.1f} top3={p['zh']['top3']:2d} | "
              f"es median={p['es']['median']:5.1f} top3={p['es']['top3']:2d}")
    print(json.dumps(corr, indent=1))
    for k, m in mixed.items():
        w = m["whole"]
        print(k, "whole", w["chars"], w["centroid_similarity"], w["zh"]["median"], w["zh"]["top3"], w["es"]["median"])
        for p in m["parts"]:
            print("   part", p["chars"], p["centroid_similarity"], p["zh"]["median"], p["zh"]["top3"], p["es"]["median"],
                  p["text"][:50].replace("\n", " "))


if __name__ == "__main__":
    main()
