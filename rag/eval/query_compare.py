"""Facts for the query comparison page: every combination × query configuration × question.

A combination is an index (configs/) with a table return unit; a query configuration is a choice
of the query-side modules with their options (QueryConfig: decomposition with its merge and the
original-question route, phrasings with their fusion; the empty configuration is V3.13; P3.3 is not
evaluated). Every configuration runs on the frozen plans (generation 0, cached): two per question,
without and with decomposition. Per question only what any K and any pair of configurations can be
derived from is kept: each information item's completion rank and first-unit rank, the position of
every evidence unit, the leading units (indices into the combination's unit table) and the cost.
Without a merge there is no return order: an item's ranks are taken over the rankings of the
sub-questions annotated for it (DECOMPOSITION_MAPPING). Where each leading unit came from is kept
apart, per configuration (`process`). Rules: docs/changes/v3.14.md.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations
from typing import Any, Collection, Iterable

from rag.eval.judge import judge_case
from rag.eval.metrics import completion_ranks, first_ranks, routed_completion_rank
from rag.eval.runner import _ingest_cost, prepare
from rag.jsonio import read_json
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


def evidence_positions(evidence, run: execution.QueryRun) -> dict[str, int]:
    """Evidence unit → its position in the return order: the units holding any chunk of any
    acceptable set of any excerpt."""
    chunks = {c for x in evidence.excerpts.values() for s in x.acceptable_sets for c in s}
    return {u.key: n for n, u in enumerate(run.units, start=1) if chunks & set(u.chunk_ids)}


# The rule since 2026-10-28 (docs/changes/v3.14.md, question 3): completion vectors by dominance.

def completion_vector(needed: Iterable[int | None]) -> list[float]:
    """The groups' completion ranks in ascending order; never complete counts as after every rank."""
    return sorted(math.inf if n is None else n for n in needed)


def verdict(left: list[float], right: list[float]) -> str:
    """"better", "worse", "mixed" or "same" for the right side, place by place."""
    earlier = any(r < l for l, r in zip(left, right))
    later = any(r > l for l, r in zip(left, right))
    return "mixed" if earlier and later else "better" if earlier else "worse" if later else "same"


def leads(left: list[float], right: list[float]) -> dict[str, list[tuple[int, int]]]:
    """The K ranges in which each side has more groups complete."""
    top = int(max([x for x in left + right if x != math.inf], default=1))
    out: dict[str, list[tuple[int, int]]] = {"left": [], "right": []}
    for k in range(1, top + 1):
        a, b = sum(x <= k for x in left), sum(x <= k for x in right)
        side = "left" if a > b else "right" if b > a else None
        if side:
            ranges = out[side]
            if ranges and ranges[-1][1] == k - 1:
                ranges[-1] = (ranges[-1][0], k)
            else:
                ranges.append((k, k))
    return out


PLAN_MODEL = "google/gemini-3.8-flash"   # docs/changes/v3.14.md, question 1
# Sub-question ↔ information item annotation of the frozen plans (docs/changes/v3.14.md, "分层评估")
DECOMPOSITION_MAPPING = "reports/experiments/v3.14-decomposition/mapping.json"
PHRASING_OF = {"P3.2.1": "rewrite", "P3.2.2": "translation", "P3.2.3": "hyde", "P3.2.4": "step_back"}
MERGES = {"rr": "round_robin", "rrf": "rrf"}   # key code → execution.SELECTORS


@dataclass(frozen=True)
class QueryConfig:
    """One query configuration (docs/changes/v3.14.md, "分层评估")."""
    decompose: bool = False
    merge: str | None = None          # with decomposition: rr | rrf; None: unmerged (evaluation only)
    original_route: bool = False      # with a merge
    phrasings: tuple[str, ...] = ()   # P3.2.x module ids, in module order
    fuse: bool = True                 # with phrasings; False: exactly one phrasing, in place of the question

    def __post_init__(self):
        if self.merge is not None and (not self.decompose or self.merge not in MERGES):
            raise ValueError(f"合并方式不合法：{self.merge}")
        if self.original_route and self.merge is None:
            raise ValueError("原问题一路只在选了合并方式时可开")
        if list(self.phrasings) != [m for m in PHRASING_OF if m in self.phrasings]:
            raise ValueError(f"写法须按模块顺序且不重复：{self.phrasings}")
        if not self.fuse and len(self.phrasings) != 1:
            raise ValueError("不融合时只能选一种写法")

    @property
    def merged(self) -> bool:
        """Whether the configuration has a return order."""
        return not self.decompose or self.merge is not None

    @property
    def key(self) -> str:
        """Readable and safe in file names and URLs: 'P3.1@rr-o_P3.2.1+P3.2.2@rrf'; '' is V3.13.
        Without '@' a part has no merge (decomposition) or no fusion (phrasing)."""
        parts = []
        if self.decompose:
            parts.append("P3.1" + (f"@{self.merge}" if self.merge else "") + ("-o" if self.original_route else ""))
        if self.phrasings:
            parts.append("+".join(self.phrasings) + ("@rrf" if self.fuse else ""))
        return "_".join(parts)

    @classmethod
    def parse(cls, key: str) -> "QueryConfig":
        fields: dict[str, Any] = {}
        for part in filter(None, key.split("_")):
            body, _, option = part.partition("@")
            if body == "P3.1":
                merge, _, route = option.partition("-")
                fields.update(decompose=True, merge=merge or None, original_route=route == "o")
            else:
                fields.update(phrasings=tuple(body.split("+")), fuse=option == "rrf")
        config = cls(**fields)
        if config.key != key:
            raise ValueError(f"配置键不合法：{key}")
        return config

    def settings(self) -> execution.QuerySettings:
        return execution.QuerySettings(frozenset(PHRASING_OF[m] for m in self.phrasings), self.fuse,
                                       MERGES[self.merge] if self.merge else None, self.original_route)


def evaluated_configs() -> list[QueryConfig]:
    """Phrasing states (none; RRF over each non-empty subset; each phrasing unfused) × decomposition
    states (none; unmerged; round robin and global RRF, each without and with the original route)."""
    ids = list(PHRASING_OF)
    phrasings = [((), True)] + [(c, True) for n in range(1, len(ids) + 1) for c in combinations(ids, n)]         + [((m,), False) for m in ids]
    decompositions = [dict(), dict(decompose=True)] + [dict(decompose=True, merge=m, original_route=o)
                                                       for m in MERGES for o in (False, True)]
    return [QueryConfig(**d, phrasings=p, fuse=f) for p, f in phrasings for d in decompositions]


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


class Labels:
    """Phrasing labels ("question", "translation:english", …), each kept once; process detail refers
    to them by index."""

    def __init__(self):
        self.rows: list[str] = []

    def ranks(self, ranks) -> list[list[int]]:
        out = []
        for name, rank in ranks:
            if name not in self.rows:
                self.rows.append(name)
            out.append([self.rows.index(name), rank])
        return out


def case_facts(evidence, run: execution.QueryRun, table: UnitTable, labels: Labels) -> tuple[dict[str, Any], list[Any]]:
    """The facts of one run, and where each leading unit came from."""
    units = run.units
    needed = completion_ranks([u.chunk_ids for u in units], evidence.groups)
    first = first_ranks([u.chunk_ids for u in units], evidence.groups)
    by_key = {u.key: u for u in units}
    leading = run.order[:LEADING]
    cost = run.cost
    facts = {"n": [needed.get(g.group_id) for g in evidence.groups],
             "f": [first.get(g.group_id) for g in evidence.groups],
             "u": [table.of(s.unit) for s in leading],
             "e": {str(table.of(by_key[k])): p for k, p in evidence_positions(evidence, run).items()},
             "c": [cost.retrievals, round(cost.retrieval_seconds, 4), cost.llm_calls,
                   round(cost.llm_seconds, 2), round(cost.llm_cost_usd, 6)]}
    process = [[s.question_id, s.round, labels.ranks(s.ranks)] for s in leading]
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


def routed_facts(case, evidence, run: execution.QueryRun, routes_of: dict[str, list[str]],
                 table: UnitTable, labels: Labels) -> tuple[dict[str, Any], dict[str, Any]]:
    """Unmerged: no return order. An item's completion and first-unit ranks over the rankings of its
    sub-questions (routes_of: group → question ids; empty, an omitted item, takes all); the leading
    units of every ranking as the process."""
    rankings = {r.question_id: [u.chunk_ids for u in r.units] for r in run.routes}
    groups = {g.group_id: g for g in evidence.groups}
    n, f = [], []
    for group in case.groups:
        mine = [rankings[q] for q in (routes_of[group.group_id] or rankings)]
        schemes = [[evidence.excerpts[x].acceptable_sets for x in s.excerpt_ids] for s in group.schemes]
        n.append(routed_completion_rank(mine, schemes))
        firsts = [first_ranks(units, [groups[group.group_id]]).get(group.group_id) for units in mine]
        f.append(min((x for x in firsts if x is not None), default=None))
    cost = run.cost
    facts = {"n": n, "f": f, "c": [cost.retrievals, round(cost.retrieval_seconds, 4), cost.llm_calls,
                                   round(cost.llm_seconds, 2), round(cost.llm_cost_usd, 6)]}
    process = {"routes": [[r.question_id, [[table.of(u), labels.ranks(ranks)]
                                           for u, ranks in zip(r.units[:LEADING], r.ranks)]]
                          for r in run.routes]}
    return facts, process


def collect(workspace: Workspace, name: str, combos: tuple[Combo, ...] = COMBOS,
            planner: Planner | None = None, progress=None) -> dict[str, Any]:
    """Runs every evaluated configuration on every combination. Returns the page data: the facts
    under "configs" and the process detail under "process", both by configuration key; a
    configuration some question cannot run is left out and listed under "unavailable" with why."""
    from rag.cli import _assembly
    planner = planner or make_planner(workspace)
    configs = evaluated_configs()
    facts: dict[str, dict[str, Any]] = {c.key: {} for c in configs}
    process: dict[str, dict[str, Any]] = {c.key: {} for c in configs}
    unavailable: dict[str, list[str]] = {}
    units: dict[str, list] = {}
    holds: dict[str, dict[str, Any]] = {}   # combo → case → evidence unit index → excerpt ids
    plans: dict[str, dict[str, Any]] = {}
    mapping = read_json(workspace.root / DECOMPOSITION_MAPPING)["cases"]
    labels = Labels()
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
        for config in configs:
            facts[config.key][combo.label] = {}
            process[config.key][combo.label] = {}
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
            annotated = mapping[case.case_id]
            if annotated["subquestions"] != {q.id: q.text for q in pair[True].questions}:
                raise ValueError(f"{case.case_id}：对应标注的子问题与冻结的规划不一致")
            for config in configs:
                if not config.key:
                    run = execution.plain(search, case.question, documents)
                else:
                    try:
                        run = execution.planned(search, pair[config.decompose], config.settings(), documents)
                    except execution.Unavailable as e:
                        unavailable.setdefault(config.key, []).append(f"{combo.label} {case.case_id}：{e}")
                        continue
                done = (case_facts(evidence, run, table, labels) if config.merged
                        else routed_facts(case, evidence, run, annotated["mapping"], table, labels))
                facts[config.key][combo.label][case.case_id], process[config.key][combo.label][case.case_id] = done
                if not config.key:
                    holds[combo.label][case.case_id] = excerpts_held(evidence, run, table)
        units[combo.label] = table.rows
        combo_info.append({"label": combo.label, "config": combo.config,
                           "table_return_unit": combo.table_return_unit,
                           "ingest_cost": _ingest_cost(workspace, combo.config)})
        if progress:
            progress(combo.label)
    assert dataset is not None
    for key in unavailable:
        facts.pop(key)
        process.pop(key)
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
            "modules": [{"id": m, "name": n} for m, n in MODULES], "ks": list(KS), "plan_model": PLAN_MODEL,
            "combos": combo_info, "cases": cases, "plans": plans, "units": units, "holds": holds,
            "phrasing_labels": labels.rows,
            "configs": facts, "process": process, "unavailable": unavailable,
            "decomposition": {k: {x: v[x] for x in ("mapping", "extra", "multi", "lost_qualifier", "note")}
                              for k, v in mapping.items()}}


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
