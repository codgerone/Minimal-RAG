"""LLM interface and the OpenRouter implementation."""

from __future__ import annotations

import os
from typing import Protocol


class LLMError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code  # missing_key | auth_failed | rate_limited | timeout | upstream_failed
        super().__init__(message)


class LLM(Protocol):
    model: str

    def complete(self, messages: list[dict[str, str]]) -> str: ...


class OpenRouterLLM:
    base_url = "https://openrouter.ai/api/v1"

    def __init__(self, model: str = "google/gemini-2.5-flash-lite", temperature: float = 0):
        self.model = os.environ.get("OPENROUTER_MODEL") or model
        self.temperature = temperature
        self.api_key: str | None = None

    def complete(self, messages: list[dict[str, str]]) -> str:
        key = self.api_key or os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not key:
            raise LLMError("missing_key", "未设置 OPENROUTER_API_KEY")
        from openai import APITimeoutError, OpenAI
        try:
            client = OpenAI(api_key=key, base_url=self.base_url, max_retries=0)
            response = client.chat.completions.create(model=self.model, messages=messages,
                                                      temperature=self.temperature)
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            code = ("auth_failed" if status in {401, 403} else "rate_limited" if status == 429
                    else "timeout" if isinstance(exc, APITimeoutError) else "upstream_failed")
            raise LLMError(code, f"OpenRouter 请求失败（{code}）") from exc
        answer = response.choices[0].message.content
        if not isinstance(answer, str) or not answer.strip():
            raise LLMError("upstream_failed", "OpenRouter 返回了空回答")
        return answer.strip()
