"""Why does dense + sparse fall below dense? Per question: in-scope chunks with score > 0 under
BM25 and sparse, and the shared tokens behind the sparse top hits."""
import json, sys, collections
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_experiment as ex
runs = json.loads((HERE / "runs.json").read_text(encoding="utf-8"))
enc = ex.SparseEncoder()
for config in ("plain_text", "structured"):
    cache = {cid: {int(k): v for k, v in vec.items()} for cid, vec in
             json.loads((HERE / "cache" / f"{config}.json").read_text(encoding="utf-8")).items()}
    res = {u: json.loads((HERE / "eval" / runs[config][u] / "result.json").read_text(encoding="utf-8")) for u in runs[config]}
    token_drivers = collections.Counter()
    print(f"\n== {config}")
    for i, case in enumerate(res["semantic"]["cases"]):
        scope = set(case["scope"]["document_ids"])
        in_scope = [c for c in cache if c.split("-c")[0] in scope]
        q = enc.encode([case["question"]])[0]
        pos = [c for c in in_scope if any(t in cache[c] for t in q)]
        top = res["sparse"]["cases"][i]["hits"][0]["chunk_id"] if res["sparse"]["cases"][i]["hits"] else None
        contrib = sorted(((enc.tok.convert_ids_to_tokens(t), q[t] * cache[top][t]) for t in q if top and t in cache[top]), key=lambda x: -x[1])
        for tok, v in contrib[:2]: token_drivers[tok] += 1
        status = lambda u: "完整" if res[u]["cases"][i]["complete"] else ("命中" if res[u]["cases"][i]["hit"] else "未中")
        print(f"{case['case_id']:22s} scope={len(in_scope):3d} sparse>0={len(pos):3d} | dense {status('semantic')} d+bm25 {status('hybrid')} d+sparse {status('hybrid_sparse')} | sparse#1 driven by {[(t, round(v,3)) for t, v in contrib[:3]]}")
    print("top-1 sparse hit's two biggest shared tokens, counted over questions:", token_drivers.most_common(10))
