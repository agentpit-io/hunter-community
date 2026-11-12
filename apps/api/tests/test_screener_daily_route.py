"""日K路由必须允许未登记的全市场股票走日线服务。"""
import asyncio
from unittest.mock import patch


def test_unregistered_symbol_reaches_daily_service():
    from app.routers import kline
    rows = [{"ts": "2026-10-08", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 1}]
    with patch.object(kline, "to_symbol", return_value=None), patch.object(kline, "get_kline", return_value=rows) as get:
        assert asyncio.run(kline.get_kline_route("PLTR", limit=250)) == rows
        get.assert_called_once_with("PLTR", period="daily", limit=250)


def test_daily_source_precedes_watchlist_mapping():
    from app.services import finance_data_client as fd, market_source
    rows = [{"ts": "2026-10-08", "close": 198.78}]
    with patch.object(market_source, "daily", return_value=rows) as daily, patch.object(fd, "_get") as upstream:
        assert fd.get_kline("PLTR", limit=250) == rows
        daily.assert_called_once_with("PLTR", limit=250)
        upstream.assert_not_called()


def test_a_share_does_not_use_foreign_daily_source():
    from app.services import finance_data_client as fd, market_source
    with patch.object(market_source, "daily") as daily, patch.object(fd, "_get", return_value=[]):
        assert fd.get_kline("600519") == []
        daily.assert_not_called()


