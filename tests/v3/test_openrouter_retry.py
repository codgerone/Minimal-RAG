from types import SimpleNamespace

import pytest

from rag.v3.adapters.openrouter import LanguageModelError, OpenRouterLanguageModel
from rag.v3.contracts.retrieval import LanguageModelRequest, PromptMessage


class ServiceError(Exception):
    def __init__(self, status_code):
        self.status_code = status_code


def test_auth_retry_reuses_exact_message_once_and_other_errors_do_not_retry():
    attempts = []
    replacements = []

    def client_factory(*, api_key, **kwargs):
        def create(**request):
            attempts.append((api_key, request))
            if api_key == "old":
                raise ServiceError(401)
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content="answer"))])
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=create)))

    request = LanguageModelRequest((PromptMessage("user", "question"),), "model", 0)
    adapter = OpenRouterLanguageModel(lambda: "old", client_factory,
        lambda: replacements.append("asked") or "new")
    assert adapter.complete(request) == "answer"
    assert replacements == ["asked"]
    assert [item[0] for item in attempts] == ["old", "new"]
    assert attempts[0][1] == attempts[1][1]

    def rate_limited(*, api_key, **kwargs):
        def create(**request):
            raise ServiceError(429)
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=create)))

    with pytest.raises(LanguageModelError) as error:
        OpenRouterLanguageModel(lambda: "old", rate_limited,
            lambda: replacements.append("wrong") or "new").complete(request)
    assert error.value.code == "llm_rate_limited"
    assert replacements == ["asked"]
