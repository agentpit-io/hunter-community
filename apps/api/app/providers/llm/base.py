"""ILLM · unified chat/stream/list_models interface.

Contract:
  - chat(messages, model, **kw) → {content, usage, model}
  - chat_stream(...) → async iterator of str chunks
  - list_models() → list of provider-native model ids
"""
from abc import ABC, abstractmethod
from typing import AsyncIterator


class ILLM(ABC):
    @abstractmethod
    async def chat(self, messages: list[dict], model: str | None = None, **kw) -> dict: ...

    @abstractmethod
    async def chat_stream(self, messages: list[dict], model: str | None = None, **kw) -> AsyncIterator[str]: ...

    @abstractmethod
    def list_models(self) -> list[str]: ...

    async def health_check(self) -> dict:
        return {"ok": True, "provider": self.__class__.__name__}
