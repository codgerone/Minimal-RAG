"""Experiment: is step-back worse because it drops the question's names and quoted terms?

Step-back removes the qualifiers that narrow a question to one instance; in these questions those are
mostly Latin-script names and quoted terms (companies, models, Spanish clause names). For a Chinese
question they are also the only words BM25 can match. For every question whose step-back text dropped
Latin-script words of the question: the original text, the step-back text, and the step-back text with
the dropped words appended in brackets (nothing else changed), codes stripped throughout, on the
semantic and hybrid retrievers.

Writes exp_stepback.json next to this script. Run: python reports/experiments/v3.14-factors/exp_stepback.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from probe import Probe
from rag.query.scope import strip_codes

HERE = Path(__file__).resolve().parent
PLANS = json.loads((HERE.parent / "v3.14-phrasings-v8/plans.json").read_text(encoding="utf-8"))["generations"]["0"]
COMBOS = ["RM-T", "RM-C"]
LATIN = re.compile(r"[A-Za-zÁÉÍÓÚÑáéíóúñü][A-Za-z0-9ÁÉÍÓÚÑáéíóúñü'-]*(?:\s+[A-Za-zÁÉÍÓÚÑáéíóúñü][A-Za-z0-9ÁÉÍÓÚÑáéíóúñü'-]*)*")


def main() -> None:
    texts = {}
    for case_id, plans in PLANS.items():
        codes = plans["scope"]["codes"]
        q = plans["plain"]["questions"][0]
        question = strip_codes(q["text"], codes)[0]
        step = strip_codes(next(x["text"] for x in q["phrasings"] if x["kind"] == "step_back"), codes)[0]
        dropped = [w for w in LATIN.findall(question) if w not in step and len(w) > 1]
        if dropped:
            texts[case_id] = {"原句": question, "step-back": step, "补回": f"{step}（{'，'.join(dropped)}）",
                              "dropped": dropped}
    out = {}
    for retriever in ("semantic", "hybrid"):
        probe = Probe(COMBOS, retriever)
        for case_id, t in texts.items():
            for name in ("原句", "step-back", "补回"):
                for combo in COMBOS:
                    r = probe.ranks(combo, case_id, t[name])
                    out.setdefault(case_id, {"texts": t, "n": {}})["n"].setdefault(retriever, {}).setdefault(name, {})[combo] = \
                        [v["n"] for g, v in r.items() if g != "_top"]
            print(retriever, case_id, {k: v["RM-T"] for k, v in out[case_id]["n"][retriever].items()}, t["dropped"], flush=True)
        del probe
    (HERE / "exp_stepback.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
