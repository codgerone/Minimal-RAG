"""One retrieval and one grounded generation per answer request."""

from __future__ import annotations

from typing import Protocol

from rag.v3.contracts.retrieval import (
    AnswerRequest, AnswerResult, LanguageModelRequest, PromptMessage,
    RetrievalRequest, RetrievalResult,
)


class RetrieverPort(Protocol):
    def retrieve(self, request: RetrievalRequest) -> RetrievalResult: ...


class PromptPort(Protocol):
    def build(self, request: AnswerRequest,
              retrieval: RetrievalResult) -> tuple[PromptMessage, ...]: ...


class LanguageModelPort(Protocol):
    def complete(self, request: LanguageModelRequest) -> str: ...


class SingleTurnChatbot:
    def __init__(self, retriever: RetrieverPort, prompt: PromptPort,
                 language_model: LanguageModelPort, model_name: str):
        if not model_name:
            raise ValueError("language model name is required")
        self.retriever = retriever
        self.prompt = prompt
        self.language_model = language_model
        self.model_name = model_name

    def answer(self, request: AnswerRequest) -> AnswerResult:
        retrieval = self.retriever.retrieve(RetrievalRequest(
            request.question, request.top_k, request.document_id,
            request.index_identity, request.configuration_name))
        messages = self.prompt.build(request, retrieval)
        answer = self.language_model.complete(LanguageModelRequest(
            messages, self.model_name, 0))
        return AnswerResult(answer, retrieval, messages, self.model_name)
