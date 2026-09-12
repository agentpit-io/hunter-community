"""小鹿智能体 · 「VCP 波段交易」策略引擎(纯计算,不连库不联网 —— tests/test_agent_vcp.py 直接测)。

2026-09-12 用户给了一份 Backtrader 策略(VCPTrendStrategy),要求小鹿智能体按它跑纸上交易,
每日观察列表 = 筛选器「VCP 波段收缩」当天的结果。这里是那份策略的逐条移植,
只做**日线收盘后**的决策:每个交易日收盘后跑一次,信号当天以收盘价成交
(日线里没有开盘价,做不了 Backtrader 默认的"次日开盘成交";相当于 cheat_on_close)。

## 与原脚本不同的三处(都是原脚本里的明显 bug / 歧义,改法写在这里,用户可以要求改回去)

1. **倒三角加仓永远触发不了**:原脚本 `_manage_exits` 先把 highest_price 更新成 max(旧, 今收),
   `_check_pyramid_signals` 再判 `close > highest_price` —— 恒为假。这里按意图实现:
   今收 > **昨天为止**的最高价才算"突破前浪高点"。
2. **+10% 减半会重复触发**:原脚本 `pyramid_level != -1` 在 -2(已第二次止盈)时又成立,
   会再卖一半并把状态改回 -1。这里 -1 / -2 都不再触发第一档。
3. **止盈状态下还能加仓**:原脚本 `pyramid_level >= 3` 才拦,-1 / -2 都能过。这里只在 1~2 档加仓。

## 用户 2026-09-12 拍板的两处改动(回填实测原样跑 25 个交易日 0 笔成交之后)

- **R-03 收缩看突破前一天、阈值 1.0**:原脚本要求突破当天 ATR5 < 0.7·ATR20,但 ATR5 含突破日本身,
  突破日振幅必然放大 —— 全市场 20 个交易日 76887 个票-日里,突破且放量的 669 个只有 12 个能过。
  改成前一天(不含突破日)ATR5 < 1.0·ATR20:258 个里能过 119 个。
- **观察池 = 最近 10 个交易日筛选结果的并集**(agent_run 负责拼):「VCP 波段收缩」筛出来的是还没突破的票,
  真突破那天往往已经不在当天的结果里,只看当天永远接不到突破。

其余逐条照搬:过滤(趋势 / 流动性 / ATR 收缩 / 突破未超伸 / 放量)、仓位(风险 2% 与初始 8% 取小)、
止损(涨过 5% 回落到成本 / -5% 减半 / -8% 清仓 / 跌破 SMA50 2%)、止盈(+10% 减半 / +15% 再减半 / +20% 清仓)、
时间止损(第 5 天没涨 5% 减半 / 第 10 天清仓)、倒三角加仓(第 2 注是第 1 注的一半,最多 3 注,单股 ≤25%)。
原脚本算了 SMA200 但没用到,这里也不用。

## 指标口径(对齐 Backtrader)

EMA / ATR 都用 SMA 做种子再递推(bt 的 EMA 与 Smoothed/Wilder 都是这样);
ATR 的 TR = max(高-低, |高-昨收|, |低-昨收|)。`pivot_high` = 昨天往前 20 根的最高价(不含今天)。
ADTV = 20 日 (量×收) 均值。

## 规则编号(R-xx 一旦分配不复用,交易记录和成长总结引用它)

见 RULES。买入必须五条过滤同时满足,交易记录挂在 R-04(突破)上,rationale 里逐条写数字。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

# ── 参数(原脚本 params)──────────────────────────────────────
PARAMS = {
    "portfolio_risk": 0.02, "max_stop_pct": 0.08, "avg_loss_limit": 0.06,
    "initial_pos_pct": 0.08, "max_single_stock_pct": 0.25, "max_holdings": 10,
    "chase_limit": 1.05, "vol_boost": 1.40,
    "min_adtv": 10_000_000.0, "min_price": 10.0, "atr_compact": 1.00,   # 原脚本 0.70(当天算),见文件头
    "watch_pool_days": 10,
    "pivot_period": 20, "ema_fast": 8, "ema_slow": 21, "sma_trend": 50,
    # 卖出侧(原脚本写死在 _manage_exits 里的数字,2026-09-12 抽成参数给优化器用)
    "pullback_trigger": 0.05,   # R-07 / R-14 / R-15 的「涨过 +5%」
    "half_loss_pct": 0.05,      # R-08 亏 -5% 减半
    "sma_stop_pct": 0.02,       # R-10 跌破 SMA50 2%
    "tp1": 0.10, "tp2": 0.15, "tp3": 0.20,     # R-11 / R-12 / R-13
    "time1_days": 5, "time2_days": 10,         # R-14 / R-15
    "pyramid_max_reaction": 0.10,              # R-16 自然调整幅度上限
}
# 止损类参数只许收紧不许放宽(用户 2026-09-12 定的前提):优化器生成候选时按这张表校验,
# 方向是「数值更小 = 更紧」
STOP_KEYS = ("max_stop_pct", "half_loss_pct", "sma_stop_pct")
# 护栏(原脚本没有,UI 契约 §3.3 需要;2026-09-12 先取默认值,待用户确认)
GUARDS = {"initial_capital": 100_000.0, "daily_loss_halt_pct": -3.0, "consecutive_loss_pause": 3}

MIN_BARS = 60          # 少于这么多根日线的票不交易(SMA50 + 枢轴 + ATR20 都要够)

RULES = [
    {"id": "R-01", "kind": "buy", "condition": "趋势:EMA8 > EMA21,且收盘在 EMA8 上方"},
    {"id": "R-02", "kind": "buy", "condition": "流动性:20 日日均成交额 ≥ 1000 万美元,且股价 ≥ $10"},
    {"id": "R-03", "kind": "buy", "condition": "VCP 收缩:突破前一天的 5 日 ATR 低于 20 日 ATR(不含突破日;原脚本当天算、70%)"},
    {"id": "R-04", "kind": "buy", "condition": "突破:收盘高于前 20 日枢轴高点,且不超过枢轴 5%(不追高)"},
    {"id": "R-05", "kind": "buy", "condition": "放量:当日成交量 ≥ 50 日均量的 1.4 倍"},
    {"id": "R-06", "kind": "risk", "condition": "仓位:单笔风险 2% 总资产(按 -8% 止损反推)与初始仓位 8% 总资产取小"},
    {"id": "R-07", "kind": "sell", "condition": "涨过 +5% 后回落到买入价:全部清仓"},
    {"id": "R-08", "kind": "sell", "condition": "亏损达 -5%(仍是初始仓位):卖出一半"},
    {"id": "R-09", "kind": "sell", "condition": "亏损达 -8%:硬止损,全部清仓"},
    {"id": "R-10", "kind": "sell", "condition": "跌破 50 日均线 2% 以上:强制清仓"},
    {"id": "R-11", "kind": "sell", "condition": "盈利 +10%:卖出一半"},
    {"id": "R-12", "kind": "sell", "condition": "盈利 +15%:再卖出剩余的一半"},
    {"id": "R-13", "kind": "sell", "condition": "盈利 +20%:全部清仓"},
    {"id": "R-14", "kind": "sell", "condition": "时间止损:持有第 5 个交易日仍没涨过 +5%,减仓一半"},
    {"id": "R-15", "kind": "sell", "condition": "时间止损:持有第 10 个交易日仍没涨过 +5%,全部清仓"},
    {"id": "R-16", "kind": "buy", "condition": "倒三角加仓:盈利中、近 3 日回撤 ≤10% 后收盘突破前高,加第 1 注的一半(最多 3 注)"},
    {"id": "R-17", "kind": "risk", "condition": "单股总持仓不超过总资产 25%"},
    {"id": "R-18", "kind": "risk", "condition": "最多同时持有 10 只"},
    {"id": "R-19", "kind": "risk", "condition": "护栏:单日权益回撤达 -3% 当天停止开仓;连亏 3 笔后下一个交易日不开仓"},
]
def rules_for(p: dict = PARAMS) -> list[dict]:
    """规则手册的文案按参数生成 —— 优化器改了参数,面板上的条件文字必须跟着变。"""
    def pct(x):
        return f"{x * 100:.0f}%"
    out = []
    for r in RULES:
        c = dict(r)
        rid = r["id"]
        if rid == "R-02":
            c["condition"] = f"流动性:20 日日均成交额 ≥ ${p['min_adtv'] / 1e6:.0f}M,且股价 ≥ ${p['min_price']:.0f}"
        elif rid == "R-03":
            c["condition"] = f"VCP 收缩:突破前一天的 5 日 ATR 低于 20 日 ATR 的 {pct(p['atr_compact'])}(不含突破日;原脚本当天算、70%)"
        elif rid == "R-04":
            c["condition"] = f"突破:收盘高于前 {p['pivot_period']} 日枢轴高点,且不超过枢轴 {pct(p['chase_limit'] - 1)}(不追高)"
        elif rid == "R-05":
            c["condition"] = f"放量:当日成交量 ≥ 50 日均量的 {p['vol_boost']:.1f} 倍"
        elif rid == "R-07":
            c["condition"] = f"涨过 +{pct(p['pullback_trigger'])} 后回落到买入价:全部清仓"
        elif rid == "R-08":
            c["condition"] = f"亏损达 -{pct(p['half_loss_pct'])}(仍是初始仓位):卖出一半"
        elif rid == "R-09":
            c["condition"] = f"亏损达 -{pct(p['max_stop_pct'])}:硬止损,全部清仓"
        elif rid == "R-10":
            c["condition"] = f"跌破 50 日均线 {pct(p['sma_stop_pct'])} 以上:强制清仓"
        elif rid == "R-11":
            c["condition"] = f"盈利 +{pct(p['tp1'])}:卖出一半"
        elif rid == "R-12":
            c["condition"] = f"盈利 +{pct(p['tp2'])}:再卖出剩余的一半"
        elif rid == "R-13":
            c["condition"] = f"盈利 +{pct(p['tp3'])}:全部清仓"
        elif rid == "R-14":
            c["condition"] = f"时间止损:持有第 {p['time1_days']} 个交易日仍没涨过 +{pct(p['pullback_trigger'])},减仓一半"
        elif rid == "R-15":
            c["condition"] = f"时间止损:持有第 {p['time2_days']} 个交易日仍没涨过 +{pct(p['pullback_trigger'])},全部清仓"
        elif rid == "R-16":
            c["condition"] = f"倒三角加仓:盈利中、近 3 日回撤 ≤{pct(p['pyramid_max_reaction'])} 后收盘突破前高,加第 1 注的一半(最多 3 注)"
        out.append(c)
    return out


RULE_NAME = {
    "R-04": "VCP 突破买入", "R-07": "涨后回落到成本", "R-08": "-5% 减半", "R-09": "-8% 硬止损",
    "R-10": "跌破 50 日线", "R-11": "+10% 止盈一半", "R-12": "+15% 再止盈", "R-13": "+20% 清仓",
    "R-14": "5 日不涨减半", "R-15": "10 日不涨清仓", "R-16": "倒三角加仓",
}
ENTRY_RULE_IDS = ("R-01", "R-02", "R-03", "R-04", "R-05")


@dataclass
class Position:
    code: str
    name: str
    size: int
    initial_size: int
    entry_price: float       # 第一注的成交价(原脚本 profit_pct 的分母)
    entry_date: str          # ISO
    avg_cost: float
    highest: float           # 截至**昨天**的持有期最高收盘(今天的比较用它,比完再更新)
    level: int               # 1..3 加仓档;-1 / -2 止盈状态
    bars_held: int = 0       # 持有的交易日数(入场当天 0)
    entry_rule: str = "R-04"

    def to_dict(self) -> dict:
        return asdict(self)


# ═══════════════════════════════════════════════════════════════
# 指标
# ═══════════════════════════════════════════════════════════════

def _sma(xs, n):
    return sum(xs[-n:]) / n if len(xs) >= n else None


def _ema(xs, n):
    """bt 口径:前 n 根 SMA 做种子,之后 alpha = 2/(n+1) 递推。"""
    if len(xs) < n:
        return None
    a = 2.0 / (n + 1)
    e = sum(xs[:n]) / n
    for x in xs[n:]:
        e = a * x + (1 - a) * e
    return e


def _atr(bars, n):
    """Wilder ATR:TR 的前 n 个 SMA 做种子,之后 (prev·(n-1) + tr)/n。bars = [(d, c, h, l, v)]"""
    if len(bars) < n + 1:
        return None
    trs = []
    for i in range(1, len(bars)):
        _, c, h, lo, _ = bars[i]
        pc = bars[i - 1][1]
        if h is None or lo is None:
            return None
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    a = sum(trs[:n]) / n
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n
    return a


def indicators(bars: list[tuple], p: dict = PARAMS) -> dict | None:
    """一只票截到今天的日线 → 今天的指标;不够根数或缺高低量 → None。"""
    if len(bars) < MIN_BARS:
        return None
    c = [b[1] for b in bars]
    h = [b[2] for b in bars]
    lo = [b[3] for b in bars]
    v = [b[4] for b in bars]
    if any(x is None for x in h[-60:] + lo[-60:] + v[-60:]):
        return None
    pv = p["pivot_period"]
    out = {
        "close": c[-1], "high": h[-1], "low": lo[-1], "volume": v[-1],
        "ema8": _ema(c, p["ema_fast"]), "ema21": _ema(c, p["ema_slow"]),
        "sma50": _sma(c, p["sma_trend"]),
        "vol_sma50": _sma(v, 50) if len(v) >= 50 and all(x is not None for x in v[-50:]) else None,
        "adtv": _sma([cc * vv for cc, vv in zip(c[-20:], v[-20:])], 20),
        "pivot_high": max(h[-pv - 1:-1]) if len(h) >= pv + 1 else None,
        "atr20": _atr(bars[-61:], 20), "atr5": _atr(bars[-61:], 5),
        # 前一天的 ATR(不含今天):R-03 用它 —— 突破日本身振幅放大,含今天几乎永远过不了
        "atr20_prev": _atr(bars[-62:-1], 20), "atr5_prev": _atr(bars[-62:-1], 5),
        "low3": min(lo[-3:]),
    }
    return out


# ═══════════════════════════════════════════════════════════════
# 买入过滤 + 观察列表的「还差什么」
# ═══════════════════════════════════════════════════════════════

def entry_flags(ind: dict, p: dict = PARAMS) -> dict:
    """五条买入过滤的判定 + 判定用到的数字(不拼文字 —— 优化器一天要判几万次,文字只在展示时拼)。"""
    ema8, ema21, close = ind["ema8"], ind["ema21"], ind["close"]
    adtv, ph, vs = ind["adtv"], ind["pivot_high"], ind["vol_sma50"]
    a5, a20 = ind.get("atr5_prev"), ind.get("atr20_prev")
    ratio = (a5 / a20) if (a5 is not None and a20) else None
    dist = (close / ph - 1) * 100 if ph else None
    vr = (ind["volume"] / vs) if vs else None
    return {
        "R-01": ema8 is not None and ema21 is not None and ema8 > ema21 and close > ema8,
        "R-02": adtv is not None and adtv >= p["min_adtv"] and close >= p["min_price"],
        "R-03": ratio is not None and ratio < p["atr_compact"],
        "R-04": dist is not None and 0 < dist <= (p["chase_limit"] - 1) * 100,
        "R-05": vr is not None and vr >= p["vol_boost"],
        "ratio": ratio, "dist": dist, "vr": vr,
    }


def entry_ok(ind: dict, p: dict = PARAMS) -> bool:
    f = entry_flags(ind, p)
    return f["R-01"] and f["R-02"] and f["R-03"] and f["R-04"] and f["R-05"]


def entry_checks(ind: dict, p: dict = PARAMS) -> list[dict]:
    """五条买入过滤 → [{rule, ok, text}],text 是给人看的差距(数字来自 ind)。"""
    f = entry_flags(ind, p)
    out = []
    ok1 = f["R-01"]
    out.append({"rule": "R-01", "ok": ok1,
                "text": (f"EMA8 ${ind['ema8']:.2f} / EMA21 ${ind['ema21']:.2f},收盘 ${ind['close']:.2f}"
                         if ind["ema8"] is not None and ind["ema21"] is not None else "EMA 算不出")
                        + ("" if ok1 else " —— 趋势不满足")})
    adtv = ind["adtv"]
    ok2 = f["R-02"]
    out.append({"rule": "R-02", "ok": ok2,
                "text": (f"日均成交额 ${adtv / 1e6:.1f}M" if adtv is not None else "成交额算不出")
                        + (f",股价 ${ind['close']:.2f}") + ("" if ok2 else " —— 流动性不够")})
    ratio = f["ratio"]
    ok3 = f["R-03"]
    out.append({"rule": "R-03", "ok": ok3,
                "text": (f"前一天 ATR5/ATR20 = {ratio:.2f}" if ratio is not None else "ATR 算不出")
                        + (f"(<{p['atr_compact']:.2f} 算收缩)" if ok3 else f" —— 还不够紧,要低于 {p['atr_compact']:.2f}")})
    ph, dist = ind["pivot_high"], f["dist"]
    ok4 = f["R-04"]
    if dist is None:
        t4 = "枢轴算不出"
    elif dist <= 0:
        t4 = f"距 20 日枢轴高点 ${ph:.2f} 还差 {-dist:.1f}%"
    elif ok4:
        t4 = f"收盘高出枢轴 ${ph:.2f} {dist:.1f}%(≤{(p['chase_limit'] - 1) * 100:.0f}%,未超伸)"
    else:
        t4 = f"已高出枢轴 ${ph:.2f} {dist:.1f}%,超过 {(p['chase_limit'] - 1) * 100:.0f}% 不追"
    out.append({"rule": "R-04", "ok": ok4, "text": t4})
    vr = f["vr"]
    ok5 = f["R-05"]
    out.append({"rule": "R-05", "ok": ok5,
                "text": (f"量能 {vr:.2f}× 50 日均量" if vr is not None else "均量算不出")
                        + (f"(≥{p['vol_boost']:.1f}×)" if ok5 else f" —— 要 ≥{p['vol_boost']:.1f}×")})
    return out


def watch_item(code: str, name: str | None, ind: dict | None, held: bool, blocked_reason: str | None,
               score=None) -> dict:
    """观察列表一项(契约 §3.9):gap 写「还差什么才买」。"""
    it = {"symbol": code, "name": name, "score": score, "rule_id": "R-04", "rule_text": RULES[3]["condition"]}
    if ind is None:
        it.update({"price": None, "progress_pct": None, "gap": "日线不足 60 根或缺高低量,指标算不出"})
        return it
    it["price"] = round(ind["close"], 2)
    checks = entry_checks(ind)
    passed = sum(1 for c in checks if c["ok"])
    it["progress_pct"] = int(passed / len(checks) * 100)
    fails = [c for c in checks if not c["ok"]]
    if held:
        it["gap"] = "已持仓 · 等加仓或出场信号"
    elif not fails:
        it["gap"] = "五条全满足 —— 今日收盘触发买入"
    else:
        it["gap"] = f"{passed}/5 满足 · 还差:" + ";".join(f"{c['rule']} {c['text']}" for c in fails[:3])
    if blocked_reason:
        it["blocked"] = True
        it["blocked_reason"] = blocked_reason
    return it


# ═══════════════════════════════════════════════════════════════
# 一天的决策
# ═══════════════════════════════════════════════════════════════

def _fill(side, pos: Position, shares: int, price: float, rule: str, rationale: str, **extra) -> dict:
    d = {"side": side, "symbol": pos.code, "name": pos.name, "shares": int(shares), "price": round(price, 2),
         "rule_id": rule, "rule_name": RULE_NAME.get(rule, rule), "rationale": rationale,
         "entry_date": pos.entry_date, "level": pos.level}
    d.update(extra)
    return d


def _sell(pos: Position, shares: int, price: float, rule: str, rationale: str, state: dict) -> dict:
    shares = min(int(shares), pos.size)
    pnl_abs = (price - pos.avg_cost) * shares
    pnl_pct = (price / pos.avg_cost - 1) * 100
    state["cash"] += shares * price
    pos.size -= shares
    state["closed_pnl"].append(pnl_abs)
    return _fill("sell", pos, shares, price, rule, rationale,
                 pnl_abs=round(pnl_abs, 2), pnl_pct=round(pnl_pct, 2), hold_days=pos.bars_held)


def manage_position(pos: Position, ind: dict, state: dict, p: dict = PARAMS) -> list[dict]:
    """持仓的出场 / 加仓(原脚本 _manage_exits + _check_pyramid_signals,同一优先级顺序)。
    ind 是今天的指标;pos.highest 是截至昨天的最高;函数结束时把今天并进去。"""
    fills: list[dict] = []
    px = ind["close"]
    prev_high = pos.highest
    high_now = max(prev_high, px)
    pos.bars_held += 1
    n = pos.bars_held
    prof = (px - pos.entry_price) / pos.entry_price
    ep = pos.entry_price
    base = f"买入价 ${ep:.2f},今收 ${px:.2f}({prof * 100:+.1f}%),持有 {n} 个交易日,期间最高 ${high_now:.2f}。"

    def close_all(rule, why):
        fills.append(_sell(pos, pos.size, px, rule, base + why, state))

    def half(rule, why):
        fills.append(_sell(pos, int(pos.size * 0.5), px, rule, base + why, state))

    pb, hl, ms, ss = p["pullback_trigger"], p["half_loss_pct"], p["max_stop_pct"], p["sma_stop_pct"]
    tp1, tp2, tp3, t1, t2 = p["tp1"], p["tp2"], p["tp3"], p["time1_days"], p["time2_days"]
    # A. 止损
    if high_now >= ep * (1 + pb) and px <= ep:
        close_all("R-07", f"曾涨过 +{pb * 100:.0f}%(最高 ${high_now:.2f} ≥ ${ep * (1 + pb):.2f}),今天收回买入价以下 —— 全部清仓。")
    elif prof <= -hl and pos.size == pos.initial_size:
        half("R-08", f"亏损达到 -{hl * 100:.0f}%(仍是初始仓位 {pos.initial_size} 股)—— 先卖出一半。")
    elif prof <= -ms:
        close_all("R-09", f"亏损达到 -{ms * 100:.0f}% 硬止损线 ${ep * (1 - ms):.2f} —— 全部清仓。")
    elif ind["sma50"] is not None and px < ind["sma50"] * (1 - ss):
        close_all("R-10", f"收盘 ${px:.2f} 跌破 50 日均线 ${ind['sma50']:.2f} 的 {ss * 100:.0f}% 以下 —— 强制清仓。")
    # B. 止盈
    elif prof >= tp3:
        close_all("R-13", f"盈利达到 +{tp3 * 100:.0f}% —— 全部清仓。")
    elif prof >= tp2 and pos.level == -1:
        half("R-12", f"盈利达到 +{tp2 * 100:.0f}%(已做过第一次止盈)—— 再卖出剩余的一半。")
        pos.level = -2
    elif prof >= tp1 and pos.level not in (-1, -2):
        half("R-11", f"盈利达到 +{tp1 * 100:.0f}% —— 卖出一半,进入止盈状态。")
        pos.level = -1
    # C. 时间
    elif n == t1 and high_now < ep * (1 + pb):
        half("R-14", f"持有第 {t1} 个交易日,期间最高只到 ${high_now:.2f}(没涨过 +{pb * 100:.0f}% 的 ${ep * (1 + pb):.2f})—— 减仓一半。")
    elif n >= t2 and high_now < ep * (1 + pb):
        close_all("R-15", f"持有第 {n} 个交易日仍没涨过 +{pb * 100:.0f}% —— 全部清仓。")
    # D. 倒三角加仓(只在没出场、盈利中、加仓档 1~2)
    elif px > ep and 1 <= pos.level < 3:
        equity = state["equity"]
        pos_val = pos.size * px
        if pos_val < equity * p["max_single_stock_pct"]:
            reaction = (prev_high - ind["low3"]) / prev_high if prev_high else 1.0
            if reaction <= p["pyramid_max_reaction"] and px > prev_high:
                nxt = int(pos.initial_size * (0.5 ** pos.level))
                cost = nxt * px
                if nxt > 0 and cost <= state["cash"]:
                    state["cash"] -= cost
                    pos.avg_cost = (pos.avg_cost * pos.size + cost) / (pos.size + nxt)
                    pos.size += nxt
                    pos.level += 1
                    fills.append(_fill("buy", pos, nxt, px, "R-16",
                        base + f"近 3 日最低 ${ind['low3']:.2f},距前高 ${prev_high:.2f} 回撤 {reaction * 100:.1f}%(≤10% 算自然调整),"
                               f"今收突破前高 —— 加第 {pos.level} 注 {nxt} 股(第 1 注 {pos.initial_size} 股的 1/{2 ** (pos.level - 1)}),"
                               f"单股持仓 {pos.size * px / equity * 100:.1f}% 总资产。",
                        amount=round(cost, 2), position_pct=round(pos.size * px / equity * 100, 2)))
    pos.highest = high_now
    if pos.size <= 0:
        state["closed"].append(pos)
    return fills


def try_entry(code: str, name: str | None, ind: dict, state: dict, p: dict = PARAMS,
              want_text: bool = True) -> tuple[dict | None, str | None]:
    """新开仓。→ (成交 | None, 被挡的原因 | None)。want_text=False 时不拼 rationale(优化器模拟用)。"""
    if not entry_ok(ind, p):
        return None, None
    if len(state["positions"]) >= p["max_holdings"]:
        return None, f"五条全满足,但已持有 {len(state['positions'])} 只,达到上限(R-18)"
    if state.get("halt_reason"):
        return None, f"五条全满足,但护栏挡下:{state['halt_reason']}(R-19)"
    equity = state["equity"]
    px = ind["close"]
    risk_size = int(equity * p["portfolio_risk"] / (px * p["max_stop_pct"]))
    cap_size = int(equity * p["initial_pos_pct"] / px)
    size = min(risk_size, cap_size)
    if size <= 0:
        return None, "五条全满足,但按仓位算法算出的股数为 0"
    cost = size * px
    if cost > state["cash"]:
        size = int(state["cash"] / px)
        cost = size * px
        if size <= 0:
            return None, f"五条全满足,但现金只剩 ${state['cash']:.0f},买不起 1 股"
    state["cash"] -= cost
    pos = Position(code=code, name=name or code, size=size, initial_size=size, entry_price=px,
                   entry_date=state["date"], avg_cost=px, highest=px, level=1, bars_held=0)
    state["positions"].append(pos)
    if not want_text:
        return _fill("buy", pos, size, px, "R-04", "", amount=round(cost, 2), position_pct=round(cost / equity * 100, 2)), None
    t = {c["rule"]: c["text"] for c in entry_checks(ind, p)}
    rationale = (f"{t['R-04']};{t['R-05']};{t['R-03']};{t['R-01']};{t['R-02']}。"
                 f"按单笔风险 2%(${equity * p['portfolio_risk']:.0f} ÷ 止损距离 ${px * p['max_stop_pct']:.2f} = {risk_size} 股)"
                 f"与初始仓位 8%(${equity * p['initial_pos_pct']:.0f} ÷ ${px:.2f} = {cap_size} 股)取小,买入 {size} 股,"
                 f"占总资产 {cost / equity * 100:.1f}%。止损挂在 ${px * (1 - p['max_stop_pct']):.2f}(-8%)。")
    return _fill("buy", pos, size, px, "R-04", rationale,
                 amount=round(cost, 2), position_pct=round(cost / equity * 100, 2)), None


def run_day(date_iso: str, positions: list[Position], cash: float, bars_of, watch: list[tuple],
            prev_equity: float | None, consec_losses: int, p: dict = PARAMS, g: dict = GUARDS,
            ind_of=None, want_text: bool = True) -> dict:
    """跑一个交易日(收盘后)。

    bars_of(code) → 截到今天的日线;watch = [(code, name, score)] 今天的观察列表(筛选结果)。
    ind_of(code) → 今天的指标(可选;优化器的模拟用预先算好的缓存,不重算)。
    → {fills, positions, cash, equity, watch_items, halt_reason, consec_losses, closed}
    """
    state = {"date": date_iso, "cash": cash, "positions": list(positions), "closed": [], "closed_pnl": [],
             "equity": None, "halt_reason": None}
    if ind_of is None:
        def ind_of(code):
            return indicators(bars_of(code) or [], p)
    # 今天的指标
    ind_cache: dict = {}
    for pos in state["positions"]:
        ind_cache[pos.code] = ind_of(pos.code)
    ind_of_pos = ind_cache
    # 今天收盘的权益(成交前),仓位算法的分母
    mv = sum(pos.size * (ind_of_pos[pos.code]["close"] if ind_of_pos[pos.code] else pos.avg_cost) for pos in state["positions"])
    equity = cash + mv
    state["equity"] = equity
    # 护栏:单日权益回撤 / 连亏
    if prev_equity and (equity / prev_equity - 1) * 100 <= g["daily_loss_halt_pct"]:
        state["halt_reason"] = f"今日权益 {(equity / prev_equity - 1) * 100:+.1f}%,触及单日亏损熔断 {g['daily_loss_halt_pct']:.0f}%,今天不开新仓"
    elif consec_losses >= g["consecutive_loss_pause"]:
        state["halt_reason"] = f"此前连亏 {consec_losses} 笔,按护栏今天不开新仓"

    fills: list[dict] = []
    # 1. 持仓管理(出场 / 加仓)
    for pos in list(state["positions"]):
        ind = ind_of_pos[pos.code]
        if ind is None:
            pos.bars_held += 1
            continue                          # 今天没有这只票的日线(停牌)—— 不动
        fills += manage_position(pos, ind, state, p)
    state["positions"] = [x for x in state["positions"] if x.size > 0]
    # 2. 观察列表 → 开仓
    held = {x.code for x in state["positions"]}
    watch_items = []
    for code, name, score in watch:
        ind = ind_cache[code] if code in ind_cache else ind_of(code)
        ind_cache[code] = ind
        blocked = None
        if code not in held and ind is not None:
            f, blocked = try_entry(code, name, ind, state, p, want_text)
            if f:
                fills.append(f)
                held.add(code)
        if want_text:
            watch_items.append(watch_item(code, name, ind, code in held and not any(
                x["symbol"] == code and x["side"] == "buy" and x["rule_id"] == "R-04" for x in fills), blocked, score))
    # 连亏计数:按今天卖出成交的盈亏更新
    for pnl in state["closed_pnl"]:
        consec_losses = consec_losses + 1 if pnl < 0 else 0
    # 成交后的权益(仍按今天收盘)
    mv = sum(pos.size * (ind_cache.get(pos.code) or ind_of(pos.code) or {"close": pos.avg_cost})["close"]
             for pos in state["positions"])
    # 观察列表:被挡的排最后
    watch_items.sort(key=lambda x: (bool(x.get("blocked")), -(x.get("progress_pct") or 0)))
    return {"fills": fills, "positions": state["positions"], "cash": state["cash"],
            "equity": state["cash"] + mv, "watch_items": watch_items, "halt_reason": state["halt_reason"],
            "consec_losses": consec_losses, "closed": state["closed"]}
