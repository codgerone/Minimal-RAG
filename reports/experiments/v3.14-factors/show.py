"""Side by side: the retrieval texts and the leading units of two configurations for one question and
combination, from the stored page data (no retrieval). Evidence units are marked with the information
items they hold (*e1); for unmerged decomposition every sub-question's ranking is listed.

Run: python reports/experiments/v3.14-factors/show.py <left key|none> <right key|none> <combo> <case> [n]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from rag.eval.query_compare import PHRASING_OF, QueryConfig
from rag.query.scope import strip_codes

ROOT = Path(__file__).resolve().parents[3]
PAGE = ROOT / "reports/query/v3.14"
D = json.loads((PAGE / "data.json").read_text(encoding="utf-8"))


def load(kind, k):
    text = (PAGE / kind / f"{k or 'none'}.js").read_text(encoding="utf-8")
    return json.loads(re.search(r"\]=(.*);\s*$", text, re.S).group(1))


def texts(k, case):
    c = QueryConfig.parse(k)
    plan = D["plans"][case]["decomposed" if c.decompose else "plain"]
    codes = D["plans"][case]["scope"]["codes"]
    kinds = ({"question"} if c.fuse or not c.phrasings else set()) | {PHRASING_OF[m] for m in c.phrasings}
    out = []
    for q in plan["questions"]:
        for x in q["phrasings"]:
            if x["kind"] in kinds:
                t = strip_codes(x["text"], codes)[0] if c.strip else x["text"]
                out.append(f"{q['id']} {x['kind']}: {t}")
    return out


def main(left, right, combo, case, n=8):
    left, right = ("" if k == "none" else k for k in (left, right))
    q = next(x for x in D["cases"] if x["case_id"] == case)
    units, held = D["units"][combo], D["holds"][combo][case]
    groups_of = {x["excerpt_id"]: x["groups"] for x in q["excerpts"]}

    def mark(i):
        gs = sorted({g for x in held.get(str(i), []) for g in groups_of[x]})
        return ("*" + ",".join(gs)) if gs else ""

    def line(r, i):
        u = units[i]
        return f"   {r:2} {u['key'].split('-')[-1][-12:]:12} {mark(i):8} {'表 ' if u['table'] else ''}{u['text'][:110]!r}"

    print(case, q["question"])
    for g in q["groups"]:
        print("  ", g["group_id"], g["information_item"])
    for k in (left, right):
        print(f"\n== {k or 'V3.13'} ({combo})")
        for t in texts(k, case) if k else [q["question"]]:
            print("   text:", t[:200])
        f = load("facts", k)[combo][case]
        print("   n:", f["n"])
        if "u" in f:
            for r, i in enumerate(f["u"][:n], start=1):
                print(line(r, i))
        else:
            proc = load("process", k)[combo][case]
            mapping = D["decomposition"][case]["mapping"]
            for qid, rows in proc["routes"]:
                items = [g for g, qs in mapping.items() if qid in qs]
                print(f"  -- {qid} (items {items})")
                for r, (i, _) in enumerate(rows[:n], start=1):
                    print(line(r, i))


if __name__ == "__main__":
    main(*sys.argv[1:5], *(int(x) for x in sys.argv[5:6]))
