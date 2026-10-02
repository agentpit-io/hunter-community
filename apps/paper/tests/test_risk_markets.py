"""风控六条 · **三市场矩阵**（`CN_A` / `HK` / `US`，纯函数，不连库）。

这是 N2 的出口标准 ①：六条规则**每个市场各有一套用例**。规则逻辑一条没改，
改的是参数来源 —— 所以这里把 `fin_market_rule` 三行（`db/migrations/0030`）的
参数逐市场喂进六条规则，断言各自的行为差异。

⚠️ 三个 `_MODEL_*` 是**代表性的费用模型口径，不是生产数据**：港美股费率
（SEC 规费 / HKEX 各费目）必须按公布口径落 `fin_fee_model` 并标 `effective_from`
（N1 报告决策：本期不落数）。这里的用例只验证**「按市场取参、按表决定印花税方向」
这条机制**，不代表线上费率。
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.market_time import market_tz
from app.risk import fee as fee_mod
from app.risk import funds as funds_mod
from app.risk import lot as lot_mod
from app.risk import price_limit as limit_mod
from app.risk import session as session_mod
from app.risk import t1 as t1_mod
from app.risk.engine import RiskInputs, evaluate

# ── 三市场规则（与 db/migrations/0030 的三行逐项一致）────────────────────────
RULE = {
    "CN_A": {"market": "CN_A", "currency": "CNY", "timezone": "Asia/Shanghai",
             "sessions": [{"open": "09:30", "close": "11:30"},
                          {"open": "13:00", "close": "15:00"}],
             "sellable_rule": "t_plus_n", "sellable_days": 1,
             "lot_rule": "fixed", "lot_fixed": 100, "price_limit_mode": "pct"},
    "HK": {"market": "HK", "currency": "HKD", "timezone": "Asia/Hong_Kong",
           "sessions": [{"open": "09:30", "close": "12:00"},
                        {"open": "13:00", "close": "16:00"}],
           "sellable_rule": "same_day", "sellable_days": 0,
           "lot_rule": "per_instrument", "lot_fixed": None, "price_limit_mode": "none"},
    "US": {"market": "US", "currency": "USD", "timezone": "America/New_York",
           "sessions": [{"open": "09:30", "close": "16:00"}],
           "sellable_rule": "same_day", "sellable_days": 0,
           "lot_rule": "one", "lot_fixed": 1, "price_limit_mode": "none"},
}

# 代表性费用模型（**非生产数据**，见文件头）。
_MODEL = {
    "CN_A": {"version": "fee-cn-a-v1", "market": "CN_A", "currency": "CNY",
             "commission_pct": Decimal("0.00025"), "commission_min": Decimal("5.00"),
             "stamp_tax_pct": Decimal("0.0005"), "transfer_fee_pct": Decimal("0.00001"),
             "stamp_side": "sell"},
    "HK": {"version": "fee-hk-v1", "market": "HK", "currency": "HKD",
           "commission_pct": Decimal("0.00025"), "commission_min": Decimal("5.00"),
           "stamp_tax_pct": Decimal("0.001"), "transfer_fee_pct": Decimal("0.0001"),
           "stamp_side": "both"},
    "US": {"version": "fee-us-v1", "market": "US", "currency": "USD",
           "commission_pct": Decimal("0.0001"), "commission_min": Decimal("1.00"),
           "stamp_tax_pct": Decimal("0"), "transfer_fee_pct": Decimal("0.00002"),
           "stamp_side": "none"},
}


def _cal(market, *, trading=True, sessions=None):
    return {"market": market, "is_trading": trading, "calendar_source": "test",
            "sessions": sessions if sessions is not None else RULE[market]["sessions"]}


def _at(hhmm: str, market: str, day=(2026, 10, 2)) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime(*day, h, m, tzinfo=market_tz(market))


def _base(market: str, **kw):
    params = dict(
        at=_at("10:00", market), side="buy", qty=100, price=Decimal("10.00"),
        calendar=_cal(market), instrument={"limit_up_pct": Decimal("0.10"),
                                                    "limit_down_pct": Decimal("0.10"),
                                                    "lot_size": 100},
        fee_model=_MODEL[market], prev_close=Decimal("10.00"),
        available=Decimal("100000"), position_qty=0, sellable_qty=0,
        market=market, market_rule=RULE[market],
    )
    params.update(kw)
    return RiskInputs(**params)


# ══ 第 1 条 · 交易时段 ══════════════════════════════════════════════════════
def test_session_cn_a_windows():
    cal = _cal("CN_A")
    assert session_mod.check_session(cal, _at("10:00", "CN_A"), market="CN_A").ok
    assert not session_mod.check_session(cal, _at("12:00", "CN_A"), market="CN_A").ok   # 午休
    assert session_mod.check_session(cal, _at("14:00", "CN_A"), market="CN_A").ok


def test_session_hk_windows():
    cal = _cal("HK")
    assert session_mod.check_session(cal, _at("11:00", "HK"), market="HK").ok          # 上午到 12:00
    assert not session_mod.check_session(cal, _at("12:30", "HK"), market="HK").ok      # 午休
    assert session_mod.check_session(cal, _at("15:00", "HK"), market="HK").ok


def test_session_us_no_lunch():
    cal = _cal("US")
    assert session_mod.check_session(cal, _at("09:30", "US"), market="US").ok
    assert session_mod.check_session(cal, _at("12:00", "US"), market="US").ok          # 美股无午休
    assert session_mod.check_session(cal, _at("16:00", "US"), market="US").ok          # 收盘含在内
    assert not session_mod.check_session(cal, _at("16:01", "US"), market="US").ok


def test_session_us_respects_dst():
    """同为 09:30 的挂钟时刻，冬令时日（EST）成立；切到夏令时后按本地时刻判，仍成立。"""
    cal = _cal("US")
    winter = _at("09:30", "US", day=(2026, 1, 15))
    summer = _at("09:30", "US", day=(2026, 7, 15))
    assert winter.utcoffset().total_seconds() == -5 * 3600
    assert summer.utcoffset().total_seconds() == -4 * 3600
    assert session_mod.check_session(cal, winter, market="US").ok
    assert session_mod.check_session(cal, summer, market="US").ok


def test_session_falls_back_to_market_rule_sessions():
    """日历行没写时段（半日市没标）→ 用 `fin_market_rule.sessions` 放宽处理。"""
    cal = {"is_trading": True, "sessions": [], "calendar_source": "hkex_official"}
    r = session_mod.check_session(cal, _at("15:00", "HK"), market="HK",
                                  sessions=RULE["HK"]["sessions"])
    assert r.ok


@pytest.mark.parametrize("market", ["CN_A", "HK", "US"])
def test_session_missing_calendar_rejects(market):
    r = session_mod.check_session(None, _at("10:00", market), market=market)
    assert not r.ok and "交易日历" in r.reason
    assert r.detail["market"] == market


def test_session_non_trading_day_rejects():
    cal = _cal("HK", trading=False)
    r = session_mod.check_session(cal, _at("10:00", "HK"), market="HK")
    assert not r.ok and "非交易日" in r.reason


def test_session_reports_calendar_source():
    cal = _cal("US")
    cal["calendar_source"] = "manual_seed"
    r = session_mod.check_session(cal, _at("10:00", "US"), market="US")
    assert r.ok and r.detail["calendar_source"] == "manual_seed"


# ══ 第 2 条 · 可卖数量（**跨市场差异的专项用例**）═════════════════════════
def test_same_day_can_sell_today_buy():
    """同日买入、无可卖量：港美股可卖，A 股不可卖 —— 同一笔委托两种判定。"""
    cn = t1_mod.check_t1("sell", 100, 0, rule="t_plus_n", days=1, position_qty=100)
    hk = t1_mod.check_t1("sell", 100, 0, rule="same_day", days=0, position_qty=100)
    us = t1_mod.check_t1("sell", 100, 0, rule="same_day", days=0, position_qty=100)
    assert not cn.ok and "T+1" in cn.reason
    assert hk.ok and us.ok


def test_t_plus_n_uses_sellable_not_position():
    # 持仓 100 但可卖 0（当日买入）→ A 股拒
    assert not t1_mod.check_t1("sell", 100, 0, rule="t_plus_n", position_qty=100).ok
    # 隔日（可卖 100）→ 通过
    assert t1_mod.check_t1("sell", 100, 100, rule="t_plus_n", position_qty=100).ok


def test_effective_sellable_by_rule():
    assert t1_mod.effective_sellable("same_day", 100, 0) == 100
    assert t1_mod.effective_sellable("t_plus_n", 100, 0) == 0
    assert t1_mod.effective_sellable("t_plus_n", 100, 40) == 40


def test_unknown_sellable_rule_rejects():
    assert not t1_mod.check_t1("sell", 1, 1, rule="t_plus_9").ok


# ══ 第 3 条 · 整手 ══════════════════════════════════════════════════════════
def test_lot_fixed_cn_a():
    n = lot_mod.resolve_lot_size("fixed", 100, None)
    assert lot_mod.check_lot("buy", 100, n).ok
    assert not lot_mod.check_lot("buy", 150, n).ok


def test_lot_per_instrument_hk():
    n = lot_mod.resolve_lot_size("per_instrument", None, 500)
    assert n == 500
    assert lot_mod.check_lot("buy", 500, n).ok
    assert not lot_mod.check_lot("buy", 100, n).ok


def test_lot_one_us():
    n = lot_mod.resolve_lot_size("one", None, None)
    assert n == 1
    assert lot_mod.check_lot("buy", 1, n).ok
    assert lot_mod.check_lot("buy", 7, n).ok


def test_lot_resolve_failures_return_none():
    assert lot_mod.resolve_lot_size("per_instrument", None, None) is None   # 标的没手数
    assert lot_mod.resolve_lot_size("fixed", None, None) is None
    assert lot_mod.resolve_lot_size("weird", 100, 100) is None
    assert not lot_mod.check_lot("buy", 10, None).ok                        # None → 拒绝


# ══ 第 4 条 · 价格带（`none` 留痕专项）═══════════════════════════════════
def test_price_limit_pct_enforced_cn_a():
    inst = {"limit_up_pct": Decimal("0.10"), "limit_down_pct": Decimal("0.10")}
    assert limit_mod.check_price_limit(inst, "buy", "11.00", "10.00", mode="pct").ok
    assert not limit_mod.check_price_limit(inst, "buy", "11.01", "10.00", mode="pct").ok


def test_price_limit_none_skips_but_leaves_trace():
    """专项：`none` 不作校验，但**必须留痕**（checked=false + 文案写明未做）。"""
    r = limit_mod.check_price_limit(None, "buy", "999", None, mode="none")
    assert r.ok
    assert r.detail["price_limit_checked"] is False
    assert r.detail["checked"] is False
    assert "未做价格带校验" in r.detail["note"]


def test_price_limit_band_not_implemented_rejects():
    r = limit_mod.check_price_limit(None, "buy", "1", "1", mode="band")
    assert not r.ok and "band" in r.reason


def test_price_limit_unknown_mode_rejects():
    assert not limit_mod.check_price_limit(None, "buy", "1", "1", mode="percent").ok


# ══ 第 5 条 · 费用（印花税方向由表决定）═════════════════════════════════
def test_fee_cn_a_stamp_sell_only():
    buy = fee_mod.compute_fee("buy", Decimal("100000"), _MODEL["CN_A"])
    sell = fee_mod.compute_fee("sell", Decimal("100000"), _MODEL["CN_A"])
    assert buy.stamp_tax == Decimal("0.0000")
    assert sell.stamp_tax == Decimal("50.0000")


def test_fee_hk_stamp_both_sides():
    buy = fee_mod.compute_fee("buy", Decimal("100000"), _MODEL["HK"])
    sell = fee_mod.compute_fee("sell", Decimal("100000"), _MODEL["HK"])
    assert buy.stamp_tax == Decimal("100.0000")
    assert sell.stamp_tax == Decimal("100.0000")


def test_fee_us_stamp_none():
    buy = fee_mod.compute_fee("buy", Decimal("100000"), _MODEL["US"])
    sell = fee_mod.compute_fee("sell", Decimal("100000"), _MODEL["US"])
    assert buy.stamp_tax == Decimal("0.0000") and sell.stamp_tax == Decimal("0.0000")


def test_fee_model_rejects_bad_stamp_side():
    bad = dict(_MODEL["HK"], stamp_side="sometimes")
    assert not fee_mod.check_fee_model(bad).ok


def test_fee_model_defaults_to_sell_when_stamp_side_absent():
    """老行没 `stamp_side` 列值时 = 一期 A 股口径（仅卖出）。"""
    model = dict(_MODEL["CN_A"])
    model.pop("stamp_side")
    assert fee_mod.compute_fee("buy", Decimal("100000"), model).stamp_tax == Decimal("0.0000")
    assert fee_mod.compute_fee("sell", Decimal("100000"), model).stamp_tax == Decimal("50.0000")


# ══ 第 6 条 · 资金与持仓（币种）══════════════════════════════════════════
def test_funds_currency_label():
    cn = funds_mod.check_funds("buy", Decimal("1000"), Decimal("5"), Decimal("1"), currency="CNY")
    us = funds_mod.check_funds("buy", Decimal("1000"), Decimal("5"), Decimal("1"), currency="USD")
    hk = funds_mod.check_funds("buy", Decimal("1000"), Decimal("5"), Decimal("1"), currency="HKD")
    assert "元" in cn.reason and "USD" in us.reason and "HKD" in hk.reason


def test_funds_cny_message_unchanged():
    r = funds_mod.check_funds("buy", Decimal("1000"), Decimal("5"), Decimal("1"), currency=None)
    assert r.reason == "可用资金不足：需要 1005 元（含费用），可用 1 元"


# ══ 引擎：三市场整笔 ═══════════════════════════════════════════════════════
def test_engine_cn_a_passes():
    out = evaluate(_base("CN_A"))
    assert out.passed and out.fee.total > 0
    assert out.price_limit.detail["price_limit_checked"] is True


@pytest.mark.parametrize("market,qty", [("HK", 500), ("US", 10)])
def test_engine_hk_us_pass_and_trace(market, qty):
    out = evaluate(_base(market, qty=qty))
    assert out.passed, out.decline_reason
    assert out.price_limit.detail["price_limit_checked"] is False   # none 留痕


def test_engine_missing_market_rule_rejects():
    out = evaluate(_base("HK", market_rule=None))
    assert not out.passed
    assert [r.name for r in out.results] == ["risk"]
    assert "HK" in out.decline_reason and "市场规则" in out.decline_reason


def test_engine_same_day_sell_allowed_for_us_not_cn():
    """同一笔卖出：美股可卖当日买入，A 股不可 —— 走完整六条。"""
    us = evaluate(_base("US", side="sell", qty=10, position_qty=10, sellable_qty=0))
    cn = evaluate(_base("CN_A", side="sell", qty=100, position_qty=100, sellable_qty=0))
    assert us.passed, us.decline_reason
    assert not cn.passed and "t1" in cn.failed_names
