"""akshare-backed data source · A-shares · free · fully local.

Wraps the sync akshare library in asyncio.to_thread. Rate-limited by
the underlying free public data feeds — not for high-QPS use.
"""
import asyncio
from typing import Any
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
        import akshare as ak
        df = await asyncio.to_thread(ak.stock_zh_a_spot_em)
        matched = df[df["代码"] == code]
        if matched.empty:
            raise ValueError(f"stock not found: {code}")
        row = matched.iloc[0]
        return {
            "code": code,
            "name": str(row.get("名称", "")),
            "price": _f(row.get("最新价")),
            "change_pct": _f(row.get("涨跌幅")),
            "volume": _f(row.get("成交量")),
            "amount": _f(row.get("成交额")),
            "high": _f(row.get("最高")),
            "low": _f(row.get("最低")),
            "open": _f(row.get("今开")),
            "prev_close": _f(row.get("昨收")),
        }

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
