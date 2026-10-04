"""Activities —— 假 HTTP 传输，覆盖日历三分支 / 下单 / 收盘顺序 / 日历同步。"""

from __future__ import annotations

import httpx
import pytest

from app import activities
from app.bridge.hunter_api import HunterApiClient
from app.bridge.paper import IdempotencyConflict, PaperClient


def _install(monkeypatch, handler):
    """把 activities 里的 PaperClient / HunterApiClient 换成打假传输的实例。"""
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    transport = httpx.MockTransport(wrapped)

    def paper_factory(*a, **kw):
        return PaperClient(base_url="http://paper.test", internal_key="k",
                           client=httpx.Client(transport=transport))

    def api_factory(*a, **kw):
        return HunterApiClient(base_url="http://api.test", internal_key="k",
                               client=httpx.Client(transport=transport))

    monkeypatch.setattr(activities, "PaperClient", paper_factory)
    monkeypatch.setattr(activities, "HunterApiClient", api_factory)
    return seen


# ── 交易日历三分支 ────────────────────────────────────────────────────────

def test_read_calendar_known_trading(monkeypatch):
    _install(monkeypatch, lambda r: httpx.Response(200, json={
        "trade_date": "2026-10-09", "is_trading": True, "sessions": [{"open": "09:30"}]}))
    out = activities.read_calendar({"trade_date": "2026-10-09"})
    assert out == {"known": True, "trading": True, "sessions": [{"open": "09:30"}],
                   "trade_date": "2026-10-09", "market": "CN_A", "note": None}


def test_read_calendar_uses_the_requested_market(monkeypatch):
    """港美股日历按 (market, date) 读 —— 请求里带上 market。"""
    seen = {}

    def h(req):
        seen["market"] = req.url.params.get("market")
        return httpx.Response(200, json={"trade_date": "2026-10-09", "is_trading": True,
                                         "sessions": [{"open": "09:30", "close": "16:00"}]})

    _install(monkeypatch, h)
    out = activities.read_calendar({"trade_date": "2026-10-09", "market": "US"})
    assert out["market"] == "US"
    assert seen["market"] == "US"


def test_read_calendar_known_non_trading(monkeypatch):
    _install(monkeypatch, lambda r: httpx.Response(200, json={
        "trade_date": "2026-10-01", "is_trading": False, "sessions": [], "note": "国庆"}))
    out = activities.read_calendar({"trade_date": "2026-10-01"})
    assert out["known"] is True and out["trading"] is False


def test_read_calendar_missing_is_unknown(monkeypatch):
    _install(monkeypatch, lambda r: httpx.Response(404, json={"detail": "没有"}))
    out = activities.read_calendar({"trade_date": "2026-10-09"})
    assert out["known"] is False
    assert out["trading"] is False
    assert "未知" in out["note"]


# ── 日历同步 ──────────────────────────────────────────────────────────────

def test_sync_calendar_writes_every_day_in_window(monkeypatch):
    def h(req):
        if req.url.path == "/api/internal/calendar/trading-days":
            return httpx.Response(200, json={"trading_days": ["2026-10-08", "2026-10-09"]})
        if req.url.path.startswith("/api/v1/market-calendar/"):
            return httpx.Response(200, json={"trade_date": req.url.path.rsplit("/", 1)[-1]})
        raise AssertionError(req.url.path)

    _install(monkeypatch, h)
    out = activities.sync_calendar({"trade_date": "2026-10-09",
                                    "lookback_days": 2, "lookahead_days": 3})
    assert out["ok"] is True
    assert out["trading_days"] == 2
    # 窗口 = 2 + 1 + 3 = 6 天，每天一行
    assert out["written"] == 6


def test_sync_calendar_api_down_returns_not_ok(monkeypatch):
    _install(monkeypatch, lambda r: httpx.Response(503, text="akshare 不可用"))
    out = activities.sync_calendar({"trade_date": "2026-10-09"})
    assert out["ok"] is False
    assert "error" in out


# ── 下单 ──────────────────────────────────────────────────────────────────

def _project_response(version=0):
    return httpx.Response(200, json={
        "project": {"project_id": "prj_1", "tier": "play", "version": version,
                    "initial_capital": "10000.0000"},
        "param": {"max_position_pct": "0.200000", "max_positions": 3},
        "cash": {"available": "10000.0000", "frozen": "0.0000"},
        "positions": [],
    })


def _fake_paper_with_idempotency(version_holder: dict):
    """一个**带幂等语义**的假 paper：同键同内容 → 返原回执；同键不同内容 → 409。

    这正是 `fin_idempotency` 的行为。用它能把「重试会不会重复下单」测出来。
    """
    import hashlib
    import json

    store: dict[str, dict] = {}

    def h(req):
        if req.method == "GET" and req.url.path == "/api/v1/projects/prj_1":
            return _project_response(version=version_holder["v"])
        if req.method == "POST" and req.url.path == "/api/v1/orders":
            body = json.loads(req.content)
            key = body["idempotency_key"]
            digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
            prev = store.get(key)
            if prev:
                if prev["digest"] != digest:
                    return httpx.Response(409, json={"detail": "同键不同内容，拒绝覆盖"})
                return httpx.Response(200, json={**prev["receipt"], "idempotent_replay": True})
            receipt = {"status": "filled", "trade_id": f"trd_{len(store) + 1}",
                       "order_id": f"ord_{len(store) + 1}"}
            store[key] = {"digest": digest, "receipt": receipt, "body": body}
            version_holder["v"] += 1          # 成交 → 账户版本 +1
            return httpx.Response(200, json=receipt)
        raise AssertionError(f"{req.method} {req.url.path}")

    return h, store


def test_build_decision_is_deterministic(monkeypatch):
    _install(monkeypatch, lambda r: _project_response(version=0))
    req = {"project_id": "prj_1", "trade_date": "2026-10-09", "point": "0930",
           "now": "2026-10-09T09:30:00+08:00"}
    a = activities.build_decision(req)
    b = activities.build_decision(req)
    assert a["idempotency_key"] == b["idempotency_key"]
    assert a["command"] == b["command"]
    body = a["command"]
    assert body["code"] == "601398" and body["side"] == "buy"
    assert body["price_type"] == "market" and body["qty"] == 100     # play 档
    assert body["expected_version"] == 0
    assert body["intent_ref"]["strategy"] == "sample-fixed@1.0"


def test_build_decision_manage_tier_uses_larger_lot(monkeypatch):
    _install(monkeypatch, lambda r: httpx.Response(200, json={
        "project": {"project_id": "prj_1", "tier": "manage", "version": 2},
        "param": None, "cash": {}, "positions": []}))
    built = activities.build_decision({"project_id": "prj_1", "trade_date": "2026-10-09",
                                       "point": "0930", "now": "2026-10-09T09:30:00+08:00"})
    assert built["command"]["qty"] == 1000
    assert built["command"]["expected_version"] == 2


def test_build_decision_unknown_project_raises(monkeypatch):
    _install(monkeypatch, lambda r: httpx.Response(404, json={"detail": "没有"}))
    with pytest.raises(LookupError):
        activities.build_decision({"project_id": "nope", "trade_date": "2026-10-09",
                                   "point": "0930", "now": "2026-10-09T09:30:00+08:00"})


def test_submit_is_idempotent_when_the_frozen_command_is_replayed(monkeypatch):
    """**崩溃续跑不重复下单**的核心用例。

    模拟真实场景：第一次提交成功、Worker 在返回前死掉 → Temporal 重试
    `submit_decision`，提交的是**历史里那份冻结命令**。
    账户版本此时已经因为第一次成交变成了 1，但命令里还是 0 —— 命令没变，
    于是 paper 认出「同键同内容」，返回原回执，**不再开第二笔**。
    """
    version = {"v": 0}
    handler, store = _fake_paper_with_idempotency(version)
    _install(monkeypatch, handler)

    built = activities.build_decision({"project_id": "prj_1", "trade_date": "2026-10-09",
                                       "point": "0930", "now": "2026-10-09T09:30:00+08:00"})
    assert built["command"]["expected_version"] == 0

    first = activities.submit_decision({"command": built["command"],
                                        "idempotency_key": built["idempotency_key"]})
    assert first["order_status"] == "filled"
    assert version["v"] == 1          # 第一次成交把账户版本推到了 1

    # Temporal 重试：同一条冻结命令再提交一次
    second = activities.submit_decision({"command": built["command"],
                                         "idempotency_key": built["idempotency_key"]})
    assert second["trade_id"] == first["trade_id"]       # 原回执，不是新成交
    assert len(store) == 1                               # 幂等表里只有一条


def test_rebuilding_the_decision_after_a_fill_would_conflict(monkeypatch):
    """反证：**如果**把「读版本」和「提交」合在一起（M4 第一版就是这么写的），
    重试会读到新版本 → 同一个幂等键配不同内容 → paper 回 409，工作流永久失败。

    这条用例把这个陷阱钉住，说明为什么必须拆成两个 Activity。
    """
    version = {"v": 0}
    handler, _ = _fake_paper_with_idempotency(version)
    _install(monkeypatch, handler)

    req = {"project_id": "prj_1", "trade_date": "2026-10-09", "point": "0930",
           "now": "2026-10-09T09:30:00+08:00"}
    first = activities.build_decision(req)
    activities.submit_decision({"command": first["command"],
                                "idempotency_key": first["idempotency_key"]})

    rebuilt = activities.build_decision(req)            # 重读 → 版本已是 1
    assert rebuilt["idempotency_key"] == first["idempotency_key"]
    with pytest.raises(IdempotencyConflict):
        activities.submit_decision({"command": rebuilt["command"],
                                    "idempotency_key": rebuilt["idempotency_key"]})


def test_submit_hold_seconds_places_order_once_then_returns(monkeypatch):
    """故障注入开关：保持期间只下一单，保持够就返回（心跳在非 Activity 上下文里被吞掉）。"""
    version = {"v": 0}
    handler, store = _fake_paper_with_idempotency(version)
    _install(monkeypatch, handler)
    built = activities.build_decision({"project_id": "prj_1", "trade_date": "2026-10-09",
                                       "point": "0930", "now": "2026-10-09T09:30:00+08:00"})
    out = activities.submit_decision({"command": built["command"],
                                      "idempotency_key": built["idempotency_key"],
                                      "hold_seconds": 1})
    assert out["order_status"] == "filled"
    assert len(store) == 1


# ── 收盘顺序 ──────────────────────────────────────────────────────────────

def test_close_day_order_is_expire_then_valuation_then_recon(monkeypatch):
    paths: list[str] = []

    def h(req):
        paths.append(req.url.path)
        return httpx.Response(200, json={"ok": True})

    _install(monkeypatch, h)
    activities.close_day({"project_id": "prj_1", "now": "2026-10-09T15:30:00+08:00"})
    assert paths == [
        "/api/v1/projects/prj_1/orders/expire",
        "/api/v1/projects/prj_1/valuation",
        "/api/v1/projects/prj_1/recon",
    ]


# ── 业务检查点 ────────────────────────────────────────────────────────────

def test_begin_point_job_submits_then_marks_running(monkeypatch):
    paths: list[tuple[str, str]] = []

    def h(req):
        paths.append((req.method, req.url.path))
        if req.url.path == "/api/v1/jobs":
            return httpx.Response(200, json={"job_id": "job_1", "created": True})
        return httpx.Response(200, json={"job_id": "job_1"})

    _install(monkeypatch, h)
    out = activities.begin_point_job({"project_id": "prj_1", "trade_date": "2026-10-09",
                                      "point": "0930", "at": "09:30", "now": "n"})
    assert out["job_id"] == "job_1"
    assert out["idempotency_key"] == "fin-job:prj_1:2026-10-09:0930"
    assert ("POST", "/api/v1/jobs") in paths
    assert ("POST", "/api/v1/jobs/job_1/running") in paths
    assert ("POST", "/api/v1/jobs/job_1/checkpoint") in paths


# ── P2 · 按市场出价型：只接限价单的市场（港美股） ────────────────────────────

_MARKET_RULES = [
    {"market": "CN_A", "currency": "CNY", "market_order_supported": True},
    {"market": "HK", "currency": "HKD", "market_order_supported": False},
    {"market": "US", "currency": "USD", "market_order_supported": False},
]


def _hk_handler(seen=None):
    def h(req):
        p = req.url.path
        if p == "/api/v1/projects/prj_hk":
            assert req.url.params.get("market") == "HK"     # 必须带 market
            return httpx.Response(200, json={
                "project": {"project_id": "prj_hk", "tier": "manage", "version": 0,
                            "market_scope": "HK"},
                "param": {"max_position_pct": "0.25", "max_positions": 4},
                "cash": {"available": "500000.0000", "frozen": "0.0000"},
                "positions": [], "market": "HK", "currency": "HKD",
            })
        if p == "/api/v1/market-rules":
            return httpx.Response(200, json={"items": _MARKET_RULES})
        if p == "/api/v1/instruments/00700":
            return httpx.Response(200, json={"code": "00700", "lot_size": 100, "market": "HK"})
        if p == "/api/internal/fin/quote/00700":
            return httpx.Response(200, json={"code": "00700", "market": "HK",
                                             "last_price": "421.2", "prev_close": "431",
                                             "event_time": "2026-10-02T16:08:10+08:00"})
        raise AssertionError(f"{req.method} {p}")

    return h


def test_build_decision_hk_emits_limit_order_at_real_quote(monkeypatch):
    """港美股（`market_order_supported=false`）→ **限价单**，限价 = 行情源真实报价。

    这就是「调度路径不给 code 也能出单」的关键一步：老代码出市价单，被 paper
    「港美股只接限价单」拒掉（HK 一笔都成交不了）。
    """
    _install(monkeypatch, _hk_handler())
    built = activities.build_decision({"project_id": "prj_hk", "trade_date": "2026-10-02",
                                       "point": "HK-0930", "market": "HK",
                                       "now": "2026-10-02T16:08:10+08:00"})
    body = built["command"]
    assert body["code"] == "00700"
    assert body["price_type"] == "limit"
    assert body["limit_price"] == "421.2"
    # R12：数量按 max_position_pct 定量 —— 500000 × 0.25 × 0.995 ÷ (421.2 × 100) → 2 手 = 200 股
    assert body["qty"] == 200


def test_build_decision_hk_without_budget_halts(monkeypatch):
    """可用资金买不起一手 → **halted**（不出委托，也不报错）。"""

    def h(req):
        if req.url.path == "/api/v1/projects/prj_hk":
            return httpx.Response(200, json={
                "project": {"project_id": "prj_hk", "tier": "play", "version": 0},
                "param": None, "cash": {"available": "10000.0000", "frozen": "0.0000"},
                "positions": [], "market": "HK"})
        return _hk_handler()(req)

    _install(monkeypatch, h)
    built = activities.build_decision({"project_id": "prj_hk", "trade_date": "2026-10-02",
                                       "point": "HK-0930", "market": "HK",
                                       "now": "2026-10-02T16:08:10+08:00"})
    assert built["halted"] is True
    assert "买不起" in built["reason"]
    assert built["command"] is None


def test_build_decision_multi_without_market_raises(monkeypatch):
    """`MULTI` 项目不传 market → paper 回 400 → 翻成 ValueError（**不猜 A 股**）。

    P1 遗留：老代码 `project["market_scope"] or "CN_A"` 会得到字面量 "MULTI"，
    `canonical_market` 抛 UnknownMarket。
    """
    import pytest as _pytest

    def h(req):
        if req.url.path == "/api/v1/projects/prj_multi":
            return httpx.Response(400, json={"detail": "该项目有多个市场，请指定 market"})
        raise AssertionError(req.url.path)

    _install(monkeypatch, h)
    with _pytest.raises(ValueError, match="多市场"):
        activities.build_decision({"project_id": "prj_multi", "trade_date": "2026-10-09",
                                   "point": "0930",
                                   "now": "2026-10-09T09:30:00+08:00"})


def test_build_decision_cn_a_still_market_order(monkeypatch):
    """A 股不得回归：价型仍是 market、数量仍是档位固定手数、只读项目不读行情。"""
    seen = []

    def h(req):
        seen.append(req.url.path)
        if req.url.path == "/api/v1/projects/prj_1":
            return _project_response(version=0)
        if req.url.path == "/api/v1/market-rules":
            return httpx.Response(200, json={"items": _MARKET_RULES})
        raise AssertionError(req.url.path)

    _install(monkeypatch, h)
    built = activities.build_decision({"project_id": "prj_1", "trade_date": "2026-10-09",
                                       "point": "0930", "market": "CN_A",
                                       "now": "2026-10-09T09:30:00+08:00"})
    assert built["command"]["price_type"] == "market"
    assert built["command"]["qty"] == 100
    # A 股**不读行情**（不取参考价、不取每手）
    assert not any("/quote/" in p or "/instruments/" in p for p in seen)
