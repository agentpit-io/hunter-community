"""N3 · `/internal/fin/instruments` 放开市场（不联网、不连库）。

一期这个端点对非 `cn` **直接返回空**并写「一期只同步 A 股」；N3 放开港美股：
每手股数（港股）/ 交易所（美股）**必须有来源**，拿不到就 `available=false`
（沿用一期「同步不上就拒绝该标的」，绝不猜）。

直接调端点函数（不经 FastAPI 请求栈）—— 鉴权只读 `request.headers`，
一个带 headers 的桩对象就够；这样不依赖 `HUNTER_INTERNAL_KEY` 的注入时机。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.routers import fin_data as router
from app.services import fin_data
from app.services.gm import findata_db

_REPO_ROOT = Path(__file__).resolve().parents[3]
_HK_CSV = _REPO_ROOT / "data" / "hk_master.csv"


class _Req:
    headers = {"X-Hunter-Internal-Key": "k"}


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setattr(router, "_INTERNAL_KEY", "k")


def _hk_csv(monkeypatch):
    if not _HK_CSV.exists():
        pytest.skip("仓库里没有 data/hk_master.csv")
    monkeypatch.setattr(findata_db, "_HK_CSV", str(_HK_CSV))
    monkeypatch.setattr(findata_db, "_hk_csv_cache", None)


def test_hk_codes_are_resolved_with_official_lot_sizes(monkeypatch):
    _hk_csv(monkeypatch)
    out = router.get_instruments(_Req(), codes="00700,00005", market="hk", limit=2000)
    assert out["market"] == "hk" and out["source_count"] == 2
    items = {i["code"]: i for i in out["items"]}
    assert items["00700"]["available"] and items["00700"]["lot_size"] == 100
    assert items["00005"]["lot_size"] == 400
    assert items["00700"]["exchange"] == "HKEX"
    assert out["lot_source"] == "hkex_listofsecurities"


def test_hk_code_not_in_official_list_is_unavailable(monkeypatch):
    monkeypatch.setattr(fin_data, "_hk_master_row", lambda c: None)
    out = router.get_instruments(_Req(), codes="99999", market="hk", limit=2000)
    assert out["items"][0]["available"] is False
    assert "每手" in out["items"][0]["reason"]


def test_us_codes_use_gateway_exchange(monkeypatch):
    monkeypatch.setattr(fin_data, "_us_exchange_and_name",
                        lambda c: ("NASDAQ", "Apple Inc.") if c == "AAPL" else (None, None))
    out = router.get_instruments(_Req(), codes="AAPL,ZZZZ", market="us", limit=2000)
    items = {i["code"]: i for i in out["items"]}
    assert items["AAPL"]["available"] and items["AAPL"]["lot_size"] == 1
    assert items["AAPL"]["limit_up_pct"] is None      # 美股无涨跌幅 → NULL
    assert items["ZZZZ"]["available"] is False


def test_empty_universe_is_reported_not_guessed(monkeypatch):
    """没有本地清单时**如实说**，不拿代码形态硬凑一批出来。"""
    monkeypatch.setattr(router, "_universe_codes", lambda market, limit: [])
    out = router.get_instruments(_Req(), codes="", market="us", limit=2000)
    assert out["items"] == [] and out["universe"] == "none"
    assert "codes" in out["note"]


def test_unknown_market_returns_empty_with_note():
    out = router.get_instruments(_Req(), codes="", market="jp", limit=2000)
    assert out["items"] == [] and "未知市场" in out["note"]
