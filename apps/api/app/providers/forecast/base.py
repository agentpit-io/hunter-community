"""IForecast · K-line prediction interface.

Return shape:
  {code, pred_len, ohlc: [[o,h,l,c], ...], confidence: 0..1, model, generated_at}

Noop impl returns is_enabled() = False so the frontend can hide the
Kronos forecast SKILL card entirely.
"""
from abc import ABC, abstractmethod


class IForecast(ABC):
    @abstractmethod
    async def predict(self, code: str, pred_len: int = 10) -> dict: ...

    def is_enabled(self) -> bool:
        return True

    async def health_check(self) -> dict:
        return {"ok": self.is_enabled(), "provider": self.__class__.__name__}
