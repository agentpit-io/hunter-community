"""IDataSource · unified quote/kline/news interface.

All impls (saas · akshare · yfinance) return the same shape so upper
business code doesn't need to know which backend served the data.
"""
from abc import ABC, abstractmethod


class IDataSource(ABC):
    @abstractmethod
    async def get_quote(self, code: str) -> dict:
        """Returns {code, name, price, change_pct, volume, ...}"""

    @abstractmethod
    async def get_kline(self, code: str, days: int = 30) -> dict:
        """Returns {code, ohlc: [[ts,o,h,l,c,v], ...]}"""

    @abstractmethod
    async def get_news(self, code: str, limit: int = 10) -> list[dict]:
        """Returns [{title, url, published_at, source}, ...]"""

    async def health_check(self) -> dict:
        return {"ok": True, "provider": self.__class__.__name__}
