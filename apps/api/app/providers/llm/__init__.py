"""LLM provider factory · env-driven singleton.

Set LLM_PROVIDER to one of:
  - openai_compat (default · works with OpenAI · OpenRouter · OneAPI · DeepSeek)
  - anthropic     (direct Claude · requires `anthropic` pip package)
  - saas_gemini   (alias for openai_compat pointed at hunter's Gemini gateway)

Required env for openai_compat / saas_gemini:
  LLM_BASE_URL · LLM_API_KEY · LLM_DEFAULT_MODEL
For anthropic:
  LLM_API_KEY · LLM_DEFAULT_MODEL (optional · defaults to claude-sonnet-4-6)
"""
import os
from loguru import logger
from .base import ILLM

_INSTANCE: ILLM | None = None


def get_llm() -> ILLM:
    global _INSTANCE
    if _INSTANCE is not None:
        return _INSTANCE

    provider = (os.getenv("LLM_PROVIDER") or "openai_compat").lower()
    logger.info("[providers.llm] loading provider={}", provider)

    base_url = os.getenv("LLM_BASE_URL", "")
    api_key = os.getenv("LLM_API_KEY", "")
    default_model = os.getenv("LLM_DEFAULT_MODEL", "gpt-4o-mini")

    if provider in ("openai_compat", "saas_gemini"):
        if not base_url:
            raise RuntimeError(
                f"LLM_PROVIDER={provider} requires LLM_BASE_URL "
                "(e.g. https://api.openai.com/v1)"
            )
        from .openai_compat import OpenAICompatLLM
        _INSTANCE = OpenAICompatLLM(base_url, api_key, default_model)
    elif provider == "anthropic":
        if not api_key:
            raise RuntimeError("LLM_PROVIDER=anthropic requires LLM_API_KEY")
        from .anthropic_impl import AnthropicLLM
        _INSTANCE = AnthropicLLM(
            api_key,
            base_url=base_url or None,
            default_model=default_model or "claude-sonnet-4-6",
        )
    else:
        raise RuntimeError(
            f"unknown LLM_PROVIDER={provider!r} · "
            "expected one of: openai_compat | anthropic | saas_gemini"
        )
    return _INSTANCE


__all__ = ["ILLM", "get_llm"]
