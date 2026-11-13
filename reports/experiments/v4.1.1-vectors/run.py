"""V4.1.1 acceptance: are the vectors of the rebuilt indexes the same as V4.1's?

Proposal question 3 (docs/archive/changes/v4.1.1.md), three layers, all must pass:
1. correspondence: every chunk ID has a vector on both sides, same dimension, same text;
2. values: each chunk's ‖Δv‖ (new − V4.1) is within the batch-noise bound, measured independently
   here: every distinct chunk text encoded alone, and in random batches of 8 and of 32; the bound
   is the largest ‖difference‖ between two encodings of the same text;
3. rankings: the 58 questions' vector rankings over all chunks of each index; a pair of chunks may
   swap only when its V4.1 score gap ≤ the sum of the two ‖Δv‖ (unit query and chunk vectors), so
   every swap must satisfy that bound.

Usage: python reports/experiments/v4.1.1-vectors/run.py <V4.1 copy of .rag/indexes>
Writes noise.json (the bound; reused when present) and summary.json next to this script.
Also re-encodes every text alone in this process and compares with the stored vector (written
by the rebuild in another process), to report how reproducible single-text encoding is.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import chromadb
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
CONFIGS = ("structured", "structured_labeled", "structured_llm", "structured_llm_labeled",
           "structured_llm_labeled_lead", "structured_llm_lead", "structured_summary", "plain_text")


def load(index_dir: Path) -> dict[str, tuple[str, np.ndarray]]:
    got = chromadb.PersistentClient(path=str(index_dir / "chroma")).get_collection("chunks").get(
        include=["documents", "embeddings"])
    return {i: (t, np.asarray(e, dtype=np.float64)) for i, t, e in zip(got["ids"], got["documents"], got["embeddings"])}


def noise_bound(embedder, texts: list[str], new_vectors: dict[str, np.ndarray]) -> dict:
    """Largest ‖difference‖ between encodings of one text alone, in batches of 8, in batches of 32."""
    encodings = {"alone": {t: new_vectors[t] for t in texts}}   # the rebuilt indexes encode each text alone
    for size in (8, 32):
        order = texts[:]
        random.Random(size).shuffle(order)
        encodings[f"batch{size}"] = {}
        for start in range(0, len(order), size):
            group = order[start:start + size]
            for text, vector in zip(group, embedder.encode_passages(group)):
                encodings[f"batch{size}"][text] = np.asarray(vector, dtype=np.float64)
    pairs = {}
    for a, b in (("alone", "batch8"), ("alone", "batch32"), ("batch8", "batch32")):
        norms = np.array([np.linalg.norm(encodings[a][t] - encodings[b][t]) for t in texts])
        pairs[f"{a}~{b}"] = {"max": float(norms.max()), "median": float(np.median(norms)),
                             "bit_identical": int((norms == 0).sum())}
    return {"texts": len(texts), "pairs": pairs, "bound": max(p["max"] for p in pairs.values())}


def reproducibility(embedder, texts: list[str], stored: dict[str, np.ndarray]) -> dict:
    """Each text encoded alone again here vs the vector the rebuild stored (encoded alone there)."""
    norms = np.array([np.linalg.norm(np.asarray(embedder.encode_passages([t])[0], dtype=np.float64) - stored[t])
                      for t in texts])
    return {"texts": len(texts), "bit_identical": int((norms == 0).sum()), "max": float(norms.max())}


def questions() -> list[tuple[str, str]]:
    out = []
    for path in sorted((ROOT / "eval" / "ground-truth").glob("*.json")):
        if path.name == "manifest.json":
            continue
        out += [(c["case_id"], c["question"]) for c in json.loads(path.read_text(encoding="utf-8"))["cases"]]
    return out


def compare(old: dict, new: dict, bound: float, queries: np.ndarray, case_ids: list[str]) -> dict:
    ids = sorted(old)
    layer1 = {"ids_same": set(old) == set(new), "chunks": len(ids),
              "dims": sorted({len(v) for _, v in old.values()} | {len(v) for _, v in new.values()}),
              "text_mismatches": [i for i in ids if i in new and old[i][0] != new[i][0]]}
    if not layer1["ids_same"]:
        return {"layer1": layer1}
    a = np.stack([old[i][1] for i in ids])
    b = np.stack([new[i][1] for i in ids])
    delta = np.linalg.norm(a - b, axis=1)
    layer2 = {"max": float(delta.max()), "median": float(np.median(delta)),
              "bit_identical": int((delta == 0).sum()),
              "over_bound": [{"chunk": ids[k], "norm": float(delta[k])} for k in np.flatnonzero(delta > bound)]}
    swaps, violations = [], []
    for case_id, q in zip(case_ids, queries):
        s_old, s_new = a @ q, b @ q
        flip = np.sign(s_old[:, None] - s_old[None, :]) != np.sign(s_new[:, None] - s_new[None, :])
        for x, y in zip(*np.nonzero(np.triu(flip, 1))):
            gap = abs(float(s_old[x] - s_old[y]))
            item = {"case": case_id, "chunks": [ids[x], ids[y]], "old_gap": gap,
                    "bound": float(delta[x] + delta[y]),
                    "old_ranks": [int((s_old > s_old[x]).sum()) + 1, int((s_old > s_old[y]).sum()) + 1]}
            (swaps if gap <= delta[x] + delta[y] else violations).append(item)
    layer3 = {"questions": len(case_ids), "swaps_within_bound": swaps, "violations": violations}
    passed = (not layer1["text_mismatches"] and len(layer1["dims"]) == 1 and not layer2["over_bound"]
              and not violations)
    return {"layer1": layer1, "layer2": layer2, "layer3": layer3, "passed": passed}


def main() -> None:
    from rag.index.embedder import bge_m3
    baseline = Path(sys.argv[1])
    embedder = bge_m3()
    new_all = {c: load(ROOT / ".rag" / "indexes" / c) for c in CONFIGS}
    by_text: dict[str, np.ndarray] = {}
    for index in new_all.values():
        for text, vector in index.values():
            by_text.setdefault(text, vector)
    noise_path = HERE / "noise.json"
    if noise_path.is_file():
        noise = json.loads(noise_path.read_text(encoding="utf-8"))
    else:
        noise = noise_bound(embedder, sorted(by_text), by_text)
    if "reproducibility" not in noise:
        noise["reproducibility"] = reproducibility(embedder, sorted(by_text), by_text)
    noise_path.write_text(json.dumps(noise, ensure_ascii=False, indent=1), encoding="utf-8")
    cases = questions()
    queries = np.array([embedder.encode_query(q) for _, q in cases], dtype=np.float64)
    results = {c: compare(load(baseline / c), new_all[c], noise["bound"], queries, [i for i, _ in cases])
               for c in CONFIGS}
    summary = {"noise": noise, "configs": results, "passed": all(r.get("passed") for r in results.values())}
    (HERE / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    for c, r in results.items():
        print(c, "通过" if r.get("passed") else "不通过", r.get("layer2", {}).get("max"))
    print("噪声上界", noise["bound"], "全部通过" if summary["passed"] else "有不通过")


if __name__ == "__main__":
    main()
