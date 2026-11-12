"""M6 · 总开关与策略切换在 decide 时点的行为（假 HTTP 传输，不连网不连库）。

盯住三件会静默出错的事：

1. **总开关为 false 时不得产出任何命令** —— 这是「总开关真的能停」的技术兑现点。
   若它只是「下单后被拒」，`fin_order` 里会多出一行被拒委托，用户看到的是「它还在动」。
2. **判定放在出意图之前**（不是提交前再查一次）：出意图与提交是两个 Activity，
   中间可能隔着重试；放在提交侧，重试读到的可能是已经恢复的开关。
3. **策略 key 取 `fin_param.strategies` 里标了 active 的那一条**，不是写死的
   `FIN_SAMPLE_STRATEGY_KEY`；没有标记时回落到第一条。

    cd apps/fin-worker && python -m pytest tests/test_switch_and_strategy.py -q
"""
from __future__ import annotations

import httpx
import pytest

from app import activities
from app.bridge.paper import PaperClient

BASE_PARAM = {
    "max_position_pct": "0.20",
    "max_positions": 4,
    "strategies": [
        {"key": "ma_momentum", "name": "均线动量", "version": "v1", "params": {"fast": 5}},
        {"key": "volume_breakout", "name": "量价突破", "version": "v2", "params": {"window": 20}},
    ],
}


def _install(monkeypatch, project: dict, param: dict):
    """打假 paper：GET /api/v1/projects/{id} 回一份固定的项目视图。"""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "/api/v1/projects/" in str(request.url):
            return httpx.Response(200, json={"project": project, "param": param})
        raise AssertionError(f"用例没预料到的请求：{request.method} {request.url}")

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(activities, "PaperClient", lambda *a, **kw: PaperClient(
        base_url="http://paper.test", key="k",
        client=httpx.Client(transport=transport)))


PROJECT = {"project_id": "prj_x", "tier": "manage", "version": 3, "initial_capital": "100000"}


def _req() -> dict:
    return {"project_id": "prj_x", "trade_date": "2026-10-09", "point": "0930",
            "now": "2026-10-09T09:30:00+08:00"}


def test_switch_off_produces_no_command(monkeypatch):
    _install(monkeypatch, PROJECT, {**BASE_PARAM, "auto_enabled": False})
    out = activities.build_decision(_req())
    assert out["halted"] is True
    assert out["command"] is None
    assert out["idempotency_key"] is None
    assert out["decision"] is None
    assert "auto_enabled" in out["reason"]


def test_switch_on_produces_command(monkeypatch):
    _install(monkeypatch, PROJECT, {**BASE_PARAM, "auto_enabled": True})
    out = activities.build_decision(_req())
    assert out.get("halted") is not True
    assert out["command"]["code"] == "601398"
    assert out["command"]["intent_ref"]["strategy_key"] == "ma_momentum"
    assert out["idempotency_key"].startswith("fin-order:prj_x:2026-10-09:0930:")


def test_switch_missing_treated_as_on(monkeypatch):
    """字段缺失（老库没补列）按「开着」处理 —— 与 `DEFAULT true` 一致。"""
    _install(monkeypatch, PROJECT, dict(BASE_PARAM))
    out = activities.build_decision(_req())
    assert out.get("halted") is not True


def test_active_strategy_marks_and_fallback():
    # 标了 active 的优先
    param = {"strategies": [
        {"key": "a", "version": "1"}, {"key": "b", "version": "2", "active": True}]}
    assert activities._active_strategy(param)["key"] == "b"
    # 没标 → 回落第一条
    assert activities._active_strategy({"strategies": [{"key": "a"}, {"key": "b"}]})["key"] == "a"
    # 空 / None → None
    assert activities._active_strategy({"strategies": []}) is None
    assert activities._active_strategy(None) is None


def test_strategy_key_comes_from_param(monkeypatch):
    """切换策略之后，下一次决策用的就是新策略 key。"""
    param = {**BASE_PARAM, "auto_enabled": True, "strategies": [
        {"key": "ma_momentum", "version": "v1", "active": True},
        {"key": "volume_breakout", "version": "v2"},
    ]}
    _install(monkeypatch, PROJECT, param)
    out = activities.build_decision(_req())
    assert out["decision"]["strategy_key"] == "ma_momentum"
    assert out["decision"]["strategy_version"] == "v1"


def test_active_rule_matches_api_copy():
    """与 api 侧 `control.active_strategy` 的同判检查。

    两份实现（两个容器、两套依赖，不能互相 import）必须给出同一个答案 ——
    规则只有一句「有 active 用它，没有用第一条」。这里对同一组输入逐个比。
    """
    import importlib.util
    from pathlib import Path

    api_path = Path(__file__).resolve().parents[2] / "api" / "app" / "services" / "fin" / "control.py"
    assert api_path.exists(), f"读不到 api 侧实现：{api_path}"
    src = api_path.read_text(encoding="utf-8")
    # 不 import（api 依赖不在本容器里）：把那个纯函数抠出来执行一次。
    start = src.index("def active_strategy(")
    end = src.index("def set_strategy(")
    ns: dict = {}
    exec(src[start:end], ns)                      # noqa: S102 —— 只跑仓库里自己的纯函数
    api_active = ns["active_strategy"]

    cases = [
        {"strategies": [{"key": "a"}, {"key": "b", "active": True}]},
        {"strategies": [{"key": "a"}, {"key": "b"}]},
        {"strategies": []},
        None,
        {},
    ]
    for c in cases:
        mine = activities._active_strategy(c)
        theirs = api_active(c)
        assert (mine or {}).get("key") == (theirs or {}).get("key"), f"两份实现不同判：{c}"
