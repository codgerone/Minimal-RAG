import json, re, sys
from pathlib import Path
D = json.loads(Path("reports/query/v3.14/data.json").read_text(encoding="utf-8"))
def js(path, name):
    t = Path(path).read_text(encoding="utf-8"); return json.loads(re.search(r"\]=(.*);\s*$", t, re.S).group(1))
P0 = js("reports/query/v3.14/process/none.js", "")
P1 = js("reports/query/v3.14/process/P3.1.js", "")
F0 = js("reports/query/v3.14/facts/none.js", ""); F1 = js("reports/query/v3.14/facts/P3.1.js", "")
cases = {c["case_id"]: c for c in D["cases"]}
targets = [("CONTRACT-105-Q001","e1"),("CONTRACT-105-Q002","e5"),("CONTRACT-105-Q002","e2"),("CONTRACT-105-Q010","e1"),
           ("PO-24-12-Q001","e1"),("PO-24-12-Q004","e3"),("PO-25-19-Q002","e2"),("PO-25-19-Q006","e2"),("E001-602-Q002","e1")]
for combo in sys.argv[1:]:
    U = D["units"][combo]; H = D["holds"][combo]
    for case, gid in targets:
        q = cases[case]; held = H[case]
        ex = [x["excerpt_id"] for x in q["excerpts"] if gid in x["groups"]]
        def show(units):
            return " ".join(f"{n+1}:{U[i]['key'].split('-')[-1]}{'*' if any(e in ex for e in held.get(str(i),[])) else ('+' if str(i) in held else '')}" for n,i in enumerate(units[:8]))
        print(f"== {combo} {case} {gid} {[g['information_item'] for g in q['groups'] if g['group_id']==gid][0]} | excerpts {ex}: " + "; ".join(x['text'][:60] for x in q['excerpts'] if x['excerpt_id'] in ex))
        print("   原问题  ", show(F0[combo][case]["u"]))
        m = D["decomposition"][case]["mapping"][gid]
        for r in P1[combo][case]["routes"]:
            if r[0] in m: print(f"   {r[0]}      ", show([x[0] for x in r[1]]))
        ev = sorted({int(i) for i,v in held.items() if any(e in ex for e in v)})
        for i in ev[:3]: print(f"     证据单元 {U[i]['key'].split('-')[-1]}: {U[i]['text'][:150]!r}")
