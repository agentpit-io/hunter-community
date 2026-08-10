"""yfinance-backed data source · US/HK stocks · needs internet reach to Yahoo.

Symbol convention:
  A-shares  600519 → 600519.SS · 000001 → 000001.SZ
  HK        00700  → 0700.HK
  US        AAPL   → AAPL (as-is)
"""
import asyncio
from .base import IDataSource


class YFinanceDataSource(IDataSource):
    def __init__(self):
        try:
            import yfinance  # noqa: F401
        except Exception as e:
            raise RuntimeError(
                "yfinance is not installed · add it to requirements.txt "
                "or switch DATA_SOURCE_PROVIDER"
            ) from e

    @staticmethod
    def _to_yahoo(code: str) -> str:
        c = code.upper().strip()
        if c.isdigit():
            if len(c) == 6:
                # A-share heuristic · 6xxxxx=SS, 0/3xxxxx=SZ
                return f"{c}.{'SS' if c.startswith('6') else 'SZ'}"
            if len(c) in (4, 5):
                return f"{int(c):04d}.HK"
        return c  # US ticker as-is

    async def get_quote(self, code: str) -> dict:
        """Reliable quote via 2-day history · yf.fast_info breaks when the
        market is closed (KeyError: 'currentTradingPeriod') on newer yfinance.
        """
        import yfinance as yf
        symbol = self._to_yahoo(code)
        t = await asyncio.to_thread(yf.Ticker, symbol)
        # 5d history · we need last two closes to compute change_pct
        hist = await asyncio.to_thread(t.history, period="5d")
        if hist is None or hist.empty:
            return {
                "code": code, "symbol": symbol, "name": symbol,
                "price": None, "prev_close": None, "change_pct": None,
                "volume": None, "open": None, "high": None, "low": None,
            }
        last = hist.iloc[-1]
        prev = hist.iloc[-2] if len(hist) >= 2 else None
        last_close = _f(last.get("Close"))
        prev_close = _f(prev.get("Close")) if prev is not None else None
        return {
            "code": code,
            "symbol": symbol,
            "name": symbol,  # yfinance shortName is behind a slow HTTP call, skip
            "price": last_close,
            "prev_close": prev_close,
            "change_pct": _pct(last_close, prev_close),
            "volume": _f(last.get("Volume")),
            "open": _f(last.get("Open")),
            "high": _f(last.get("High")),
            "low": _f(last.get("Low")),
            "ts": last.name.isoformat() if hasattr(last.name, "isoformat") else str(last.name),
        }

    async def get_kline(self, code: str, days: int = 30) -> dict:
        import yfinance as yf
        symbol = self._to_yahoo(code)
        t = await asyncio.to_thread(yf.Ticker, symbol)
        hist = await asyncio.to_thread(t.history, period=f"{max(days, 5)}d")
        ohlc = [
            [ts.isoformat(), _f(r["Open"]), _f(r["High"]),
             _f(r["Low"]), _f(r["Close"]), _f(r.get("Volume"))]
            for ts, r in hist.tail(days).iterrows()
        ]
        return {"code": code, "symbol": symbol, "ohlc": ohlc}

    async def get_news(self, code: str, limit: int = 10) -> list[dict]:
        import yfinance as yf
        symbol = self._to_yahoo(code)
        t = await asyncio.to_thread(yf.Ticker, symbol)
        try:
            news = await asyncio.to_thread(lambda: t.news or [])
        except Exception:
            return []
        return [
            {
                "title": n.get("title", ""),
                "url": n.get("link", ""),
                "published_at": str(n.get("providerPublishTime", "")),
                "source": n.get("publisher", "yahoo"),
            }
            for n in news[:limit]
        ]


def _f(v):
    try:
        if v is None:
            return None
        f = float(v)
        return None if f != f else f  # NaN check
    except (TypeError, ValueError):
        return None


def _pct(now, prev):
    if now is None or prev is None or prev == 0:
        return None
    return round((float(now) - float(prev)) / float(prev) * 100, 2)
