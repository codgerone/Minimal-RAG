"""Facts for the query comparison page: every combination × module configuration × question.

A combination is an index (configs/) with a table return unit; a module configuration is a set
of the query-side modules (P3.1, P3.2.1–P3.2.4, P3.3; the empty set is V3.13). Per question only
what any K can be derived from is kept: each evidence group's needed units in the return order,
the rank of the first relevant unit, the tokens and keys of the leading units, and the cost.
Metrics at K (rag.reports.query_compare and the page script) follow docs/rules/evaluation.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from rag.eval.metrics import completion_ranks
from rag.eval.judge import judge_case
from rag.eval.runner import EvalContext, _ingest_cost, prepare
from rag.paths import Workspace
from rag.query import execution

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


def case_facts(ctx: EvalContext, case, run: execution.QueryRun) -> dict[str, Any]:
    evidence = judge_case(case, ctx.chunks)
    order = [u.chunk_ids for u in run.units]
    needed = completion_ranks(order, evidence.groups)
    relevant = {cid for g in evidence.groups for s in g.acceptable_sets for cid in s}
    first = next((rank for rank, ids in enumerate(order, start=1) if any(c in relevant for c in ids)), None)
    leading = run.order[:LEADING]
    return {
        "needed": [needed.get(g.group_id) for g in evidence.groups],
        "first_relevant": first,
        "tokens": [ctx.tokens_of(s.unit.text) for s in leading],
        "units": [s.unit.key for s in leading],
        "relevant": [any(c in relevant for c in s.unit.chunk_ids) for s in leading],
        "cost": {"retrievals": run.cost.retrievals, "llm_calls": run.cost.llm_calls,
                 "llm_seconds": round(run.cost.llm_seconds, 4),
                 "retrieval_seconds": round(run.cost.retrieval_seconds, 4),
                 "rerank_seconds": round(run.cost.rerank_seconds, 4),
                 "llm_cost_usd": round(run.cost.llm_cost_usd, 6)},
    }


def collect_combo(workspace: Workspace, combo: Combo) -> dict[str, Any]:
    """The plain execution (V3.13) of every question on one combination."""
    from rag.cli import _assembly
    assembly = _assembly(workspace, combo.config, "hybrid", combo.table_return_unit)
    ctx = prepare(workspace, assembly)
    ctx.warm_up()
    facts, units = {}, {}
    for case in ctx.dataset.cases:
        if not case.answerable:
            continue
        run = execution.plain(ctx.searcher, case.question, ctx.scope(case.question).document_ids)
        facts[case.case_id] = case_facts(ctx, case, run)
        for s in run.order[:LEADING]:
            u = s.unit
            units.setdefault(u.key, {"text": u.text[:TEXT_LIMIT], "pages": list(u.pages),
                                           "table": u.table is not None})
    return {"facts": facts, "units": units, "ingest_cost": _ingest_cost(workspace, combo.config),
            "dataset": ctx.dataset}


def collect(workspace: Workspace, name: str, combos: tuple[Combo, ...] = COMBOS) -> dict[str, Any]:
    configs: dict[str, dict[str, Any]] = {config_key(()): {}}
    units: dict[str, Any] = {}
    combo_info, dataset = [], None
    for combo in combos:
        got = collect_combo(workspace, combo)
        dataset = got["dataset"]
        configs[config_key(())][combo.label] = got["facts"]
        units[combo.label] = got["units"]
        combo_info.append({"label": combo.label, "config": combo.config,
                           "table_return_unit": combo.table_return_unit, "ingest_cost": got["ingest_cost"]})
    assert dataset is not None
    cases = [{"case_id": c.case_id, "document_id": c.document_id, "document_name": c.document_name,
              "question": c.question,
              "groups": [{"group_id": g.group_id, "information_item": g.information_item} for g in c.groups]}
             for c in dataset.cases if c.answerable]
    return {"name": name, "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "dataset": {"version": dataset.version, "fingerprint": dataset.fingerprint,
                        "questions": len(dataset.cases), "answerable": len(cases)},
            "modules": [{"id": m, "name": n} for m, n in MODULES], "ks": list(KS),
            "combos": combo_info, "cases": cases, "configs": configs, "units": units}


def metrics_at(facts: dict[str, dict[str, Any]], k: int) -> dict[str, float]:
    """Over answerable questions: hit and complete counts, MRR, tokens per question at K."""
    n = len(facts)
    hit = complete = 0
    rr = tokens = 0.0
    for f in facts.values():
        covered = [x for x in f["needed"] if x is not None and x <= k]
        hit += bool(covered)
        complete += len(covered) == len(f["needed"])
        if f["first_relevant"] is not None and f["first_relevant"] <= k:
            rr += 1 / f["first_relevant"]
        tokens += sum(f["tokens"][:k])
    return {"hit": hit, "complete": complete, "mrr": rr / n if n else 0.0,
            "tokens": tokens / n if n else 0.0}
