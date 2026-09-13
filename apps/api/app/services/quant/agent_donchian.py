"""小鹿智能体 · 唐奇安突破线(2026-09-13 研究台方案的第一条新研究线,用户批准)。

方案见 docs/agent-research-plan.md。和 VCP 线差得最远的一种趋势策略:不看形态、不等收缩,
只认价格创新高,让利润自己跑。选它先做,是因为和 VCP 比出来的信息量最大。

## 规则(D-xx,和 VCP 的 R-xx / C-xx / V-xx 不共用编号)

买入:
- D-01 突破:收盘 > 前 55 个交易日的最高价(不含今天)。
- D-02 新鲜:前一天收盘还没突破它自己的前 55 日最高 —— 只在第一次突破那天进。
        不加这条的话,一只连涨一个月的票每天都满足 D-01,等于在趋势末端追高。
- D-03 仓位:单笔风险 1% 总资产 ÷ 每股风险(2 ATR)= 股数;单股不超过 20% 总资产;最多 8 只。
卖出(先到先出):
- D-04 初始止损:收盘跌破 进场价 − 2 ATR(20)。进场时定死,之后不动。
- D-05 通道止损:收盘跌破前 20 个交易日的最低价(不含今天)。趋势走远后它会自己上移,就是移动止损。
- D-06 护栏:与 VCP 线同一套(单日权益 -3% 当天不开仓、连亏 3 笔停一天)。

收盘后决策、信号当天收盘价成交,和 VCP 线同一口径。不加仓(原版海龟有 4 个单位的加仓,先不做,
满 30 笔判定通过之后再说)。**不做参数优化**:研究线规则固定,满 30 笔前不开优化器(tunable 为空)。

## 股票池(POOL)只是宽松预筛

扫描源的 Highest(high, N) 只能映射 5 / 21 / 63 / 126 / 252 个交易日,**没有 55**;拿 63 日顶替 55 日就是
「换写法不换数据」(仓内 CLAUDE.md 那条)。所以池子只圈「离 3 个月高点 3% 以内、流动性够」的票,
**55 日最高由引擎用自家日线精确算**。已知会漏掉:56~63 天前有过更高点、今天突破 55 日最高时仍比那个高点低 3% 以上的票。
池子按 RS 排序取前 300,只用当天结果(不做 10 天并集:突破是当天的事,前几天的候选今天不突破就不相干)。
"""
from __future__ import annotations

from app.services.quant import agent_vcp as av

PARAMS = {
    "entry_n": 55, "exit_n": 20, "stop_atr": 2.0,
    "risk_pct": 0.01, "max_pos_pct": 0.20, "max_holdings": 8,
    "watch_pool_days": 1,
}
STOP_KEYS = ("stop_atr",)
MIN_BARS = 60
ENTRY_RULE = "D-01"
WATCH_POOL_DAYS = 1

POOL = "donchian"
POOL_LIMIT = 300
POOL_LABEL = "唐奇安预筛池(收盘离 3 个月高点 3% 以内 · 日均成交额 ≥ 2000 万美元 · RS 排序前 300)"
POOL_SCRIPT = """# ===== 唐奇安突破预筛池(小鹿 · 唐奇安突破线)=====
# 55 日最高由引擎用日线精确算;扫描源没有 55 日窗口,这里只做宽松预筛
def c_price = close > 10;
def c_liq   = close * average_volume_30d_calc > 20000000;
def c_near  = close >= high_63d * 0.97;

plot scan = c_price and c_liq and c_near;
"""
EXEC_NOTE = "纸上交易 · 日线收盘价成交"

RULES = [
    {"id": "D-01", "kind": "buy", "condition": "突破:收盘 > 前 55 个交易日最高价(不含今天)"},
    {"id": "D-02", "kind": "buy", "condition": "新鲜:前一天收盘还没突破它自己的前 55 日最高 —— 只在第一次突破那天进"},
    {"id": "D-03", "kind": "risk", "condition": "仓位:单笔风险 1% 总资产 ÷ 每股风险(2 ATR);单股 ≤ 20% 总资产;最多 8 只"},
    {"id": "D-04", "kind": "sell", "condition": "初始止损:收盘跌破 进场价 − 2 ATR(20),进场时定死"},
    {"id": "D-05", "kind": "sell", "condition": "通道止损:收盘跌破前 20 个交易日最低价(不含今天)"},
    {"id": "D-06", "kind": "risk", "condition": "护栏:单日权益回撤达 -3% 当天停止开仓;连亏 3 笔后下一个交易日不开仓"},
]
RULE_NAME = {"D-01": "55 日突破买入", "D-04": "2 ATR 初始止损", "D-05": "跌破 20 日最低"}
RULE_PARAM_KEY: dict = {}          # 规则固定,不进优化器


def rules_for(p: dict = PARAMS) -> list[dict]:
    out = []
    for r in RULES:
        c = dict(r)
        rid = r["id"]
        if rid == "D-01":
            c["condition"] = f"突破:收盘 > 前 {p['entry_n']} 个交易日最高价(不含今天)"
        elif rid == "D-02":
            c["condition"] = f"新鲜:前一天收盘还没突破它自己的前 {p['entry_n']} 日最高 —— 只在第一次突破那天进"
        elif rid == "D-03":
            c["condition"] = (f"仓位:单笔风险 {p['risk_pct'] * 100:.0f}% 总资产 ÷ 每股风险({p['stop_atr']:.0f} ATR);"
                              f"单股 ≤ {p['max_pos_pct'] * 100:.0f}% 总资产;最多 {p['max_holdings']} 只")
        elif rid == "D-04":
            c["condition"] = f"初始止损:收盘跌破 进场价 − {p['stop_atr']:.0f} ATR(20),进场时定死"
        elif rid == "D-05":
            c["condition"] = f"通道止损:收盘跌破前 {p['exit_n']} 个交易日最低价(不含今天)"
        out.append(c)
    return out


def summary(p: dict = PARAMS) -> str:
    return (f"唐奇安通道突破:收盘第一次突破前 {p['entry_n']} 日最高价时进,跌破前 {p['exit_n']} 日最低价"
            f"或进场价下方 {p['stop_atr']:.0f} 个 ATR 时出;单笔风险 {p['risk_pct'] * 100:.0f}% 总资产、按 ATR 反推股数,"
            f"单股 ≤ {p['max_pos_pct'] * 100:.0f}%、最多 {p['max_holdings']} 只。不看形态、不设止盈,让趋势自己走。")


# ═══════════════════════════════════════════════════════════════
# 指标
# ═══════════════════════════════════════════════════════════════

def indicators(bars: list[tuple], p: dict = PARAMS, bench: dict | None = None) -> dict | None:
    """bars = [(d, c, h, l, v)] 升序,最后一根是今天。窗口按 PARAMS 的 entry_n / exit_n 算好缓存 ——
    指标缓存与参数无关是模拟器的前提,所以这两个窗口**不许**进优化器(tunable 为空)。"""
    n, m = int(p["entry_n"]), int(p["exit_n"])
    if len(bars) < max(MIN_BARS, n + 2):
        return None
    c = [b[1] for b in bars]
    h = [b[2] for b in bars]
    lo = [b[3] for b in bars]
    if any(x is None for x in h[-(n + 2):] + lo[-(n + 2):]):
        return None
    atr = av._atr(bars[-61:], 20)
    return {
        "close": c[-1], "prev_close": c[-2], "high": h[-1], "low": lo[-1],
        "atr20": atr,
        "hi_entry": max(h[-n - 1:-1]),          # 前 n 日最高(不含今天)
        "prev_hi_entry": max(h[-n - 2:-2]),     # 昨天看到的前 n 日最高(不含昨天)
        "lo_entry": min(lo[-n - 1:-1]),         # 通道下沿,只给观察列表算进度
        "lo_exit": min(lo[-m - 1:-1]),          # 前 m 日最低(不含今天)
    }


def entry_flags(ind: dict) -> dict:
    brk = ind["close"] > ind["hi_entry"]
    fresh = ind["prev_close"] <= ind["prev_hi_entry"]
    return {"D-01": brk, "D-02": fresh, "ok": brk and fresh}


# ═══════════════════════════════════════════════════════════════
# 观察列表
# ═══════════════════════════════════════════════════════════════

def watch_item(code, name, ind, held, blocked_reason, score=None, p: dict = PARAMS) -> dict:
    it = {"symbol": code, "name": name, "score": score, "rule_id": "D-01", "rule_text": rules_for(p)[0]["condition"]}
    if ind is None:
        it.update({"price": None, "progress_pct": None, "gap": f"日线不足 {max(MIN_BARS, p['entry_n'] + 2)} 根或缺高低价,算不出通道"})
        return it
    px, hi, lo = ind["close"], ind["hi_entry"], ind["lo_entry"]
    it["price"] = round(px, 2)
    # 进度 = 收盘在 55 日通道里的位置(下沿 0、上沿 100),同一条规则内可比
    it["progress_pct"] = int(max(0.0, min(100.0, (px - lo) / (hi - lo) * 100))) if hi > lo else None
    f = entry_flags(ind)
    if held:
        it["gap"] = "已持仓 · 等出场信号"
    elif f["ok"]:
        it["gap"] = f"收盘 ${px:.2f} 第一次突破前 {p['entry_n']} 日最高 ${hi:.2f} —— 今日收盘触发买入"
    elif f["D-01"]:
        it["gap"] = (f"已在前 {p['entry_n']} 日最高 ${hi:.2f} 上方,但前一天就突破过了(D-02 不新鲜)—— 不追,"
                     f"等回落后重新突破")
    else:
        it["gap"] = f"收盘 ${px:.2f},距前 {p['entry_n']} 日最高 ${hi:.2f} 还差 {(hi / px - 1) * 100:.1f}%"
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
    pos.bars_held += 1
    pos.highest = max(pos.highest, px)
    n, ep = pos.bars_held, pos.entry_price
    lo_exit = ind["lo_exit"]
    rule = why = None
    # 两条线谁高谁先被穿:通道下沿已经抬到止损之上时,出场原因记通道止损
    if px < lo_exit and lo_exit >= pos.stop:
        rule, why = "D-05", f"收盘跌破前 {p['exit_n']} 日最低 ${lo_exit:.2f} —— 通道止损出场。"
    elif px < pos.stop:
        rule, why = "D-04", f"收盘跌破初始止损 ${pos.stop:.2f}(进场价 − {p['stop_atr']:.0f} ATR)—— 止损出场。"
    elif px < lo_exit:
        rule, why = "D-05", f"收盘跌破前 {p['exit_n']} 日最低 ${lo_exit:.2f} —— 通道止损出场。"
    if rule is None:
        return []
    pnl = (px - pos.avg_cost) * pos.size
    state["cash"] += pos.size * px
    state["closed_pnl"].append(pnl)
    base = (f"买入价 ${ep:.2f},初始止损 ${pos.stop:.2f},今收 ${px:.2f}({(px / ep - 1) * 100:+.1f}%),"
            f"持有 {n} 个交易日,期间最高收盘 ${pos.highest:.2f}。") if want_text else ""
    fill = _fill("sell", pos, pos.size, px, rule, base + why if want_text else "",
                 pnl_abs=round(pnl, 2), pnl_pct=round((px / pos.avg_cost - 1) * 100, 2), hold_days=n)
    pos.size = 0
    state["closed"].append(pos)
    return [fill]


def try_entry(code, name, ind, state: dict, p: dict = PARAMS, want_text: bool = True, score=None):
    f = entry_flags(ind)
    if not f["ok"]:
        return None, None
    if len(state["positions"]) >= p["max_holdings"]:
        return None, f"突破成立,但已持有 {len(state['positions'])} 只,达到上限(D-03)"
    if state.get("halt_reason"):
        return None, f"突破成立,但护栏挡下:{state['halt_reason']}(D-06)"
    atr, px, equity = ind["atr20"], ind["close"], state["equity"]
    if not atr or atr <= 0:
        return None, "突破成立,但 ATR 算不出,没法定止损和仓位"
    risk = p["stop_atr"] * atr
    stop = px - risk
    if stop <= 0:
        return None, f"突破成立,但 {p['stop_atr']:.0f} ATR 止损位不大于 0(波动相对股价太大),不进"
    by_risk = int(equity * p["risk_pct"] / risk)
    by_cap = int(equity * p["max_pos_pct"] / px)
    size = min(by_risk, by_cap)
    capped = by_cap < by_risk
    if size <= 0:
        return None, "突破成立,但按仓位算法算出的股数为 0"
    cost = size * px
    cash_cut = False
    if cost > state["cash"]:
        size = int(state["cash"] / px)
        cost = size * px
        cash_cut = True
        if size <= 0:
            return None, f"突破成立,但现金只剩 ${state['cash']:.0f},买不起 1 股"
    state["cash"] -= cost
    pos = av.Position(code=code, name=name or code, size=size, initial_size=size, entry_price=px,
                      entry_date=state["date"], avg_cost=px, highest=px, level=1, bars_held=0,
                      entry_rule=ENTRY_RULE, stop=stop, risk=risk)
    state["positions"].append(pos)
    extra = {"amount": round(cost, 2), "position_pct": round(cost / equity * 100, 2)}
    if not want_text:
        return _fill("buy", pos, size, px, ENTRY_RULE, "", **extra), None
    rationale = (f"收盘 ${px:.2f} 突破前 {p['entry_n']} 日最高 ${ind['hi_entry']:.2f}(高出 {(px / ind['hi_entry'] - 1) * 100:.1f}%),"
                 f"前一天收盘 ${ind['prev_close']:.2f} 还没突破当时的前 {p['entry_n']} 日最高 ${ind['prev_hi_entry']:.2f}(D-02 新鲜)。"
                 f"ATR(20) = ${atr:.2f},初始止损 = 进场价 − {p['stop_atr']:.0f} ATR = ${stop:.2f}(距收盘 {risk / px * 100:.1f}%)。"
                 f"单笔风险 {p['risk_pct'] * 100:.0f}% 总资产 ${equity * p['risk_pct']:.0f} ÷ 每股风险 ${risk:.2f} = {by_risk} 股"
                 + (f",超过单股上限 {p['max_pos_pct'] * 100:.0f}%(${equity * p['max_pos_pct']:.0f} ÷ ${px:.2f} = {by_cap} 股),取 {by_cap} 股" if capped else "")
                 + (f";现金只够 {size} 股" if cash_cut else "")
                 + f"。买入 {size} 股,占总资产 {cost / equity * 100:.1f}%。")
    return _fill("buy", pos, size, px, ENTRY_RULE, rationale, **extra), None


def run_day(date_iso: str, positions, cash: float, bars_of, watch, prev_equity, consec_losses: int,
            p: dict = PARAMS, g: dict = av.GUARDS, ind_of=None, want_text: bool = True) -> dict:
    """接口与 agent_vcp / agent_vcp3 / agent_vcp4 相同(agent_run / agent_sim 按引擎名调用)。"""
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
                x["symbol"] == code and x["side"] == "buy" for x in fills), blocked, score, p))
    for pnl in state["closed_pnl"]:
        consec_losses = consec_losses + 1 if pnl < 0 else 0
    mv = sum(pos.size * (ind_cache.get(pos.code) or ind_of(pos.code) or {"close": pos.avg_cost})["close"]
             for pos in state["positions"])
    watch_items.sort(key=lambda x: (bool(x.get("blocked")), -(x.get("progress_pct") or 0)))
    return {"fills": fills, "positions": state["positions"], "cash": state["cash"],
            "equity": state["cash"] + mv, "watch_items": watch_items, "halt_reason": state["halt_reason"],
            "consec_losses": consec_losses, "closed": state["closed"]}
