import json, re, sys
from pathlib import Path
D = json.loads(Path("reports/query/v3.14/data.json").read_text(encoding="utf-8"))
def js(p):
    t = Path(p).read_text(encoding="utf-8"); return json.loads(re.search(r"\]=(.*);\s*$", t, re.S).group(1))
F0, F1, FR = js("reports/query/v3.14/facts/none.js"), js("reports/query/v3.14/facts/P3.1.js"), js("reports/query/v3.14/facts/P3.1@rr.js")
PR = js("reports/query/v3.14/process/P3.1@rr.js")
cases = {c["case_id"]: c for c in D["cases"]}
combo = sys.argv[1]
U = D["units"][combo]; H = D["holds"][combo]
for case in sys.argv[2:]:
    q = cases[case]
    print(f"== {combo} {case} 信息项 {[g['group_id']+g['information_item'] for g in q['groups']]}")
    print(f"   凑齐名次 原问题 {F0[combo][case]['n']} | 检索层 {F1[combo][case]['n']} | 轮流取 {FR[combo][case]['n']}")
    order = FR[combo][case]["u"]; src = PR[combo][case]
    print("   轮流取前 10:", " ".join(f"{n+1}:{U[i]['key'].split('-')[-1][:10]}({src[n][0]}){'*' if str(i) in H[case] else ''}" for n, i in enumerate(order[:10])))
    print("   原问题前 6:", " ".join(f"{n+1}:{U[i]['key'].split('-')[-1][:10]}{'*' if str(i) in H[case] else ''}" for n, i in enumerate(F0[combo][case]["u"][:6])))
