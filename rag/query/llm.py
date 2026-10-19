"""LLM interface and the OpenRouter implementation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol


class LLMError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code  # missing_key | auth_failed | rate_limited | timeout | upstream_failed
        super().__init__(message)


@dataclass(frozen=True)
class Completion:
    text: str
    prompt_tokens: int | None
    completion_tokens: int | None
    cost_usd: float | None          # as billed by the provider, when it reports it
    reasoning: str | None = None    # the model's native reasoning, when returned
    reasoning_tokens: int | None = None


class LLM(Protocol):
    model: str

    def complete(self, messages: list[dict[str, str]]) -> str: ...


class OpenRouterLLM:
    base_url = "https://openrouter.ai/api/v1"

    def __init__(self, model: str = "google/gemini-2.5-flash-lite", temperature: float | None = 0,
                 timeout: float = 600, env_override: bool = True):
        # OPENROUTER_MODEL overrides the answering model only; ingest-time callers pass False.
        self.model = (os.environ.get("OPENROUTER_MODEL") if env_override else None) or model
        self.temperature = temperature  # None: not sent (some reasoning models accept none)
        self.timeout = timeout
        self.api_key: str | None = None

    def complete(self, messages: list[dict[str, str]]) -> str:
        return self.complete_with_usage(messages).text

    def complete_with_usage(self, messages: list[dict[str, str]]) -> Completion:
        return self._create(messages, {}, {"usage": {"include": True}})

    def complete_structured(self, messages: list[dict[str, str]], name: str,
                            schema: dict[str, Any], reasoning_effort: str) -> Completion:
        """JSON Schema strict output with native reasoning, sent only to providers supporting both."""
        return self._create(
            messages,
            {"response_format": {"type": "json_schema",
                                 "json_schema": {"name": name, "strict": True, "schema": schema}}},
            {"usage": {"include": True}, "reasoning": {"effort": reasoning_effort},
             "provider": {"require_parameters": True}})

    def _create(self, messages: list[dict[str, str]], options: dict[str, Any],
                extra_body: dict[str, Any]) -> Completion:
        key = self.api_key or os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not key:
            raise LLMError("missing_key", "未设置 OPENROUTER_API_KEY")
        from openai import APITimeoutError, OpenAI
        try:
            client = OpenAI(api_key=key, base_url=self.base_url, max_retries=0, timeout=self.timeout)
            if self.temperature is not None:
                options = {**options, "temperature": self.temperature}
            response = client.chat.completions.create(model=self.model, messages=messages,
                                                      extra_body=extra_body, **options)
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            code = ("auth_failed" if status in {401, 403} else "rate_limited" if status == 429
                    else "timeout" if isinstance(exc, APITimeoutError) else "upstream_failed")
            raise LLMError(code, f"OpenRouter 请求失败（{code}）") from exc
        message = response.choices[0].message if response.choices else None
        answer = message.content if message else None
        if not isinstance(answer, str) or not answer.strip():
            raise LLMError("upstream_failed", "OpenRouter 返回了空回答")
        usage = response.usage
        cost = getattr(usage, "cost", None) if usage else None
        details = getattr(usage, "completion_tokens_details", None) if usage else None
        reasoning = getattr(message, "reasoning", None)
        return Completion(answer.strip(), getattr(usage, "prompt_tokens", None),
                          getattr(usage, "completion_tokens", None),
                          float(cost) if cost is not None else None,
                          reasoning if isinstance(reasoning, str) and reasoning else None,
                          getattr(details, "reasoning_tokens", None) if details else None)
