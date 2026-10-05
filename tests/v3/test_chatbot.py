from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from rag.v3.adapters.openrouter import LanguageModelError, OpenRouterLanguageModel
from rag.v3.application.assembly import builtin_configuration, index_identity
from rag.v3.application.chatbot import SingleTurnChatbot
from rag.v3.application.prompt import GroundedPromptBuilder, GroundedPromptParameters
from rag.v3.contracts.retrieval import AnswerRequest, LanguageModelRequest, PromptMessage, RetrievalResult


def test_chatbot_passes_one_actual_retrieval_into_prompt_and_model():
    index = index_identity(builtin_configuration("plain_text"))
    calls = []

    class Retriever:
        def retrieve(self, request):
            calls.append("retrieve")
            return RetrievalResult(request, (), index)

    class Model:
        def complete(self, request):
            calls.append("complete")
            assert request.temperature == 0
            assert "<document_context>" in request.messages[1].content
            return "提供的文档中没有足够信息"

    bot = SingleTurnChatbot(Retriever(),
        GroundedPromptBuilder(GroundedPromptParameters("single_page")), Model(), "model")
    result = bot.answer(AnswerRequest(" question ", 3, None, "plain_text", index))
    assert calls == ["retrieve", "complete"]
    assert result.retrieval.request.question == " question "
    assert result.answer == "提供的文档中没有足够信息"


def test_openrouter_transport_never_calls_client_without_key():
    request = LanguageModelRequest((PromptMessage("user", "hello"),), "model", 0)
    called = []
    model = OpenRouterLanguageModel(lambda: None,
        lambda **kwargs: called.append(kwargs))
    with pytest.raises(LanguageModelError) as missing:
        model.complete(request)
    assert missing.value.code == "llm_missing_credentials"
    assert called == []

    def factory(**kwargs):
        assert kwargs["max_retries"] == 0
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **call: SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content="  answer  "))]))))

    assert OpenRouterLanguageModel(lambda: "secret", factory).complete(request) == "answer"
