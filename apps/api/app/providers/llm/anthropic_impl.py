"""Direct Anthropic (Claude) chat provider.

Uses the official anthropic SDK · install with pip install anthropic.
Not enabled by default — add anthropic to requirements.txt to use.
"""
from typing import AsyncIterator

from .base import ILLM


class AnthropicLLM(ILLM):
    def __init__(self, api_key: str, base_url: str | None = None,
                 default_model: str = "claude-sonnet-4-6"):
        try:
            from anthropic import AsyncAnthropic
        except ImportError as e:
            raise RuntimeError(
                "anthropic package not installed · "
                "add `anthropic` to requirements.txt or switch LLM_PROVIDER"
            ) from e
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = AsyncAnthropic(**kwargs)
        self._default_model = default_model

    @staticmethod
    def _convert_messages(messages: list[dict]) -> tuple[str | None, list[dict]]:
        """Split OpenAI-style messages into (system, [user/assistant])."""
        system = None
        rest = []
        for m in messages:
            if m.get("role") == "system":
                system = m.get("content", "")
            else:
                rest.append({"role": m.get("role", "user"),
                             "content": m.get("content", "")})
        return system, rest

    async def chat(self, messages: list[dict], model: str | None = None, **kw) -> dict:
        system, msgs = self._convert_messages(messages)
        params = {
            "model": model or self._default_model,
            "max_tokens": kw.pop("max_tokens", 4096),
            "messages": msgs,
        }
        if system:
            params["system"] = system
        params.update(kw)
        resp = await self._client.messages.create(**params)
        content = "".join(b.text for b in resp.content if getattr(b, "text", None))
        return {
            "content": content,
            "model": resp.model,
            "usage": {
                "input_tokens": resp.usage.input_tokens,
                "output_tokens": resp.usage.output_tokens,
            },
            "finish_reason": resp.stop_reason,
        }

    async def chat_stream(self, messages: list[dict], model: str | None = None, **kw) -> AsyncIterator[str]:
        system, msgs = self._convert_messages(messages)
        params = {
            "model": model or self._default_model,
            "max_tokens": kw.pop("max_tokens", 4096),
            "messages": msgs,
        }
        if system:
            params["system"] = system
        params.update(kw)
        async with self._client.messages.stream(**params) as stream:
            async for text in stream.text_stream:
                yield text

    def list_models(self) -> list[str]:
        return [self._default_model,
                "claude-opus-4-7", "claude-haiku-4-5-20251001"]
