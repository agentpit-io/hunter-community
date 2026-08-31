"""Kronos-over-HTTP forecast provider · covers both local and SaaS.

Assumes a Kronos inference server exposing POST /predict returning
{code, pred_len, ohlc: [...], confidence}. Both self-hosted (needs GPU)
and hunter.agentpit.io's managed endpoint speak this shape.
"""
import httpx
from datetime import datetime, timezone
from .base import IForecast


class KronosHTTPForecast(IForecast):
    def __init__(self, base_url: str, api_key: str = "", model: str = "kronos-v1"):
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=60.0,
        )
        self._model = model

    async def predict(self, code: str, pred_len: int = 10) -> dict:
        r = await self._client.post(
            "/predict",
            # 上游 hunter gateway API 期望字段是 symbol (2026-08-31 排查)
            # code 保留 · 便于向下兼容潜在旧版
            json={"symbol": code, "code": code, "pred_len": pred_len},
        )
        r.raise_for_status()
        out = r.json()
        out.setdefault("code", code)
        out.setdefault("pred_len", pred_len)
        out.setdefault("model", self._model)
        out.setdefault("generated_at", datetime.now(timezone.utc).isoformat())
        return out

    async def health_check(self) -> dict:
        try:
            r = await self._client.get("/health", timeout=5.0)
            return {"ok": r.status_code == 200, "provider": "kronos_http"}
        except Exception as e:
            return {"ok": False, "provider": "kronos_http", "error": str(e)[:120]}
