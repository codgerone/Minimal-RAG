"""Minimal retrieval evaluation and optional live LLM smoke tests."""

from __future__ import annotations

import json
import hashlib
import math
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rag.errors import RagError
from rag.models import EvaluationCase, EvaluationResult
from rag.pipeline import RAGPipeline
from rag.retriever import Retriever
from rag.evaluation_metrics import aggregate_documents, calculate_question_metrics
from rag.evaluation_models import (
    CaseEvaluationFact, EvaluationDocumentResult, EvaluationQueryConfig,
    EvaluationRunRecord, ExpectedEvidenceSnapshot, GroundTruthIdentity,
    RetrievedChunkSnapshot, RunDocumentEntry, TestSetIdentity,
)
from rag.evaluation_repository import load_ground_truth_dataset, load_test_set_dataset
from rag.evaluation_reports import json_bytes, render_document, write_summary
from rag.pipeline_manifest import load_pipeline_manifest


DEFAULT_REFUSAL_TERMS = (
    "没有足够信息",
    "文档中没有",
    "not enough information",
    "does not specify",
)


def load_evaluation_cases(path: Path) -> list[EvaluationCase]:
    try:
        paths = (path,) if path.is_file() else tuple(sorted(
            item for item in path.parent.glob("*.json") if item.name != path.name
        ))
        if not paths:
            raise OSError(f"no evaluation JSON found under {path.parent}")
        cases: list[EvaluationCase] = []
        for source in paths:
            raw = json.loads(source.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ValueError("root must be a list")
            for item in raw:
                if not isinstance(item, dict):
                    raise ValueError("case must be an object")
                parsed = _parse_case(item)
                if len(paths) > 1:
                    parsed = EvaluationCase(
                        f"{source.stem}:{parsed.case_id}", parsed.question_zh,
                        parsed.question_en, parsed.expected_documents,
                        parsed.expected_pages, parsed.expected_terms, parsed.answerable,
                        parsed.expected_refusal_terms,
                    )
                cases.append(parsed)
        return cases
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise RagError(
            f"无法读取评估文件：{path}",
            "请检查 eval/questions.json 的结构。",
            cause=exc,
        ) from exc


def _parse_case(item: dict[str, Any]) -> EvaluationCase:
    question = str(item.get("question", "")).strip()
    return EvaluationCase(
        case_id=str(item["id"]),
        question_zh=str(item.get("question_zh", question)).strip(),
        question_en=str(item.get("question_en", "")).strip(),
        expected_documents=tuple(str(value) for value in item["expected_documents"]),
        expected_pages=tuple(int(value) for value in item["expected_pages"]),
        expected_terms=tuple(str(value) for value in item.get("expected_terms", [])),
        answerable=bool(item["answerable"]),
        expected_refusal_terms=tuple(
            str(value) for value in item.get("expected_refusal_terms", [])
        ),
    )


class Evaluator:
    def __init__(
        self,
        retriever: Retriever,
        pipeline: RAGPipeline | None = None,
    ) -> None:
        self.retriever = retriever
        self.pipeline = pipeline

    def evaluate_retrieval(
        self, cases: list[EvaluationCase]
    ) -> list[EvaluationResult]:
        results: list[EvaluationResult] = []
        for case in cases:
            questions = tuple(dict.fromkeys(item for item in (
                ("zh", case.question_zh),
                ("en", case.question_en),
            ) if item[1]))
            for language, question in questions:
                hits = self.retriever.search(question)
                def pages(hit) -> tuple[int, ...]:
                    values = getattr(hit, "page_numbers", None)
                    if values is not None:
                        return tuple(values)
                    page = getattr(hit, "page_number", None)
                    return () if page is None else (page,)
                sources = ", ".join(
                    f"{hit.document_name}:p{','.join(str(item) for item in pages(hit)) or 'unavailable'}"
                    for hit in hits
                )
                if not case.answerable:
                    results.append(
                        EvaluationResult(
                            f"{case.case_id}:{language}",
                            question,
                            None,
                            f"不可回答题仅报告来源：{sources or '无'}",
                        )
                    )
                    continue
                passed = any(
                    hit.document_name in case.expected_documents
                    and bool(set(pages(hit)) & set(case.expected_pages))
                    for hit in hits
                )
                results.append(
                    EvaluationResult(
                        f"{case.case_id}:{language}",
                        question,
                        passed,
                        f"检索来源：{sources or '无'}",
                    )
                )
        return results

    def evaluate_live(
        self, cases: list[EvaluationCase]
    ) -> list[EvaluationResult]:
        if self.pipeline is None:
            raise RagError("live eval 未配置问答 Pipeline。")
        results: list[EvaluationResult] = []
        for case in cases:
            questions = tuple(dict.fromkeys(item for item in (
                ("zh", case.question_zh),
                ("en", case.question_en),
            ) if item[1]))
            for language, question in questions:
                answer = self.pipeline.ask(question).answer
                folded = answer.casefold()
                if case.answerable:
                    passed = (all(term.casefold() in folded for term in case.expected_terms)
                              if case.expected_terms else None)
                else:
                    refusal_terms = case.expected_refusal_terms or DEFAULT_REFUSAL_TERMS
                    passed = any(term.casefold() in folded for term in refusal_terms)
                results.append(EvaluationResult(
                    f"{case.case_id}:{language}", question, passed, answer,
                ))
        return results


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _hit_pages(hit) -> tuple[int, ...]:
    pages = getattr(hit, "page_numbers", None)
    if pages is not None:
        return tuple(pages)
    page = getattr(hit, "page_number", None)
    return () if page is None else (page,)


def _approved_test_set(project_root: Path, pipeline_id: str, build_fingerprint: str, ground_truth):
    matches = []
    for directory in sorted((project_root / "eval" / "test-sets").iterdir()):
        if not directory.is_dir() or not (directory / "manifest.json").is_file():
            continue
        try:
            dataset = load_test_set_dataset(project_root, directory, ground_truth, required_status="approved")
        except Exception:
            continue
        if dataset.manifest.pipeline_id == pipeline_id and dataset.manifest.build_config_fingerprint == build_fingerprint:
            matches.append(dataset)
    if len(matches) != 1:
        raise RagError(f"必须且只能找到一套匹配的 approved test set，实际为 {len(matches)} 套。")
    return matches[0]


def run_formal_evaluation(settings, retriever: Retriever, store, top_k: int) -> Path:
    """Run the approved deterministic retrieval evaluation and publish one immutable run."""
    started = datetime.now(timezone.utc)
    gt = load_ground_truth_dataset(settings.project_root, required_status="approved")
    manifest = load_pipeline_manifest(settings.manifest_path)
    if manifest is None:
        raise RagError("正式评估需要可用的 pipeline manifest。")
    test_set = _approved_test_set(settings.project_root, settings.identity.pipeline_id,
                                  manifest.build_config_fingerprint, gt)
    if (test_set.manifest.collection_name != manifest.collection_name
            or test_set.manifest.build_config != manifest.build_config):
        raise RagError("test set、pipeline manifest 与 BuildConfig 不一致。")
    records = {record.record_id: record for record in store.list_records()}
    expected_ids = {
        chunk_id
        for document in test_set.documents for case in document.cases
        for group in case.evidence_groups for chunk_set in group.acceptable_chunk_sets
        for chunk_id in chunk_set
    }
    missing = sorted(expected_ids - set(records))
    if missing:
        raise RagError(f"test set 引用了索引中不存在的 chunk：{missing[:5]}。")

    timestamp = started.strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{timestamp}-{settings.identity.pipeline_id}-{manifest.build_config_fingerprint[:8]}-k{top_k}"
    validation_root = settings.project_root / "validation" / "retrieval" / "system-v2.0"
    work = validation_root / ".work" / run_id
    final = validation_root / "runs" / f"pipeline-{settings.identity.pipeline_id}__cfg-{manifest.build_config_fingerprint[:12]}__k-{top_k}__{run_id}"
    if work.exists() or final.exists():
        raise RagError(f"评估 run_id 已存在：{run_id}。")
    work.mkdir(parents=True)
    gt_by_key = {item.document_key: item for item in gt.documents}
    ts_by_key = {item.document_key: item for item in test_set.documents}
    results = []
    entries = []
    for gt_doc in gt.documents:
        mapping_doc = ts_by_key[gt_doc.document_key]
        cases = []
        for case, mapping_case in zip(gt_doc.cases, mapping_doc.cases, strict=True):
            mappings = mapping_case.evidence_groups
            expected = tuple(
                ExpectedEvidenceSnapshot(group.evidence_group_id, mapping.status,
                                         group.excerpts, mapping.acceptable_chunk_sets)
                for group, mapping in zip(case.evidence_groups, mappings, strict=True)
            )
            hits = retriever.search(case.question.strip(), top_k=top_k)
            if len(hits) > top_k or len({hit.chunk_id for hit in hits}) != len(hits):
                raise RagError(f"{case.case_id} 返回数量或 chunk ID 唯一性违反检索契约。")
            if any(not math.isfinite(hit.distance) for hit in hits):
                raise RagError(f"{case.case_id} 返回了非有限距离。")
            target_ids = ({x.document_id for group in case.evidence_groups for x in group.excerpts}
                          if case.answerable else {gt_doc.document_id})
            relevant_by_group = {
                mapping.evidence_group_id: {cid for option in mapping.acceptable_chunk_sets for cid in option}
                for mapping in mappings
            }
            snapshots = tuple(
                RetrievedChunkSnapshot(
                    rank, hit.chunk_id, hit.document_id, hit.document_name, hit.relative_path,
                    _hit_pages(hit),
                    hit.chunk_index, getattr(hit, "chunk_kind", None), hit.text,
                    hit.distance, hit.similarity,
                    tuple(group.evidence_group_id for group in mappings
                          if hit.chunk_id in relevant_by_group[group.evidence_group_id]),
                    any(hit.chunk_id in values for values in relevant_by_group.values()),
                    hit.document_id not in target_ids,
                ) for rank, hit in enumerate(hits, 1)
            )
            metrics = calculate_question_metrics(mappings, snapshots) if case.answerable else None
            cases.append(CaseEvaluationFact(
                case.case_id, "completed", case.question, case.answerable, case.reference_answer,
                expected, snapshots,
                tuple(x.evidence_group_id for x in mappings if x.status == "unmappable"), metrics, None,
            ))
        result = EvaluationDocumentResult(run_id, gt_doc.document_key, gt_doc.document_id,
                                          gt_doc.document_name, gt_doc.relative_path,
                                          gt_doc.file_hash, tuple(cases))
        results.append(result)
        json_payload = json_bytes(result)
        html_payload = render_document(result).encode("utf-8")
        json_rel = f"documents/{gt_doc.document_key}.json"
        html_rel = f"documents/{gt_doc.document_key}.html"
        _write(work / json_rel, json_payload)
        _write(work / html_rel, html_payload)
        entries.append(RunDocumentEntry(gt_doc.document_key, json_rel, _sha(json_payload),
                                        html_rel, _sha(html_payload)))
    aggregate = aggregate_documents(run_id, tuple(results))
    aggregate_payload = json_bytes(aggregate)
    _write(work / "aggregate.json", aggregate_payload)
    completed = datetime.now(timezone.utc)
    query = EvaluationQueryConfig(top_k, manifest.build_config.tokenizer.query_prefix)
    run = EvaluationRunRecord(
        run_id, "completed", "2.0", settings.identity.pipeline_id, manifest.collection_name,
        manifest.build_config, manifest.build_config_fingerprint, query, "retrieval_evaluation_v2",
        GroundTruthIdentity(gt.manifest.dataset_version, gt.manifest.ground_truth_fingerprint),
        TestSetIdentity(test_set.manifest.test_set_version, test_set.manifest.test_set_fingerprint,
                        test_set.manifest.annotation_rule_version),
        started.isoformat().replace("+00:00", "Z"), completed.isoformat().replace("+00:00", "Z"),
        tuple(entries), "aggregate.json", _sha(aggregate_payload), None,
    )
    _write(work / "run.json", json_bytes(run))
    final.parent.mkdir(parents=True, exist_ok=True)
    os.replace(work, final)
    write_summary(validation_root)
    return final
