# -*- coding: utf-8 -*-
"""小鹿智能体 · 优化器 + 模拟器用例(纯计算)。

    cd apps/api && PYTHONPATH=. python tests/test_agent_opt.py

重点盯两件事:止损参数绝不能放宽;防过拟合的门槛(样本 / 两段 / 观察期 / 冷却 / 评估频率)真的在拦。
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.services.quant import agent_vcp as av, agent_opt as ao, agent_sim   # noqa: E402

fails: list[str] = []
passed = 0


def check(name, cond, extra=""):
    global passed
    if cond:
        passed += 1
    else:
        fails.append(f"{name}  {extra}")


BASE = dict(av.PARAMS)

# ── 候选生成:一次一个参数、止损只收紧 ─────────────────────────
cs = ao.candidates("sell", BASE)
keys = {c["key"] for c in cs}
check("候选 · 方向 B 只动卖出侧参数", keys <= set(ao.BRANCHES["sell"]["tunable"]), str(keys))
check("候选 · 一次只动一个参数", all(sum(1 for k in BASE if c["params"][k] != BASE[k]) == 1 for c in cs))
stops = [(c["key"], c["value"]) for c in cs if c["key"] in av.STOP_KEYS]
check("⭐候选 · 止损类参数没有一个比基准宽", all(v < BASE[k] for k, v in stops), str(stops))
check("⭐候选 · 硬止损 8% 的候选只有 6% / 7%", sorted(v for k, v in stops if k == "max_stop_pct") == [0.06, 0.07], str(stops))
check("⭐allowed · 相对当前也不许放宽(6% → 7% 不行)", not ao.allowed("max_stop_pct", 0.07, cur=dict(BASE, max_stop_pct=0.06)))
check("allowed · 放宽止损被拒", not ao.allowed("max_stop_pct", 0.10) and not ao.allowed("half_loss_pct", 0.06)
      and ao.allowed("max_stop_pct", 0.06) and ao.allowed("tp1", 0.5))
check("候选 · 止盈档位必须递增(tp1=0.12 时 tp2=0.13 仍合法,tp3=0.18 与 tp2=0.15 合法)",
      all(c["params"]["tp1"] < c["params"]["tp2"] < c["params"]["tp3"] for c in cs))
P4 = dict(ao.engine_of("buy").PARAMS)          # 2026-09-13 起方向 A 是 agent_vcp4
cb = ao.candidates("buy", P4)
check("候选 · 方向 A 只动买入侧(vcp4 的四个买入档位)", {c["key"] for c in cb} <= set(ao.BRANCHES["buy"]["tunable"]) and cb
      and all(k in ("atr_chase", "vol_boost", "breakout_window", "vcp_last_depth_max") for k in {c["key"] for c in cb}))
check("候选 · 基准方向没有候选", ao.candidates("base", BASE) == [])
# 用户把当前值改到档位之外时,当前值不在候选里(不会退回去)
cur2 = dict(BASE, max_stop_pct=0.06)
check("候选 · 已收紧到 6% 后不会再出 7%/8% 的候选(那是放宽)", all(v < 0.06 for k, v in [(c["key"], c["value"]) for c in ao.candidates("sell", cur2) if c["key"] == "max_stop_pct"]))

# ── 同参数不许来回改 / 邻域 ─────────────────────────────────────
hist = [{"key": "time1_days", "old": 5, "value": 7, "version": 2}]
cs2 = ao.candidates("sell", dict(BASE, time1_days=7), versions=hist)
check("⭐候选 · 最近改过的参数冻结(time1_days 不再当候选)", not any(c["key"] == "time1_days" for c in cs2))
hist2 = [{"key": "time1_days", "old": 5, "value": 7, "version": 2}, {"key": "tp1", "old": 0.10, "value": 0.12, "version": 3},
         {"key": "tp2", "old": 0.15, "value": 0.18, "version": 4}]
cs3 = ao.candidates("sell", dict(BASE, time1_days=7, tp1=0.12, tp2=0.18), versions=hist2)
vals = [c["value"] for c in cs3 if c["key"] == "time1_days"]
check("⭐候选 · 冻结期过了也不许改回历史旧值(5 天不再出现)", vals and 5 not in vals, str(vals))
nb = ao.neighbors("sell", BASE, "time1_days", 7)
check("邻域 · 7 天的邻档是 5(当前,跳过)和无 → 空;3 天的邻档是 5(当前)→ 空", nb == [] and ao.neighbors("sell", BASE, "time1_days", 3) == [])
nb2 = ao.neighbors("buy", P4, "vol_boost", 1.5)
check("邻域 · 方向 A(vcp4)vol_boost 1.5 的邻档是 1.2;1.2 的邻档去掉当前 1.0 只剩 1.5",
      [x["vol_boost"] for x in nb2] == [1.2] and [x["vol_boost"] for x in ao.neighbors("buy", P4, "vol_boost", 1.2)] == [1.5], str(nb2))
nb3 = ao.neighbors("c", c3_base := dict(ao.engine_of("c").PARAMS), "time_days", 20)
check("邻域 · C 方向 time_days 20 的邻档是 15(当前)→ 空;10 的邻档 15(当前)→ 空", nb3 == [] and ao.neighbors("c", c3_base, "time_days", 10) == [])
csc = ao.candidates("c", c3_base)
check("候选 · 方向 C 有候选且止损只收紧(stop_atr 只出 0.25)", csc and [c["value"] for c in csc if c["key"] == "stop_atr"] == [0.25], str([(c["key"], c["value"]) for c in csc if c["key"] == "stop_atr"]))
check("常量 · 观察期 10 天", ao.OBS_DAYS == 10)


# ── 模拟器:和实盘同一段代码、可复现 ─────────────────────────
def mk(closes, start=date(2026, 1, 5), vol=1_000_000.0, rng=0.01):
    out, d = [], start
    for c in closes:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append((d, c, c * (1 + rng), c * (1 - rng), vol))
        d += timedelta(days=1)
    return out


# 一只票:60 根铺垫 + 横盘 + 突破(放量)+ 上涨 12% + 回落
closes = [88 + i * (9 / 59) for i in range(60)] + [97.5 + (i % 2) * 0.6 for i in range(20)] + [101.5] + \
         [101.5 * (1 + 0.012 * i) for i in range(1, 12)] + [112.0 - 1.5 * i for i in range(1, 8)]
bars = mk(closes)
bars = bars[:74] + [(d, c, c * 1.002, c * 0.998, v) for d, c, _h, _l, v in bars[74:80]] + \
       [(bars[80][0], 101.5, 101.8, 101.0, 2_000_000.0)] + bars[81:]
dates = [b[0] for b in bars][60:]
screen = {d: [("AAA", "甲", 80)] for d in dates}
pool = agent_sim.pooled_watch(dates, screen, 10)
check("观察池 · 并集里每天都有 AAA 且 since = 窗口内首次入选日", all(len(pool[d]) == 1 and pool[d][0][3] == dates[max(0, i - 9)] for i, d in enumerate(dates)))
cache = agent_sim.build_cache(dates, pool, lambda c: bars if c == "AAA" else [])
check("缓存 · 每个交易日都有指标", all((("AAA", d) in cache) for d in dates))
r1 = agent_sim.simulate(av, BASE, av.GUARDS, dates, pool, cache)
r2 = agent_sim.simulate(av, BASE, av.GUARDS, dates, pool, cache)
check("模拟 · 可复现(两次结果逐位相同)", r1["equity"] == r2["equity"] and len(r1["trades"]) == len(r2["trades"]))
check("模拟 · 教科书走势有买有卖", any(t["side"] == "buy" for t in r1["trades"]) and any(t["side"] == "sell" for t in r1["trades"]), str([(t["date"], t["side"], t["rule_id"]) for t in r1["trades"]]))
m = r1["metrics"]
check("指标 · 完整周期数与胜率是数字", isinstance(m["cycles"], int) and (m["win_rate"] is None or 0 <= m["win_rate"] <= 100), str(m))
# 更早止盈(tp1 8%)的模拟结果应当不同
r3 = agent_sim.simulate(av, dict(BASE, tp1=0.08), av.GUARDS, dates, pool, cache)
check("模拟 · 改参数结果不同(tp1 10% → 8%)", r3["equity"] != r1["equity"] or r3["trades"] != r1["trades"])

# ── 优化器 step:门槛逐条 ────────────────────────────────────
st = {"params": dict(BASE), "version": 1, "versions": [], "observing": None, "cooldown_days_left": 0, "days_since_eval": ao.EVAL_EVERY - 1}
r = ao.step("base", st, dates[-1], dates, pool, cache, av.GUARDS)
check("step · 基准方向不优化", r["action"] == "fixed")
st = {"params": dict(BASE), "version": 1, "versions": [], "observing": None, "cooldown_days_left": 0, "days_since_eval": ao.EVAL_EVERY - 1}
r = ao.step("sell", st, dates[-1], dates, pool, cache, av.GUARDS)
check("⭐step · 样本不足(只有 1 个周期)不评估、不改参数", r["action"] == "insufficient" and st["params"] == BASE and st["version"] == 1, str(r))
st["days_since_eval"] = 0
r = ao.step("sell", st, dates[-1], dates, pool, cache, av.GUARDS)
check("step · 没到评估日就跳过", r["action"] == "skip", str(r))
st = {"params": dict(BASE), "version": 1, "versions": [], "observing": None, "cooldown_days_left": 3, "days_since_eval": ao.EVAL_EVERY}
r = ao.step("sell", st, dates[-1], dates, pool, cache, av.GUARDS)
check("step · 冷却期不评估并递减", r["action"] == "cooldown" and st["cooldown_days_left"] == 2)

# 观察期收尾:候选比当前差 → 不换版;比当前好 → 换版并记录
obs_since = dates[-ao.OBS_DAYS - 1]
st = {"params": dict(BASE), "version": 1, "versions": [], "cooldown_days_left": 0, "days_since_eval": 0,
      "observing": {"key": "tp1", "value": 0.08, "old": 0.10, "params": dict(BASE, tp1=0.08), "since": str(obs_since),
                    "change": "R-11 第一档止盈 10% → 8%", "reason": "测试", "train_gain": 1.0, "test_gain": 1.0}}
r = ao.step("sell", st, dates[-1], dates, pool, cache, av.GUARDS)
check("step · 观察期满会给出结论(换版或不换)", r["action"] in ("promoted", "rejected") and st["observing"] is None, str(r))
if r["action"] == "promoted":
    check("step · 换版后版本 +1、参数生效、进入冷却", st["version"] == 2 and st["params"]["tp1"] == 0.08 and st["cooldown_days_left"] == ao.COOLDOWN and st["versions"][-1]["key"] == "tp1")
else:
    check("step · 不换版则参数不变", st["params"] == BASE and st["version"] == 1)
st2 = {"params": dict(BASE), "version": 1, "versions": [], "cooldown_days_left": 0, "days_since_eval": 0,
       "observing": {"key": "tp1", "value": 0.08, "old": 0.10, "params": dict(BASE, tp1=0.08), "since": str(dates[-2]),
                     "change": "x", "reason": "y", "train_gain": 1.0, "test_gain": 1.0}}
r = ao.step("sell", st2, dates[-1], dates, pool, cache, av.GUARDS)
check("step · 观察期没满就一直观察,不换版", r["action"] == "observing" and st2["observing"] is not None and st2["version"] == 1, str(r))

# compare:两段都要赢
cur_res = agent_sim.simulate(av, BASE, av.GUARDS, dates, pool, cache)
same = ao.compare(cur_res, cur_res, dates, av.GUARDS)
check("compare · 和自己比不算更好", not same["ok"] and "训练段" in same["why"])
fake = {"equity": [(d, e + (200 if i >= len(dates) // 2 else 0)) for i, (d, e) in enumerate(cur_res["equity"])],
        "metrics": dict(cur_res["metrics"])}
c2 = ao.compare(cur_res, fake, dates, av.GUARDS)
check("⭐compare · 只在后半段赚钱(训练段没赢)不算", not c2["ok"], str(c2))
fake2 = {"equity": [(d, e + 100 * (i + 1)) for i, (d, e) in enumerate(cur_res["equity"])], "metrics": dict(cur_res["metrics"])}
c3 = ao.compare(cur_res, fake2, dates, av.GUARDS)
check("compare · 两段都稳定多赚才算", c3["ok"], str(c3))
fake3 = {"equity": fake2["equity"], "metrics": dict(cur_res["metrics"], max_dd_pct=(cur_res["metrics"]["max_dd_pct"] or -0.5) * 3 - 5)}
c4 = ao.compare(cur_res, fake3, dates, av.GUARDS)
check("⭐compare · 回撤明显更差不算(即便更赚)", not c4["ok"] and "回撤" in c4["why"], str(c4))

# rules_for 文案跟参数
check("文案 · 参数改了规则手册跟着变", "8%" in av.rules_for(dict(BASE, tp1=0.08))[10]["condition"]
      and "7 天" not in av.rules_for(BASE)[13]["condition"] and "第 7 个交易日" in av.rules_for(dict(BASE, time1_days=7))[13]["condition"])

total = passed + len(fails)
print(f"优化器用例 {total} 条")
if fails:
    print(f"FAIL {len(fails)} 条:")
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("ALL OK")
