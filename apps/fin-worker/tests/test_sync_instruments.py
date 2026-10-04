"""标的元数据同步（M7 · M-20）—— 三条不能破的：

1. 同步由 **fin-worker 的定时任务**做（不是迁移）；
2. **同步不上就不写**（`available=false` 的那条跳过 → 风控会拒绝该标的）；
3. 失败如实报（api 不可用 → `ok=False`）。
"""

from __future__ import annotations

import httpx

from app import activities
from app.bridge.hunter_api import HunterApiClient
from app.bridge.paper import PaperClient


def _install(monkeypatch, handler):
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    transport = httpx.MockTransport(wrapped)

    monkeypatch.setattr(activities, "PaperClient", lambda *a, **kw: PaperClient(
        base_url="http://paper.test", key="k", client=httpx.Client(transport=transport)))
    monkeypatch.setattr(activities, "HunterApiClient", lambda *a, **kw: HunterApiClient(
        base_url="http://api.test", key="k", client=httpx.Client(transport=transport)))
    return seen


_OK = {"code": "600519", "available": True, "name": "贵州茅台", "exchange": "SH",
       "board": "main", "is_st": False, "limit_up_pct": "0.10",
       "limit_down_pct": "0.10", "lot_size": 100,
       "source": "company_master/stock_universe"}
_BAD = {"code": "999999", "available": False,
        "reason": "无法确定交易所或板块——拒绝该标的，不猜涨跌停幅度"}


def test_available_items_are_written(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"market": "cn", "items": [_OK], "source_count": 1})
        assert request.method == "PUT" and "/api/v1/instruments/600519" in str(request.url)
        return httpx.Response(200, json={"code": "600519"})

    seen = _install(monkeypatch, handler)
    out = activities.sync_instruments({})
    assert out["ok"] is True and out["written"] == 1 and out["skipped"] == 0
    methods = [r.method for r in seen]
    assert methods == ["GET", "PUT"]


def test_unavailable_items_are_skipped_not_guessed(monkeypatch):
    """判不出的标的**不写** —— 账本里没有它，风控第 4 条才会拒绝它。"""
    written: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"market": "cn", "items": [_OK, _BAD]})
        written.append(str(request.url))
        return httpx.Response(200, json={})

    _install(monkeypatch, handler)
    out = activities.sync_instruments({})
    assert out["written"] == 1 and out["skipped"] == 1
    assert out["skipped_detail"][0]["code"] == "999999"
    assert len(written) == 1 and "600519" in written[0]   # 只有可用的那条被写


def test_api_failure_is_reported_not_swallowed(monkeypatch):
    _install(monkeypatch, lambda r: httpx.Response(500, text="boom"))
    out = activities.sync_instruments({})
    assert out["ok"] is False and out["written"] == 0
    assert "500" in out["error"]


# ── N3：市场参数按市场跑 + 港美股条目写对 market / currency ────────────────

_HK = {"code": "00700", "available": True, "name": "TENCENT", "exchange": "HKEX",
       "board": "hk_main", "is_st": False, "limit_up_pct": None, "limit_down_pct": None,
       "lot_size": 100, "market": "HK", "currency": "HKD",
       "source": "hkex_listofsecurities(data/hk_master.csv)"}
_US = {"code": "AAPL", "available": True, "name": "Apple Inc.", "exchange": "NASDAQ",
       "board": "us_main", "is_st": False, "limit_up_pct": None, "limit_down_pct": None,
       "lot_size": 1, "market": "US", "currency": "USD",
       "source": "hunter_gateway(us_quote.exchange)"}


def _capture_put(monkeypatch, payload, market):
    """跑一次同步，返回 (结果, 请求过的 URL, 最后一条 PUT 的 body)。"""
    import json

    seen: list[httpx.Request] = []
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "GET":
            assert f"market={market}" in str(request.url)
            return httpx.Response(200, json=payload)
        bodies.append(json.loads(request.content.decode()))
        return httpx.Response(200, json={})

    _install(monkeypatch, handler)
    out = activities.sync_instruments({"market": market})
    return out, seen, bodies


def test_market_is_passed_through_to_the_api(monkeypatch):
    """`market` 不再是写死的 `cn` —— 传什么就按什么同步（N3 §3）。"""
    out, seen, _ = _capture_put(monkeypatch,
                                {"market": "hk", "items": [_HK], "source_count": 1}, "hk")
    assert out["ok"] is True and out["market"] == "hk" and out["written"] == 1
    assert [r.method for r in seen] == ["GET", "PUT"]


def test_hk_item_keeps_market_currency_and_null_limits(monkeypatch):
    """港股：market=HK / currency=HKD 写下去，**涨跌幅是 None 不是 0**。"""
    _, _, bodies = _capture_put(monkeypatch,
                                {"market": "hk", "items": [_HK], "source_count": 1}, "hk")
    body = bodies[0]
    assert body["market"] == "HK" and body["currency"] == "HKD"
    assert body["lot_size"] == 100
    assert body["limit_up_pct"] is None and body["limit_down_pct"] is None
    assert body["source"].startswith("hkex_listofsecurities")


def test_us_item_written_with_lot_one(monkeypatch):
    _, _, bodies = _capture_put(monkeypatch,
                                {"market": "us", "items": [_US], "source_count": 1}, "us")
    body = bodies[0]
    assert body["market"] == "US" and body["currency"] == "USD" and body["lot_size"] == 1


def test_market_defaults_apply_when_api_omits_them(monkeypatch):
    """老形状（api 没给 market/currency）→ 按本次同步的市场补，不写成 A 股。"""
    item = {k: v for k, v in _HK.items() if k not in ("market", "currency")}
    _, _, bodies = _capture_put(monkeypatch,
                                {"market": "hk", "items": [item], "source_count": 1}, "hk")
    assert bodies[0]["market"] == "HK" and bodies[0]["currency"] == "HKD"
