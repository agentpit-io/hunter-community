"""小鹿智能体 · 方向 C「VCP 三段式」—— 2026-09-12 Claude 自设计的买卖规则(用户同意后实现)。

设计依据是全年回测的三个事实(agent_vcp / 全年数据):
1. 原策略买入侧最大的浪费在「当天收盘突破 20 日高点」:筛选器已经把「正在收缩、贴着枢轴」的票挑出来了,
   策略只在突破那一根 K 线进,而突破日常常一根大阳线直接超伸 5%,又被「不追高」挡掉 —— 候选 44/45 卡在这里。
2. 出场里时间止损占大头,说明大部分买入后 5~10 天既没涨 5% 也没跌 8%,是「没动」不是「错了」;
   收缩后的突破经常先横两周再走,5/10 天的时间止损在 VCP 上尤其吃亏。
3. 筛选器已经包含趋势 / 流动性 / 收缩三条,策略再重复一遍只是把入场变苛刻,没有增加信息。

## 防过拟合的设计原则

- 参数少,每个都有物理意义;能用结构性规则就不用数字阈值。
- 数字用**形态自身的尺度**(ATR、枢轴到底部的距离),不用固定百分比 —— 同一条规则对 $20 和 $400 的票都成立。
- 每笔风险固定,仓位由止损距离反推(原脚本这条是对的,保留)。
- 不做参数搜索:先用默认值跑,再看邻域稳定性(优化器里的邻域检验),某个参数一挪就崩的规则直接删。

## 规则(C-xx,和方向 A/B 的 R-xx 不共用编号)

买入(只看形态位置,不看突破日 K 线本身;趋势 / 流动性 / 收缩交给筛选器):
- C-01 触发:收盘在枢轴(筛选器算出的最后一次收缩高点)上方,且高出不超过 `atr_chase` 个 ATR(20)。
        用 ATR 不用 5%:收缩后的 ATR 本身很小,自然限制追高。
- C-02 确认:突破是新鲜的(前 `confirm_days` 天里有收盘还在枢轴下方),且这 `confirm_days` 天里任意一天
        成交量 ≥ 20 日均量 × `vol_boost` —— 不要求必须是突破当天放量,解决「突破日超伸、次日回踩没人接」。
- C-03 仓位:单笔风险 `risk_pct` 总资产 ÷ 止损距离;单股不超过 `max_pos_pct`;最多 `max_holdings` 只。

卖出(三条,全按形态尺度):
- C-04 初始止损:底部低点(最后一次收缩的低点)下方 `stop_atr` 个 ATR,但最深不超过 -`max_stop_pct`
        (用户「不放宽止损」的底线)。收缩越紧止损越近、仓位越大 —— 这是 VCP 的本意。
- C-05 保本 + 移动止损:涨到 1R(一倍初始风险)后止损上移到成本,之后按前 `trail_days` 日最低价跟踪。
        不用固定 +10% / +15% 分批止盈:趋势票会自己走远,固定止盈是把最赚钱的几笔提前砍掉。
- C-06 时间止损:持有 `time_days` 个交易日仍没涨过 1R,清仓。比 5/10 天宽,收缩后常要两周才启动。
- C-07 护栏:与 A/B 同一套(单日 -3% 熔断、连亏 3 笔停一天)。

收盘后决策、信号当天收盘价成交,和 A/B 同一口径。不做加仓。

## 2026-09-12 晚(全年回撤归因之后,用户拍板)的三处改动

回撤 -10.4% 的主因是敞口:平均仓位 63%(基准 12%)、单笔 12.8%、同时 4~8 只;47 笔里 22 笔止损被 -8% 封顶接管,
形态的紧凑没换来更近的止损,仓位却按 1% ÷ 8% 给足 —— 这一半只赚了 +$712,另一半(止损来自形态)赚了 +$6,296。
1. **止损被封顶就不进**(C-04):底部低点下方 stop_atr 个 ATR 若超过 max_stop_pct,说明形态不够紧,跳过。
2. **组合总风险上限**(C-09):所有持仓的 (现价 − 止损) × 股数 之和 ≤ heat_cap 总资产;新仓放不下就按余额缩,缩到 0 股就不进。
   移动止损上移后开放风险自然下降,额度会腾出来 —— 比粗暴限制持仓数好。
3. **评分定仓位**(C-08 / C-03):五个形态特征各 0~2 分(见 grade),S ≥ 8 → 20% 总资产,A 6~7 → 15%,B 4~5 → 10%,
   C 2~3 → 5%,D < 2 → 不买(达到信号也不买:结构松散、止损成本高、盈亏比差)。
   **特征和档位是先按形态逻辑定的,再拿历史 45 笔验证是否单调**,不是反过来按结果调 —— 那是过拟合的正门。
   验证结果写在提交说明和 CLAUDE.md 里,不单调的地方如实写。
"""
from __future__ import annotations

from app.services.quant import agent_vcp as av
from app.services.quant import vcp

PARAMS = {
    "atr_chase": 1.0, "vol_boost": 1.3, "confirm_days": 3,
    "risk_pct": 0.01, "max_pos_pct": 0.20, "max_holdings": 8,
    "stop_atr": 0.5, "max_stop_pct": 0.08,
    "trail_days": 10, "time_days": 15,
    "watch_pool_days": av.PARAMS["watch_pool_days"],
    "skip_capped": True,                       # 止损被 -8% 封顶就不进
    "heat_cap": 0.04,                          # 组合开放风险上限(占总资产)
    "grade_size": {"S": 0.20, "A": 0.15, "B": 0.10, "C": 0.05, "D": 0.0},   # 按评分定仓位(占总资产)
}
GRADE_MIN = {"S": 8, "A": 6, "B": 4, "C": 2}   # 10 分制的档位下限;<2 = D
STOP_KEYS = ("stop_atr", "max_stop_pct")     # 数值越小越紧;只许收紧
MIN_BARS = 60
_MAX_CONFIRM = 5
_MAX_TRAIL = 15

RULES = [
    {"id": "C-01", "kind": "buy", "condition": "触发:收盘在枢轴(最后一次收缩的高点)上方,且高出不超过 1.0 个 ATR(20)"},
    {"id": "C-02", "kind": "buy", "condition": "确认:突破是新鲜的(前 3 天里有收盘在枢轴下方),且这 3 天里任一天成交量 ≥ 20 日均量 1.3 倍"},
    {"id": "C-03", "kind": "risk", "condition": "仓位按评分:S 20% / A 15% / B 10% / C 5% 总资产,D 不买;最多 8 只"},
    {"id": "C-04", "kind": "sell", "condition": "初始止损:底部低点下方 0.5 个 ATR;若这个位置比 -8% 还远,形态不够紧,不进"},
    {"id": "C-05", "kind": "sell", "condition": "保本 + 移动止损:涨到 1R 后止损上移到成本,之后按前 10 日最低价跟踪"},
    {"id": "C-06", "kind": "sell", "condition": "时间止损:持有 15 个交易日仍没涨过 1R,清仓"},
    {"id": "C-07", "kind": "risk", "condition": "护栏:单日权益回撤达 -3% 当天停止开仓;连亏 3 笔后下一个交易日不开仓"},
    {"id": "C-08", "kind": "buy", "condition": "评分(10 分):紧凑度 / 收缩结构 / 量能枯竭 / 突破质量 / 相对强度各 0~2;S ≥8 · A 6~7 · B 4~5 · C 2~3 · D <2 不买"},
    {"id": "C-09", "kind": "risk", "condition": "组合总风险:所有持仓 (现价−止损)×股数 之和 ≤ 4% 总资产,放不下就缩仓或不进"},
]
RULE_NAME = {"C-01": "VCP 枢轴突破买入", "C-04": "初始止损", "C-05": "移动止损", "C-06": "时间止损"}
RULE_PARAM_KEY = {"C-01": "atr_chase", "C-02": "vol_boost", "C-04": "stop_atr", "C-05": "trail_days", "C-06": "time_days"}
ENTRY_RULE = "C-01"


def rules_for(p: dict = PARAMS) -> list[dict]:
    out = []
    for r in RULES:
        c = dict(r)
        rid = r["id"]
        if rid == "C-01":
            c["condition"] = f"触发:收盘在枢轴(最后一次收缩的高点)上方,且高出不超过 {p['atr_chase']:.2f} 个 ATR(20)"
        elif rid == "C-02":
            c["condition"] = (f"确认:突破是新鲜的(前 {p['confirm_days']} 天里有收盘在枢轴下方),"
                              f"且这 {p['confirm_days']} 天里任一天成交量 ≥ 20 日均量 {p['vol_boost']:.1f} 倍")
        elif rid == "C-03":
            gs = p["grade_size"]
            c["condition"] = (f"仓位按评分:S {gs['S'] * 100:.0f}% / A {gs['A'] * 100:.0f}% / B {gs['B'] * 100:.0f}% / C {gs['C'] * 100:.0f}% 总资产,"
                              f"D 不买;最多 {p['max_holdings']} 只")
        elif rid == "C-04":
            c["condition"] = (f"初始止损:底部低点下方 {p['stop_atr']:.2f} 个 ATR"
                              + (f";若这个位置比 -{p['max_stop_pct'] * 100:.0f}% 还远,形态不够紧,不进" if p.get("skip_capped")
                                 else f",最深不超过 -{p['max_stop_pct'] * 100:.0f}%"))
        elif rid == "C-09":
            c["condition"] = f"组合总风险:所有持仓 (现价−止损)×股数 之和 ≤ {p['heat_cap'] * 100:.0f}% 总资产,放不下就缩仓或不进"
        elif rid == "C-05":
            c["condition"] = f"保本 + 移动止损:涨到 1R 后止损上移到成本,之后按前 {p['trail_days']} 日最低价跟踪"
        elif rid == "C-06":
            c["condition"] = f"时间止损:持有 {p['time_days']} 个交易日仍没涨过 1R,清仓"
        out.append(c)
    return out


def summary(p: dict = PARAMS) -> str:
    gs = p["grade_size"]
    return (f"从筛选器的 VCP 候选里,只在收盘站上枢轴、高出不超过 {p['atr_chase']:.1f} 个 ATR 时进,"
            f"要求 {p['confirm_days']} 天内放量 {p['vol_boost']:.1f} 倍确认;止损挂在底部低点下方 {p['stop_atr']:.1f} 个 ATR,"
            f"比 -{p['max_stop_pct'] * 100:.0f}% 还远就不进;按形态评分定仓位(S {gs['S'] * 100:.0f}% / A {gs['A'] * 100:.0f}% / "
            f"B {gs['B'] * 100:.0f}% / C {gs['C'] * 100:.0f}%,D 不买),组合总风险 ≤ {p['heat_cap'] * 100:.0f}%;"
            f"涨到 1R 后保本、按前 {p['trail_days']} 日最低价跟踪,不设固定止盈;{p['time_days']} 天没到 1R 就走。")


# ═══════════════════════════════════════════════════════════════
# 指标
# ═══════════════════════════════════════════════════════════════

def indicators(bars: list[tuple], p: dict = PARAMS) -> dict | None:
    if len(bars) < MIN_BARS:
        return None
    c = [b[1] for b in bars]
    h = [b[2] for b in bars]
    lo = [b[3] for b in bars]
    v = [b[4] for b in bars]
    if any(x is None for x in h[-60:] + lo[-60:] + v[-60:]):
        return None
    vs = vcp.vcp_stats(bars) or {}
    return {
        "close": c[-1], "high": h[-1], "low": lo[-1], "volume": v[-1],
        "atr20": av._atr(bars[-61:], 20),
        "vol_sma20": av._sma(v, 20),
        "pivot": vs.get("pivot"), "base_low": vs.get("last_low"), "contractions": vs.get("contractions"),
        "last_depth": vs.get("last_depth"), "low_vol_ratio": vs.get("low_vol_ratio"),
        "recent_closes": c[-_MAX_CONFIRM - 1:],          # 含今天
        "recent_vols": v[-_MAX_CONFIRM:],                 # 含今天
        "lows_prior": lo[-_MAX_TRAIL - 1:-1],             # 不含今天
    }


# ═══════════════════════════════════════════════════════════════
# 买入过滤 + 观察列表文字
# ═══════════════════════════════════════════════════════════════

def entry_flags(ind: dict, p: dict = PARAMS) -> dict:
    ph, atr, close = ind["pivot"], ind["atr20"], ind["close"]
    k = int(p["confirm_days"])
    above = ph is not None and close > ph
    chase_ok = above and atr is not None and close <= ph + p["atr_chase"] * atr
    rc = ind["recent_closes"]
    fresh = ph is not None and len(rc) >= 2 and min(rc[-k - 1:-1]) <= ph
    vs = ind["vol_sma20"]
    vr = (max(ind["recent_vols"][-k:]) / vs) if vs else None
    vol_ok = vr is not None and vr >= p["vol_boost"]
    return {"C-01": bool(ph is not None and (ind["contractions"] or 0) >= 1 and chase_ok),
            "C-02": bool(fresh and vol_ok), "above": above, "fresh": fresh, "vr": vr,
            "dist_atr": ((close - ph) / atr) if (ph is not None and atr) else None}


def stop_of(ind: dict, p: dict = PARAMS) -> tuple[float | None, bool]:
    """→ (止损位, 是否被 -max_stop_pct 封顶)。底部低点下方 stop_atr 个 ATR;比封顶线还远 = 封顶。"""
    if ind.get("base_low") is None or not ind.get("atr20"):
        return None, False
    px = ind["close"]
    raw = ind["base_low"] - p["stop_atr"] * ind["atr20"]
    cap = px * (1 - p["max_stop_pct"])
    return max(raw, cap), raw < cap


def grade(ind: dict, p: dict = PARAMS, score=None) -> dict:
    """形态评分(10 分制)→ {grade, points, factors:[(名, 分, 说明)]}。档位先定、后验证,见文件头。

    五个特征都是「进场前就知道」的:
    1 紧凑度  止损距离(收盘到止损)≤4% → 2;≤6% → 1;否则 0
    2 收缩结构  收缩 ≥3 次且末次深度 ≤8% → 2;收缩 ≥2 次 → 1;否则 0
    3 量能枯竭  最低点量比 ≤0.6 → 2;≤0.8 → 1;否则 0
    4 突破质量  高出枢轴 ≤0.5 ATR 且放量 ≥1.5 倍 → 2;放量 ≥1.3 倍 → 1;否则 0
    5 相对强度  筛选器的 RS 评级 ≥90 → 2;≥80 → 1;否则 0
    """
    f = entry_flags(ind, p)
    stop, _capped = stop_of(ind, p)
    px = ind["close"]
    factors = []
    dist = (1 - stop / px) * 100 if stop else None
    pt = 2 if dist is not None and dist <= 4 else 1 if dist is not None and dist <= 6 else 0
    factors.append(("紧凑度", pt, f"止损距离 {dist:.1f}%" if dist is not None else "止损算不出"))
    n, ld = ind.get("contractions") or 0, ind.get("last_depth")
    pt = 2 if n >= 3 and ld is not None and ld <= 8 else 1 if n >= 2 else 0
    factors.append(("收缩结构", pt, f"收缩 {n} 次" + (f",末次 {ld:.1f}%" if ld is not None else "")))
    lv = ind.get("low_vol_ratio")
    pt = 2 if lv is not None and lv <= 0.6 else 1 if lv is not None and lv <= 0.8 else 0
    factors.append(("量能枯竭", pt, f"最低点量比 {lv:.2f}" if lv is not None else "量比算不出"))
    da, vr = f.get("dist_atr"), f.get("vr")
    pt = 2 if da is not None and da <= 0.5 and vr is not None and vr >= 1.5 else 1 if vr is not None and vr >= 1.3 else 0
    factors.append(("突破质量", pt, (f"高出枢轴 {da:.1f} ATR" if da is not None else "") + (f",放量 {vr:.2f}×" if vr is not None else "")))
    pt = 2 if score is not None and score >= 90 else 1 if score is not None and score >= 80 else 0
    factors.append(("相对强度", pt, f"RS {score:.0f}" if score is not None else "RS 缺"))
    total = sum(x[1] for x in factors)
    g = "D"
    for k in ("S", "A", "B", "C"):
        if total >= GRADE_MIN[k]:
            g = k
            break
    return {"grade": g, "points": total, "factors": factors,
            "text": f"{g} 级({total}/10):" + "、".join(f"{a} {b}({c})" for a, b, c in factors)}


def entry_ok(ind: dict, p: dict = PARAMS) -> bool:
    f = entry_flags(ind, p)
    return f["C-01"] and f["C-02"]


def entry_checks(ind: dict, p: dict = PARAMS) -> list[dict]:
    f = entry_flags(ind, p)
    ph = ind["pivot"]
    if ph is None:
        t1 = "筛选器没算出枢轴(近一年没有收缩)"
    elif not f["above"]:
        t1 = f"收盘 ${ind['close']:.2f} 还在枢轴 ${ph:.2f} 下方 {(1 - ind['close'] / ph) * 100:.1f}%"
    elif not f["C-01"]:
        t1 = f"已高出枢轴 ${ph:.2f} {f['dist_atr']:.1f} 个 ATR,超过 {p['atr_chase']:.1f} 个不追"
    else:
        t1 = f"收盘 ${ind['close']:.2f} 站上枢轴 ${ph:.2f}(高出 {f['dist_atr']:.1f} 个 ATR)"
    t2 = ((f"近 {p['confirm_days']} 天量能 {f['vr']:.2f}× 20 日均量" if f["vr"] is not None else "均量算不出")
          + ("" if f["fresh"] else ";突破不新鲜(几天前就在枢轴上方了)")
          + ("" if f["vr"] is None or f["vr"] >= p["vol_boost"] else f" —— 要 ≥{p['vol_boost']:.1f}×"))
    return [{"rule": "C-01", "ok": f["C-01"], "text": t1}, {"rule": "C-02", "ok": f["C-02"], "text": t2}]


def watch_item(code, name, ind, held, blocked_reason, score=None) -> dict:
    it = {"symbol": code, "name": name, "score": score, "rule_id": "C-01", "rule_text": RULES[0]["condition"]}
    if ind is None:
        it.update({"price": None, "progress_pct": None, "gap": "日线不足 60 根或缺高低量,指标算不出"})
        return it
    it["price"] = round(ind["close"], 2)
    checks = entry_checks(ind)
    passed = sum(1 for c in checks if c["ok"])
    it["progress_pct"] = int(passed / len(checks) * 100)
    fails = [c for c in checks if not c["ok"]]
    if held:
        it["gap"] = "已持仓 · 等出场信号"
    elif not fails:
        it["gap"] = "两条全满足 —— 今日收盘触发买入"
    else:
        it["gap"] = f"{passed}/2 满足 · 还差:" + ";".join(f"{c['rule']} {c['text']}" for c in fails)
    if blocked_reason:
        it["blocked"] = True
        it["blocked_reason"] = blocked_reason
    return it


# ═══════════════════════════════════════════════════════════════
# 一天的决策
# ═══════════════════════════════════════════════════════════════

def _fill(side, pos: av.Position, shares, price, rule, rationale, **extra) -> dict:
    d = {"side": side, "symbol": pos.code, "name": pos.name, "shares": int(shares), "price": round(price, 2),
         "rule_id": rule, "rule_name": RULE_NAME.get(rule, rule), "rationale": rationale,
         "entry_date": pos.entry_date, "level": pos.level}
    d.update(extra)
    return d


def manage_position(pos: av.Position, ind: dict, state: dict, p: dict = PARAMS, want_text: bool = True) -> list[dict]:
    px = ind["close"]
    prev_high = pos.highest
    high_now = max(prev_high, px)
    pos.bars_held += 1
    n = pos.bars_held
    ep, r1 = pos.entry_price, pos.risk
    reached_1r = high_now >= ep + r1
    base = (f"买入价 ${ep:.2f},止损 ${pos.stop:.2f}(1R = ${r1:.2f}),今收 ${px:.2f}({(px / ep - 1) * 100:+.1f}%),"
            f"持有 {n} 个交易日。") if want_text else ""
    fills = []

    def close_all(rule, why):
        pnl = (px - pos.avg_cost) * pos.size
        state["cash"] += pos.size * px
        state["closed_pnl"].append(pnl)
        fills.append(_fill("sell", pos, pos.size, px, rule, base + why if want_text else "",
                           pnl_abs=round(pnl, 2), pnl_pct=round((px / pos.avg_cost - 1) * 100, 2), hold_days=n))
        pos.size = 0

    if px < pos.stop:
        rule = "C-05" if pos.stop >= ep else "C-04"
        close_all(rule, f"收盘跌破止损位 ${pos.stop:.2f} —— {'移动止损' if rule == 'C-05' else '初始止损'}出场。")
    elif n >= p["time_days"] and not reached_1r:
        close_all("C-06", f"持有 {n} 个交易日,期间最高 ${high_now:.2f} 没涨过 1R(${ep + r1:.2f})—— 时间止损清仓。")
    else:
        # 明天用的止损:到过 1R → 至少保本,再按前 trail_days 日最低价跟踪(只上不下)
        if reached_1r:
            trail = min(ind["lows_prior"][-int(p["trail_days"]):]) if ind["lows_prior"] else pos.stop
            pos.stop = max(pos.stop, ep, trail)
    pos.highest = high_now
    if pos.size <= 0:
        state["closed"].append(pos)
    return fills


def try_entry(code, name, ind, state: dict, p: dict = PARAMS, want_text: bool = True, score=None):
    if not entry_ok(ind, p):
        return None, None
    if len(state["positions"]) >= p["max_holdings"]:
        return None, f"两条全满足,但已持有 {len(state['positions'])} 只,达到上限(C-03)"
    if state.get("halt_reason"):
        return None, f"两条全满足,但护栏挡下:{state['halt_reason']}(C-07)"
    equity, px = state["equity"], ind["close"]
    stop, capped = stop_of(ind, p)
    if stop is None or stop >= px:
        return None, "两条全满足,但止损位不低于收盘(底部低点在收盘之上),形态不成立"
    if capped and p.get("skip_capped"):
        raw = ind["base_low"] - p["stop_atr"] * ind["atr20"]
        return None, (f"两条全满足,但底部低点下方 {p['stop_atr']:.1f} ATR 在 ${raw:.2f},距收盘 {(1 - raw / px) * 100:.1f}% "
                      f"超过 {p['max_stop_pct'] * 100:.0f}% —— 形态不够紧,不进(C-04)")
    risk = px - stop
    gr = grade(ind, p, score)
    pct = p["grade_size"].get(gr["grade"], 0.0)
    if pct <= 0:
        return None, f"两条全满足,但评分 {gr['text']} —— D 级不买(C-08)"
    size = int(equity * pct / px)
    # 组合总风险上限:已有持仓的开放风险 + 这一笔 ≤ heat_cap
    room = equity * p["heat_cap"] - state.get("open_risk", 0.0)
    if size * risk > room:
        size = int(room / risk) if room > 0 else 0
        if size <= 0:
            return None, (f"两条全满足({gr['grade']} 级),但组合开放风险已达 {state.get('open_risk', 0.0) / equity * 100:.1f}% "
                          f"总资产,上限 {p['heat_cap'] * 100:.0f}%,放不下(C-09)")
    if size <= 0:
        return None, "两条全满足,但按仓位算法算出的股数为 0"
    cost = size * px
    if cost > state["cash"]:
        size = int(state["cash"] / px)
        cost = size * px
        if size <= 0:
            return None, f"两条全满足,但现金只剩 ${state['cash']:.0f},买不起 1 股"
    state["cash"] -= cost
    state["open_risk"] = state.get("open_risk", 0.0) + size * risk
    pos = av.Position(code=code, name=name or code, size=size, initial_size=size, entry_price=px,
                      entry_date=state["date"], avg_cost=px, highest=px, level=1, bars_held=0,
                      entry_rule=ENTRY_RULE, stop=stop, risk=risk)
    state["positions"].append(pos)
    extra = {"amount": round(cost, 2), "position_pct": round(cost / equity * 100, 2), "grade": gr["grade"], "points": gr["points"]}
    if not want_text:
        return _fill("buy", pos, size, px, ENTRY_RULE, "", **extra), None
    t = {c["rule"]: c["text"] for c in entry_checks(ind, p)}
    rationale = (f"{t['C-01']};{t['C-02']}。评分 {gr['text']} → 仓位 {pct * 100:.0f}% 总资产;"
                 f"止损挂在底部低点 ${ind['base_low']:.2f} 下方 {p['stop_atr']:.1f} 个 ATR = ${stop:.2f}(距收盘 {(1 - stop / px) * 100:.1f}%);"
                 f"买入 {size} 股,占总资产 {cost / equity * 100:.1f}%,这一笔开放风险 ${size * risk:.0f}"
                 f"(组合合计 {state['open_risk'] / equity * 100:.1f}%,上限 {p['heat_cap'] * 100:.0f}%)。")
    return _fill("buy", pos, size, px, ENTRY_RULE, rationale, **extra), None


def run_day(date_iso: str, positions, cash: float, bars_of, watch, prev_equity, consec_losses: int,
            p: dict = PARAMS, g: dict = av.GUARDS, ind_of=None, want_text: bool = True) -> dict:
    """接口与 agent_vcp.run_day 相同(agent_run / agent_sim 按引擎名调用)。"""
    state = {"date": date_iso, "cash": cash, "positions": list(positions), "closed": [], "closed_pnl": [],
             "equity": None, "halt_reason": None}
    if ind_of is None:
        def ind_of(code):
            return indicators(bars_of(code) or [], p)
    ind_cache = {pos.code: ind_of(pos.code) for pos in state["positions"]}
    mv = sum(pos.size * (ind_cache[pos.code]["close"] if ind_cache[pos.code] else pos.avg_cost) for pos in state["positions"])
    equity = cash + mv
    state["equity"] = equity
    if prev_equity and (equity / prev_equity - 1) * 100 <= g["daily_loss_halt_pct"]:
        state["halt_reason"] = f"今日权益 {(equity / prev_equity - 1) * 100:+.1f}%,触及单日亏损熔断 {g['daily_loss_halt_pct']:.0f}%,今天不开新仓"
    elif consec_losses >= g["consecutive_loss_pause"]:
        state["halt_reason"] = f"此前连亏 {consec_losses} 笔,按护栏今天不开新仓"
        consec_losses = 0
    fills = []
    for pos in list(state["positions"]):
        ind = ind_cache[pos.code]
        if ind is None:
            pos.bars_held += 1
            continue
        fills += manage_position(pos, ind, state, p, want_text)
    state["positions"] = [x for x in state["positions"] if x.size > 0]
    # 今天收盘后的组合开放风险(移动止损已上移的,风险自然变小)
    state["open_risk"] = sum(
        pos.size * max((ind_cache[pos.code]["close"] if ind_cache.get(pos.code) else pos.avg_cost) - pos.stop, 0.0)
        for pos in state["positions"])
    held = {x.code for x in state["positions"]}
    watch_items = []
    for code, name, score in watch:
        ind = ind_cache[code] if code in ind_cache else ind_of(code)
        ind_cache[code] = ind
        blocked = None
        if code not in held and ind is not None:
            f, blocked = try_entry(code, name, ind, state, p, want_text, score)
            if f:
                fills.append(f)
                held.add(code)
        if want_text:
            watch_items.append(watch_item(code, name, ind, code in held and not any(
                x["symbol"] == code and x["side"] == "buy" for x in fills), blocked, score))
    for pnl in state["closed_pnl"]:
        consec_losses = consec_losses + 1 if pnl < 0 else 0
    mv = sum(pos.size * (ind_cache.get(pos.code) or ind_of(pos.code) or {"close": pos.avg_cost})["close"]
             for pos in state["positions"])
    watch_items.sort(key=lambda x: (bool(x.get("blocked")), -(x.get("progress_pct") or 0)))
    return {"fills": fills, "positions": state["positions"], "cash": state["cash"],
            "equity": state["cash"] + mv, "watch_items": watch_items, "halt_reason": state["halt_reason"],
            "consec_losses": consec_losses, "closed": state["closed"]}
