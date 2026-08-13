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


# 这段文字会被 MCP 原样交给模型,所以它同时是**给模型的指令**,不只是给人看的提示。
#
# 最后那句禁止不是客套:实测 gemini-3-flash-preview 收到"需要 key"之后,
# 照样编出了完整行情 —— 价格 16.85 元、跌 2.43%、成交额 24.87 亿、52 周区间、
# 连"数据更新于 2026-08-13 14:15:22"都编得有鼻子有眼。财经产品里这是最危险的
# 失败模式:用户完全看不出这是假的。
NEED_KEY_MSG = (
    "【工具不可用】尚未配置 Hunter key,无法获取任何真实行情数据。\n"
    "【必须这样回复用户】告诉他:点页面左下角「解锁全部工具」免费申请 Hunter key,"
    "填入后即可查询实时行情。\n"
    "【严禁】编造价格、涨跌幅、成交额、52 周区间、时间戳等任何数字。"
    "你手上没有这只股票的任何数据,一个数字都不许写。"
)


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
            raise HunterKeyRequired(NEED_KEY_MSG, hunter_key.APPLY_URL)
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
