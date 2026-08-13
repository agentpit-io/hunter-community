"""Hunter tool-gateway data source · the "unlocked" path.

Talks to ``{HUNTER_UPSTREAM_URL}/api/saas/tools/*`` with the platform key from
``app.services.hunter_key``. This is what actually makes the SKILLs work —
akshare/yfinance cover A-shares and basic US/HK quotes, but the gateway is
where the curated data lives (SEC filings · analyst ratings · 龙虎榜 · UZI · Kronos).

The key is resolved **per call**, not baked into the client at construction:
a user who pastes their key in the UI should see tools start working right
away, not after restarting the container.

Get a free key: https://hunter.agentpit.io/dev/api-keys
"""
from __future__ import annotations

import httpx
from loguru import logger

from app.services import hunter_key
from .base import IDataSource


class HunterKeyRequired(RuntimeError):
    """Raised when no key is configured, or the configured key was rejected.

    Carries the upstream guidance text so callers can surface "go apply here"
    instead of a bare 401.
    """

    def __init__(self, message: str, apply_url: str):
        super().__init__(message)
        self.apply_url = apply_url


class HunterToolsDataSource(IDataSource):
    def __init__(self, timeout: float = 30.0):
        self._base = hunter_key.UPSTREAM
        self._timeout = timeout

    async def _post(self, tool: str, payload: dict):
        key = hunter_key.resolve()
        if not key:
            raise HunterKeyRequired(
                "尚未配置 Hunter key，工具不可用。点左下角「解锁全部工具」申请并填入。",
                hunter_key.APPLY_URL,
            )
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            r = await c.post(
                f"{self._base}/api/saas/tools/{tool}",
                json=payload,
                headers={"Authorization": f"Bearer {key}"},
            )
        if r.status_code == 401:
            body = {}
            try:
                body = r.json()
            except Exception:
                pass
            raise HunterKeyRequired(
                body.get("message") or "Hunter key 无效或已吊销。",
                body.get("apply_url") or hunter_key.APPLY_URL,
            )
        r.raise_for_status()
        return r.json()

    async def get_quote(self, code: str) -> dict:
        return await self._post("quote", {"code": code})

    async def get_kline(self, code: str, days: int = 30) -> dict:
        return await self._post("kline", {"code": code, "limit": days})

    async def get_news(self, code: str, limit: int = 10) -> list[dict]:
        data = await self._post("news", {"code": code, "limit": limit})
        return data.get("items", data) if isinstance(data, dict) else data

    async def health_check(self) -> dict:
        key = hunter_key.resolve()
        if not key:
            return {"ok": False, "provider": "hunter", "error": "no key configured",
                    "apply_url": hunter_key.APPLY_URL}
        try:
            m = await hunter_key.manifest(key)
            return {"ok": bool(m.get("unlocked")), "provider": "hunter",
                    "tools": len(m.get("tools") or [])}
        except Exception as e:
            logger.warning("[providers.hunter] health check failed: {}", e)
            return {"ok": False, "provider": "hunter", "error": str(e)[:120]}
