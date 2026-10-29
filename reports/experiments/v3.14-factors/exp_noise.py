"""Experiment: the wording noise floor. How often do edits that keep the meaning exactly (made by code,
no LLM) change the verdict against the original question? If a measure's better / worse counts are of
the same size, its effect cannot be told apart from rewording at random.

Edits of the original question (codes kept, as in V3.13): question marks → full stops; "请问" in front;
the question's sentences in reverse order (each sentence unchanged). Verdict (question 3) per question
and combination, as in pairs.py.

Writes exp_noise.json next to this script. Run: python reports/experiments/v3.14-factors/exp_noise.py
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

from probe import Probe

HERE = Path(__file__).resolve().parent
COMBOS = ["RM-T", "RM-C", "LM-C", "LL-T"]


def edits(q: str) -> dict[str, str]:
    sentences = [s for s in re.split(r"(?<=[？?。])", q) if s.strip()]
    lead = re.match(r"^(.*?[，,])", sentences[0]) if len(sentences) > 1 else None
    return {"问号改句号": q.replace("？", "。").replace("?", "."),
            "前加请问": "请问" + q,
            "句子倒序": "".join(reversed(sentences)) if len(sentences) > 1 else None}


def verdict(a, b):
    a = sorted(math.inf if x is None else x for x in a)
    b = sorted(math.inf if x is None else x for x in b)
    e, l = any(y < x for x, y in zip(a, b)), any(y > x for x, y in zip(a, b))
    return "mixed" if e and l else "better" if e else "worse" if l else "same"


def main() -> None:
    probe = Probe(COMBOS)
    out = {}
    for case_id, case in probe.cases.items():
        if not case.answerable:
            continue
        base = {c: [v["n"] for g, v in probe.ranks(c, case_id, case.question).items() if g != "_top"] for c in COMBOS}
        out[case_id] = {}
        for name, text in edits(case.question).items():
            if text is None:
                continue
            got = {c: [v["n"] for g, v in probe.ranks(c, case_id, text).items() if g != "_top"] for c in COMBOS}
            out[case_id][name] = {"text": text, "v": {c: verdict(base[c], got[c]) for c in COMBOS},
                                  "base": base, "n": got}
        print(case_id, {k: list(v["v"].values()) for k, v in out[case_id].items()}, flush=True)
    (HERE / "exp_noise.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
