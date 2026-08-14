"""OpenRouter OpenAI-compatible client."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from openai import OpenAI

from rag.config import Settings
from rag.errors import LlmServiceError


class OpenRouterClient:
    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self.settings = settings
        if not settings.openrouter_api_key:
            raise LlmServiceError(
                "缺少 OPENROUTER_API_KEY。",
                "请在项目根目录 .env 中配置 API Key。",
            )
        self._client = client or OpenAI(
            api_key=settings.openrouter_api_key,
            base_url="https://openrouter.ai/api/v1",
        )

    def answer(self, messages: Sequence[dict[str, str]]) -> str:
        try:
            response = self._client.chat.completions.create(
                model=self.settings.openrouter_model,
                messages=list(messages),
                temperature=0,
            )
            content = response.choices[0].message.content
            if not content or not content.strip():
                raise LlmServiceError(
                    "OpenRouter 返回了空回答。",
                    "请稍后重试，或更换 OPENROUTER_MODEL。",
                )
            return content.strip()
        except LlmServiceError:
            raise
        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            if status_code in {401, 403}:
                category = "认证失败"
            elif status_code == 429:
                category = "请求受到限流"
            elif isinstance(status_code, int) and status_code >= 500:
                category = "上游服务暂时不可用"
            else:
                category = "请求失败"
            raise LlmServiceError(
                f"OpenRouter {category}。",
                "请检查 API Key、模型名称和网络连接后重试。",
                cause=exc,
            ) from exc

