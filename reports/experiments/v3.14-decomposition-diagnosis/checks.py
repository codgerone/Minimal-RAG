import json, re
from pathlib import Path
D = json.loads(Path("reports/query/v3.14/data.json").read_text(encoding="utf-8"))
def js(k, kind="facts"):
    t = Path(f"reports/query/v3.14/{kind}/{k or 'none'}.js").read_text(encoding="utf-8"); return json.loads(re.search(r"\]=(.*);\s*$", t, re.S).group(1))
F = {k: js(k) for k in ("", "P3.1", "P3.1_P3.2.2@rrf", "P3.1_P3.2.2", "P3.1@rr", "P3.1@rrf", "P3.1@rrf-o", "P3.2.2@rrf")}
P1 = js("P3.1", "process")
combos = [c["label"] for c in D["combos"]]
from rag.eval.metrics import routed_completion_rank
# 1. evidence rank over all sub-questions (needs acceptable sets: recompute from units' chunk ids is not stored -> use holds: unit -> excerpts)
cases = {c["case_id"]: c for c in D["cases"]}
items = [("CONTRACT-105-Q001","e1"),("CONTRACT-105-Q002","e5"),("CONTRACT-105-Q002","e2"),("CONTRACT-105-Q010","e1"),
         ("PO-24-12-Q001","e1"),("PO-24-12-Q004","e3"),("PO-25-19-Q002","e2"),("PO-25-19-Q006","e2"),("E001-602-Q002","e1")]
for case, gid in items:
    i = [g["group_id"] for g in cases[case]["groups"]].index(gid)
    row = lambda k: [F[k][c][case]["n"][i] for c in combos]
    print(f"{case} {gid}")
    for k, name in (("", "原问题"), ("P3.1", "检索层(对应子问题)"), ("P3.1_P3.2.2@rrf", "检索层+翻译RRF"), ("P3.1_P3.2.2", "检索层+翻译代替"), ("P3.2.2@rrf", "原问题+翻译RRF")):
        print(f"   {name:<18} {row(k)}")
    # first rank of a unit holding this item's excerpts in any sub-question route (top 20)
    ex = {x["excerpt_id"] for x in cases[case]["excerpts"] if gid in x["groups"]}
    best = []
    for c in combos:
        H = D["holds"][c][case]
        ranks = [(r[0], n + 1) for r in P1[c][case]["routes"] for n, (u, _) in enumerate(r[1]) if ex & set(H.get(str(u), []))]
        best.append(min(ranks, key=lambda x: x[1]) if ranks else None)
    print(f"   任一子问题中首个证据单元 {best}")
print()
print("合并层：凑齐向量（RM-T / RM-C）")
for case in ("CONTRACT-105-Q002","CONTRACT-105-Q010","CONTRACT-105-Q012","PO-24-12-Q001","PO-24-12-Q004","PO-24-12-Q005","E001-602-Q002","PO-25-19-Q009"):
    print(case, {k or "V3.13": [sorted(x if x else 99 for x in F[k][c][case]["n"]) for c in ("RM-T","RM-C")] for k in ("", "P3.1@rr", "P3.1@rrf", "P3.1@rrf-o")})
