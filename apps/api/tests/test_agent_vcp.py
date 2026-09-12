# -*- coding: utf-8 -*-
"""小鹿智能体 · VCP 波段交易引擎用例(纯计算)。

    cd apps/api && PYTHONPATH=. python tests/test_agent_vcp.py

覆盖原脚本每一条买卖规则各至少一例,以及与原脚本不同的三处(见 agent_vcp 文件头)。
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.services.quant import agent_vcp as av   # noqa: E402

fails: list[str] = []
passed = 0


def check(name, cond, extra=""):
    global passed
    if cond:
        passed += 1
    else:
        fails.append(f"{name}  {extra}")


def mk(closes, vols=None, rng=0.01, start=date(2026, 1, 5)):
    """收盘序列 → 日线;高低 = 收 ±rng。"""
    out, d = [], start
    for i, c in enumerate(closes):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        v = vols[i] if vols else 1_000_000.0
        out.append((d, c, c * (1 + rng), c * (1 - rng), v))
        d += timedelta(days=1)
    return out


# ── 指标 ──────────────────────────────────────────────────────
flat = mk([100.0] * 80)
ind = av.indicators(flat)
check("指标 · 平走:EMA = SMA = 收盘", abs(ind["ema8"] - 100) < 1e-9 and abs(ind["sma50"] - 100) < 1e-9)
check("指标 · 枢轴 = 昨天往前 20 根的最高(不含今天)", abs(ind["pivot_high"] - 101.0) < 1e-9, str(ind["pivot_high"]))
check("指标 · ATR = 高低差", abs(ind["atr20"] - 2.0) < 1e-6 and abs(ind["atr5"] - 2.0) < 1e-6, str((ind["atr20"], ind["atr5"])))
check("指标 · ADTV = 收×量 的 20 日均", abs(ind["adtv"] - 1e8) < 1e-3)
check("指标 · 不足 60 根 → None", av.indicators(flat[:50]) is None)
check("指标 · 缺高低 → None", av.indicators([(d, c, None, None, v) for d, c, _h, _l, v in flat]) is None)

# ── 买入五过滤:构造一个教科书突破 ─────────────────────────────
# 前 60 根 90→100 慢涨(趋势),最近 20 根在 98~100 横盘且振幅缩小(ATR5 < 0.7·ATR20),
# 今天放量 2× 收 101.5(突破枢轴 100.99,+0.5%)
closes = [88 + i * (9 / 59) for i in range(60)] + [97.5 + (i % 2) * 0.6 for i in range(20)] + [101.5]
vols = [1_000_000.0] * 80 + [2_000_000.0]
bars = mk(closes, vols, rng=0.03)
# 最近 5 根把振幅收小(ATR5 明显小于 ATR20),突破日本身振幅也不大
tight = [(d, c, c * 1.002, c * 0.998, v) for d, c, _h, _l, v in bars[-6:-1]] + [(bars[-1][0], 101.5, 101.8, 101.0, 2_000_000.0)]
bars = bars[:-6] + tight
ind = av.indicators(bars)
chk = {c["rule"]: c["ok"] for c in av.entry_checks(ind)}
check("买入 · 教科书突破五条全过", all(chk.values()), str(chk) + str({k: ind[k] for k in ("close", "pivot_high", "atr5", "atr20", "ema8", "ema21")}))

state = {"date": "2026-04-24", "cash": 100_000.0, "positions": [], "closed": [], "closed_pnl": [],
         "equity": 100_000.0, "halt_reason": None}
f, blocked = av.try_entry("AAA", "甲", ind, state)
check("买入 · 成交挂在 R-04,股数 = 8% 总资产 ÷ 收盘", f and f["rule_id"] == "R-04" and f["shares"] == int(8000 / 101.5), str(f))
check("买入 · 现金扣掉、持仓建立", state["positions"][0].size == f["shares"] and abs(state["cash"] - (100_000 - f["amount"])) < 1e-6)
check("买入 · rationale 里有枢轴、量能、ATR 的数字", "枢轴" in f["rationale"] and "×" in f["rationale"] and "ATR5/ATR20" in f["rationale"], f["rationale"][:120])

# 追高:收在枢轴上方 6% → 不买,gap 说明原因
bars_hi = bars[:-1] + [(bars[-1][0], 108.0, 108.3, 107.5, 2_000_000.0)]
chk = {c["rule"]: c for c in av.entry_checks(av.indicators(bars_hi))}
check("买入 · 超伸 5% 不追", not chk["R-04"]["ok"] and "不追" in chk["R-04"]["text"], chk["R-04"]["text"])
# 缩量:量能只有 1.1×
bars_lv = bars[:-1] + [(bars[-1][0], 101.5, 101.8, 101.0, 1_100_000.0)]
chk = {c["rule"]: c for c in av.entry_checks(av.indicators(bars_lv))}
check("买入 · 量能不够不买,gap 写要 ≥1.4×", not chk["R-05"]["ok"] and "1.4" in chk["R-05"]["text"], chk["R-05"]["text"])
w = av.watch_item("BBB", "乙", av.indicators(bars_lv), False, None)
check("观察列表 · 进度 4/5,gap 写还差什么", w["progress_pct"] == 80 and "还差" in w["gap"] and "R-05" in w["gap"], str(w))

# 上限 10 只 / 护栏
state10 = dict(state, positions=[av.Position("X%d" % i, "", 1, 1, 1.0, "2026-01-01", 1.0, 1.0, 1) for i in range(10)])
f, blocked = av.try_entry("AAA", "甲", ind, state10)
check("买入 · 满 10 只被挡且说明", f is None and "R-18" in blocked, str(blocked))
f, blocked = av.try_entry("AAA", "甲", ind, dict(state, positions=[], halt_reason="连亏 3 笔"))
check("买入 · 护栏挡下并说明", f is None and "R-19" in blocked, str(blocked))


# ── 持仓管理:每条卖出规则 ────────────────────────────────────
def pos(entry=100.0, size=80, level=1, held=0, highest=None):
    return av.Position("AAA", "甲", size, size, entry, "2026-04-24", entry, highest or entry, level, held)


def st():
    return {"cash": 50_000.0, "equity": 100_000.0, "closed": [], "closed_pnl": [], "positions": []}


def day(price, sma50=90.0, low3=None, atr=1.0):
    return {"close": price, "high": price, "low": price, "volume": 1e6, "sma50": sma50,
            "low3": low3 if low3 is not None else price * 0.99}


p = pos(highest=106.0); s = st()
fl = av.manage_position(p, day(99.5), s)
check("卖出 · R-07 曾涨过 +5% 今收回买入价 → 全部清仓", fl and fl[0]["rule_id"] == "R-07" and p.size == 0, str(fl))
p = pos(); s = st()
fl = av.manage_position(p, day(94.5), s)
check("卖出 · R-08 亏 -5% 且初始仓位 → 卖一半", fl and fl[0]["rule_id"] == "R-08" and p.size == 40 and fl[0]["pnl_pct"] < 0, str(fl))
fl2 = av.manage_position(p, day(94.5), s)
check("卖出 · 已减半后 -5% 不再触发 R-08(size ≠ 初始)", not fl2, str(fl2))
p = pos(); s = st()
fl = av.manage_position(p, day(91.5), s)
check("卖出 · 初始仓位一天跌到 -8.5%:按原脚本顺序先触发 R-08 卖一半(-8% 清仓在下一天)", fl and fl[0]["rule_id"] == "R-08", str(fl))
p = pos(size=40); p.initial_size = 80; s = st()
fl = av.manage_position(p, day(91.5), s)
check("卖出 · R-09 -8% 硬止损清仓(已减过半的仓位)", fl and fl[0]["rule_id"] == "R-09" and p.size == 0, str(fl))
p = pos(); s = st()
fl = av.manage_position(p, day(103.0, sma50=106.0), s)
check("卖出 · R-10 跌破 SMA50 2% 清仓(即便还在盈利)", fl and fl[0]["rule_id"] == "R-10" and p.size == 0, str(fl))
p = pos(); s = st()
fl = av.manage_position(p, day(110.5), s)
check("卖出 · R-11 +10% 卖一半,进入止盈状态 -1", fl and fl[0]["rule_id"] == "R-11" and p.size == 40 and p.level == -1, str(fl))
fl = av.manage_position(p, day(112.0), s)
check("卖出 · ⭐止盈状态下 +10% 不重复触发", not fl, str(fl))
fl = av.manage_position(p, day(115.5), s)
check("卖出 · R-12 +15% 再卖一半 → -2", fl and fl[0]["rule_id"] == "R-12" and p.size == 20 and p.level == -2, str(fl))
fl = av.manage_position(p, day(116.0), s)
check("卖出 · ⭐-2 状态下 +10% 也不触发(原脚本会)", not fl, str(fl))
fl = av.manage_position(p, day(120.5), s)
check("卖出 · R-13 +20% 清仓", fl and fl[0]["rule_id"] == "R-13" and p.size == 0, str(fl))
check("盈亏 · 三次卖出都记了 pnl 且为正", len(s["closed_pnl"]) == 3 and all(x > 0 for x in s["closed_pnl"]), str(s["closed_pnl"]))
p = pos(held=4); s = st()
fl = av.manage_position(p, day(102.0), s)
check("卖出 · R-14 第 5 天没涨过 5% 减半", fl and fl[0]["rule_id"] == "R-14" and p.size == 40, str(fl))
p = pos(held=9, size=40); p.initial_size = 80; s = st()
fl = av.manage_position(p, day(102.0), s)
check("卖出 · R-15 第 10 天没涨过 5% 清仓", fl and fl[0]["rule_id"] == "R-15" and p.size == 0, str(fl))
p = pos(held=4, highest=106.0); s = st()
fl = av.manage_position(p, day(104.0), s)
check("卖出 · 第 5 天但曾涨过 5% → 不触发时间止损", not fl, str(fl))

# 倒三角加仓
p = pos(highest=104.0); s = st()
fl = av.manage_position(p, day(105.0, low3=101.0), s)
check("加仓 · ⭐今收突破昨日前高、回撤 ≤10% → 加第 2 注 = 第 1 注一半", fl and fl[0]["rule_id"] == "R-16" and fl[0]["shares"] == 40 and p.level == 2 and p.size == 120, str(fl))
check("加仓 · 均价更新、现金扣减", abs(p.avg_cost - (100 * 80 + 105 * 40) / 120) < 1e-9 and abs(s["cash"] - (50_000 - 40 * 105)) < 1e-6)
fl = av.manage_position(p, day(106.0, low3=104.0), s)
check("加仓 · 第 3 注 = 第 1 注的 1/4", fl and fl[0]["shares"] == 20 and p.level == 3, str(fl))
fl = av.manage_position(p, day(107.0, low3=105.0), s)
check("加仓 · 满 3 注不再加", not fl, str(fl))
p = pos(highest=104.0); s = st()
fl = av.manage_position(p, day(104.0, low3=101.0), s)
check("加仓 · 没突破前高(等于)不加", not fl, str(fl))
p = pos(highest=104.0); s = st(); s["equity"] = 30_000.0    # 80 股×104 = 27.7% > 25%
fl = av.manage_position(p, day(105.0, low3=101.0), s)
check("加仓 · 单股超 25% 不加(R-17)", not fl, str(fl))
p = pos(highest=104.0, level=-1); s = st()
fl = av.manage_position(p, day(105.0, low3=101.0), s)
check("加仓 · ⭐止盈状态下不加仓(原脚本会)", not fl, str(fl))

# ── run_day 整合:护栏、连亏、观察列表排序 ───────────────────
bars_map = {"AAA": bars, "BBB": bars_lv}
r = av.run_day("2026-04-24", [], 100_000.0, lambda c: bars_map.get(c), [("AAA", "甲", 90), ("BBB", "乙", 80)], None, 0)
check("整合 · 买入 AAA,BBB 没买(量能不够)", [f["symbol"] for f in r["fills"]] == ["AAA"] and len(r["positions"]) == 1, str(r["fills"]))
check("整合 · 权益 = 现金 + 持仓市值 = 起始资金(当天收盘成交)", abs(r["equity"] - 100_000) < 1e-6, str(r["equity"]))
check("整合 · 观察列表两项,已买的写「已持仓」还是「触发买入」", r["watch_items"][0]["symbol"] == "AAA" and "触发买入" in r["watch_items"][0]["gap"], str(r["watch_items"][0]))
r2 = av.run_day("2026-04-24", [], 100_000.0, lambda c: bars_map.get(c), [("AAA", "甲", 90)], 104_000.0, 0)
check("整合 · ⭐单日回撤 -3.8% 触发熔断,不开仓且被挡项说明", not r2["fills"] and r2["watch_items"][0].get("blocked") and "熔断" in r2["watch_items"][0]["blocked_reason"], str(r2["watch_items"]))
r3 = av.run_day("2026-04-24", [], 100_000.0, lambda c: bars_map.get(c), [("AAA", "甲", 90)], None, 3)
check("整合 · 连亏 3 笔不开仓", not r3["fills"] and "连亏" in r3["watch_items"][0]["blocked_reason"], str(r3["watch_items"]))
check("整合 · 被挡的排最后", r2["watch_items"][-1].get("blocked") is True)

total = passed + len(fails)
print(f"小鹿引擎用例 {total} 条")
if fails:
    print(f"FAIL {len(fails)} 条:")
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("ALL OK")
