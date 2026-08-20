"""akshare-backed data source · A-shares · free · fully local.

Wraps the sync akshare library in asyncio.to_thread. Rate-limited by
the underlying free public data feeds — not for high-QPS use.

2026-08-20 · quote 收敛到共享层:
  · get_quote 走 agents.data_sources.akshare_quote.fetch_quote(东财 → 腾讯)
  · 腾讯解析复用 source_mapping._delimited(spec 驱动 · §5.2/§5.3 单位约定沉淀于此)
  · 不再手写 _tencent_quote_sync —— 那份下标 [6] 写错了、盲目 ×100 会重造 §5.3 坑
  · TODO: get_kline 也该走 agents.data_sources.akshare_kline.fetch_kline
    · 见 apps/api/agents/data_sources/akshare_kline.py · 留给下一轮
"""
import asyncio
from typing import Any

from loguru import logger

from .base import IDataSource


class AkshareDataSource(IDataSource):
    def __init__(self):
        try:
            import akshare  # noqa: F401 · lazy validation at construction time
        except Exception as e:
            raise RuntimeError(
                "akshare is not installed · add it to requirements.txt "
                "or switch DATA_SOURCE_PROVIDER"
            ) from e

    async def get_quote(self, code: str) -> dict:
        from agents.data_sources.akshare_quote import fetch_quote
        q = await asyncio.to_thread(fetch_quote, code, "A")
        if q is None:
            raise ValueError(f"stock not found or all quote sources failed: {code}")
        return q

    async def get_kline(self, code: str, days: int = 30) -> dict:
        import akshare as ak
        # akshare wants date range · not "days"
        from datetime import date, timedelta
        end = date.today().strftime("%Y%m%d")
        start = (date.today() - timedelta(days=days * 2)).strftime("%Y%m%d")
        df = await asyncio.to_thread(
            ak.stock_zh_a_hist, symbol=code, period="daily",
            start_date=start, end_date=end, adjust="qfq",
        )
        ohlc = [
            [row["日期"].isoformat() if hasattr(row["日期"], "isoformat") else str(row["日期"]),
             _f(row["开盘"]), _f(row["最高"]), _f(row["最低"]),
             _f(row["收盘"]), _f(row.get("成交量"))]
            for _, row in df.tail(days).iterrows()
        ]
        return {"code": code, "ohlc": ohlc}

    async def get_news(self, code: str, limit: int = 10) -> list[dict]:
        # akshare has stock_news_em · returns eastmoney news list
        import akshare as ak
        try:
            df = await asyncio.to_thread(ak.stock_news_em, symbol=code)
        except Exception:
            return []
        items = []
        for _, row in df.head(limit).iterrows():
            items.append({
                "title": str(row.get("新闻标题", "")),
                "url": str(row.get("新闻链接", "")),
                "published_at": str(row.get("发布时间", "")),
                "source": str(row.get("文章来源", "eastmoney")),
            })
        return items


def _f(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None
