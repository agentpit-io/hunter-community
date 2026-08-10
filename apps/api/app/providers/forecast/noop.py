"""Default forecast provider · disabled placeholder."""
from .base import IForecast


class NoopForecast(IForecast):
    def is_enabled(self) -> bool:
        return False

    async def predict(self, code: str, pred_len: int = 10) -> dict:
        return {
            "code": code,
            "pred_len": pred_len,
            "ohlc": [],
            "confidence": 0.0,
            "model": "noop",
            "disabled": True,
            "reason": "FORECAST_PROVIDER=noop · switch to kronos_local or kronos_saas to enable",
        }
