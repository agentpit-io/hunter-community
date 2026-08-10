"""SaaS data source · consumes an Hunter-compatible HTTP API.

Point at hunter.agentpit.io or your own aggregation service.
Free-tier keys: https://hunter.agentpit.io/dev/api-keys
"""
import httpx
from .base import IDataSource


class SaasDataSource(IDataSource):
    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0):
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=timeout,
        )

    async def get_quote(self, code: str) -> dict:
        r = await self._client.get(f"/quote/{code}")
        r.raise_for_status()
        return r.json()

    async def get_kline(self, code: str, days: int = 30) -> dict:
        r = await self._client.get(f"/kline/{code}", params={"days": days})
        r.raise_for_status()
        return r.json()

    async def get_news(self, code: str, limit: int = 10) -> list[dict]:
        r = await self._client.get(f"/news/{code}", params={"limit": limit})
        r.raise_for_status()
        data = r.json()
        return data.get("items", data) if isinstance(data, dict) else data

    async def health_check(self) -> dict:
        try:
            r = await self._client.get("/health", timeout=5.0)
            return {"ok": r.status_code == 200, "provider": "saas",
                    "status": r.status_code}
        except Exception as e:
            return {"ok": False, "provider": "saas", "error": str(e)[:120]}
