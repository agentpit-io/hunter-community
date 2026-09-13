"""小鹿智能体 · 规则优化器(纯计算,tests/test_agent_opt.py 直接测)。

2026-09-12 用户要求:从年初起每日总结、智能优化规则;**不能放宽止损**;多个方向并行、各自保留结果:
方向 A 固定卖出规则调买入时机,方向 B 固定买入时机调卖出时机。并强调**避免过拟合**。

## 怎么防过拟合(每一条都是硬门槛,缺一个就不改规则)

1. **一次只动一个参数、只在预设的几个档位里挑**(见 BRANCHES.tunable),不做连续搜索、不做多参数联合搜索。
   参数空间越大,越容易在历史上"找到"一个碰巧好看的组合。
2. **样本门槛**:当前参数在整段历史上至少要有 MIN_CYCLES 个走完的持仓周期,否则不评估。
   两三笔交易的差异全是运气。
3. **前后两段都要赢(walk-forward)**:历史按交易日 7:3 切成训练段 / 检验段,候选在**两段上都**要比当前参数
   净盈亏更高、且回撤不能差过 DD_TOL —— 只在前段好后段差的,就是过拟合的典型样子。
4. **观察期**:选出的候选不立即生效,先「观察」OBS_DAYS 个交易日:这几天实盘仍按当前参数跑,
   同时算候选在这几天(它没见过的数据)上的表现;期末候选不比当前差才正式换版。观察期内出现别的候选也不换,
   一次只观察一个。
5. **冷却期**:换版之后 COOLDOWN 个交易日内不再换。频繁换版本身就是过拟合的症状。
6. **每 EVAL_EVERY 个交易日才评估一次候选**(观察期的收尾每天看)。
7. **止损只许收紧**:止损类参数候选值不能比基准宽、也不能比当前宽(各引擎的 STOP_KEYS,数值越小越紧)。
   代码里用 `allowed()` 校验,测试有用例盯着。
8. **同一参数不许来回改**(2026-09-12 全年回测方向 B 把时间止损 5 → 7 → 3 天改了个来回之后加的):
   最近 FREEZE_VERSIONS 版里改过的参数不再当候选;任何参数都不许改回它历史上用过的旧值。
9. **邻域检验**:选中的候选,它相邻的档位(上一档 / 下一档)也得不比当前差;只有孤零零一个档位好看的,
   是碰巧撞上的数字,不是规律。
10. **观察期 10 天**(原 5 天,同一次回测后用户同意拉长)。

## 引擎

方向 base / buy / sell 用 agent_vcp(用户的 Backtrader 策略移植);方向 c 用 agent_vcp3(Claude 自设计的三段式)。
两个引擎接口相同(PARAMS / STOP_KEYS / rules_for / run_day / indicators),优化器和模拟器按 BRANCHES[branch]["engine"] 选。

改版记录(版本号、改了什么、为什么、之后效果)全部落库,面板「策略演进」逐条可追。
"""
from __future__ import annotations

from datetime import date

from app.services.quant import agent_vcp as av
from app.services.quant import agent_vcp3 as av3
from app.services.quant import agent_vcp4 as av4
from app.services.quant import agent_sim

ENGINES = {"vcp": av, "vcp3": av3, "vcp4": av4}

MIN_CYCLES = 8
OBS_DAYS = 10           # 原 5,2026-09-12 全年回测后用户同意拉长
COOLDOWN = 10
FREEZE_VERSIONS = 2     # 最近 2 版里改过的参数不再当候选
EVAL_EVERY = 5          # 每 5 个交易日才评估一次候选(观察期收尾每天看)。天天换参数本身就是过拟合
TRAIN_FRAC = 0.7
DD_TOL = 1.2            # 候选的最大回撤不能超过当前的 1.2 倍(回撤是负数,比绝对值)
MIN_GAIN = 50.0         # 两段上净盈亏至少各多这么多美元(起始资金 10 万 → 0.05%),避免抖动

BRANCHES: dict = {
    "base": {"engine": "vcp", "label": "基准 v1", "direction": "规则固定,不优化", "tunable": {}},
    # 2026-09-13 起方向 A 换成 agent_vcp4(用户按 SEPA 拆解方向 C 后的 v4 草案);原来的「固定卖出调买入」记录已清,
    # 引擎 A 的那组买入档位留在 git 历史里(atr_compact / chase_limit / min_adtv)
    "buy": {"engine": "vcp4", "label": "方向 A · SEPA 优化",
            "direction": "用户按 Minervini SEPA 拆解方向 C 后的 v4:市场过滤 + 趋势模板 + 加权评分否决 + 风险定仓 + 加仓 + 盘中止损",
            "tunable": {"atr_chase": [0.15, 0.25, 0.35], "vol_boost": [1.0, 1.2, 1.5],
                        "breakout_window": [5, 10, 15], "vcp_last_depth_max": [8.0, 10.0, 12.0]}},
    "sell": {"engine": "vcp", "label": "方向 B · 调卖出", "direction": "固定买入时机,只优化卖出时机(止损只许收紧不许放宽)",
             "tunable": {"tp1": [0.08, 0.10, 0.12], "tp2": [0.13, 0.15, 0.18], "tp3": [0.18, 0.20, 0.25],
                         "time1_days": [3, 5, 7], "time2_days": [8, 10, 14],
                         "pullback_trigger": [0.03, 0.05, 0.08],
                         "max_stop_pct": [0.06, 0.07, 0.08], "half_loss_pct": [0.04, 0.05],
                         "sma_stop_pct": [0.01, 0.02]}},
    "c": {"engine": "vcp3", "label": "方向 C · 三段式", "direction": "Claude 自设计:枢轴 + ATR 触发、底部止损、1R 后移动止损(止损只许收紧)",
          "tunable": {"atr_chase": [0.5, 1.0, 1.5], "vol_boost": [1.1, 1.3, 1.5], "confirm_days": [1, 3, 5],
                      "trail_days": [7, 10, 15], "time_days": [10, 15, 20], "stop_atr": [0.25, 0.5]}},
}
BRANCH_ORDER = ["base", "buy", "sell", "c"]


def engine_of(branch: str):
    return ENGINES[BRANCHES[branch]["engine"]]

PARAM_LABEL = {
    "atr_compact": "R-03 收缩阈值(前一天 ATR5/ATR20)", "vol_boost": "R-05 放量倍数", "chase_limit": "R-04 追高上限",
    "min_adtv": "R-02 最低日均成交额", "tp1": "R-11 第一档止盈", "tp2": "R-12 第二档止盈", "tp3": "R-13 清仓止盈",
    "time1_days": "R-14 时间止损天数", "time2_days": "R-15 时间清仓天数", "pullback_trigger": "R-07 涨过多少算涨过",
    "max_stop_pct": "R-09 硬止损", "half_loss_pct": "R-08 减半止损", "sma_stop_pct": "R-10 跌破均线幅度",
    "atr_chase": "C-01 追高上限(ATR 倍数)", "confirm_days": "C-02 确认天数", "trail_days": "C-05 移动止损回看天数",
    "time_days": "C-06 时间止损天数", "stop_atr": "C-04 初始止损(底部下方 ATR 倍数)",
    "breakout_window": "V-03 突破后有效天数", "vcp_last_depth_max": "V-03 末次收缩上限(%)",
}
# 同名参数在不同引擎里挂在不同规则上,按引擎覆盖文案
PARAM_LABEL_BY_ENGINE = {
    "vcp4": {"atr_chase": "V-03 追高上限(ATR 倍数)", "vol_boost": "V-04 突破日放量倍数", "stop_atr": "V-06 初始止损(枢轴下方 ATR 倍数)"},
}


def param_label(branch: str, key: str) -> str:
    return PARAM_LABEL_BY_ENGINE.get(BRANCHES[branch]["engine"], {}).get(key) or PARAM_LABEL.get(key, key)


def fmt(key: str, v) -> str:
    if key == "min_adtv":
        return f"${v / 1e6:.0f}M"
    if key.endswith("_days"):
        return f"{int(v)} 天"
    if key in ("chase_limit",):
        return f"+{(v - 1) * 100:.0f}%"
    if key in ("atr_compact", "vol_boost", "atr_chase", "stop_atr"):
        return f"{v:.2f}×" if key in ("atr_compact", "stop_atr") else f"{v:.1f}×"
    return f"{v * 100:.0f}%"


def allowed(key: str, value, base: dict | None = None, cur: dict | None = None, engine=av) -> bool:
    """止损类参数不许比基准宽,也不许比**当前**宽(只能单向收紧:收到 6% 之后 7% 也不行)。"""
    base = base or engine.PARAMS
    if key in engine.STOP_KEYS:
        return value <= base[key] and (cur is None or value <= cur.get(key, base[key]))
    return True


def _valid(p: dict) -> bool:
    if "tp1" in p and not (p["tp1"] < p["tp2"] < p["tp3"]):
        return False
    if "time1_days" in p and not (p["time1_days"] < p["time2_days"]):
        return False
    return True


def candidates(branch: str, cur: dict, versions: list | None = None) -> list[dict]:
    """一次只动一个参数 → [{key, value, params}]。跳过当前值、不合法的组合、
    最近 FREEZE_VERSIONS 版改过的参数、以及任何参数历史上用过的旧值(不许来回改)。"""
    eng = engine_of(branch)
    versions = versions or []
    frozen = {v["key"] for v in versions[-FREEZE_VERSIONS:]}
    used_old = {}
    for v in versions:
        used_old.setdefault(v["key"], set()).add(v["old"])
    out = []
    for key, vals in BRANCHES[branch]["tunable"].items():
        if key in frozen:
            continue
        for v in vals:
            if v == cur.get(key) or v in used_old.get(key, ()) or not allowed(key, v, cur=cur, engine=eng):
                continue
            p = dict(cur)
            p[key] = v
            if not _valid(p):
                continue
            out.append({"key": key, "value": v, "params": p})
    return out


def neighbors(branch: str, cur: dict, key: str, value) -> list[dict]:
    """候选值相邻的档位(上一档 / 下一档,去掉当前值与不合法的)。邻域检验用。"""
    vals = BRANCHES[branch]["tunable"][key]
    i = vals.index(value)
    out = []
    for j in (i - 1, i + 1):
        if 0 <= j < len(vals) and vals[j] != cur.get(key):
            p = dict(cur)
            p[key] = vals[j]
            if _valid(p):
                out.append(p)
    return out


def _split(dates: list[date]) -> int:
    return max(1, int(len(dates) * TRAIN_FRAC))


def compare(cur_res: dict, cand_res: dict, dates: list[date], g: dict) -> dict:
    """两段各算净盈亏与回撤。→ {train_gain, test_gain, ok, why}"""
    k = _split(dates)
    cur_eq = [e for _, e in cur_res["equity"]]
    cand_eq = [e for _, e in cand_res["equity"]]
    init = g["initial_capital"]
    tr_cur, tr_cand = cur_eq[k - 1] - init, cand_eq[k - 1] - init
    te_cur, te_cand = cur_eq[-1] - cur_eq[k - 1], cand_eq[-1] - cand_eq[k - 1]
    dd_cur, dd_cand = cur_res["metrics"]["max_dd_pct"], cand_res["metrics"]["max_dd_pct"]
    train_gain, test_gain = tr_cand - tr_cur, te_cand - te_cur
    ok = train_gain >= MIN_GAIN and test_gain >= MIN_GAIN and (dd_cand >= dd_cur * DD_TOL - 1e-9)
    why = []
    if train_gain < MIN_GAIN:
        why.append(f"训练段只多赚 ${train_gain:+.0f}")
    if test_gain < MIN_GAIN:
        why.append(f"检验段只多赚 ${test_gain:+.0f}")
    if dd_cand < dd_cur * DD_TOL - 1e-9:
        why.append(f"回撤 {dd_cand:.1f}% 比当前 {dd_cur:.1f}% 差太多")
    return {"train_gain": train_gain, "test_gain": test_gain, "dd_cur": dd_cur, "dd_cand": dd_cand,
            "ok": ok, "why": ";".join(why) or "两段都更好"}


def step(branch: str, st: dict, today: date, dates: list[date], pool: dict, cache: dict, g: dict) -> dict:
    """每天收盘后跑一次(在实盘那一步之后)。改 st(params / version / versions / observing / cooldown_until)。
    → 摘要 {evaluated, best, action, text}。"""
    if not BRANCHES[branch]["tunable"]:
        return {"evaluated": 0, "action": "fixed", "text": "基准方向不优化"}
    eng = engine_of(branch)
    cur = st["params"]
    cur_res = agent_sim.simulate(eng, cur, g, dates, pool, cache)
    cyc = cur_res["metrics"]["cycles"]
    # 观察期收尾
    obs = st.get("observing")
    if obs:
        obs_dates = [d for d in dates if d > date.fromisoformat(obs["since"])]
        if len(obs_dates) >= OBS_DAYS:
            cand_res = agent_sim.simulate(eng, obs["params"], g, dates, pool, cache)
            k = len(dates) - len(obs_dates)
            gain = (cand_res["equity"][-1][1] - cand_res["equity"][k - 1][1]) - (cur_res["equity"][-1][1] - cur_res["equity"][k - 1][1])
            st["observing"] = None
            if gain >= 0:
                st["version"] = st.get("version", 1) + 1
                st["params"] = obs["params"]
                st["cooldown_days_left"] = COOLDOWN
                st.setdefault("versions", []).append({
                    "version": st["version"], "date": str(today), "key": obs["key"], "value": obs["value"],
                    "old": obs["old"], "change": obs["change"], "reason": obs["reason"],
                    "obs_gain": gain, "train_gain": obs["train_gain"], "test_gain": obs["test_gain"]})
                return {"evaluated": 1, "action": "promoted", "cycles": cyc,
                        "text": f"观察期 {len(obs_dates)} 天结束:候选比当前多赚 ${gain:+.0f} → 升到 v{st['version']}:{obs['change']}"}
            return {"evaluated": 1, "action": "rejected", "cycles": cyc,
                    "text": f"观察期 {len(obs_dates)} 天结束:候选比当前少赚 ${-gain:.0f},不换版(这就是回测好看、实盘不行的那种)"}
        return {"evaluated": 0, "action": "observing", "cycles": cyc,
                "text": f"观察中(第 {len(obs_dates)}/{OBS_DAYS} 天):{obs['change']}"}
    if st.get("cooldown_days_left", 0) > 0:
        st["cooldown_days_left"] -= 1
        return {"evaluated": 0, "action": "cooldown", "cycles": cyc,
                "text": f"换版后冷却,还剩 {st['cooldown_days_left']} 个交易日不评估"}
    if st.get("days_since_eval", 0) + 1 < EVAL_EVERY:
        st["days_since_eval"] = st.get("days_since_eval", 0) + 1
        return {"evaluated": 0, "action": "skip", "cycles": cyc,
                "text": f"每 {EVAL_EVERY} 个交易日评估一次候选(第 {st['days_since_eval']} 天)"}
    st["days_since_eval"] = 0
    if cyc < MIN_CYCLES:
        return {"evaluated": 0, "action": "insufficient", "cycles": cyc,
                "text": f"走完的持仓周期只有 {cyc} 个(要 {MIN_CYCLES} 个才评估)—— 样本不够,改了也是运气"}
    cands = candidates(branch, cur, st.get("versions"))
    passed = []
    for c in cands:
        res = agent_sim.simulate(eng, c["params"], g, dates, pool, cache)
        cmp_ = compare(cur_res, res, dates, g)
        c["cmp"] = cmp_
        c["pnl"] = res["metrics"]["pnl"]
        if cmp_["ok"]:
            passed.append(c)
    passed.sort(key=lambda c: -(c["cmp"]["train_gain"] + c["cmp"]["test_gain"]))
    best = None
    n_lonely = 0
    for c in passed:
        # 邻域检验:相邻档位也得不比当前差,否则是孤零零的一个好看数字
        ok = True
        for np_ in neighbors(branch, cur, c["key"], c["value"]):
            r2 = agent_sim.simulate(eng, np_, g, dates, pool, cache)
            c2 = compare(cur_res, r2, dates, g)
            if c2["train_gain"] + c2["test_gain"] < 0:
                ok = False
                break
        if ok:
            best = c
            break
        n_lonely += 1
    if best is None:
        why = f"没有一个在训练段和检验段都更好" if not passed else f"{len(passed)} 个两段都更好,但相邻档位都不比当前好(邻域检验没过)"
        return {"evaluated": len(cands), "action": "none", "cycles": cyc,
                "text": f"评估了 {len(cands)} 个候选(一次只动一个参数),{why} —— 不改"}
    change = f"{param_label(branch, best['key'])} {fmt(best['key'], cur[best['key']])} → {fmt(best['key'], best['value'])}"
    st["observing"] = {"key": best["key"], "value": best["value"], "old": cur[best["key"]], "params": best["params"],
                       "since": str(today), "change": change,
                       "reason": f"训练段多赚 ${best['cmp']['train_gain']:+.0f}、检验段多赚 ${best['cmp']['test_gain']:+.0f},回撤 {best['cmp']['dd_cand']:.1f}%",
                       "train_gain": best["cmp"]["train_gain"], "test_gain": best["cmp"]["test_gain"]}
    return {"evaluated": len(cands), "action": "observe", "cycles": cyc,
            "text": f"评估了 {len(cands)} 个候选,{change} 在训练段 / 检验段都更好(${best['cmp']['train_gain']:+.0f} / ${best['cmp']['test_gain']:+.0f}),进入 {OBS_DAYS} 天观察期"}
