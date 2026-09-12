# -*- coding: utf-8 -*-
"""小鹿智能体 · 方向 C「VCP 三段式」引擎用例(纯计算)。

    cd apps/api && PYTHONPATH=. python tests/test_agent_vcp3.py
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.services.quant import agent_vcp as av, agent_vcp3 as c3   # noqa: E402

fails: list[str] = []
passed = 0


def check(name, cond, extra=""):
    global passed
    if cond:
        passed += 1
    else:
        fails.append(f"{name}  {extra}")


def path(legs, start=50.0, vol=1000.0):
    """legs = [(根数, 目标价, 日均量, 振幅)] → 日线。"""
    out, p, d = [], start, date(2026, 1, 2)
    for n, target, v, rng in legs:
        step = (target - p) / n
        for _ in range(n):
            p += step
            while d.weekday() >= 5:
                d += timedelta(days=1)
            out.append((d, p, p * (1 + rng), p * (1 - rng), v))
            d += timedelta(days=1)
    return out


# 教科书 VCP:涨 → 三次收缩(25% / 12% / 5%)→ 横盘在枢轴下方 → 放量突破枢轴 1 个 ATR 以内
BASE = [(40, 100.0, 1000.0, 0.02),
        (15, 75.0, 1000.0, 0.02), (15, 99.0, 800.0, 0.02),
        (10, 87.0, 700.0, 0.015), (10, 98.0, 700.0, 0.015),
        (6, 93.0, 400.0, 0.01), (6, 97.0, 500.0, 0.01),
        (4, 97.5, 450.0, 0.006)]
bars = path(BASE)
ind = c3.indicators(bars)
check("指标 · 有枢轴与底部低点", ind and ind["pivot"] is not None and ind["base_low"] is not None, str(ind and (ind["pivot"], ind["base_low"])))
check("指标 · 底部低点 < 收盘 < 枢轴(还没突破)", ind and ind["base_low"] < ind["close"] < ind["pivot"], str((ind["base_low"], ind["close"], ind["pivot"])))
f = c3.entry_flags(ind)
check("买入 · 还在枢轴下方不触发", not f["C-01"] and not f["above"])

# 突破日:收盘 = 枢轴 + 0.5 ATR,放量 2 倍
atr = ind["atr20"]
brk = bars + [(bars[-1][0] + timedelta(days=1), ind["pivot"] + 0.5 * atr, ind["pivot"] + 0.7 * atr, ind["pivot"] - 0.2 * atr, 1400.0)]
while brk[-1][0].weekday() >= 5:
    brk[-1] = (brk[-1][0] + timedelta(days=1),) + brk[-1][1:]
ind2 = c3.indicators(brk)
f2 = c3.entry_flags(ind2)
check("买入 · ⭐突破日收盘站上枢轴 0.5 ATR + 放量 → 两条都过", f2["C-01"] and f2["C-02"], str(f2))
state = {"date": str(brk[-1][0]), "cash": 100_000.0, "positions": [], "closed": [], "closed_pnl": [], "equity": 100_000.0, "halt_reason": None}
fill, blocked = c3.try_entry("AAA", "甲", ind2, state)
exp_stop = max(ind2["base_low"] - 0.5 * ind2["atr20"], ind2["close"] * 0.92)
check("买入 · 成交挂 C-01,止损 = 底部低点下方 0.5 ATR 与 -8% 取高(这里 -8% 更近,封顶生效)",
      fill and fill["rule_id"] == "C-01" and abs(state["positions"][0].stop - exp_stop) < 1e-9, str(fill))
pos = state["positions"][0]
check("买入 · ⭐股数 = 1% 总资产 ÷ 止损距离", pos.size == int(1000.0 / (pos.entry_price - pos.stop)), str((pos.size, pos.entry_price, pos.stop)))
check("买入 · rationale 有枢轴、ATR、止损位、股数", "枢轴" in fill["rationale"] and "止损" in fill["rationale"] and "股" in fill["rationale"], fill["rationale"][:100])

# 追高 2 个 ATR:不买
hi = bars + [(brk[-1][0], ind["pivot"] + 2.0 * atr, ind["pivot"] + 2.2 * atr, ind["pivot"] + 1.5 * atr, 1400.0)]
f3 = c3.entry_flags(c3.indicators(hi))
check("买入 · 高出枢轴 2 个 ATR 不追", not f3["C-01"] and f3["above"], str(f3))
# 突破但 3 天内没放量:不买
lv = bars + [(brk[-1][0], ind["pivot"] + 0.5 * atr, ind["pivot"] + 0.7 * atr, ind["pivot"] - 0.2 * atr, 500.0)]
f4 = c3.entry_flags(c3.indicators(lv))
check("买入 · 没放量不买", f4["C-01"] and not f4["C-02"], str(f4))
# 突破第 2 天才放量:仍然算(3 天内)
d2 = brk[-1][0] + timedelta(days=1)
while d2.weekday() >= 5:
    d2 += timedelta(days=1)
two = lv + [(d2, ind["pivot"] + 0.6 * atr, ind["pivot"] + 0.8 * atr, ind["pivot"] + 0.1 * atr, 1500.0)]
f5 = c3.entry_flags(c3.indicators(two))
check("买入 · ⭐突破次日才放量,3 天窗口内照样确认", f5["C-01"] and f5["C-02"], str(f5))
# 突破已经 6 天了(最近 6 天收盘全在枢轴上方):不新鲜
f6 = c3.entry_flags(dict(ind2, recent_closes=[ind2["pivot"] + 0.3 * atr] * 6))
check("买入 · 6 天前就突破了,不新鲜 → 不买", f6["C-01"] and not f6["C-02"] and not f6["fresh"], str(f6))
# 观察列表文字
w = c3.watch_item("BBB", "乙", c3.indicators(lv), False, None)
check("观察 · 差一条时 gap 写还差什么", w["progress_pct"] == 50 and "还差" in w["gap"] and "C-02" in w["gap"], str(w))


# ── 出场 ──────────────────────────────────────────────────────
def pos_(entry=100.0, stop=95.0, size=200, held=0, highest=None):
    return av.Position("AAA", "甲", size, size, entry, "2026-04-01", entry, highest or entry, 1, held, "C-01", stop, entry - stop)


def day(px, lows_prior=None):
    return {"close": px, "high": px, "low": px, "volume": 1e3, "atr20": 1.0, "lows_prior": lows_prior or [px * 0.97] * 15}


def st_():
    return {"cash": 50_000.0, "equity": 100_000.0, "closed": [], "closed_pnl": [], "positions": []}


p = pos_(); s = st_()
fl = c3.manage_position(p, day(94.5), s)
check("卖出 · C-04 跌破初始止损清仓", fl and fl[0]["rule_id"] == "C-04" and p.size == 0 and fl[0]["pnl_abs"] < 0, str(fl))
p = pos_(); s = st_()
fl = c3.manage_position(p, day(103.0, lows_prior=[101.0] * 15), s)
check("卖出 · 没到 1R(105)不移动止损", not fl and p.stop == 95.0, str(p.stop))
fl = c3.manage_position(p, day(106.0, lows_prior=[101.5] * 15), s)
check("卖出 · ⭐到 1R 后止损上移到前 10 日最低(≥ 成本)", not fl and p.stop == 101.5, str(p.stop))
fl = c3.manage_position(p, day(108.0, lows_prior=[100.0] * 15), s)
check("卖出 · 移动止损只上不下", p.stop == 101.5, str(p.stop))
fl = c3.manage_position(p, day(101.0), s)
check("卖出 · C-05 跌破移动止损出场(盈利)", fl and fl[0]["rule_id"] == "C-05" and fl[0]["pnl_abs"] > 0, str(fl))
p = pos_(held=14); s = st_()
fl = c3.manage_position(p, day(102.0), s)
check("卖出 · C-06 第 15 天没到 1R 清仓", fl and fl[0]["rule_id"] == "C-06" and p.size == 0, str(fl))
p = pos_(held=14, highest=106.0); s = st_()
fl = c3.manage_position(p, day(102.0, lows_prior=[101.0] * 15), s)
check("卖出 · 到过 1R 就不受时间止损管", not fl, str(fl))

# ── 整合 + 文案 ───────────────────────────────────────────────
bars_map = {"AAA": brk}
r = c3.run_day(str(brk[-1][0]), [], 100_000.0, lambda c: bars_map.get(c), [("AAA", "甲", 80)], None, 0)
check("整合 · 突破日买入,权益不变", [f["symbol"] for f in r["fills"]] == ["AAA"] and abs(r["equity"] - 100_000) < 1e-6, str(r["fills"]))
r3 = c3.run_day(str(brk[-1][0]), [], 100_000.0, lambda c: bars_map.get(c), [("AAA", "甲", 80)], None, 3)
check("整合 · 连亏停机只停一天并说明", not r3["fills"] and "C-07" in r3["watch_items"][0]["blocked_reason"] and r3["consec_losses"] == 0, str(r3["watch_items"]))
check("文案 · 参数改了规则手册跟着变", "20 天" not in c3.rules_for()[5]["condition"] and "20 个交易日" in c3.rules_for(dict(c3.PARAMS, time_days=20))[5]["condition"])
check("接口 · 引擎常量齐全", c3.ENTRY_RULE == "C-01" and set(c3.STOP_KEYS) <= set(c3.PARAMS) and callable(c3.summary))

total = passed + len(fails)
print(f"方向 C 引擎用例 {total} 条")
if fails:
    print(f"FAIL {len(fails)} 条:")
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("ALL OK")
