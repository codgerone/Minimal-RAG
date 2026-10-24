"""Run every ground-truth question through the configured retriever and score the results."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from rag.eval.dataset import Case, Dataset, load_dataset
from rag.eval.judge import CaseEvidence, GroupEvidence, JudgeChunk, judge_case
from rag.eval.metrics import METRICS, QuestionScore, aggregate, completion_ranks, score_question
from rag.eval import table_metrics as tm
from rag.index.builder import current_status, open_lexical, open_store, open_tables
from rag.jsonio import read_json, write_json
from rag.paths import Workspace
from rag.query import execution
from rag.query.execution import QueryCost
from rag.query.retriever import build_retriever
from rag.query.scope import SearchScope, load_catalog, resolve_scope
from rag.query.units import ResultUnit, UnitSearch
from rag.registry import Assembly


class EvalError(RuntimeError):
    pass


@dataclass(frozen=True)
class CaseResult:
    case: Case
    units: list[ResultUnit]       # the top-K return units
    tokens: list[int]             # token count of each returned unit
    evidence: CaseEvidence
    score: QuestionScore | None   # None for unanswerable questions
    scope: SearchScope
    required_k: dict[str, int]    # group → units needed in the full ranking; uncoverable groups left out
    fewest_units: dict[str, int]  # group → fewest return units any acceptable set spreads over
    table: dict[str, Any] | None = None   # table evidence metrics, for table questions
    leading_tokens: tuple[int, ...] = ()  # tokens of the leading return units of the full ranking
    cost: QueryCost | None = None         # what the query execution spent on this question

    @property
    def evidence_documents(self) -> set[str]:
        return {x.document_id for x in self.case.excerpts}

    @property
    def identified_correctly(self) -> bool:
        return (self.scope.kind == "identified"
                and set(self.scope.document_ids or ()) == self.evidence_documents)


def run_name(config_name: str, top_k: int, document_filter: bool, retriever: str,
             table_return_unit: str = "chunk") -> str:
    """Folder name: date, config, K, filter state, (unless semantic) the retriever and (when
    whole tables are returned) `_table`, so a run reads without opening it."""
    state = "filter-on" if document_filter else "filter-off"
    suffix = "" if retriever == "semantic" else f"_{retriever}"
    if table_return_unit == "table":
        suffix += "_table"
    return f"{datetime.now():%Y-%m-%d}_{config_name}_k{top_k}_{state}{suffix}"


def _run_folder(workspace: Workspace, name: str) -> Path:
    base = workspace.eval_reports() / name
    folder, n = base, 1
    while folder.exists():
        n += 1
        folder = base.with_name(f"{base.name}_{n}")
    return folder


def _judge_chunks(workspace: Workspace, config_name: str, chunk_ids: set[str]) -> list[JudgeChunk]:
    """Chunk text and source regions from the index's intermediate results (chunks.json)."""
    def box(raw):
        return None if raw is None else (raw["x0"], raw["y0"], raw["x1"], raw["y1"])

    chunks = []
    for path in sorted((workspace.index_dir(config_name) / "documents").glob("*/chunks.json")):
        for c in read_json(path)["chunks"]:
            regions = c.get("regions") or []
            chunks.append(JudgeChunk(
                c["chunk_id"], c["document_id"], c["text"], c["parent_unit_id"],
                tuple((r["page_number"], box(r["bbox"])) for r in regions if r["precision"] == "fine"),
                tuple((r["page_number"], box(r["bbox"])) for r in regions if r["precision"] == "coarse")))
    found = {c.chunk_id for c in chunks}
    without = [c.chunk_id for c in chunks if not c.fine and not c.coarse]
    if found != chunk_ids or without:
        raise EvalError(f"配置 {config_name} 的中间结果缺少 chunk 来源区域（索引建于 V3.4 之前），"
                        f"请运行 ingest --force 重建")
    return chunks


def _metrics_dict(scores: list[QuestionScore]) -> dict[str, Any]:
    ratios = aggregate(scores)
    return {key: {"value": ratios[key].value, "numerator": ratios[key].numerator,
                  "denominator": ratios[key].denominator} for key, _, _ in METRICS}


def run_evaluation(workspace: Workspace, assembly: Assembly, budgets: Sequence[int]) -> list[Path]:
    """Each question runs through the query execution once; every budget B scores the first B
    units of its return order and is written as its own run (folder `..._k<B>_...`)."""
    budgets = sorted(set(budgets))
    status = current_status(workspace, assembly)
    if not status.queryable:
        raise EvalError(f"配置 {assembly.name} 的索引不可用（{status.state}），请先运行 ingest")
    stale = [d.relative_path for d in status.documents if d.state != "current"]
    if stale:
        raise EvalError("以下文档的索引不是最新的，评估结果会失真，请先运行 ingest："
                        + "；".join(stale))
    dataset: Dataset = load_dataset(workspace.ground_truth)
    sources = {d.source.document_id: d.source for d in status.documents if d.source}
    changed = [doc for doc, labelled in dataset.document_hashes.items()
               if doc in sources and sources[doc].file_hash != labelled]
    missing = [doc for doc in dataset.document_hashes if doc not in sources]
    if missing:
        raise EvalError(f"标注涉及的 PDF 不在 documents/ 中：{missing}")

    unanchored = [f"{c.case_id}/{x.excerpt_id}（{'坐标缺失' if x.anchor_state == 'missing' else '原文已改，坐标失效'}）"
                  for c in dataset.cases for x in c.excerpts if x.anchor_state != "ok"]
    if unanchored:
        raise EvalError("以下 excerpt 没有可用的 PDF 坐标，请运行 python -m rag anchors 核对后加 --write："
                        + "；".join(unanchored))
    if changed:
        raise EvalError(f"PDF 在标注之后被修改过，excerpt 坐标可能不再对应原文，需重新标注：{changed}")
    store = open_store(workspace, assembly.name)
    chunks = _judge_chunks(workspace, assembly.name, {c.chunk_id for c in store.list_chunks()})
    enabled = assembly.config.document_filter
    catalog = (load_catalog(workspace.documents, tuple(sources.values()))
               if enabled else None)
    table_return_unit = assembly.config.table_return_unit
    tables = open_tables(workspace, assembly.name)
    searcher = UnitSearch(build_retriever(assembly, store, open_lexical(workspace, assembly)), store,
                          tables, table_return_unit)
    owners = ({cid: t.table_id for cid, t in tables.tables_for_chunks([c.chunk_id for c in chunks]).items()}
              if table_return_unit == "table" else {})
    from rag.ingest.chunkers.tokens import E5TokenCounter
    counter = E5TokenCounter()
    token_cache: dict[str, int] = {}

    def tokens_of(text: str) -> int:
        if text not in token_cache:
            token_cache[text] = counter.count_passage(text)
        return token_cache[text]

    table_boxes = _table_boxes(workspace, assembly.name)
    table_excerpts = {(c.case_id, x.excerpt_id) for c in dataset.cases for x in c.excerpts
                      if tm.is_table_excerpt(x, table_boxes.get(x.document_id, []))}
    if dataset.cases:   # loads the encoder, so that its start-up is not timed as the first retrieval
        first = dataset.cases[0].question
        execution.plain(searcher, first, resolve_scope(first, catalog, enabled).document_ids)
    prepared: list[tuple[CaseResult, execution.QueryRun]] = []
    for case in dataset.cases:
        scope = resolve_scope(case.question, catalog, enabled)
        run = execution.plain(searcher, case.question, scope.document_ids)
        ranking = run.units
        evidence = judge_case(case, chunks)
        groups = tm.table_groups(case, evidence, {x for c, x in table_excerpts if c == case.case_id})
        table = None
        needed = tm.CURVE_K
        if groups:
            per_group, k2 = tm.required_units([u.chunk_ids for u in ranking], groups)
            unit_of = lambda cid: owners.get(cid, cid)
            table = {"groups": [{"group_id": g.group_id, "excerpt_ids": list(g.excerpt_ids),
                                 "chunk_rank": tm.chunk_rank(list(run.chunk_order), g.any_word_chunks),
                                 "required_k": per_group.get(g.group_id),
                                 # the group's acceptable sets of table excerpts, as return units
                                 "unit_sets": sorted({tuple(sorted({unit_of(c) for c in s})) for s in g.table_sets})}
                                for g in groups],
                     "required_k": k2}
            needed = max(needed, k2 or 0)
        leading = tuple(tokens_of(u.text) for u in ranking[:needed])
        if table is not None:
            table["tokens"] = sum(leading[:table["required_k"]]) if table["required_k"] else None
            table["leading_tokens"] = list(leading)
        owner = lambda cid: owners.get(cid, cid)
        item = CaseResult(case, [], [], evidence,
                          None, scope, completion_ranks([u.chunk_ids for u in ranking], evidence.groups),
                          {g.group_id: min(len({owners.get(c, c) for c in s}) for s in g.acceptable_sets)
                           for g in evidence.groups if g.acceptable_sets}, table, leading, run.cost)
        if table is not None:
            # Fewest return units covering the table evidence (same scope as metric 2).
            table["fewest_units"] = tm.fewest_units(
                [GroupEvidence(g.group_id, "coordinate", g.table_sets) for g in groups], owner)
        prepared.append((item, run))

    unmapped = [f"{r.case.case_id}/{x}：{e.problem}" for r, _ in prepared
                for x, e in r.evidence.excerpts.items() if e.mode == "unmapped"]
    if unmapped:
        raise EvalError("以下 excerpt 找不到对应 chunk，评估结果会失真：" + "；".join(unmapped))
    folders = {}
    for budget in budgets:
        folders[budget] = _run_folder(workspace, run_name(assembly.name, budget, enabled,
                                                          assembly.config.retriever.use, table_return_unit))
    for budget in budgets:
        results = []
        for item, run in prepared:
            units = run.first(budget)
            item = replace(item, units=units, tokens=[tokens_of(u.text) for u in units])
            if item.case.answerable:
                item = replace(item, score=score_question(
                    [u.chunk_ids for u in units], [u.document_id for u in units],
                    item.evidence.groups, item.evidence_documents))
            results.append(item)
        _write_run(workspace, assembly, folders[budget], budget, folders, results, dataset, sources,
                   changed, enabled, catalog is not None, len(table_excerpts))
    return [folders[b] for b in budgets]


def _write_run(workspace: Workspace, assembly: Assembly, folder: Path, top_k: int,
               folders: dict[int, Path], results: list[CaseResult], dataset: Dataset,
               sources: dict[str, Any], changed: list[str], enabled: bool, has_catalog: bool,
               table_excerpts: int) -> None:
    table_return_unit = assembly.config.table_return_unit
    names = {doc_id: source.document_name for doc_id, source in sources.items()}
    scored = [r.score for r in results if r.score is not None]
    per_document: dict[str, Any] = {}
    for doc_id in dict.fromkeys(r.case.document_id for r in results):
        doc_scores = [r.score for r in results if r.case.document_id == doc_id and r.score]
        name = next(r.case.document_name for r in results if r.case.document_id == doc_id)
        per_document[doc_id] = {"document_name": name, "questions": len(doc_scores),
                                "metrics": _metrics_dict(doc_scores)}
    result = {
        "config": assembly.name,
        "fingerprint": assembly.fingerprint(),
        "build_settings": assembly.build_settings(),
        "retriever": assembly.config.retriever.use,
        "table_return_unit": table_return_unit,
        "top_k": top_k,
        "query_execution": "plain",
        # the runs written from the same query executions, one per budget
        "budget_runs": {str(b): f.name for b, f in folders.items()},
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "dataset": {"version": dataset.version, "fingerprint": dataset.fingerprint,
                    "questions": len(dataset.cases), "answerable": len(scored),
                    "pdf_changed_since_labelling": changed},
        "evidence_modes": {mode: sum(x.mode == mode for r in results for x in r.evidence.excerpts.values())
                           for mode in ("coordinate", "fallback", "unmapped")},   # counted per excerpt
        "metrics": _metrics_dict(scored),
        "returned_tokens": _tokens_summary([r for r in results if r.score is not None]),
        "query_cost": _cost_summary([r for r in results if r.score is not None]),
        "document_scope": _scope_summary(results, enabled, has_catalog),
        "documents": per_document,
        "table_evidence": _table_summary(results, table_excerpts),
        "ingest_cost": _ingest_cost(workspace, assembly.name),
        "cases": [_case_json(r, names) for r in results],
    }
    write_json(folder / "result.json", result)
    from rag.reports.evaluation import latest_before, write_eval_report
    write_eval_report(workspace, folder, result, latest_before(workspace, result))


def _table_boxes(workspace: Workspace, config_name: str) -> dict[str, list[tuple[int, tuple[float, float, float, float]]]]:
    """Per document, every table's box on each of its pages, as the parser gave it."""
    boxes: dict[str, list[tuple[int, tuple[float, float, float, float]]]] = {}
    for path in sorted((workspace.index_dir(config_name) / "documents").glob("*/parsed.json")):
        parsed = read_json(path)
        own = boxes.setdefault(parsed["document_id"], [])
        for node in parsed["nodes"]:
            if "table" in node:
                own += [(s["page_number"], (s["bbox"]["x0"], s["bbox"]["y0"], s["bbox"]["x1"], s["bbox"]["y1"]))
                        for s in node["sources"] if s.get("bbox")]
    return boxes


def _table_summary(results: list[CaseResult], excerpts: int) -> dict[str, Any]:
    """Table questions, and the K curve: for K = 1..CURVE_K, complete questions, table-evidence
    complete questions, and the tokens returned summed over answerable questions."""
    answerable = [r for r in results if r.score is not None]
    table = [r for r in answerable if r.table is not None]
    curve = []
    for k in range(1, tm.CURVE_K + 1):
        complete = sum(len(r.required_k) == len(r.evidence.groups) and max(r.required_k.values(), default=0) <= k
                       for r in answerable)
        table_complete = sum(r.table["required_k"] is not None and r.table["required_k"] <= k for r in table)
        curve.append({"k": k, "complete": complete, "table_complete": table_complete,
                      "tokens": sum(sum(r.leading_tokens[:k]) for r in answerable)})
    return {"excerpts": excerpts, "questions": len(table), "answerable": len(answerable),
            "groups": sum(len(r.table["groups"]) for r in table), "curve": curve}


def _ingest_cost(workspace: Workspace, config_name: str) -> dict[str, Any] | None:
    """LLM table processing recorded at ingest: spend, call time, retries, degraded tables."""
    tables = calls = retries = degraded = 0
    cost = seconds = 0.0
    found = False
    for path in sorted((workspace.index_dir(config_name) / "documents").glob("*/parsed.json")):
        for node in read_json(path)["nodes"]:
            llm = node.get("llm") if "table" in node else None
            if not llm:
                continue
            found = True
            tables += 1
            attempts = llm["attempts"]
            calls += len(attempts)
            retries += len(attempts) - 1
            cost += sum(a["cost_usd"] or 0 for a in attempts)
            seconds += sum(a["seconds"] or 0 for a in attempts)
            passed = llm["annotation"] if llm["task"] == "annotation" else llm["summary"]
            degraded += passed is None
    if not found:
        return None
    return {"tables": tables, "calls": calls, "retries": retries, "degraded_tables": degraded,
            "cost_usd": round(cost, 6), "seconds": round(seconds, 1)}


def _tokens_summary(answerable: list[CaseResult]) -> dict[str, Any]:
    """Tokens returned per answerable question: the sum over questions / N, and the largest."""
    per_question = [sum(r.tokens) for r in answerable]
    return {"total": sum(per_question), "denominator": len(per_question),
            "value": sum(per_question) / len(per_question) if per_question else None,
            "max": max(per_question, default=0)}


def _cost_summary(answerable: list[CaseResult]) -> dict[str, Any]:
    """Query execution cost per answerable question: the sum over questions / N, and the largest.
    The same for every budget of a run (the return order is built once)."""
    def summary(values: list[float]) -> dict[str, Any]:
        return {"total": sum(values), "denominator": len(values),
                "value": sum(values) / len(values) if values else None, "max": max(values, default=0)}
    costs = [r.cost for r in answerable if r.cost]
    return {"retrievals": summary([c.retrievals for c in costs]),
            "retrieval_seconds": summary([c.retrieval_seconds for c in costs]),
            "llm_calls": summary([c.llm_calls for c in costs])}


def _scope_summary(results: list[CaseResult], enabled: bool, has_catalog: bool) -> dict[str, Any]:
    """Document identification over answerable questions: identified set == evidence documents."""
    answerable = [r for r in results if r.score is not None]
    kinds = ("identified", "unidentified", "no_catalog", "disabled")
    return {"enabled": enabled, "catalog": has_catalog,
            "identified_correctly": {"numerator": sum(r.identified_correctly for r in answerable),
                                     "denominator": len(answerable)},
            "kinds": {kind: sum(r.scope.kind == kind for r in answerable) for kind in kinds}}


def _case_json(item: CaseResult, names: dict[str, str]) -> dict[str, Any]:
    case, score = item.case, item.score
    relevant = {cid for g in item.evidence.groups for s in g.acceptable_sets for cid in s}
    evidence_docs = item.evidence_documents
    return {
        "case_id": case.case_id, "document_id": case.document_id,
        "document_name": case.document_name, "question": case.question,
        "reference_answer": case.reference_answer, "answerable": case.answerable,
        "scope": {"kind": item.scope.kind, "codes": list(item.scope.codes),
                  "document_ids": list(item.scope.document_ids) if item.scope.document_ids else None,
                  "document_names": [names.get(d, d) for d in item.scope.document_ids or ()],
                  "identified_correctly": item.identified_correctly},
        "evidence_document_ids": sorted(item.evidence_documents),
        "hit": score.hit if score else None, "complete": score.complete if score else None,
        "first_relevant_rank": score.first_relevant_rank if score else None,
        "excerpts": [{
            "excerpt_id": x.excerpt_id, "document_name": x.document_name, "pages": list(x.pages),
            "text": x.text, "mode": item.evidence.excerpts[x.excerpt_id].mode,
            "acceptable_sets": [sorted(s) for s in item.evidence.excerpts[x.excerpt_id].acceptable_sets],
            "problem": item.evidence.excerpts[x.excerpt_id].problem, "table_header": x.table_header,
        } for x in case.excerpts],
        "groups": [{
            "group_id": group.group_id,
            "information_item": group.information_item,
            "note": group.note,
            "mode": judged.mode,
            "covered_at_rank": score.group_completion_ranks.get(group.group_id) if score else None,
            "required_k": item.required_k.get(group.group_id),
            "fewest_units": item.fewest_units.get(group.group_id),
            "acceptable_sets": [sorted(s) for s in judged.acceptable_sets],
            "schemes": [{"scheme_id": scheme.scheme_id, "excerpt_ids": list(scheme.excerpt_ids),
                         "acceptable_sets": [sorted(s) for s in scheme_judged.acceptable_sets]}
                        for scheme, scheme_judged in zip(group.schemes, judged.schemes)],
        } for group, judged in zip(case.groups, item.evidence.groups)],
        "returned_tokens": sum(item.tokens),
        "query_cost": ({"retrievals": item.cost.retrievals,
                        "retrieval_seconds": round(item.cost.retrieval_seconds, 4),
                        "llm_calls": item.cost.llm_calls} if item.cost else None),
        "table": item.table,
        "hits": [{
            "rank": rank, "chunk_id": u.hit.chunk.chunk_id, "document_id": u.document_id,
            "document_name": u.hit.chunk.document_name, "pages": list(u.pages),
            "kind": u.hit.chunk.kind, "score": u.hit.score,
            "semantic_rank": u.hit.semantic_rank, "similarity": u.hit.similarity,
            "lexical_rank": u.hit.lexical_rank, "bm25": u.hit.bm25,
            "table_id": u.table.table_id if u.table else None,
            "chunk_ids": list(u.chunk_ids),
            "relevant_chunk_ids": [cid for cid in u.chunk_ids if cid in relevant],
            "relevant": any(cid in relevant for cid in u.chunk_ids),
            "cross_document": u.document_id not in evidence_docs,
            "tokens": tokens,
            "text": u.text,
        } for rank, (u, tokens) in enumerate(zip(item.units, item.tokens), start=1)],
    }
