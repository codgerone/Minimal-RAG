"""Facts for the query comparison page: every combination × module configuration × question.

A combination is an index (configs/) with a table return unit; a module configuration is a set
of the query-side modules (P3.1, P3.2.1–P3.2.4, P3.3; the empty set is V3.13; P3.3 is not
evaluated). Every configuration runs on the frozen plans (generation 0, cached): two per question,
without and with decomposition; with decomposition the original-question route is always on
(decision 014). Per question only what any K and any pair of configurations can be derived from
is kept: each evidence group's needed units, the position of every evidence unit, the leading
units (indices into the combination's unit table) and the cost. Where each leading unit came from
is kept apart, per configuration (`process`). Rules: docs/changes/v3.14.md.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations
from typing import Any, Collection, Iterable

from rag.eval.judge import judge_case
from rag.eval.metrics import completion_ranks
from rag.eval.runner import _ingest_cost, prepare
from rag.paths import Workspace
from rag.query import execution
from rag.query.planning import Plan, Planner, document_languages

MODULES = (   # id, name
    ("P3.1", "分解"),
    ("P3.2.1", "改写"),
    ("P3.2.2", "翻译"),
    ("P3.2.3", "HyDE"),
    ("P3.2.4", "step-back"),
    ("P3.3", "重排"),
)
KS = (3, 4, 5, 6, 7, 8, 9, 10, 12, 15, 20)
LEADING = max(KS)
TEXT_LIMIT = 300   # characters of a unit's text kept for the page


@dataclass(frozen=True)
class Combo:
    label: str
    config: str
    table_return_unit: str


# The standard combinations (scripts/eval_tables.sh): six table chunk configs × chunk / whole table,
# and the summary config with whole tables.
COMBOS = tuple(
    Combo(f"{label}-{'C' if unit == 'chunk' else 'T'}", config, unit)
    for label, config in (("RM", "structured"), ("RL", "structured_labeled"), ("LM", "structured_llm"),
                          ("LL", "structured_llm_labeled"), ("LM+", "structured_llm_lead"),
                          ("LL+", "structured_llm_labeled_lead"))
    for unit in ("chunk", "table")) + (Combo("S-T", "structured_summary", "table"),)


def config_key(modules: Iterable[str]) -> str:
    """The key of a module configuration: its module ids in MODULES order joined by '+'."""
    chosen = set(modules)
    unknown = chosen - {m for m, _ in MODULES}
    if unknown:
        raise ValueError(f"未知模块：{sorted(unknown)}")
    return "+".join(m for m, _ in MODULES if m in chosen)


def comparison_error(left: Iterable[str], right: Iterable[str]) -> str | None:
    """None when `left vs right` can be compared: both empty (V3.13), or left a proper subset of right."""
    left, right = set(left), set(right)
    if not left and not right:
        return None
    if not right:
        return "右侧为空时左侧也必须为空"
    if not left < right:
        extra = sorted(left - right)
        return (f"左侧的 {'、'.join(extra)} 不在右侧" if extra
                else "右侧必须比左侧至少多选一个模块")
    return None


CHANGE_THRESHOLD = math.log(1.5)   # experiment parameter (docs/changes/v3.14.md, question 3)


def evidence_positions(evidence, run: execution.QueryRun) -> dict[str, int]:
    """Evidence unit → its position in the return order: the units holding any chunk of any
    acceptable set of any excerpt."""
    chunks = {c for x in evidence.excerpts.values() for s in x.acceptable_sets for c in s}
    return {u.key: n for n, u in enumerate(run.units, start=1) if chunks & set(u.chunk_ids)}


def evidence_change(left: dict[str, int], right: dict[str, int]) -> float:
    """Σ ln(left position / right position) over the evidence units (both sides rank the same units)."""
    return sum(math.log(left[k] / right[k]) for k in left if k in right)


def direction(change: float) -> int:
    """1 better, -1 worse, 0 no change."""
    return 1 if change > CHANGE_THRESHOLD else -1 if change < -CHANGE_THRESHOLD else 0


PLAN_MODEL = "google/gemini-3.8-flash"   # docs/changes/v3.14.md, question 1
PHRASING_OF = {"P3.2.1": "rewrite", "P3.2.2": "translation", "P3.2.3": "hyde", "P3.2.4": "step_back"}
EVALUATED = ("P3.1", *PHRASING_OF)   # P3.3 (reranking) is set aside


def evaluated_configs() -> list[tuple[str, ...]]:
    """Every subset of the evaluated modules, the empty set first."""
    return [c for n in range(len(EVALUATED) + 1) for c in combinations(EVALUATED, n)]


def settings_for(config: Collection[str]) -> tuple[bool, execution.QuerySettings]:
    """(decompose, settings) of a module configuration."""
    decompose = "P3.1" in config
    return decompose, execution.QuerySettings(frozenset(PHRASING_OF[m] for m in config if m in PHRASING_OF),
                                              original_route=decompose)


def make_planner(workspace: Workspace) -> Planner:
    from rag.ingest.tables.llm_tables import LLMCache
    from rag.query.llm import OpenRouterLLM
    return Planner(OpenRouterLLM(model=PLAN_MODEL, temperature=0, env_override=False),
                   LLMCache(workspace.root / ".rag" / "llm-cache"))


def plan_json(plan: Plan) -> dict[str, Any]:
    return {"questions": [{"id": q.id, "text": q.text,
                           "phrasings": [{"kind": x.kind, "language": x.language, "text": x.text}
                                         for x in q.phrasings]} for q in plan.questions],
            "degraded": list(plan.degraded)}


class MemoSearch:
    """Remembers each ranking within one combination: configurations repeat the same texts."""

    def __init__(self, searcher):
        self.searcher = searcher
        self.memo: dict[tuple[str, tuple[str, ...] | None], Any] = {}

    def rank(self, text: str, document_ids: Collection[str] | None = None):
        key = (text, None if document_ids is None else tuple(sorted(document_ids)))
        if key not in self.memo:
            self.memo[key] = self.searcher.rank(text, document_ids)
        return self.memo[key]


class UnitTable:
    """The units of one combination the page needs, each kept once; facts refer to them by index."""

    def __init__(self, tokens_of):
        self.tokens_of = tokens_of
        self.rows: list[dict[str, Any]] = []
        self.index: dict[str, int] = {}

    def of(self, unit) -> int:
        if unit.key not in self.index:
            self.index[unit.key] = len(self.rows)
            self.rows.append({"key": unit.key, "text": unit.text[:TEXT_LIMIT], "pages": list(unit.pages),
                              "table": unit.table is not None, "tokens": self.tokens_of(unit.text)})
        return self.index[unit.key]


def case_facts(evidence, run: execution.QueryRun, table: UnitTable) -> tuple[dict[str, Any], list[Any]]:
    """The facts of one run, and where each leading unit came from."""
    units = run.units
    needed = completion_ranks([u.chunk_ids for u in units], evidence.groups)
    by_key = {u.key: u for u in units}
    leading = run.order[:LEADING]
    cost = run.cost
    facts = {"n": [needed.get(g.group_id) for g in evidence.groups],
             "u": [table.of(s.unit) for s in leading],
             "e": {str(table.of(by_key[k])): p for k, p in evidence_positions(evidence, run).items()},
             "c": [cost.retrievals, round(cost.retrieval_seconds, 4), cost.llm_calls,
                   round(cost.llm_seconds, 2), round(cost.llm_cost_usd, 6)]}
    process = [[s.question_id, s.round, [list(r) for r in s.ranks]] for s in leading]
    return facts, process


def excerpts_held(evidence, run: execution.QueryRun, table: UnitTable) -> dict[str, list[str]]:
    """Evidence unit index → the excerpts any of whose acceptable chunks it holds."""
    held: dict[str, list[str]] = {}
    for unit in run.units:
        ids = set(unit.chunk_ids)
        found = [x for x, e in evidence.excerpts.items() if any(ids & c for c in e.acceptable_sets)]
        if found:
            held[str(table.of(unit))] = found
    return held


def collect(workspace: Workspace, name: str, combos: tuple[Combo, ...] = COMBOS,
            planner: Planner | None = None, progress=None) -> dict[str, Any]:
    """Runs every evaluated configuration on every combination. Returns the page data, with the
    per-configuration process detail under "process"."""
    from rag.cli import _assembly
    planner = planner or make_planner(workspace)
    configs = evaluated_configs()
    keys = [config_key(c) for c in configs]
    facts: dict[str, dict[str, Any]] = {k: {} for k in keys}
    process: dict[str, dict[str, Any]] = {k: {} for k in keys}
    units: dict[str, list] = {}
    holds: dict[str, dict[str, Any]] = {}   # combo → case → evidence unit index → excerpt ids
    plans: dict[str, dict[str, Any]] = {}
    combo_info, dataset = [], None
    for combo in combos:
        ctx = prepare(workspace, _assembly(workspace, combo.config, "hybrid", combo.table_return_unit),
                      cache_retrievals=True)
        ctx.warm_up()
        dataset = ctx.dataset
        languages_of = document_languages(workspace.index_dir(combo.config))
        search = MemoSearch(ctx.searcher)
        table = UnitTable(ctx.tokens_of)
        holds[combo.label] = {}
        for key in keys:
            facts[key][combo.label] = {}
            process[key][combo.label] = {}
        for case in ctx.dataset.cases:
            if not case.answerable:
                continue
            scope = ctx.scope(case.question)
            documents = scope.document_ids
            languages = sorted({languages_of[d] for d in (documents or languages_of)})
            pair = {d: planner.plan(case.question, languages, d, list(scope.codes)) for d in (False, True)}
            plans.setdefault(case.case_id, {
                "scope": {"documents": list(documents or []), "languages": languages, "codes": list(scope.codes)},
                "plain": plan_json(pair[False]), "decomposed": plan_json(pair[True])})
            evidence = judge_case(case, ctx.chunks)
            for config, key in zip(configs, keys):
                if config:
                    decompose, settings = settings_for(config)
                    run = execution.planned(search, pair[decompose], settings, documents)
                else:
                    run = execution.plain(search, case.question, documents)
                facts[key][combo.label][case.case_id], process[key][combo.label][case.case_id] = \
                    case_facts(evidence, run, table)
                if not config:
                    holds[combo.label][case.case_id] = excerpts_held(evidence, run, table)
        units[combo.label] = table.rows
        combo_info.append({"label": combo.label, "config": combo.config,
                           "table_return_unit": combo.table_return_unit,
                           "ingest_cost": _ingest_cost(workspace, combo.config)})
        if progress:
            progress(combo.label)
    assert dataset is not None
    cases = [{"case_id": c.case_id, "document_id": c.document_id, "document_name": c.document_name,
              "question": c.question,
              "groups": [{"group_id": g.group_id, "information_item": g.information_item} for g in c.groups],
              "excerpts": [{"excerpt_id": x.excerpt_id, "text": x.text[:TEXT_LIMIT],
                            "groups": sorted({g.group_id for g in c.groups for sc in g.schemes
                                              if x.excerpt_id in sc.excerpt_ids})} for x in c.excerpts]}
             for c in dataset.cases if c.answerable]
    return {"name": name, "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "dataset": {"version": dataset.version, "fingerprint": dataset.fingerprint,
                        "questions": len(dataset.cases), "answerable": len(cases)},
            "modules": [{"id": m, "name": n} for m, n in MODULES], "evaluated": list(EVALUATED),
            "ks": list(KS), "threshold": CHANGE_THRESHOLD, "plan_model": PLAN_MODEL,
            "combos": combo_info, "cases": cases, "plans": plans, "units": units, "holds": holds,
            "configs": facts, "process": process}


def metrics_at(facts: dict[str, dict[str, Any]], units: list[dict[str, Any]], k: int) -> dict[str, float]:
    """Over answerable questions: hit and complete counts, tokens per question at K."""
    n = len(facts)
    hit = complete = 0
    tokens = 0.0
    for f in facts.values():
        covered = [x for x in f["n"] if x is not None and x <= k]
        hit += bool(covered)
        complete += len(covered) == len(f["n"])
        tokens += sum(units[i]["tokens"] for i in f["u"][:k])
    return {"hit": hit, "complete": complete, "tokens": tokens / n if n else 0.0}
