"""Facts for the multi-round evaluation page: every question × generation run once at the highest call limit,
the lower limits cut from the same run (runner.answer_at), and the two single-round baselines.

Only facts are kept (reports/multi_round/NAME/data/runs.json); every metric is computed by the page from them
(rag/reports/multi_round.py), so the page can be rebuilt without running anything. Per question: each
information item's acceptable chunk sets. Per run: the units in the order first handed to the LLM, with their
chunks, tokens, sources and the call they were first handed to; each call's record, output and the question
list after it; the answer and termination of every limit. Per baseline: each item's completion rank in the
full ranking and the leading units' chunks and tokens. Rules: docs/rules/evaluation.md "多轮检索的评估".
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Collection, Sequence

from rag.eval.judge import judge_case
from rag.eval.metrics import completion_ranks
from rag.eval.query_compare import MemoSearch, QueryConfig, make_planner
from rag.eval.runner import prepare
from rag.paths import Workspace
from rag.query import execution
from rag.query.planning import document_languages

GENERATIONS = (0, 1, 2, 3)
# Single-round baselines (docs/rules/evaluation.md "多轮检索的评估"): the V3.14 default, and its best measured
# single-round configuration (codes stripped, original text + translation, RRF; decision 015)
BASELINES = (("default", "V3.14 默认（原问题检索一次）", ""),
             ("strip_translation", "去编码 + 原句 + 译文 RRF", "P3.2.0_P3.2.2@rrf"))
BASELINE_LEADING = 150   # leading units kept per baseline ranking, for cutting at the multi-round tokens


class LockedSearch:
    """Retrieval is fast and shares the encoder; runs call the LLM in parallel and retrieve one at a time."""

    def __init__(self, search):
        self.search = search
        self.lock = threading.Lock()

    def rank(self, text: str, document_ids: Collection[str] | None = None):
        with self.lock:
            return self.search.rank(text, document_ids)


def call_json(call, new: list[str], lst: list[dict[str, Any]]) -> dict[str, Any]:
    return {"kind": call.kind, "prompt_tokens": call.prompt_tokens, "completion_tokens": call.completion_tokens,
            "reasoning_tokens": call.reasoning_tokens, "seconds": call.seconds, "cost_usd": call.cost_usd,
            "degraded": call.degraded, "retried": len(call.texts) > 1, "output": call.texts[-1] if call.texts else "",
            "new": new, "list": lst}


def run_facts(record, tokens_of, max_calls: int, caller) -> dict[str, Any]:
    from rag.query.multi_round.runner import answer_at, plan_calls
    units = {u: {"call": e.call, "chunks": list(e.unit.chunk_ids), "tokens": tokens_of(e.unit.text),
                 "document": e.unit.document_id, "pages": list(e.unit.pages), "table": e.unit.table is not None,
                 "text": e.unit.text, "sources": [list(s) for s in e.sources]}
             for u, e in record.table.entries.items()}
    limits = {}
    for limit in range(2, max_calls):
        answer, call, termination = answer_at(record, limit, caller)
        limits[str(limit)] = {"answer": answer, "termination": termination,
                              "call": None if call is None else call_json(call, [], [])}
    limits[str(max_calls)] = {"answer": record.answer, "termination": record.termination, "call": None}
    return {"termination": record.termination, "answer": record.answer, "plan_calls": plan_calls(record),
            "order": list(record.table.order), "units": units, "rounds": [list(r) for r in record.rounds],
            "calls": [call_json(c, new, lst) for c, new, lst in zip(record.calls, record.new_units, record.lists)],
            "limits": limits}


def baseline_facts(evidence, run: execution.QueryRun, tokens_of) -> dict[str, Any]:
    units = run.units
    needed = completion_ranks([u.chunk_ids for u in units], evidence.groups)
    return {"n": [needed.get(g.group_id) for g in evidence.groups],
            "u": [[list(u.chunk_ids), tokens_of(u.text)] for u in units[:BASELINE_LEADING]],
            "llm_cost_usd": round(run.cost.llm_cost_usd, 6), "llm_calls": run.cost.llm_calls}


def collect(workspace: Workspace, name: str, assembly, cases: Sequence[str] | None = None,
            generations: Sequence[int] = GENERATIONS, workers: int = 8, progress=None) -> dict[str, Any]:
    from rag.ingest.sources import discover
    from rag.ingest.tables.llm_tables import LLMCache
    from rag.query.llm import OpenRouterLLM
    from rag.query.multi_round.planner import PROMPT_VERSIONS, Caller
    from rag.query.multi_round.runner import run
    from rag.query.multi_round.state import SCHEDULE_A, Schedule

    settings = assembly.config.multi_round
    schedule = Schedule(settings.fallback_schedule) if settings.fallback_schedule else SCHEDULE_A
    ctx = prepare(workspace, assembly, cache_retrievals=True)
    ctx.warm_up()
    search = LockedSearch(MemoSearch(ctx.searcher))
    languages_of = document_languages(workspace.index_dir(assembly.name))
    names = {d.document_id: d.relative_path for d in discover(workspace.documents)}
    llm = OpenRouterLLM(model=settings.model, temperature=0, env_override=False)
    cache = LLMCache(workspace.root / ".rag" / "llm-cache")
    planner = make_planner(workspace)
    chosen = [c for c in ctx.dataset.cases if cases is None or c.case_id in cases]
    if cases is not None and len(chosen) != len(set(cases)):
        raise ValueError(f"找不到的题：{sorted(set(cases) - {c.case_id for c in chosen})}")

    facts: dict[str, Any] = {}
    jobs = []
    for case in chosen:
        scope = ctx.scope(case.question)
        languages = sorted({languages_of[d] for d in (scope.document_ids or languages_of)})
        scope_json = {"documents": [names[d] for d in (scope.document_ids or names)],
                      "identifier_codes": list(scope.codes)}
        entry: dict[str, Any] = {"case_id": case.case_id, "question": case.question, "answerable": case.answerable,
                                 "document_name": case.document_name, "scope": {**scope_json, "languages": languages},
                                 "groups": [], "baselines": {}, "runs": {}}
        if case.answerable:
            evidence = judge_case(case, ctx.chunks)
            entry["groups"] = [{"id": g.group_id, "item": next(x.information_item for x in case.groups if x.group_id == g.group_id),
                                "sets": [sorted(s) for s in g.acceptable_sets]} for g in evidence.groups]
            for key, _, config_key in BASELINES:
                entry["baselines"][key] = {}
                for g in generations:
                    if not config_key:
                        done = execution.plain(search, case.question, scope.document_ids)
                    else:
                        plan = planner.add_languages(planner.plan(case.question, languages, False, generation=g),
                                                     languages, generation=g)
                        done = execution.planned(search, plan, QueryConfig.parse(config_key).settings(),
                                                 scope.document_ids, scope.codes)
                    entry["baselines"][key][str(g)] = baseline_facts(evidence, done, ctx.tokens_of)
                    if not config_key:
                        break   # no LLM: the same in every generation
        facts[case.case_id] = entry
        for g in generations:
            jobs.append((case, scope, languages, scope_json, g))

    def one(job):
        case, scope, languages, scope_json, g = job
        caller = Caller(llm, cache, generation=g, scope=scope_json)
        record = run(case.question, search, scope.document_ids, scope.codes, languages, caller,
                     settings.max_llm_calls, settings.units_per_attempt, schedule)
        return case.case_id, g, run_facts(record, ctx.tokens_of, settings.max_llm_calls, caller)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for done in as_completed([pool.submit(one, j) for j in jobs]):
            case_id, g, got = done.result()
            facts[case_id]["runs"][str(g)] = got
            if progress:
                progress(case_id, g, got)
    return {"name": name, "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "config": assembly.name, "table_return_unit": assembly.config.table_return_unit,
            "dataset": {"version": ctx.dataset.version, "questions": len(ctx.dataset.cases)},
            "settings": {"max_llm_calls": settings.max_llm_calls, "units_per_attempt": settings.units_per_attempt,
                         "schedule": [list(g) for g in schedule.groups], "model": settings.model,
                         "prompts": dict(PROMPT_VERSIONS)},
            "generations": list(generations), "baselines": [{"key": k, "label": l, "config": c} for k, l, c in BASELINES],
            "cases": [facts[c.case_id] for c in chosen]}
