# -*- coding: utf-8 -*-
"""小鹿智能体 · 方向 A「VCP · SEPA 优化」引擎用例(纯计算)。

    cd apps/api && PYTHONPATH=. python tests/test_agent_vcp4.py
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.services.quant import agent_vcp as av, agent_vcp3 as c3, agent_vcp4 as c4   # noqa: E402

fails: list[str] = []
passed = 0


def check(name, cond, extra=""):
    global passed
    if cond:
        passed += 1
    else:
        fails.append(f"{name}  {extra}")


def path(legs, start=50.0):
    out, p, d = [], start, date(2025, 1, 6)
    for n, target, v, rng in legs:
        step = (target - p) / n
        for _ in range(n):
            p += step
            while d.weekday() >= 5:
                d += timedelta(days=1)
            out.append((d, p, p * (1 + rng), p * (1 - rng), v))
            d += timedelta(days=1)
    return out


def next_day(d):
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


# 200 根上升趋势(均线多头)+ 三次收缩(16% / 8% / 4%)+ 末段缩量
BASE = [(200, 95.0, 1000.0, 0.01),
        (15, 80.0, 1000.0, 0.015), (15, 96.0, 900.0, 0.015),
        (10, 89.0, 800.0, 0.01), (10, 97.0, 800.0, 0.01),
        (6, 94.5, 400.0, 0.005), (6, 96.5, 500.0, 0.005),
        (4, 97.0, 450.0, 0.004)]
bars = path(BASE)
bench = {b[0]: 100.0 - (i % 2) for i, b in enumerate(bars)}      # 隔天跌一天(抗跌项用)
ind0 = c4.indicators(bars, bench=bench)
check("指标 · 趋势模板算得出且多头排列、200 日上行", ind0 and ind0["trend"] and ind0["trend"]["stack"] and ind0["trend"]["rising200"], str(ind0 and ind0["trend"]))
check("指标 · 形态:收缩 2~4 次、末次 <5%、低点量比 ≤0.8", ind0 and 2 <= ind0["contractions"] <= 4 and ind0["last_depth"] < 5 and ind0["low_vol_ratio"] <= 0.8,
      str((ind0["contractions"], ind0["last_depth"], ind0["low_vol_ratio"])))
check("指标 · 还没突破:没有 breakout", ind0["breakout"] is None and ind0["close"] < ind0["pivot"])

atr, pivot = ind0["atr20"], ind0["pivot"]
m49 = sum(b[4] for b in bars[-49:]) / 49


def brk_bar(d, px, vol, hi=None, lo=None):
    return (d, px, hi if hi is not None else px + 0.1 * atr, lo if lo is not None else pivot - 0.3 * atr, vol)


# 突破日:收盘 = 枢轴 + 0.2 ATR,量 = 2 × 前 49 天均量,收在当日区间上部
d1 = next_day(bars[-1][0])
brk = bars + [brk_bar(d1, pivot + 0.2 * atr, 2.0 * m49)]
ind1 = c4.indicators(brk, bench=bench)
f1 = c4.entry_flags(ind1)
check("买入 · ⭐突破日:趋势模板 + 触发 + 确认三条全过", f1["V-02"] and f1["V-03"] and f1["V-04"] and c4.entry_ok(ind1), str(f1))
check("指标 · breakout 记录突破日:0 天前、量比约 1.96、低点 = 枢轴 − 0.3 ATR", ind1["breakout"]["days_ago"] == 0 and abs(ind1["breakout"]["vol_ratio"] - 2.0 * 50 / 51) < 0.01
      and abs(ind1["breakout"]["low"] - (pivot - 0.3 * atr)) < 1e-9, str(ind1["breakout"]))
check("指标 · 收盘在当日区间上 1/3(0.83)", 0.8 <= ind1["close_pos"] <= 0.86, str(ind1["close_pos"]))
# 追高
ind_w = c4.indicators(bars + [brk_bar(d1, pivot + 0.4 * atr, 2.0 * m49)], bench=bench)
fw = c4.entry_flags(ind_w)
check("买入 · ⭐高出 0.4 ATR:等回踩,不买", not fw["V-03"] and fw["wait"] and "等回踩" in c4.entry_checks(ind_w)[1]["text"], str(c4.entry_checks(ind_w)[1]))
ind_x = c4.indicators(bars + [brk_bar(d1, pivot + 0.7 * atr, 2.0 * m49, hi=pivot + 0.8 * atr)], bench=bench)
check("买入 · 高出 0.7 ATR:放弃", not c4.entry_flags(ind_x)["V-03"] and "放弃" in c4.entry_checks(ind_x)[1]["text"])
# 回踩:突破日冲到 +0.4 ATR,次日回到 +0.2 ATR(量小)→ 触发用回踩日、确认用突破日的量
d2 = next_day(d1)
pull = bars + [brk_bar(d1, pivot + 0.4 * atr, 2.0 * m49)] + [brk_bar(d2, pivot + 0.2 * atr, 0.6 * m49, hi=pivot + 0.25 * atr, lo=pivot + 0.05 * atr)]
ind_p = c4.indicators(pull, bench=bench)
fp = c4.entry_flags(ind_p)
check("买入 · ⭐回踩日可买:突破在 1 天前,量看突破日(≥1.5×)", fp["V-03"] and fp["V-04"] and ind_p["breakout"]["days_ago"] == 1 and ind_p["breakout"]["vol_ratio"] >= 1.5, str((fp, ind_p["breakout"])))
# 确认失败
ind_lv = c4.indicators(bars + [brk_bar(d1, pivot + 0.2 * atr, 1.2 * m49)], bench=bench)
check("买入 · 突破日量只有 1.2× → 确认不过", c4.entry_flags(ind_lv)["V-03"] and not c4.entry_flags(ind_lv)["V-04"] and "要 ≥1.5×" in c4.entry_checks(ind_lv)[2]["text"])
ind_lo = c4.indicators(bars + [brk_bar(d1, pivot + 0.2 * atr, 2.0 * m49, hi=pivot + 1.2 * atr)], bench=bench)
check("买入 · 收盘在当日区间下部(冲高回落)→ 确认不过", not c4.entry_flags(ind_lo)["V-04"] and ind_lo["close_pos"] < 0.67, str(ind_lo["close_pos"]))
# 趋势模板失败:距 52 周高点太远
ind_far = dict(ind1, trend=dict(ind1["trend"], near_high=0.3))
check("买入 · 距 52 周高点 30% → 趋势模板不过", not c4.entry_flags(ind_far)["V-02"] and "52 周高点" in c4.entry_checks(ind_far)[0]["text"])

# ── 止损 / 走廊 / 评分 ──────────────────────────────────────────
stop1, capped1 = c4.stop_of(ind1)
check("止损 · ⭐突破日低点与枢轴下方 1 ATR 取高者(这里是突破日低点)", abs(stop1 - (pivot - 0.3 * atr)) < 1e-9 and not capped1, str((stop1, capped1)))
ind_far_stop = dict(ind1, breakout=dict(ind1["breakout"], low=ind1["close"] * 0.85), atr20=ind1["close"] * 0.2)
check("止损 · 比 -7% 还远 → capped", c4.stop_of(ind_far_stop)[1])
check("评分 · 档位下限 S 400 / A 350 / B 300 / C 250", [c3._tier(x, c4.GRADE_MIN) for x in (400, 399, 350, 300, 250, 249)] == ["S", "A", "A", "B", "C", "D"])
gr = c4.grade(ind1, c4.PARAMS, 92)
check("评分 · ⭐五项加权:每项分 = 档位分 × 权重(形态 1.75 / 量价 1 / 抗跌 0.75 / 走廊 1 / MACD 0.5),总分 = 和",
      all(x[2] == round(c3.SUB_POINTS[x[1]] * c4.WEIGHT[x[0]]) for x in gr["factors"]) and gr["points"] == sum(x[2] for x in gr["factors"]) and gr["points"] <= 500, str(gr))
check("评分 · 教科书形态 + RS 92 + 一年新高:没有否决", gr["vetoes"] == [] and gr["factors"][3][1] == "S", gr["text"])
ind_bad = dict(ind1, contractions=1, last_depth=15.0, low_vol_ratio=1.2, close=pivot + 0.8 * atr, recent_vols=[ind1["vol_sma20"]] * len(ind1["recent_vols"]))
check("评分 · ⭐否决:形态 <B(收缩 1 次、量比 1.2、追高、没放量 → 形态 C)", any("形态" in v for v in c4.grade(ind_bad, c4.PARAMS, 85)["vetoes"]), str(c4.grade(ind_bad, c4.PARAMS, 85)["vetoes"]))
check("评分 · ⭐否决:RS 75 < 80", any("RS" in v for v in c4.grade(ind1, c4.PARAMS, 75)["vetoes"]))
ind_corr = dict(ind1, res_above=(ind1["close"] + 2.5 * (ind1["close"] - stop1), "底部左侧前高"))
check("评分 · ⭐否决:走廊 2.5R < 3R(方向 C 是 2R 剔除,这里更严)", any("走廊" in v for v in c4.grade(ind_corr, c4.PARAMS, 92)["vetoes"]))
check("评分 · 否决:止损距离 >7%", any("止损距离" in v for v in c4.grade(ind_far_stop, c4.PARAMS, 92)["vetoes"]))

# ── 开仓:市场过滤、风险定仓、板块与总风险上限 ───────────────────
mk_ok = {"ok": True, "text": "标普顺风"}
mk_bad = {"ok": False, "text": "标普在 50 日线下方"}


def st_(market=mk_ok, **kw):
    s = {"date": str(d1), "cash": 100_000.0, "positions": [], "closed": [], "closed_pnl": [], "equity": 100_000.0,
         "halt_reason": None, "open_risk": 0.0, "market": market}
    s.update(kw)
    return s


s0 = st_()
fill, blocked = c4.try_entry("AAA", "甲", ind1, s0, score=92, sector="Tech")
check("开仓 · ⭐三条全过 + 市场顺风 → 成交,挂 V-03,带档位与板块", fill and fill["rule_id"] == "V-03" and fill["grade"] == gr["grade"] and s0["positions"][0].extra["sector"] == "Tech", str(fill))
pos = s0["positions"][0]
r1 = pos.entry_price - pos.stop
full = min(int(100_000 * c4.PARAMS["risk_pct"][gr["grade"]] / r1), int(100_000 * 0.20 / pos.entry_price))
check("开仓 · ⭐股数按风险算:计划 = 风险金额 ÷ (买入价 − 止损),单票 ≤20%", pos.initial_size == full, str((pos.initial_size, full, gr["grade"])))
check("开仓 · ⭐S 级首仓一半,其他档位一次买满", pos.size == (int(full * 0.5) if gr["grade"] == "S" else full), str((pos.size, full, gr["grade"])))
check("开仓 · 理由写市场、评分、风险、止损、计划股数", all(k in fill["rationale"] for k in ("市场", "评分", "风险", "止损", "计划")), fill["rationale"][:200])
fb, bb = c4.try_entry("AAA", "甲", ind1, st_(market=mk_bad), score=92)
check("开仓 · ⭐市场逆风不开新仓并说明(V-01)", fb is None and bb and "V-01" in bb, str(bb))
fn, bn = c4.try_entry("AAA", "甲", ind1, st_(market=None), score=92)
check("开仓 · 没有基准数据也不开仓", fn is None and "V-01" in bn)
sec_pos = [av.Position("X1", "x", 10, 10, 50.0, "2026-01-01", 50.0, 50.0, 1, 3, "V-03", 48.0, 2.0, {"sector": "Tech"}),
           av.Position("X2", "x", 10, 10, 50.0, "2026-01-01", 50.0, 50.0, 1, 3, "V-03", 48.0, 2.0, {"sector": "Tech"})]
fs, bs = c4.try_entry("AAA", "甲", ind1, st_(positions=sec_pos), score=92, sector="Tech")
check("开仓 · ⭐同板块已持 2 只 → 不进(V-07)", fs is None and "板块" in bs and "V-07" in bs, str(bs))
fs2, _ = c4.try_entry("AAA", "甲", ind1, st_(positions=sec_pos), score=92, sector="Energy")
check("开仓 · 别的板块照常进", fs2 is not None)
fh, bh = c4.try_entry("AAA", "甲", ind1, st_(open_risk=3000.0), score=92)
check("开仓 · ⭐组合开放风险已到 3% → 不进(V-07)", fh is None and "V-07" in bh and "3%" in bh, str(bh))
fh2, _ = c4.try_entry("AAA", "甲", ind1, st_(open_risk=2900.0), score=92)
check("开仓 · 剩 0.1% 风险额度 → 按余额缩到 100 ÷ R 股", fh2 and fh2["shares"] == (int(int(100.0 / r1) * 0.5) if gr["grade"] == "S" else int(100.0 / r1)), str(fh2 and fh2["shares"]))
five = [av.Position(f"X{i}", "x", 10, 10, 50.0, "2026-01-01", 50.0, 50.0, 1, 3, "V-03", 48.0, 2.0, {"sector": f"S{i}"}) for i in range(5)]
f5, b5 = c4.try_entry("AAA", "甲", ind1, st_(positions=five), score=92)
check("开仓 · 最多 5 只", f5 is None and "上限 5" in b5)


# ── 持仓管理 ───────────────────────────────────────────────────
def pos_(entry=100.0, stop=95.0, size=200, plan=400, held=0, highest=None, grade="A", level=1, pivot=99.0, risk=5.0):
    # risk 是**初始** 1R(入场 100 − 初始止损 95),止损上移后 risk 不变
    return av.Position("AAA", "甲", size, plan, entry, "2026-04-01", entry, highest or entry, level, held, "V-03", stop, risk,
                       {"pivot": pivot, "grade": grade, "sector": "Tech", "plan": plan})


def day(px, hi=None, lo=None, sma20=None, sma50=None, vr=1.0, close_pos=0.8):
    return {"close": px, "high": hi if hi is not None else px * 1.01, "low": lo if lo is not None else px * 0.99,
            "volume": 1e3, "atr20": 1.0, "sma20": sma20, "sma50": sma50, "vr_today": vr, "close_pos": close_pos}


def stt():
    return {"cash": 50_000.0, "equity": 100_000.0, "closed": [], "closed_pnl": [], "positions": [], "open_risk": 0.0}


p = pos_(); s = stt()
fl = c4.manage_position(p, day(96.0, hi=98.0, lo=94.5), s)
check("卖出 · ⭐盘中最低触及止损 → 按止损价 95 成交(不是收盘 96),V-06", fl and fl[0]["rule_id"] == "V-06" and fl[0]["price"] == 95.0 and p.size == 0, str(fl))
p = pos_(); s = stt()
fl = c4.manage_position(p, day(92.0, hi=93.0, lo=91.0), s)
check("卖出 · ⭐跳空跌过止损(当日最高也在止损下方)→ 按收盘 92 成交", fl and fl[0]["price"] == 92.0 and fl[0]["rule_id"] == "V-06", str(fl))
# 1R 保本 + S 级加仓
p = pos_(grade="S"); s = stt()
fl = c4.manage_position(p, day(105.5), s)
check("持仓 · ⭐到 1R:止损上移到成本;S 级加 1/4 计划股数(100 股),level 2", p.stop == 100.0 and p.level == 2 and p.size == 300 and fl and fl[0]["rule_id"] == "V-08" and fl[0]["shares"] == 100, str((p.stop, p.level, p.size, fl)))
check("持仓 · 加仓扣现金、均价上移", abs(s["cash"] - (50_000 - 100 * 105.5)) < 1e-6 and 100.0 < p.avg_cost < 105.5, str((s["cash"], p.avg_cost)))
p = pos_(grade="A"); s = stt()
fl = c4.manage_position(p, day(105.5), s)
check("持仓 · A 级到 1R 只保本不加仓", p.stop == 100.0 and p.level == 1 and p.size == 200 and not fl)
# 2R:跟 20 日线;3R:50 日线 / 吊灯取高
p = pos_(grade="S", level=2, size=300); s = stt()
fl = c4.manage_position(p, day(110.5, sma20=104.0), s)
check("持仓 · ⭐到 2R:再加 1/4 → level 3;止损 = max(1R=105, 20 日线 104) = 105", p.level == 3 and p.size == 400 and p.stop == 105.0, str((p.level, p.size, p.stop)))
p = pos_(level=3, size=400, highest=115.5); s = stt()
fl = c4.manage_position(p, day(115.0, sma20=108.0, sma50=110.0), s)
check("持仓 · ⭐到 3R:止损 = max(50 日线 110, 最高 115.5 − 3 ATR = 112.5) = 112.5", p.stop == 112.5 and not fl, str((p.stop, fl)))
p = pos_(level=3, size=400, highest=112.0, stop=104.0); s = stt()
fl = c4.manage_position(p, day(103.0, hi=104.5, lo=102.0, sma20=105.0), s)
check("卖出 · 到过 2R 后盘中跌破跟踪止损 → V-10 按止损价", fl and fl[0]["rule_id"] == "V-10" and fl[0]["price"] == 104.0)
p = pos_(level=3, size=400, highest=112.0, stop=101.0); s = stt()
fl = c4.manage_position(p, day(103.0, hi=104.0, lo=102.5, sma20=105.0), s)
check("卖出 · ⭐今收已在 20 日线之下(没触及旧止损)→ 当天按收盘出 V-10,不等明天", fl and fl[0]["rule_id"] == "V-10" and fl[0]["price"] == 103.0 and p.size == 0, str(fl))
# 时间止损
p = pos_(held=9); s = stt()
fl = c4.manage_position(p, day(98.5), s)
check("卖出 · ⭐第 10 天没创新高且跌回枢轴(99)下方 → V-11 清仓", fl and fl[0]["rule_id"] == "V-11" and p.size == 0, str(fl))
p = pos_(held=9); s = stt()
fl = c4.manage_position(p, day(100.5), s)
check("持仓 · 第 10 天没创新高但仍在枢轴上方 → 不动", not fl and p.size == 200)
p = pos_(held=14, highest=103.0); s = stt()
fl = c4.manage_position(p, day(101.0), s)
check("卖出 · ⭐第 15 天没到 1R、仍在枢轴上方 → 减半(一次)", fl and fl[0]["rule_id"] == "V-11" and fl[0]["shares"] == 100 and p.size == 100 and p.extra["half_done"], str(fl))
fl2 = c4.manage_position(p, day(101.0), s)
check("持仓 · 减半只做一次", not fl2 and p.size == 100)
p = pos_(held=14, highest=103.0); s = stt()
fl = c4.manage_position(p, day(98.0), s)
check("卖出 · 第 15 天没到 1R 且跌破枢轴 → 清仓", fl and p.size == 0 and fl[0]["shares"] == 200)
# 高潮减仓
p = pos_(level=3, size=300, highest=111.0, stop=105.0); s = stt()
fl = c4.manage_position(p, day(110.0, hi=116.0, lo=109.0, sma20=104.0, vr=3.0, close_pos=0.15), s)
check("卖出 · ⭐到过 2R 后放量滞涨(3× 量、收在区间下部)→ 减 1/3,V-12", fl and fl[0]["rule_id"] == "V-12" and fl[0]["shares"] == 100 and p.extra["climax_done"], str(fl))

# ── 市场过滤 ───────────────────────────────────────────────────
mdates = [b[0] for b in bars]
bull = [(d, 100 + 0.1 * i - 0.3 * (i % 2), 100 + 0.1 * i + 0.5, 100 + 0.1 * i - 0.8, 1e9) for i, d in enumerate(mdates)]
mk = c4.market_regime(bull)
check("市场 · ⭐上升趋势、无放量下跌日 → 顺风", mk["ok"] and mk["dist_days"] == 0 and "顺风" in mk["text"], str(mk))
bear = [(d, 200 - 0.3 * i, 200 - 0.3 * i + 0.5, 200 - 0.3 * i - 0.5, 1e9) for i, d in enumerate(mdates)]
mkb = c4.market_regime(bear)
check("市场 · 跌破 50 / 200 日线 → 逆风", not mkb["ok"] and mkb["above"] is False and "逆风" in mkb["text"])
dist = list(bull)
for k in range(1, 13, 2):                                        # 最近 25 天里 6 个放量下跌日
    i = len(dist) - k
    dd, c, h, lo, v = dist[i]
    dist[i] = (dd, dist[i - 1][1] * 0.99, h, lo, 2e9)
mkd = c4.market_regime(dist)
check("市场 · ⭐25 天内 6 个分布日(跌 ≥0.2% 且放量)→ 逆风,即便均线仍多头", not mkd["ok"] and mkd["dist_days"] == 6 and "分布日" in mkd["text"], str(mkd))
check("市场 · 不足 200 根算不出 → 不开新仓", not c4.market_regime(bull[:100])["ok"])

# ── 整合 ───────────────────────────────────────────────────────
bars_map = {"AAA": brk}


def ind_of_factory(market):
    def ind_of(code):
        if code == c4.MARKET_KEY:
            return market
        if code == c4.SECTORS_KEY:
            return {"AAA": "Tech"}
        return c4.indicators(bars_map[code], bench=bench) if code in bars_map else None
    return ind_of


r = c4.run_day(str(d1), [], 100_000.0, lambda c: bars_map.get(c), [("AAA", "甲", 92)], None, 0, ind_of=ind_of_factory(mk))
check("整合 · ⭐顺风 + 突破日 → 买入,权益不变,持仓带板块", [f["symbol"] for f in r["fills"]] == ["AAA"] and abs(r["equity"] - 100_000) < 1e-6
      and r["positions"][0].extra["sector"] == "Tech" and r["market"]["ok"], str(r["fills"]))
rb = c4.run_day(str(d1), [], 100_000.0, lambda c: bars_map.get(c), [("AAA", "甲", 92)], None, 0, ind_of=ind_of_factory(mkb))
check("整合 · ⭐逆风 → 不买,观察列表写市场逆风(V-01)", not rb["fills"] and "V-01" in rb["watch_items"][0]["blocked_reason"], str(rb["watch_items"]))
rr = c4.run_day(str(d1), [], 100_000.0, lambda c: bars_map.get(c), [("AAA", "甲", 70)], None, 0, ind_of=ind_of_factory(mk))
check("整合 · RS 70 → 一票否决(V-05)", not rr["fills"] and "V-05" in rr["watch_items"][0]["blocked_reason"] and "RS" in rr["watch_items"][0]["blocked_reason"])
check("文案 · 参数改了规则手册跟着变", "0.25" in c4.rules_for()[2]["condition"] and "0.35" in c4.rules_for(dict(c4.PARAMS, atr_chase=0.35))[2]["condition"])
check("接口 · 引擎常量齐全", c4.ENTRY_RULE == "V-03" and c4.ADD_RULE == "V-08" and c4.GRADE_RULE == "V-05" and set(c4.STOP_KEYS) <= set(c4.PARAMS)
      and callable(c4.summary) and c4.MARKET_KEY and c4.SECTORS_KEY)

total = passed + len(fails)
print(f"方向 A(SEPA 优化)引擎用例 {total} 条")
if fails:
    print(f"FAIL {len(fails)} 条:")
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("ALL OK")
