"""Module 1 data (2026-11-03): every measure at three levels, by the averaged rule (rules/evaluation.md).

Measures 0–13 against their baseline at the same level: the original question (32) with codes stripped, and
each sub-question (about 83 per generation, those with an annotated information item) with codes stripped,
judged on its own ranking and its own items; the pooled level adds the two per generation. Measure 0 (strip
codes) has the codes kept on the left, measure 1 (decomposition) is the original question against its
sub-questions unmerged, judged per original question. Also: direct comparisons within a group and across
groups, the reference (V3.13 → the final configuration), and the wording noise floor: edits that keep the
meaning, made by code, of the stripped question and of each generation-0 sub-question.

Per comparison and level: per generation the questions better / worse / mixed / same in each combination,
averaged over the 13; the net; stable (better or worse in all four generations) and flips counted per
combination and averaged, for sub-questions only over those with the same text in all four generations.

Writes levels.json next to this script (raw ranks in levels_ranks.json).
Run: python reports/experiments/v3.14-average/levels.py   (add --stats to recompute from the saved ranks)
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PAGE = ROOT / "reports/query/v3.14"
GENS = ("0", "1", "2", "3")
V = ("better", "worse", "mixed", "same")

BASE = "P3.2.0"
MEASURES = [   # id, name, left, right (original-question keys; the sub-question key adds "P3.1_")
    (0, "去编码", "", "P3.2.0"),
    (1, "分解", "P3.2.0", "P3.1_P3.2.0"),
    (2, "翻译", BASE, "P3.2.0_P3.2.2"),
    (3, "翻译 + 原句 RRF", BASE, "P3.2.0_P3.2.2@rrf"),
    (4, "HyDE", BASE, "P3.2.0_P3.2.3"),
    (5, "HyDE + 原句 RRF", BASE, "P3.2.0_P3.2.3@rrf"),
    (6, "改写（问题语言）", BASE, "P3.2.0_P3.2.1"),
    (7, "改写（文档语言）", BASE, "P3.2.0_P3.2.1~d"),
    (8, "改写（两种 RRF）", BASE, "P3.2.0_P3.2.1~qd"),
    (9, "改写（两种 RRF）+ 原句 RRF", BASE, "P3.2.0_P3.2.1~qd@rrf"),
    (10, "step-back（问题语言）", BASE, "P3.2.0_P3.2.4"),
    (11, "step-back（文档语言）", BASE, "P3.2.0_P3.2.4~d"),
    (12, "step-back（两种 RRF）", BASE, "P3.2.0_P3.2.4~qd"),
    (13, "step-back（两种 RRF）+ 原句 RRF", BASE, "P3.2.0_P3.2.4~qd@rrf"),
]
KEY = {m: r for m, _, _, r in MEASURES}
DIRECT = [(2, 3), (4, 5), (6, 7), (6, 8), (7, 8), (8, 9), (10, 11), (10, 12), (11, 12), (12, 13), (2, 7), (3, 9)]
REFERENCE = ("最终配置：V3.13 → 去编码 + 原句 + 译文 RRF", "", "P3.2.0_P3.2.2@rrf")


def sub(key: str) -> str:
    """The sub-question key of an original-question key."""
    return "P3.1_" + key if key else "P3.1"


SUB_KEYS = sorted({sub("")} | {sub(k) for m, k in KEY.items() if m != 1} | {sub(BASE)})


def edits(q: str) -> dict:
    sentences = [s for s in re.split(r"(?<=[？?。])", q) if s.strip()]
    return {"问号改句号": q.replace("？", "。").replace("?", "."), "前加请问": "请问" + q,
            "句子倒序": "".join(reversed(sentences)) if len(sentences) > 1 else None}


# ---------------------------------------------------------------- ranks

def load(kind: str, k: str) -> dict:
    text = (PAGE / kind / f"{k or 'none'}.js").read_text(encoding="utf-8")
    return json.loads(re.search(r"\]=(.*);\s*$", text, re.S).group(1))


def original_ranks(k: str) -> dict:
    """generation → combo → case → completion ranks, from the comparison page."""
    out = {"0": {c: {q: f["n"] for q, f in cases.items()} for c, cases in load("facts", k).items()}}
    out.update(load("perturb", k))
    return out


def collect_ranks() -> dict:
    from rag.cli import _assembly
    from rag.eval.judge import judge_case
    from rag.eval.metrics import completion_ranks, routed_completion_rank
    from rag.eval.query_compare import (COMBOS, DECOMPOSITION_MAPPING, PERTURBATION_MAPPING, MemoSearch, QueryConfig,
                                        make_planner)
    from rag.eval.runner import prepare
    from rag.jsonio import read_json
    from rag.paths import Workspace
    from rag.query import execution
    from rag.query.planning import document_languages
    from rag.query.scope import strip_codes

    ws = Workspace(ROOT)
    planner = make_planner(ws)
    planner.llm.complete_structured = None   # every plan must come from the cache
    mappings = {g: read_json(ROOT / (PERTURBATION_MAPPING.format(generation=g) if g != "0" else DECOMPOSITION_MAPPING))["cases"]
                for g in GENS}
    subq: dict = {}    # generation → "case/qid" → {"text", "ranks": key → combo → n}
    noise: dict = {"original": {}, "sub": {}}   # level → id → edit → combo → n ("base" included)
    for combo in COMBOS:
        ctx = prepare(ws, _assembly(ws, combo.config, "hybrid", combo.table_return_unit, 1.0), cache_retrievals=True)
        ctx.warm_up()
        languages_of = document_languages(ws.index_dir(combo.config))
        search = MemoSearch(ctx.searcher)
        for case in ctx.dataset.cases:
            if not case.answerable:
                continue
            scope = ctx.scope(case.question)
            languages = sorted({languages_of[d] for d in (scope.document_ids or languages_of)})
            evidence = judge_case(case, ctx.chunks)
            schemes = {grp.group_id: [[evidence.excerpts[x].acceptable_sets for x in s.excerpt_ids] for s in grp.schemes]
                       for grp in case.groups}
            stripped = strip_codes(case.question, scope.codes)[0]
            for name, text in {"base": stripped, **edits(stripped)}.items():
                if text is None:
                    continue
                units = search.rank(text, scope.document_ids).units
                got = completion_ranks([u.chunk_ids for u in units], evidence.groups)
                noise["original"].setdefault(case.case_id, {}).setdefault(name, {})[combo.label] = \
                    [got.get(g.group_id) for g in evidence.groups]
            for g in GENS:
                mapping = mappings[g][case.case_id]["mapping"]
                plan = planner.add_languages(planner.plan(case.question, languages, True, generation=int(g)),
                                             languages, generation=int(g))
                for key in SUB_KEYS:
                    run = execution.planned(search, plan, QueryConfig.parse(key).settings(), scope.document_ids, scope.codes)
                    for route in run.routes:
                        groups = [grp.group_id for grp in case.groups if route.question_id in mapping[grp.group_id]]
                        if not groups:
                            continue
                        units = [u.chunk_ids for u in route.units]
                        rec = subq.setdefault(g, {}).setdefault(f"{case.case_id}/{route.question_id}",
                                                                {"text": route.text, "groups": groups, "ranks": {}})
                        rec["ranks"].setdefault(key, {})[combo.label] = [routed_completion_rank([units], schemes[x]) for x in groups]
                        if g == "0" and key == sub(BASE):   # the noise floor on the stripped sub-question
                            text = strip_codes(route.text, scope.codes)[0]
                            for name, edited in {"base": text, **edits(text)}.items():
                                if edited is None:
                                    continue
                                eu = [u.chunk_ids for u in search.rank(edited, scope.document_ids).units]
                                noise["sub"].setdefault(f"{case.case_id}/{route.question_id}", {}).setdefault(name, {})[combo.label] = \
                                    [routed_completion_rank([eu], schemes[x]) for x in groups]
        print(combo.label, flush=True)
    return {"sub": subq, "noise": noise}


# ---------------------------------------------------------------- stats

def verdict(left, right) -> str:
    a = sorted(math.inf if x is None else x for x in left)
    b = sorted(math.inf if x is None else x for x in right)
    e, t = any(r < l for l, r in zip(a, b)), any(r > l for l, r in zip(a, b))
    return "mixed" if e and t else "better" if e else "worse" if t else "same"


def tally(cells: dict) -> dict:
    """cells: generation → question → combo → verdict. Stable and flips over the questions present in every
    generation (sub-questions: the same text)."""
    gens = sorted(cells)
    combos = sorted({c for g in gens for q in cells[g] for c in cells[g][q]})
    n = len(combos)
    per = [{v: sum(cells[g][q][c] == v for q in cells[g] for c in combos) / n for v in V} for g in gens]
    common = set.intersection(*(set(cells[g]) for g in gens))
    stable = {v: sum(all(cells[g][q][c] == v for g in gens) for q in common for c in combos) / n for v in ("better", "worse")}
    flips = sum(any(cells[g][q][c] == "better" for g in gens) and any(cells[g][q][c] == "worse" for g in gens)
                for q in common for c in combos) / n
    return {"gens": per, "net": [x["better"] - x["worse"] for x in per], "questions": [len(cells[g]) for g in gens],
            "stable": stable, "flips": flips, "stable_questions": len(common)}


def pooled(a: dict, b: dict) -> dict:
    per = [{v: x[v] + y[v] for v in V} for x, y in zip(a["gens"], b["gens"])]
    return {"gens": per, "net": [x["better"] - x["worse"] for x in per],
            "questions": [x + y for x, y in zip(a["questions"], b["questions"])],
            "stable": {v: a["stable"][v] + b["stable"][v] for v in ("better", "worse")}, "flips": a["flips"] + b["flips"],
            "stable_questions": a["stable_questions"] + b["stable_questions"]}


def original_cells(left: str, right: str) -> dict:
    L, R = original_ranks(left), original_ranks(right)
    cases = list(L["0"][next(iter(L["0"]))])
    return {g: {q: {c: verdict(L[g][c][q], R[g][c][q]) for c in L[g]} for q in cases} for g in GENS}


def sub_cells(raw: dict, left: str, right: str) -> dict:
    """Sub-questions keyed by case and text, so that the same sub-question meets itself across generations."""
    return {g: {f'{sq.split("/")[0]}｜{rec["text"]}': {c: verdict(rec["ranks"][left][c], rec["ranks"][right][c])
                                                      for c in rec["ranks"][left]}
                for sq, rec in recs.items()} for g, recs in raw.items()}


def levels(raw: dict, left: str, right: str, with_sub: bool = True) -> dict:
    o = tally(original_cells(left, right))
    if not with_sub:
        return {"original": o}
    s = tally(sub_cells(raw["sub"], sub(left), sub(right)))
    return {"original": o, "sub": s, "pooled": pooled(o, s)}


def noise_stats(raw: dict) -> dict:
    out = {}
    for level in ("original", "sub"):
        cells: dict = {}
        for q, ed in raw["noise"][level].items():
            for name, combos in ed.items():
                if name != "base":
                    cells.setdefault(name, {"0": {}})["0"][q] = {c: verdict(ed["base"][c], combos[c]) for c in combos}
        out[level] = {name: tally(c) for name, c in cells.items()}
    return out


def main() -> None:
    path = HERE / "levels_ranks.json"
    if "--stats" in sys.argv:
        raw = json.loads(path.read_text(encoding="utf-8"))
    else:
        raw = collect_ranks()
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    name = {m: n for m, n, _, _ in MEASURES}
    out = {"measures": [{"id": m, "name": n, "left": l, "right": r, **levels(raw, l, r, m != 1)} for m, n, l, r in MEASURES],
           "direct": [{"from": a, "to": b, "name": f"{name[a]} → {name[b]}", "left": KEY[a], "right": KEY[b],
                       **levels(raw, KEY[a], KEY[b])} for a, b in DIRECT],
           "reference": {"name": REFERENCE[0], "left": REFERENCE[1], "right": REFERENCE[2],
                         **levels(raw, REFERENCE[1], REFERENCE[2], False)},
           "noise": noise_stats(raw)}
    (HERE / "levels.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    for m in out["measures"] + out["direct"]:
        print(m["name"], " | ".join(f'{lv} {sum(x["net"]) / 4:+.1f}（{min(x["net"]):+.1f}…{max(x["net"]):+.1f}）'
                                    for lv, x in m.items() if isinstance(x, dict) and "net" in x))
    for level, eds in out["noise"].items():
        print("噪声", level, {k: round(v["net"][0], 1) for k, v in eds.items()})


if __name__ == "__main__":
    main()
