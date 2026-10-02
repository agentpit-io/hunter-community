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
        base_url="http://paper.test", internal_key="k", client=httpx.Client(transport=transport)))
    monkeypatch.setattr(activities, "HunterApiClient", lambda *a, **kw: HunterApiClient(
        base_url="http://api.test", internal_key="k", client=httpx.Client(transport=transport)))
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
