"""Run every ground-truth question through the configured retriever and score the results."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from rag.eval.dataset import Case, Dataset, load_dataset
from rag.eval.judge import GroupEvidence, Mappings, chunk_key, judge_case
from rag.eval.metrics import METRICS, QuestionScore, aggregate, score_question
from rag.index.builder import current_status, open_store
from rag.index.store import VectorHit
from rag.jsonio import write_json
from rag.paths import Workspace
from rag.query.retriever import SemanticRetriever
from rag.query.scope import SearchScope, load_catalog, resolve_scope
from rag.registry import Assembly


class EvalError(RuntimeError):
    pass


@dataclass(frozen=True)
class CaseResult:
    case: Case
    hits: list[VectorHit]
    evidence: list[GroupEvidence]
    score: QuestionScore | None   # None for unanswerable questions
    scope: SearchScope

    @property
    def evidence_documents(self) -> set[str]:
        return {x.document_id for g in self.case.groups for x in g.excerpts}

    @property
    def identified_correctly(self) -> bool:
        return (self.scope.kind == "identified"
                and set(self.scope.document_ids or ()) == self.evidence_documents)


def run_name(config_name: str, top_k: int, document_filter: bool) -> str:
    """Folder name: date, config, K and filter state, so a run reads without opening it."""
    state = "filter-on" if document_filter else "filter-off"
    return f"{datetime.now():%Y-%m-%d}_{config_name}_k{top_k}_{state}"


def _run_folder(workspace: Workspace, name: str) -> Path:
    base = workspace.eval_reports() / name
    folder, n = base, 1
    while folder.exists():
        n += 1
        folder = base.with_name(f"{base.name}_{n}")
    return folder


def _metrics_dict(scores: list[QuestionScore]) -> dict[str, Any]:
    ratios = aggregate(scores)
    return {key: {"value": ratios[key].value, "numerator": ratios[key].numerator,
                  "denominator": ratios[key].denominator} for key, _, _ in METRICS}


def run_evaluation(workspace: Workspace, assembly: Assembly, top_k: int, *,
                   confirm_auto: bool = False) -> Path:
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

    store = open_store(workspace, assembly.name)
    chunks = store.list_chunks()
    mappings = Mappings(workspace.root / "eval" / "mappings.json")
    enabled = assembly.config.document_filter
    catalog = (load_catalog(workspace.documents, tuple(sources.values()))
               if enabled else None)
    retriever = SemanticRetriever(assembly.embedder, store)
    results: list[CaseResult] = []
    for case in dataset.cases:
        scope = resolve_scope(case.question, catalog, enabled)
        hits = retriever.retrieve(case.question, top_k, scope.document_ids)
        evidence = judge_case(case, chunks, mappings)
        item = CaseResult(case, hits, evidence, None, scope)
        if case.answerable:
            item = replace(item, score=score_question(
                [h.chunk.chunk_id for h in hits], [h.chunk.document_id for h in hits],
                evidence, item.evidence_documents))
        results.append(item)

    if confirm_auto:
        by_id = {c.chunk_id: c for c in chunks}
        for item in results:
            for group in item.evidence:
                if group.mode == "auto":
                    for chunk_set in group.acceptable_sets:
                        mappings.add(item.case.case_id, group.group_id,
                                     [chunk_key(by_id[c].document_id, by_id[c].text) for c in chunk_set])
        mappings.save()

    folder = _run_folder(workspace, run_name(assembly.name, top_k, enabled))
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
        "top_k": top_k,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "dataset": {"version": dataset.version, "fingerprint": dataset.fingerprint,
                    "questions": len(dataset.cases), "answerable": len(scored),
                    "pdf_changed_since_labelling": changed},
        "evidence_modes": {mode: sum(g.mode == mode for r in results for g in r.evidence)
                           for mode in ("confirmed", "auto", "unmapped")},
        "metrics": _metrics_dict(scored),
        "document_scope": _scope_summary(results, enabled, catalog is not None),
        "documents": per_document,
        "cases": [_case_json(r, names) for r in results],
    }
    write_json(folder / "result.json", result)
    from rag.reports.evaluation import latest_before, write_eval_report
    write_eval_report(workspace, folder, result, latest_before(workspace, result))
    return folder


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
    relevant = {cid for g in item.evidence for s in g.acceptable_sets for cid in s}
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
        "groups": [{
            "group_id": group.group_id,
            "mode": judged.mode,
            "covered_at_rank": score.group_completion_ranks.get(group.group_id) if score else None,
            "acceptable_sets": [sorted(s) for s in judged.acceptable_sets],
            "auto_coverage": judged.auto_coverage,
            "excerpts": [{"excerpt_id": x.excerpt_id, "document_name": x.document_name,
                          "pages": list(x.pages), "text": x.text} for x in group.excerpts],
        } for group, judged in zip(case.groups, item.evidence)],
        "hits": [{
            "rank": rank, "chunk_id": h.chunk.chunk_id, "document_id": h.chunk.document_id,
            "document_name": h.chunk.document_name, "pages": list(h.chunk.pages),
            "kind": h.chunk.kind, "distance": h.distance, "similarity": 1 - h.distance,
            "relevant": h.chunk.chunk_id in relevant,
            "cross_document": h.chunk.document_id not in evidence_docs,
            "text": h.chunk.text,
        } for rank, h in enumerate(item.hits, start=1)],
    }
