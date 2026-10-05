"""OpenRouter transport with bounded, machine-readable service failures."""

from __future__ import annotations

from typing import Any, Callable

from rag.v3.contracts.retrieval import LanguageModelRequest


class LanguageModelError(RuntimeError):
    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(detail)


class OpenRouterLanguageModel:
    def __init__(self, api_key_provider: Callable[[], str | None],
                 client_factory: Callable[..., Any] | None = None,
                 auth_retry_provider: Callable[[], str | None] | None = None):
        self.api_key_provider = api_key_provider
        self.client_factory = client_factory
        self.auth_retry_provider = auth_retry_provider

    def complete(self, request: LanguageModelRequest) -> str:
        key = self.api_key_provider()
        if not key or not key.strip():
            raise LanguageModelError("llm_missing_credentials", "OpenRouter API key is missing")
        for attempt in range(2):
            try:
                if self.client_factory is None:
                    from openai import OpenAI
                    client = OpenAI(api_key=key, base_url="https://openrouter.ai/api/v1",
                                    max_retries=0)
                else:
                    client = self.client_factory(api_key=key,
                        base_url="https://openrouter.ai/api/v1", max_retries=0)
                response = client.chat.completions.create(
                    model=request.model_name,
                    messages=[{"role": item.role, "content": item.content}
                              for item in request.messages],
                    temperature=request.temperature,
                )
                answer = response.choices[0].message.content
                if not isinstance(answer, str) or not answer.strip():
                    raise LanguageModelError("llm_upstream_failed", "OpenRouter returned an empty answer")
                return answer.strip()
            except LanguageModelError:
                raise
            except Exception as exc:
                from openai import APITimeoutError
                status = getattr(exc, "status_code", None)
                if status in {401, 403}:
                    code = "llm_auth_failed"
                elif status == 429:
                    code = "llm_rate_limited"
                elif isinstance(exc, APITimeoutError):
                    code = "llm_timeout"
                else:
                    code = "llm_upstream_failed"
                if code == "llm_auth_failed" and attempt == 0 and self.auth_retry_provider:
                    replacement = self.auth_retry_provider()
                    if replacement and replacement.strip():
                        key = replacement.strip()
                        continue
                raise LanguageModelError(code, "OpenRouter request failed") from exc
        raise AssertionError("unreachable LLM retry state")
