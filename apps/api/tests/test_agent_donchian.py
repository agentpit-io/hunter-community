# -*- coding: utf-8 -*-
"""小鹿智能体 · 唐奇安突破线引擎 + 研究线判定用例(纯计算,不连库)。

    cd apps/api && PYTHONPATH=. python tests/test_agent_donchian.py

每条买卖规则至少一例;D-02「只在第一次突破那天进」有正反两例(连涨一个月的票不许天天追);
研究线判定(agent_research.judge)的回测关与 30 笔判定各有过关 / 淘汰 / 等待三种结果。
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.services.quant import agent_donchian as ad   # noqa: E402
from app.services.quant import agent_vcp as av        # noqa: E402
from app.services.quant import agent_research as research   # noqa: E402

fails: list[str] = []
passed = 0


def check(name, cond, extra=""):
    global passed
    if cond:
        passed += 1
    else:
        fails.append(f"{name}  {extra}")


def mk(closes, rng=0.01, start=date(2026, 1, 5)):
    out, d = [], start
    for c in closes:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append((d, c, c * (1 + rng), c * (1 - rng), 1_000_000.0))
        d += timedelta(days=1)
    return out


G = av.GUARDS

# ── 指标 ──────────────────────────────────────────────────────
flat = mk([100.0] * 80)
ind = ad.indicators(flat)
check("指标 · 前 55 日最高 = 平台上沿(不含今天)", abs(ind["hi_entry"] - 101.0) < 1e-9, str(ind["hi_entry"]))
check("指标 · 前 20 日最低 = 平台下沿(不含今天)", abs(ind["lo_exit"] - 99.0) < 1e-9, str(ind["lo_exit"]))
check("指标 · 不足 60 根 → None", ad.indicators(flat[:50]) is None)
check("指标 · 缺高低 → None", ad.indicators([(d, c, None, None, v) for d, c, _h, _l, v in flat]) is None)

# ── D-01 + D-02:第一次突破 ────────────────────────────────────
brk = mk([100.0] * 79 + [103.0])
ind = ad.indicators(brk)
f = ad.entry_flags(ind)
check("D-01 · 收盘 103 > 前 55 日最高 101 → 突破", f["D-01"])
check("D-02 · 前一天 100 没突破 → 新鲜", f["D-02"] and f["ok"])

# 连涨:每天都创新高,但前一天也已经在前 55 日最高之上 → 不追
run = mk([100.0] * 60 + [100.0 + i * 1.5 for i in range(1, 21)])
ind_run = ad.indicators(run)
f = ad.entry_flags(ind_run)
check("D-02 · 连涨中的票:今天仍在 55 日最高上方(D-01 成立)", f["D-01"])
check("D-02 · 但前一天就突破过 → 不新鲜,不进", not f["D-02"] and not f["ok"])

# ── 进场:仓位按风险 ─────────────────────────────────────────
ind = ad.indicators(brk)
r = ad.run_day(str(brk[-1][0]), [], 100_000.0, lambda c: [], [("AAA", "AAA", 90)], None, 0, ad.PARAMS, G,
               ind_of=lambda c: ind)
buys = [x for x in r["fills"] if x["side"] == "buy"]
check("进场 · 第一次突破当天买入", len(buys) == 1, str(r["fills"]))
if buys:
    b = buys[0]
    atr = ind["atr20"]
    by_risk = int(100_000 * 0.01 / (2 * atr))
    by_cap = int(100_000 * 0.20 / 103.0)
    check("进场 · 股数 = min(1% 风险 ÷ 2 ATR, 单股 20% 上限)", b["shares"] == min(by_risk, by_cap), f"{b['shares']} vs {by_risk}/{by_cap}")
    check("进场 · 理由里写了突破价、前一天没突破、止损位、股数算法", all(k in b["rationale"] for k in ("突破前 55 日最高", "D-02 新鲜", "初始止损", "单笔风险")))
    pos = r["positions"][0]
    check("进场 · 止损 = 进场价 − 2 ATR", abs(pos.stop - (103.0 - 2 * atr)) < 1e-9)

# 连涨的票不进
r2 = ad.run_day(str(run[-1][0]), [], 100_000.0, lambda c: [], [("BBB", "BBB", 90)], None, 0, ad.PARAMS, G,
                ind_of=lambda c: ind_run)
check("进场 · 连涨中的票不追", not r2["fills"])
wi = r2["watch_items"][0]
check("观察列表 · 不新鲜的写清原因", "D-02" in wi["gap"] and "不追" in wi["gap"], wi["gap"])

# 持仓上限
full = [av.Position(code=f"H{i}", name="h", size=1, initial_size=1, entry_price=100, entry_date="2026-01-02",
                    avg_cost=100, highest=100, level=1, stop=90, risk=10) for i in range(8)]
held_ind = ad.indicators(flat)
r3 = ad.run_day(str(brk[-1][0]), full, 50_000.0, lambda c: [], [("AAA", "AAA", 90)], None, 0, ad.PARAMS, G,
                ind_of=lambda c: ind if c == "AAA" else held_ind)
check("D-03 · 已持 8 只 → 不进并写明", not any(x["side"] == "buy" for x in r3["fills"])
      and any(w.get("blocked") and "上限" in w.get("blocked_reason", "") for w in r3["watch_items"]))

# 护栏
r4 = ad.run_day(str(brk[-1][0]), [], 100_000.0, lambda c: [], [("AAA", "AAA", 90)], None, 3, ad.PARAMS, G,
                ind_of=lambda c: ind)
check("D-06 · 连亏 3 笔 → 今天不开仓", not r4["fills"] and r4["halt_reason"])

# ── 出场 ─────────────────────────────────────────────────────
def pos_at(stop, ep=100.0):
    return av.Position(code="AAA", name="AAA", size=100, initial_size=100, entry_price=ep, entry_date="2026-01-02",
                       avg_cost=ep, highest=ep, level=1, stop=stop, risk=ep - stop)


# 通道下沿 99 在止损 90 之上,收盘 98 → D-05
ind_x = dict(ad.indicators(flat), close=98.0)
r5 = ad.run_day("2026-04-01", [pos_at(90.0)], 0.0, lambda c: [], [], None, 0, ad.PARAMS, G, ind_of=lambda c: ind_x)
s5 = [x for x in r5["fills"] if x["side"] == "sell"]
check("D-05 · 跌破 20 日最低 → 通道止损出场", len(s5) == 1 and s5[0]["rule_id"] == "D-05", str(r5["fills"]))
check("出场 · 盈亏 = (收盘 − 成本) × 股数", s5 and abs(s5[0]["pnl_abs"] - (98 - 100) * 100) < 1e-6)

# 止损 99.5 在通道下沿 99 之上,收盘 99.2 → D-04(通道没破)
ind_y = dict(ad.indicators(flat), close=99.2)
r6 = ad.run_day("2026-04-01", [pos_at(99.5)], 0.0, lambda c: [], [], None, 0, ad.PARAMS, G, ind_of=lambda c: ind_y)
s6 = [x for x in r6["fills"] if x["side"] == "sell"]
check("D-04 · 跌破 2 ATR 初始止损 → 止损出场", len(s6) == 1 and s6[0]["rule_id"] == "D-04", str(r6["fills"]))

# 两条线都没破 → 继续拿
ind_z = dict(ad.indicators(flat), close=100.5)
r7 = ad.run_day("2026-04-01", [pos_at(90.0)], 0.0, lambda c: [], [], None, 0, ad.PARAMS, G, ind_of=lambda c: ind_z)
check("持有 · 没破线 → 不卖,持有天数 +1", not r7["fills"] and r7["positions"][0].bars_held == 1)

check("接口 · 规则固定,不进优化器", ad.RULE_PARAM_KEY == {})
check("接口 · 规则编号与 VCP 线不冲突", all(r["id"].startswith("D-") for r in ad.RULES))

# ── 研究线判定(agent_research.judge)────────────────────────────
D0, D1 = date(2026, 1, 2), date(2026, 9, 11)


def met(cycles, exp_, dd, eq_end=100_000.0):
    return {"branch": "x", "first": D0, "last": D1, "equity": {D0: 100_000.0, D1: eq_end},
            "cycles": cycles, "expectancy_net": exp_, "max_dd_pct": dd}


cmp_ = met(14, 364.0, -5.51, 105_130.0)
v = research.judge("backtest", met(5, 120.0, -4.0), cmp_, caught_up=False)
check("回测关 · 没追到最新交易日 → 等", v["decision"] == "wait", str(v))
v = research.judge("backtest", met(0, None, 0.0), cmp_)
check("回测关 · 一笔完整交易都没有 → 淘汰", v["decision"] == "kill", str(v))
v = research.judge("backtest", met(12, -35.0, -4.0), cmp_)
check("回测关 · 每笔平均净损益 ≤ 0 → 淘汰", v["decision"] == "kill" and "≤ 0" in v["text"], str(v))
v = research.judge("backtest", met(12, 80.0, -9.0), cmp_)
check("回测关 · 回撤超过对照组 1.5 倍(-9% < -8.27%)→ 淘汰", v["decision"] == "kill" and "1.5" in v["text"], str(v))
v = research.judge("backtest", met(12, 80.0, -8.0), cmp_)
check("回测关 · 期望 > 0 且回撤在 1.5 倍内 → 过关进纸上跑", v["decision"] == "pass", str(v))
v = research.judge("paper", met(29, -500.0, -3.0), cmp_)
check("30 笔判定 · 29 笔哪怕亏钱也不下结论 → 等", v["decision"] == "wait" and "29/30" in v["text"], str(v))
v = research.judge("paper", met(30, -1.0, -3.0), cmp_)
check("30 笔判定 · 满 30 笔、期望 < 0 → 淘汰", v["decision"] == "kill", str(v))
v = research.judge("paper", met(30, 50.0, -3.0, 103_000.0), cmp_)
check("30 笔判定 · 同段收益 +3% 不如对照组 +5.13% → 淘汰", v["decision"] == "kill" and "不如对照组" in v["text"], str(v))
v = research.judge("paper", met(31, 50.0, -3.0, 108_000.0), cmp_)
check("30 笔判定 · 期望 > 0 且同段收益不输对照组 → 通过", v["decision"] == "pass", str(v))

print(f"{passed} passed, {len(fails)} failed")
for x in fails:
    print("FAIL", x)
print("ALL OK" if not fails else "SOME FAILED")
sys.exit(1 if fails else 0)
