"""Minimal retrieval evaluation and optional live LLM smoke tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rag.errors import RagError
from rag.models import EvaluationCase, EvaluationResult
from rag.pipeline import RAGPipeline
from rag.retriever import Retriever


DEFAULT_REFUSAL_TERMS = (
    "没有足够信息",
    "文档中没有",
    "not enough information",
    "does not specify",
)


def load_evaluation_cases(path: Path) -> list[EvaluationCase]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("root must be a list")
        cases: list[EvaluationCase] = []
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError("case must be an object")
            cases.append(_parse_case(item))
        return cases
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise RagError(
            f"无法读取评估文件：{path}",
            "请检查 eval/questions.json 的结构。",
            cause=exc,
        ) from exc


def _parse_case(item: dict[str, Any]) -> EvaluationCase:
    return EvaluationCase(
        case_id=str(item["id"]),
        question_zh=str(item["question_zh"]),
        question_en=str(item["question_en"]),
        expected_documents=tuple(str(value) for value in item["expected_documents"]),
        expected_pages=tuple(int(value) for value in item["expected_pages"]),
        expected_terms=tuple(str(value) for value in item["expected_terms"]),
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
            for language, question in (
                ("zh", case.question_zh),
                ("en", case.question_en),
            ):
                hits = self.retriever.search(question)
                sources = ", ".join(
                    f"{hit.document_name}:p{hit.page_number}" for hit in hits
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
                    and hit.page_number in case.expected_pages
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
            for language, question in (
                ("zh", case.question_zh),
                ("en", case.question_en),
            ):
                answer = self.pipeline.ask(question).answer
                folded = answer.casefold()
                if case.answerable:
                    passed = all(term.casefold() in folded for term in case.expected_terms)
                else:
                    refusal_terms = (
                        case.expected_refusal_terms or DEFAULT_REFUSAL_TERMS
                    )
                    passed = any(term.casefold() in folded for term in refusal_terms)
                results.append(
                    EvaluationResult(
                        f"{case.case_id}:{language}",
                        question,
                        passed,
                        answer,
                    )
                )
        return results

