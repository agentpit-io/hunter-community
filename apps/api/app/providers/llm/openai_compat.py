"""OpenAI-compatible chat provider.

Works against any endpoint that speaks the OpenAI chat.completions API:
  - api.openai.com/v1
  - openrouter.ai/api/v1
  - your own OneAPI / LiteLLM proxy
  - api.deepseek.com/v1 (with model=deepseek-chat)
"""
from typing import AsyncIterator

from openai import AsyncOpenAI

from .base import ILLM


class OpenAICompatLLM(ILLM):
    def __init__(self, base_url: str, api_key: str, default_model: str = "gpt-4o-mini"):
        self._client = AsyncOpenAI(base_url=base_url.rstrip("/"), api_key=api_key)
        self._default_model = default_model

    async def chat(self, messages: list[dict], model: str | None = None, **kw) -> dict:
        resp = await self._client.chat.completions.create(
            model=model or self._default_model,
            messages=messages,
            **kw,
        )
        choice = resp.choices[0]
        return {
            "content": choice.message.content or "",
            "model": resp.model,
            "usage": resp.usage.model_dump() if resp.usage else None,
            "finish_reason": choice.finish_reason,
        }

    async def chat_stream(self, messages: list[dict], model: str | None = None, **kw) -> AsyncIterator[str]:
        stream = await self._client.chat.completions.create(
            model=model or self._default_model,
            messages=messages,
            stream=True,
            **kw,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content

    def list_models(self) -> list[str]:
        # Static defaults · impls can override by hitting /models
        return [self._default_model]
