"""Explicit LLM smoke run, independent from formal retrieval metrics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from rag.v3.contracts.retrieval import AnswerRequest
from rag.v3.contracts.storage import IndexIdentity


@dataclass(frozen=True)
class LiveSmokeResult:
    case_id: str
    status: Literal["completed", "failed"]
    answer: str | None
    error_code: str | None


def run_live_smoke(repository: object, chatbot: object, index: IndexIdentity,
                   configuration_name: str, top_k: int) -> tuple[LiveSmokeResult, ...]:
    manifest, documents = repository.load_ground_truth()
    if manifest.review_status != "approved":
        raise ValueError("live smoke requires approved ground truth")
    results = []
    for document in documents:
        for case in document.cases:
            try:
                answer = chatbot.answer(AnswerRequest(case.question, top_k, None,
                                                       configuration_name, index)).answer
                results.append(LiveSmokeResult(case.case_id, "completed", answer, None))
            except Exception as exc:
                results.append(LiveSmokeResult(case.case_id, "failed", None,
                    getattr(exc, "code", None) or "llm_upstream_failed"))
    return tuple(results)
