# -*- coding: utf-8 -*-
"""档位模板的纯函数用例 —— 不连库、不联网。

     cd apps/api && PYTHONPATH=. python -m pytest tests/test_fin_tiers.py -q

逐条盯住 `03-新用户向导与操作参数方案.md` §二 / §四 / §五 / §六 的**数字**：
档位一旦和方案对不上，开户建出来的就是一笔错本金/错风控的模拟盘，而界面上
一切正常（这正是「空的比假的好」要防的那类）。所以这里把关键值全部钉死成字面量，
改方案 → 这些用例必须跟着改，不存在「悄悄漂移」。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# ── sys.path 归一 ──────────────────────────────────────────────────────
# 本仓 `apps/api/__init__.py` 是个**空文件**，于是 `/app` 本身是一个可导入的包
# `app`；而共享 `conftest.py` 会把 `/`（`_HERMES_ROOT = dirname(_ROOT)`）插进
# sys.path —— 两者一撞，pytest 进程内 `import app` 命中的是 `/app` 目录包
# （里面只有 `conftest`），不是真正的 `/app/app`，报 `No module named 'app.services'`。
# 脚本式用例跑在子进程里（sys.path[0] 是 tests/）不会撞上；pytest 进程内收集会。
# 所以这里把会撞车的根目录项摘掉、确保 API 根在最前，并清掉可能已被冒名导入的 `app`。
_API_ROOT = Path(__file__).resolve().parents[1]
for _p in ("", "/", str(_API_ROOT)):
    while _p in sys.path:
        sys.path.remove(_p)
sys.path.insert(0, str(_API_ROOT))
_bad_app = sys.modules.get("app")
if _bad_app is not None and Path(getattr(_bad_app, "__file__", "") or "").parent == _API_ROOT:
    del sys.modules["app"]

from app.services.fin import tiers  # noqa: E402
from app.services.fin import strategy as strategy_svc  # noqa: E402


def test_三档顺序与标签():
    assert tiers.TIER_ORDER == ("play", "manage", "operate")
    assert tiers.TIER_LABEL == {
        "play": "个人玩玩",
        "manage": "个人资产管理",
        "operate": "资产运营",
    }


def test_本金与档位一一对应():
    # 03 §二 2.2 第一行：¥10,000 / ¥100,000 / ¥1,000,000
    assert tiers.build_template("play")["initial_capital"] == 10_000
    assert tiers.build_template("manage")["initial_capital"] == 100_000
    assert tiers.build_template("operate")["initial_capital"] == 1_000_000


def test_三档差异化参数逐字段():
    # 03 §二 2.2 参数总表（与方案逐行对齐）
    expect = {
        "play": dict(max_positions=2, max_position_pct=0.35, min_order_amount=3_000,
                     max_price=30, stop_loss_pct=-0.04, take_profit_pct=0.06,
                     daily_max_new=1, daily_max_orders=2,
                     account_drawdown_halt_pct=-0.08, daily_loss_halt_pct=-0.02),
        "manage": dict(max_positions=4, max_position_pct=0.25, min_order_amount=20_000,
                       max_price=200, stop_loss_pct=-0.04, take_profit_pct=0.05,
                       daily_max_new=2, daily_max_orders=4,
                       account_drawdown_halt_pct=-0.08, daily_loss_halt_pct=-0.025),
        "operate": dict(max_positions=8, max_position_pct=0.15, min_order_amount=50_000,
                        max_price=1500, stop_loss_pct=-0.03, take_profit_pct=0.04,
                        daily_max_new=3, daily_max_orders=8,
                        account_drawdown_halt_pct=-0.06, daily_loss_halt_pct=-0.03),
    }
    for tier, fields in expect.items():
        tpl = tiers.build_template(tier)
        for k, v in fields.items():
            assert tpl[k] == v, f"{tier}.{k} = {tpl[k]!r}，应为 {v!r}"


def test_持有天数固定三日():
    # 03 §二 2.2「最大持有天数 3 日（本次固定为短线）」
    for tier in tiers.TIER_ORDER:
        assert tiers.build_template(tier)["hold_days_max"] == 3


def test_流动性门槛():
    # 03 §4.3：最小日均成交额 ≥ 1 亿元（三档同）；单只占昨日成交额上限 ①关/②1%/③0.5%
    assert tiers.build_template("play")["liquidity_min_amount"] == 100_000_000
    assert tiers.build_template("manage")["liquidity_min_amount"] == 100_000_000
    assert tiers.build_template("operate")["liquidity_min_amount"] == 100_000_000
    assert tiers.build_template("play")["liquidity_max_participation"] is None
    assert tiers.build_template("manage")["liquidity_max_participation"] == 0.01
    assert tiers.build_template("operate")["liquidity_max_participation"] == 0.005


def test_科创板只对上位档开放():
    # 03 §4.1：科创板默认关，且只对 ③ 资产运营开放；创业板三档均开；北交所/ST/次新一律关
    for tier, star in (("play", False), ("manage", False), ("operate", True)):
        flags = tiers.build_template(tier)["board_flags"]
        assert flags["star"] is star, f"{tier} 科创板开关应为 {star}"
        assert flags["chinext"] is True
        assert flags["main"] is True
        assert flags["bse"] is False
        assert flags["st"] is False
        assert flags["sub_new"] is False


def test_策略数量与开放清单():
    # 03 §5.2 末行：① 2 个 / ② 4 个 / ③ 5 个（全部）
    keys = lambda t: [s["key"] for s in tiers.build_template(t)["strategies"]]  # noqa: E731
    assert keys("play") == ["ma_momentum", "oversold_rebound"]
    assert keys("manage") == ["ma_momentum", "oversold_rebound", "volume_breakout", "ma_pullback"]
    assert keys("operate") == ["ma_momentum", "oversold_rebound", "volume_breakout",
                               "ma_pullback", "fund_flow"]


def test_策略默认参数照抄_5_3():
    # 03 §5.3 每个策略自带默认参数（周期、阈值）
    want = {
        "ma_momentum": {"fast": 5, "slow": 10, "vol_mult": 1.5},
        "oversold_rebound": {"ma": 20, "deviation": -0.08, "confirm_days": 1},
        "volume_breakout": {"window": 20, "vol_mult": 2.0},
        "ma_pullback": {"fast": 5, "mid": 10, "slow": 20, "tolerance": 0.01},
        "fund_flow": {"min_days": 3},
    }
    for s in tiers.build_template("operate")["strategies"]:
        assert s["params"] == want[s["key"]], s["key"]
        # L04：`version` 从自由字符串 `"v1"` 换成**登记表里的稳定版本键**（内容哈希推导）。
        # 同一份定义在任何进程算出来都是同一个键；键与内置登记行对得上（见 test_strategy_registry）。
        assert s["version"].startswith("strv_") and len(s["version"]) == len("strv_") + 24
        assert s["version"] == strategy_svc.version_id_of(
            strategy_key=s["key"], name=s["name"], params=s["params"],
            source_ref=strategy_svc.BUILTIN_SOURCE_REF)


def test_行业偏好默认不限():
    # 03 §4.2：默认全不选 = 不限行业
    for tier in tiers.TIER_ORDER:
        assert tiers.build_template(tier)["sector_prefs"] == []


def test_未知档位报错():
    with pytest.raises(ValueError):
        tiers.build_template("rich")


def test_all_templates_三档():
    tpls = tiers.all_templates()
    assert [t["tier"] for t in tpls] == ["play", "manage", "operate"]
