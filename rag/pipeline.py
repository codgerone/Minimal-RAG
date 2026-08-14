"""Retriever → PromptBuilder → OpenRouter orchestration."""

from __future__ import annotations

from dataclasses import dataclass

from rag.llm import OpenRouterClient
from rag.models import RetrievalHit
from rag.prompt import build_messages
from rag.retriever import Retriever


@dataclass(frozen=True)
class AnswerResult:
    answer: str
    hits: tuple[RetrievalHit, ...]
    messages: tuple[dict[str, str], ...]


class RAGPipeline:
    def __init__(
        self,
        retriever: Retriever,
        llm_client: OpenRouterClient,
    ) -> None:
        self.retriever = retriever
        self.llm_client = llm_client

    def ask(self, question: str) -> AnswerResult:
        hits = tuple(self.retriever.search(question))
        messages = tuple(build_messages(question, hits))
        answer = self.llm_client.answer(messages)
        return AnswerResult(answer, hits, messages)

