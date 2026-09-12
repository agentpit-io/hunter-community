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
        (6, 95.5, 400.0, 0.01), (6, 97.0, 500.0, 0.01),
        (4, 97.5, 450.0, 0.006)]
bars = path(BASE)
# 同一形态但末次收缩很深(93):止损离收盘超过 8%,会被封顶 → 新规则下不进
DEEP = BASE[:5] + [(6, 91.0, 400.0, 0.01), (6, 97.0, 500.0, 0.01), (4, 97.5, 450.0, 0.006)]
bars_deep = path(DEEP)
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
# 基准:隔一天跌一天(奇数根),这只票在这些天多数在涨 → 抗跌次数高
bench = {b[0]: 100.0 - (i % 2) for i, b in enumerate(brk)}
ind2 = c3.indicators(brk, bench=bench)
f2 = c3.entry_flags(ind2)
check("买入 · ⭐突破日收盘站上枢轴 0.5 ATR + 放量 → 两条都过", f2["C-01"] and f2["C-02"], str(f2))
state = {"date": str(brk[-1][0]), "cash": 100_000.0, "positions": [], "closed": [], "closed_pnl": [], "equity": 100_000.0, "halt_reason": None, "open_risk": 0.0}
stop0, capped0 = c3.stop_of(ind2)
check("止损 · 底部低点下方 0.5 ATR,没被封顶", not capped0 and abs(stop0 - (ind2["base_low"] - 0.5 * ind2["atr20"])) < 1e-9, str((stop0, capped0, ind2["close"])))
gr = c3.grade(ind2, c3.PARAMS, 95)
check("评分 · 五项各 S~D 计 100/80/60/40/0,总分 0~500", len(gr["factors"]) == 5 and 0 <= gr["points"] <= 500
      and all(x[2] == c3.SUB_POINTS[x[1]] for x in gr["factors"]) and gr["points"] == sum(x[2] for x in gr["factors"]), str(gr))
fg, fpts, ff = c3.form_grade(ind2, c3.PARAMS, 95)
check("评分 · 第 1 项形态 = 第一版 10 分制", len(ff) == 5 and 0 <= fpts <= 10 and gr["factors"][0][1] == fg and gr["form_points"] == fpts, str((fg, fpts)))
check("评分 · 教科书形态 + RS 95 + 基准(抗跌 S)至少 B 级", gr["grade"] in ("S", "A", "B") and gr["factors"][2][1] == "S", gr["text"])
check("评分 · 盈亏比按量度目标:枢轴 × (1 + 首次收缩深度)", abs(c3.rr_ratio(ind2)[1] - ind2["pivot"] * (1 + ind2["first_depth"] / 100)) < 1e-9, str(c3.rr_ratio(ind2)))
fill, blocked = c3.try_entry("AAA", "甲", ind2, state, score=95)
check("买入 · 成交挂 C-01,带评分,止损来自形态", fill and fill["rule_id"] == "C-01" and fill["grade"] == gr["grade"] and abs(state["positions"][0].stop - stop0) < 1e-9, str(fill))
pos = state["positions"][0]
check("买入 · ⭐股数 = 评分对应仓位 × 总资产 ÷ 收盘(不超总风险上限)", pos.size == min(int(100_000 * c3.PARAMS["grade_size"][gr["grade"]] / pos.entry_price), int(4000.0 / (pos.entry_price - pos.stop))), str((pos.size, gr["grade"], pos.entry_price, pos.stop)))
check("买入 · rationale 有评分、止损位、开放风险", "评分" in fill["rationale"] and "止损" in fill["rationale"] and "开放风险" in fill["rationale"], fill["rationale"][:160])
# ⭐止损被封顶(末次收缩太深)→ 不进
ind_deep = c3.indicators(bars_deep + [(brk[-1][0], c3.indicators(bars_deep)["pivot"] + 0.5 * atr, c3.indicators(bars_deep)["pivot"] + 0.7 * atr, c3.indicators(bars_deep)["pivot"] - 0.2 * atr, 1400.0)])
_s, capped_d = c3.stop_of(ind_deep)
st_d = dict(state, positions=[], cash=100_000.0, open_risk=0.0)
fd, bd = c3.try_entry("DDD", "深", ind_deep, st_d, score=95)
check("买入 · ⭐止损被 -8% 封顶的形态不进,并说明(C-04)", capped_d and fd is None and bd and "C-04" in bd, str((capped_d, bd)))
# ⭐D 级不买
ind_bad = dict(ind2, contractions=1, last_depth=15.0, low_vol_ratio=1.2, close=ind2["pivot"] + 0.8 * atr,
               recent_vols=[ind2["vol_sma20"] * 1.35] * len(ind2["recent_vols"]),   # 突破质量只有 1 分(放量 1.35×、高出 0.8 ATR)
               vp_net_63=0, defense_63=(1, 30), first_depth=8.0, macd_d=False, macd_w=False)   # 量价 D、抗跌 D、盈亏比 <1 D、无金叉 C
gd = c3.grade(ind_bad, c3.PARAMS, 60)
st_b = dict(state, positions=[], cash=100_000.0, open_risk=0.0)
fb, bb = c3.try_entry("BBB", "乙", ind_bad, st_b, score=60)
check("买入 · ⭐评分够不上 C 级(D)达到信号也不买", gd["grade"] == "D" and fb is None and bb and "D 级" in bb, str((gd["text"], bb)))
# ⭐组合总风险上限:已有开放风险 3.9% 时只能放 0.1%
st_h = dict(state, positions=[], cash=100_000.0, open_risk=3900.0)
fh, bh = c3.try_entry("HHH", "热", ind2, st_h, score=95)
check("买入 · ⭐组合开放风险快满时按余额缩仓", fh and fh["shares"] == int(100.0 / (fh["price"] - st_h["positions"][0].stop)), str((fh and fh["shares"], bh)))
st_h2 = dict(state, positions=[], cash=100_000.0, open_risk=4000.0)
fh2, bh2 = c3.try_entry("HHH", "热", ind2, st_h2, score=95)
check("买入 · ⭐组合开放风险已满就不进并说明(C-09)", fh2 is None and bh2 and "C-09" in bh2, str(bh2))

# ── 五项评分的子项 ──────────────────────────────────────────────
check("评分 · 档位下限按用户给的:量价 >5 S / >4 A / >3 B / >2 C / ≤2 D",
      [c3._tier(x, c3.VP_MIN) for x in (6, 5, 4, 3, 2)] == ["S", "A", "B", "C", "D"])
check("评分 · 抗跌 >15 S / >10 A / >5 B / >2 C / ≤2 D", [c3._tier(x, c3.DEF_MIN) for x in (16, 15, 11, 6, 3, 2)] == ["S", "A", "A", "B", "C", "D"])
check("评分 · 盈亏比 ≥5 S / ≥4 A / ≥3 B / ≥2 C,不足 2 都是 D", [c3._tier(x, c3.RR_MIN) for x in (5.0, 4.9, 3.0, 2.0, 1.5, 0.5)] == ["S", "A", "B", "C", "D", "D"])
check("评分 · 总分 ≥350 S / ≥300 A / ≥250 B / ≥200 C / <200 D", [c3._tier(x, c3.GRADE_MIN) for x in (350, 349, 300, 250, 200, 199)] == ["S", "A", "A", "B", "C", "D"])
# 第 2 项:最近 63 天涨日放量、跌日缩量 → 净 +63;反过来 → −63
cl = [100 + (i % 2) for i in range(120)]                      # 交替涨跌
vol_good = [2000.0 if (i % 2 == 1) else 500.0 for i in range(120)]   # 涨日(奇数根)量大
check("评分 · ⭐量价配合:涨放量跌缩量全对 → +63(S)", c3._vp_net(cl, vol_good) == 63 and c3._tier(c3._vp_net(cl, vol_good), c3.VP_MIN) == "S")
vol_bad = [500.0 if (i % 2 == 1) else 2000.0 for i in range(120)]
check("评分 · 量价配合:涨缩量跌放量全错 → −63(D)", c3._vp_net(cl, vol_bad) == -63)
check("评分 · 量价配合:不足 113 根算不出 → None", c3._vp_net(cl[:100], vol_good[:100]) is None)
# 第 3 项:基准天天跌、票天天涨 → 63;基准天天涨 → 下跌 0 天
dd0 = date(2026, 1, 5)
bb = [(dd0 + timedelta(days=i), 100.0 + i, 101.0 + i, 99.0 + i, 1000.0) for i in range(80)]
bench_dn = {b[0]: 200.0 - i for i, b in enumerate(bb)}
bench_up = {b[0]: 200.0 + i for i, b in enumerate(bb)}
check("评分 · ⭐抗跌:基准跌 63 天票全不跌 → (63, 63) S", c3._defense(bb, bench_dn) == (63, 63))
check("评分 · 抗跌:基准没跌过 → (0, 0) D;没基准 → None", c3._defense(bb, bench_up) == (0, 0) and c3._defense(bb, None) is None)
# 第 5 项:横盘 80 天后连涨 5 天 → 日线金叉在 5 天内;一路跌 → 没有
flat_up = [100.0] * 80 + [101.0, 102.5, 104.0, 106.0, 108.0]
check("评分 · ⭐MACD:横盘后连涨 5 天,日线金叉在窗口内", c3._macd_cross(flat_up, 5) is True)
check("评分 · MACD:一路下跌没有金叉;根数不够 → None", c3._macd_cross([100.0 - i * 0.5 for i in range(85)], 5) is False and c3._macd_cross([100.0] * 30, 5) is None)
old_up = [100.0] * 60 + [100 + i * 1.5 for i in range(1, 26)]       # 25 天前就金叉了,现在还在上方
check("评分 · MACD:金叉太早(不在最近 5 根内)不算「突破时」", c3._macd_cross(old_up, 5) is False)
wk = c3._weekly_closes(bb[:12])
check("评分 · 周收盘按 ISO 周分组,取每周最后一根", len(wk) == 2 and wk[-1] == bb[11][1], str(wk))
# 组合:三项 S + 盈亏比 A + 形态 ≥ B → S 级
gs = c3.grade(dict(ind2, vp_net_63=7, defense_63=(20, 30), macd_d=True, macd_w=True), c3.PARAMS, 95)
check("评分 · ⭐量价 S + 抗跌 S + 日周金叉 S → 总分 ≥350 S 级", gs["grade"] == "S" and gs["points"] >= 350 and gs["factors"][4][1] == "S", gs["text"])
gw = c3.grade(dict(ind2, macd_d=False, macd_w=True), c3.PARAMS, 95)
check("评分 · 只有周线金叉 A、只有日线 B、没有 C", gw["factors"][4][1] == "A" and c3.grade(dict(ind2, macd_d=True, macd_w=False))["factors"][4][1] == "B"
      and c3.grade(dict(ind2, macd_d=False, macd_w=False))["factors"][4][1] == "C")
gn = c3.grade(dict(ind2, vp_net_63=None, defense_63=None), c3.PARAMS, 95)
check("评分 · 算不出的项按 D 计 0 分并写原因", gn["factors"][1][2] == 0 and "算不出" in gn["factors"][1][3] and gn["factors"][2][2] == 0 and "基准" in gn["factors"][2][3], gn["text"])

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
