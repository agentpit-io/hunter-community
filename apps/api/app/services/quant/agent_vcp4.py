"""小鹿智能体 · 方向 A(2026-09-13 起)「VCP · SEPA 优化」—— 用户按 Minervini SEPA 五根柱子拆解方向 C 后给的 v4 草案。

方向 C 全年 +5.1% 的体检结论(用户 2026-09-13,按 14 笔真实交易):利润 118% 来自 BKD 一笔;S 级赚 +8,024、A 级亏 -2,368 ——
五项等权让「形态 D、其他项高」的票(OMF)混进 A 级重仓;3 笔高出枢轴 0.6~0.8 ATR 的追高单净亏 -1,295;
收盘价止损的滑点约 2,380 美元(CHRW 止损位 179.80、实际收盘 167.78)。缺的是「发动机和刹车」:市场阶段、趋势模板、
基本面、加仓。这个引擎把能用日线做的都做了,做不了的写在下面。

## 规则(V-xx)

- V-01 市场过滤:标普 500 收盘 > 50 日 > 200 日,且 25 天内分布日(跌 ≥0.2% 且量比前一天大)≤ 5;否则不开新仓(持仓照常管理)。
        **没有纳指 / QQQ 日线**,只用标普;「跟随日」「修正时减半」没做。
- V-02 趋势模板:收盘 > 50 日 > 150 日 > 200 日均线,200 日线比 21 天前高,距 52 周高点 ≤ 15%,RS ≥ 80。
        **没有 EPS / 营收 / 利润率 / 机构持股 / 行业排名的数据源**,基本面加速和「行业前 3」做不了,不用别的字段顶替。
- V-03 触发:VCP 收缩 2~4 次、末次收缩**收盘口径** ≤ 10%(收盘口径算不出才用盘中口径 ≤ 15%)、低点量比 ≤ 1.1;
        收盘站上枢轴:高出 0~0.25 ATR 全仓买,0.25~0.5 ATR **半仓**,0.5~0.75 等回踩(突破后 10 天内回到 0.5 以内再买),超过 0.75 放弃。
- V-04 确认:**突破日**成交量 ≥ 1.2 × 50 日均量 **或** ≥ 1.5 × 20 日均量,且买入当天收盘在当日区间上 1/3。
- V-05 评分:五项 = 方向 C 的五项,**加权** 形态 35% / 量价配合 20% / 抗跌 15% / 走廊 20% / MACD 10%(满分 500);
        S ≥ 400 · A ≥ 350 · B ≥ 300 · C ≥ 250 · D 不买。**一票否决**:形态 < B、RS < 80、走廊 < 1.5R、止损距离 > 7%;
        走廊 1.5~2R 的**半仓**(用户:走廊 <2R 不是 VCP 波段,是短线,不给正常仓位)。

## 2026-09-13 第一轮放宽(用户拍板「直接跑这个」)

草案原版(末次 <5% 盘中口径、量比 ≤0.8、突破日量 ≥1.5× 50 日、高出 ≤0.25 ATR、走廊 ≥3R)在两个池上全年 0 笔。
第一轮放宽就是上面 V-03 / V-05 现在写的数字(第一轮的突破日量是 1.0×50 或 1.3×20)。追高半仓与走廊半仓可以叠加(两个都占就是 1/4)。
第一轮结果:11 笔开仓、10 笔完整、胜率 20%、期望 -0.44R、7 笔倒在盘中止损。

## 2026-09-13 第二轮(用户「三处硬伤」)

1. 止损:枢轴下方 0.5 ATR,按收盘执行(见 V-06)。2. 突破日量:≥1.2×50 或 ≥1.5×20(用户按 500 个突破事件的分布定的)。
3. 高出枢轴:0~0.5 都有效(0~0.25 全仓、0.25~0.5 半仓),0.5~0.75 **等回踩**不是只看,再高放弃。
- V-06 初始止损:**枢轴下方 0.5 ATR**;比 -7% 还远不进。**收盘跌破即出,按收盘价成交**(初始 / 保本 / 跟踪三种止损都按收盘)。
        第一版用「日线最低价 ≤ 止损」近似盘中止损,一年 7 笔倒在这上面、比收盘口径少赚 577 美元 —— 日线最低价是当天最极端的价,
        拿它近似等于假设每次都在最低点被扫;用户 2026-09-13 拍板改回收盘口径、止损收紧到 0.5 ATR(`stop_on_close`,留着盘中那条路做对照)。
- V-07 仓位:风险优先 —— 单笔风险 S 1.0% / A 0.75% / B 0.5% / C 0.25% 总资产,股数 = 风险金额 ÷ (买入价 − 止损);
        单票 ≤ 20%;组合开放风险 ≤ 3%;最多 5 只;同板块 ≤ 2 只(板块来自扫描源快照)。
- V-08 加仓:**只对 S 级**:首仓是计划股数的 1/2;涨到 1R 加 1/4 并把止损上移到成本;涨到 2R 再加 1/4、止损上移到 1R。
- V-09 保本:任何档位到 1R,止损上移到成本。
- V-10 跟踪止损:2R 后按 20 日均线跟踪;3R 后按 50 日均线与最高价下方 3 ATR 取高者;只上不下。
- V-11 时间止损:10 天没创新高且跌回枢轴下方 → 清仓;15 天没到 1R:仍在枢轴上方减半(一次),跌破枢轴清仓。
- V-12 高潮减仓:到过 2R 后,某天量 ≥ 2.5 × 50 日均量且收盘在当日区间下半 → 减 1/3(一次)。
- V-13 护栏:与其他方向同一套(单日 -3% 熔断、连亏 3 笔停一天)。

买入、止损都按收盘价,和其他方向一致。「1R / 2R / 3R」按持有期最高**收盘**算,与方向 C 一致。
市场状态与板块表由 agent_run 在指标缓存里以 `__market__` / `__sectors__` 两个键按日期提供(见 MARKET_KEY / SECTORS_KEY),
模拟器(agent_sim)用同一份缓存,优化器评估和实盘不会漂移。
"""
from __future__ import annotations

import math

from app.services.quant import agent_vcp as av
from app.services.quant import agent_vcp3 as c3
from app.services.quant import vcp

MARKET_KEY = "__market__"
SECTORS_KEY = "__sectors__"
MIN_BARS = 60
TREND_BARS = 221                       # 200 日均线 + 21 天前的 200 日均线
GRADE_MIN = {"S": 400, "A": 350, "B": 300, "C": 250}
WEIGHT = {"形态": 1.75, "量价配合": 1.0, "抗跌": 0.75, "盈亏比": 1.0, "MACD 金叉": 0.5}   # 35/20/15/20/10 % × 5 → 满分 500
FORM_RANK = {"S": 4, "A": 3, "B": 2, "C": 1, "D": 0}
DIST_DROP = 0.002                      # 分布日:指数跌 ≥ 0.2% 且量比前一天大
DIST_WINDOW = 25

PARAMS = {
    "atr_chase": 0.25, "atr_half": 0.5, "atr_watch": 0.75, "breakout_window": 10,     # 高出枢轴:全仓 / 半仓 / 等回踩 / 放弃 的分界
    "vol_boost": 1.2, "vol_boost20": 1.5, "close_pos": 1 / 3,                          # 突破日量 ≥ vol_boost × 50 日 或 ≥ vol_boost20 × 20 日
    "vcp_min_contr": 2, "vcp_max_contr": 4, "vcp_last_depth_max": 10.0, "vcp_last_depth_max_hl": 15.0, "low_vol_max": 1.1,
    "near_high_pct": 0.15, "rs_min": 80, "corridor_min": 1.5, "corridor_full": 2.0, "form_min": "B",   # 走廊 <1.5R 否决,1.5~2R 半仓
    "dist_days_max": 5,
    "stop_atr": 0.5, "max_stop_pct": 0.07, "stop_on_close": True,   # 止损 = 枢轴 − 0.5 ATR;按收盘执行(False = 日线最低价近似盘中,第一轮口径)
    "risk_pct": {"S": 0.010, "A": 0.0075, "B": 0.005, "C": 0.0025, "D": 0.0},
    "heat_cap": 0.03, "max_pos_pct": 0.20, "max_holdings": 5, "max_per_sector": 2,
    "initial_frac": 0.5, "add_frac": 0.25,
    "trail2_sma": 20, "trail3_sma": 50, "chandelier_atr": 3.0,
    "time_newhigh_days": 10, "time_half_days": 15,
    "climax_vol": 2.5,
    "watch_pool_days": av.PARAMS["watch_pool_days"],
}
STOP_KEYS = ("stop_atr", "max_stop_pct")     # 越小越紧;只许收紧

RULES = [
    {"id": "V-01", "kind": "risk", "condition": "市场过滤:标普 500 收盘 > 50 日 > 200 日,25 天内分布日 ≤ 5;否则不开新仓(只用标普,没有纳指数据)"},
    {"id": "V-02", "kind": "buy", "condition": "趋势模板:收盘 > 50 日 > 150 日 > 200 日,200 日线比 21 天前高,距 52 周高点 ≤ 15%,RS ≥ 80(没有基本面数据,EPS / 营收加速做不了)"},
    {"id": "V-03", "kind": "buy", "condition": "触发:VCP 收缩 2~4 次、末次收缩收盘口径 ≤10%、低点量比 ≤1.1;收盘站上枢轴高出 0~0.25 ATR 全仓,0.25~0.5 半仓,0.5~0.75 等回踩,>0.75 放弃;突破后 10 天内有效"},
    {"id": "V-04", "kind": "buy", "condition": "确认:突破日成交量 ≥ 1.2 × 50 日均量 或 ≥ 1.5 × 20 日均量,买入当天收盘在当日区间上 1/3"},
    {"id": "V-05", "kind": "buy", "condition": "评分(加权 500):形态 35% / 量价 20% / 抗跌 15% / 走廊 20% / MACD 10%;S ≥400 · A ≥350 · B ≥300 · C ≥250 · D 不买;否决:形态 <B、RS <80、走廊 <1.5R、止损距离 >7%;走廊 1.5~2R 半仓"},
    {"id": "V-06", "kind": "sell", "condition": "初始止损:枢轴下方 0.5 ATR,比 -7% 远不进;收盘跌破即出,按收盘价成交"},
    {"id": "V-07", "kind": "risk", "condition": "仓位:单笔风险 S 1.0% / A 0.75% / B 0.5% / C 0.25%,股数 = 风险 ÷ (买入价 − 止损);单票 ≤20%、总风险 ≤3%、最多 5 只、同板块 ≤2"},
    {"id": "V-08", "kind": "buy", "condition": "加仓(只对 S 级):首仓 1/2,涨到 1R 加 1/4 并保本,涨到 2R 再加 1/4、止损上移到 1R"},
    {"id": "V-09", "kind": "sell", "condition": "保本:到 1R 止损上移到成本"},
    {"id": "V-10", "kind": "sell", "condition": "跟踪止损:2R 后按 20 日均线,3R 后按 50 日均线与最高价下方 3 ATR 取高者;只上不下"},
    {"id": "V-11", "kind": "sell", "condition": "时间止损:10 天没创新高且跌回枢轴下方清仓;15 天没到 1R:枢轴上方减半,跌破枢轴清仓"},
    {"id": "V-12", "kind": "sell", "condition": "高潮减仓:到过 2R 后,量 ≥ 2.5 × 50 日均量且收盘在当日区间下半,减 1/3"},
    {"id": "V-13", "kind": "risk", "condition": "护栏:单日权益回撤达 -3% 当天停止开仓;连亏 3 笔后下一个交易日不开仓"},
]
RULE_NAME = {"V-03": "VCP 枢轴突破买入", "V-08": "加仓", "V-06": "初始止损", "V-09": "保本止损", "V-10": "跟踪止损",
             "V-11": "时间止损", "V-12": "高潮减仓"}
RULE_PARAM_KEY = {"V-03": "atr_chase", "V-04": "vol_boost", "V-06": "stop_atr", "V-10": "trail2_sma", "V-11": "time_newhigh_days"}
ENTRY_RULE = "V-03"
ADD_RULE = "V-08"
GRADE_RULE = "V-05"
EXEC_NOTE = "纸上交易 · 日线收盘价成交(买入与止损都按收盘)"
# 股票池:用户 v4 草案的「全市场 · RS 80+ · 价格 > 50 > 150 > 200 · 距 52 周高点 ≤15%」,再加 VCP 收缩 2~4 次(V-03 反正要求,
# 放进池子只是让池子小一点)。EPS / 营收加速、行业前 3 没有数据源。「VCP 波段收缩」那个池(区间写法)一年只有 73/250 个突破
# 事件是 2 次以上收缩、突破日量 ≥1.5× 的只有 29 个,按 v4 的门槛全年 0 笔 —— 所以方向 A 不再共用那个池。
POOL = "sepa"
POOL_LABEL = "SEPA 趋势模板池(RS ≥80 · 均线多头 · 距 52 周高点 ≤15% · VCP 收缩 2~4 次)"
POOL_SCRIPT = """# ===== SEPA 趋势模板池(小鹿方向 A)=====
def c_price = close > 10;
def c_liq   = average_volume_30d_calc > 500000;
def c_trend = close > SMA50 and SMA50 > SMA150 and SMA150 > SMA200;
def c_near  = close >= price_52_week_high * 0.85;
def c_rs    = rs_rating >= 80;
def c_vcp   = vcp_contractions >= 2 and vcp_contractions <= 4;
plot scan = c_price and c_liq and c_trend and c_near and c_rs and c_vcp;
"""


def rules_for(p: dict = PARAMS) -> list[dict]:
    out = []
    for r in RULES:
        c = dict(r)
        rid = r["id"]
        if rid == "V-03":
            c["condition"] = (f"触发:VCP 收缩 {p['vcp_min_contr']}~{p['vcp_max_contr']} 次、末次收缩收盘口径 ≤{p['vcp_last_depth_max']:.0f}%、"
                              f"低点量比 ≤{p['low_vol_max']:.1f};收盘站上枢轴高出 0~{p['atr_chase']:.2f} ATR 全仓,"
                              f"{p['atr_chase']:.2f}~{p['atr_half']:.2f} 半仓,{p['atr_half']:.2f}~{p['atr_watch']:.2f} 等回踩,>{p['atr_watch']:.2f} 放弃;"
                              f"突破后 {p['breakout_window']} 天内有效")
        elif rid == "V-04":
            c["condition"] = (f"确认:突破日成交量 ≥ {p['vol_boost']:.1f} × 50 日均量 或 ≥ {p['vol_boost20']:.1f} × 20 日均量,"
                              f"买入当天收盘在当日区间上 {p['close_pos'] * 100:.0f}%")
        elif rid == "V-05":
            c["condition"] = (f"评分(加权 500):形态 35% / 量价 20% / 抗跌 15% / 走廊 20% / MACD 10%;S ≥400 · A ≥350 · B ≥300 · C ≥250 · D 不买;"
                              f"否决:形态 <{p['form_min']}、RS <{p['rs_min']}、走廊 <{p['corridor_min']:.1f}R、止损距离 >{p['max_stop_pct'] * 100:.0f}%;"
                              f"走廊 {p['corridor_min']:.1f}~{p['corridor_full']:.0f}R 半仓")
        elif rid == "V-06":
            c["condition"] = (f"初始止损:枢轴下方 {p['stop_atr']:.2f} ATR,比 -{p['max_stop_pct'] * 100:.0f}% 远不进;"
                              + ("收盘跌破即出,按收盘价成交" if p.get("stop_on_close", True) else "盘中触及即出(日线最低价近似,跳空按收盘)"))
        elif rid == "V-10":
            c["condition"] = (f"跟踪止损:2R 后按 {p['trail2_sma']} 日均线,3R 后按 {p['trail3_sma']} 日均线与最高价下方 "
                              f"{p['chandelier_atr']:.0f} ATR 取高者;只上不下")
        elif rid == "V-11":
            c["condition"] = (f"时间止损:{p['time_newhigh_days']} 天没创新高且跌回枢轴下方清仓;"
                              f"{p['time_half_days']} 天没到 1R:枢轴上方减半,跌破枢轴清仓")
        out.append(c)
    return out


def summary(p: dict = PARAMS) -> str:
    rp = p["risk_pct"]
    return (f"先看市场(标普在 50 日 > 200 日之上、分布日 ≤{p['dist_days_max']})和趋势模板(均线多头、距 52 周高点 ≤{p['near_high_pct'] * 100:.0f}%、"
            f"RS ≥{p['rs_min']}),再等 VCP 突破:高出枢轴 ≤{p['atr_chase']:.2f} ATR 全仓、≤{p['atr_half']:.2f} 半仓,"
            f"突破日量 ≥{p['vol_boost']:.1f} 倍 50 日均量或 ≥{p['vol_boost20']:.1f} 倍 20 日均量;"
            f"加权评分(形态 35%)定档,形态 <B / 走廊 <{p['corridor_min']:.1f}R / 止损 >{p['max_stop_pct'] * 100:.0f}% 一票否决,走廊 <{p['corridor_full']:.0f}R 半仓;"
            f"单笔风险 S {rp['S'] * 100:.1f}% / A {rp['A'] * 100:.2f}% / B {rp['B'] * 100:.1f}% / C {rp['C'] * 100:.2f}%,总风险 ≤{p['heat_cap'] * 100:.0f}%;"
            f"S 级 1R / 2R 各加 1/4;止损挂枢轴下方 {p['stop_atr']:.1f} ATR、收盘跌破即出,1R 保本,2R 后跟 {p['trail2_sma']} 日线,3R 后跟 {p['trail3_sma']} 日线;"
            f"{p['time_newhigh_days']} 天不创新高又跌回枢轴就走。")


# ═══════════════════════════════════════════════════════════════
# 市场过滤
# ═══════════════════════════════════════════════════════════════

def market_regime(bars: list[tuple], p: dict = PARAMS) -> dict:
    """基准日线 [(日期, 收, 高, 低, 量)] 截到当天 → {ok, text, above, dist_days, close, sma50, sma200}。"""
    if len(bars) < 200:
        return {"ok": False, "above": None, "dist_days": None,
                "text": f"基准日线只有 {len(bars)} 根,不足 200,市场过滤算不出,不开新仓"}
    c = [b[1] for b in bars]
    v = [b[4] or 0 for b in bars]
    s50, s200 = av._sma(c, 50), av._sma(c, 200)
    dist = 0
    for i in range(len(bars) - DIST_WINDOW, len(bars)):
        if c[i] < c[i - 1] * (1 - DIST_DROP) and v[i] > v[i - 1]:
            dist += 1
    above = c[-1] > s50 > s200
    ok = above and dist <= p["dist_days_max"]
    base = f"标普 {c[-1]:.0f} · 50 日 {s50:.0f} · 200 日 {s200:.0f} · {DIST_WINDOW} 天内分布日 {dist}"
    tail = " —— 顺风" if ok else (" —— 不在 50 日 > 200 日之上,逆风" if not above else f" —— 分布日超过 {p['dist_days_max']},逆风")
    return {"ok": ok, "above": above, "dist_days": dist, "close": c[-1], "sma50": s50, "sma200": s200, "text": base + tail}


# ═══════════════════════════════════════════════════════════════
# 指标
# ═══════════════════════════════════════════════════════════════

def indicators(bars: list[tuple], p: dict = PARAMS, bench: dict | None = None) -> dict | None:
    if len(bars) < MIN_BARS:
        return None
    c = [b[1] for b in bars]
    h = [b[2] for b in bars]
    lo = [b[3] for b in bars]
    v = [b[4] for b in bars]
    if any(x is None for x in h[-60:] + lo[-60:] + v[-60:]):
        return None
    n = len(c)
    vs = vcp.vcp_stats(bars) or {}
    atr = av._atr(bars[-61:], 20)
    vs20 = av._sma(v, 20)
    vs50 = av._sma(v, 50) if n >= 50 and all(x is not None for x in v[-50:]) else None
    trend = None
    if n >= TREND_BARS:
        s50, s150, s200 = av._sma(c, 50), av._sma(c, 150), av._sma(c, 200)
        s200_prev = av._sma(c[:-21], 200)
        hi252 = max(x for x in h[-252:] if x is not None)
        trend = {"sma50": s50, "sma150": s150, "sma200": s200, "sma200_prev": s200_prev, "hi252": hi252,
                 "near_high": 1 - c[-1] / hi252, "stack": c[-1] > s50 > s150 > s200, "rising200": s200 > s200_prev}
    pivot = vs.get("pivot")
    brk = None
    if pivot is not None and c[-1] > pivot:
        w = int(p["breakout_window"])
        j = None
        for i in range(n - 1, max(n - w - 2, -1), -1):
            if c[i] <= pivot:
                j = i
                break
        if j is not None and j + 1 <= n - 1:
            b = j + 1
            seg = v[b - 49:b + 1] if b >= 49 else []
            bv50 = (sum(seg) / 50) if seg and all(x is not None for x in seg) else None
            seg20 = v[b - 19:b + 1] if b >= 19 else []
            bv20 = (sum(seg20) / 20) if seg20 and all(x is not None for x in seg20) else None
            brk = {"days_ago": n - 1 - b, "vol_ratio": (v[b] / bv50) if bv50 else None, "vol_ratio20": (v[b] / bv20) if bv20 else None,
                   "low": lo[b], "date": bars[b][0]}
    rng = h[-1] - lo[-1]
    return {
        "close": c[-1], "high": h[-1], "low": lo[-1], "volume": v[-1], "atr20": atr, "vol_sma20": vs20, "vol_sma50": vs50,
        "pivot": pivot, "base_low": vs.get("last_low"), "contractions": vs.get("contractions"),
        "last_depth": vs.get("last_depth"), "last_depth_close": vs.get("last_depth_close"),
        "low_vol_ratio": vs.get("low_vol_ratio"), "first_depth": vs.get("first_depth"),
        "recent_closes": c[-c3._MAX_CONFIRM - 1:], "recent_vols": v[-c3._MAX_CONFIRM:],     # 方向 C 的形态评分要用
        "trend": trend, "breakout": brk,
        "close_pos": ((c[-1] - lo[-1]) / rng) if rng > 0 else 1.0,
        "vr_today": (v[-1] / vs50) if vs50 else None,
        "sma20": av._sma(c, 20), "sma50": av._sma(c, 50) if n >= 50 else None,
        "vp_net_63": c3._vp_net(c, v) if all(x is not None for x in v[-(c3.LOOKBACK + c3.VOL_SMA):]) else None,
        "defense_63": c3._defense(bars, bench),
        "macd_d": c3._macd_cross(c, c3.MACD_DAILY_WITHIN),
        "macd_w": c3._macd_cross(c3._weekly_closes(bars), c3.MACD_WEEKLY_WITHIN),
        "res_above": c3.res_above(bars, c[-1], atr) if atr else None,
    }


# ═══════════════════════════════════════════════════════════════
# 买入过滤
# ═══════════════════════════════════════════════════════════════

def entry_flags(ind: dict, p: dict = PARAMS) -> dict:
    tr = ind.get("trend")
    trend_ok = bool(tr and tr["stack"] and tr["rising200"] and tr["near_high"] <= p["near_high_pct"])
    ph, atr, px = ind.get("pivot"), ind.get("atr20"), ind["close"]
    above = ph is not None and px > ph
    dist = ((px - ph) / atr) if (above and atr) else None
    n, lv = ind.get("contractions") or 0, ind.get("low_vol_ratio")
    ldc, ldh = ind.get("last_depth_close"), ind.get("last_depth")
    # 末次收缩优先按收盘口径;收盘口径算不出才用盘中口径(阈值放到 vcp_last_depth_max_hl)
    depth_ok = (ldc <= p["vcp_last_depth_max"]) if ldc is not None else (ldh is not None and ldh <= p["vcp_last_depth_max_hl"])
    vcp_ok = p["vcp_min_contr"] <= n <= p["vcp_max_contr"] and depth_ok and lv is not None and lv <= p["low_vol_max"]
    chase_ok = dist is not None and dist <= p["atr_chase"]                       # 全仓
    half = dist is not None and p["atr_chase"] < dist <= p["atr_half"]           # 半仓
    watch = dist is not None and p["atr_half"] < dist <= p["atr_watch"]          # 等回踩(突破后 10 天内回到 atr_half 以内再买)
    brk = ind.get("breakout")
    vr = brk["vol_ratio"] if brk else None
    vr20 = brk.get("vol_ratio20") if brk else None
    vol_ok = (vr is not None and vr >= p["vol_boost"]) or (vr20 is not None and vr20 >= p["vol_boost20"])
    pos_ok = ind.get("close_pos") is not None and ind["close_pos"] >= 1 - p["close_pos"]
    return {"V-02": trend_ok, "V-03": bool(vcp_ok and (chase_ok or half) and brk), "V-04": bool(brk and vol_ok and pos_ok),
            "above": above, "dist_atr": dist, "vcp_ok": vcp_ok, "half": half, "watch": watch, "vr": vr, "vr20": vr20,
            "vol_ok": vol_ok, "pos_ok": pos_ok, "depth_close": ldc}


def entry_ok(ind: dict, p: dict = PARAMS) -> bool:
    f = entry_flags(ind, p)
    return f["V-02"] and f["V-03"] and f["V-04"]


def stop_of(ind: dict, p: dict = PARAMS) -> tuple[float | None, bool]:
    """→ (止损位, 是否比 -max_stop_pct 还远)。枢轴下方 stop_atr 个 ATR(第二轮起不再与突破日低点取高:那会把止损推到更近的噪声里)。"""
    ph, atr, px = ind.get("pivot"), ind.get("atr20"), ind["close"]
    if ph is None or not atr:
        return None, False
    stop = ph - p["stop_atr"] * atr
    return stop, stop < px * (1 - p["max_stop_pct"])


def corridor(ind: dict, p: dict = PARAMS) -> tuple[float | None, str]:
    """走廊 = (252 日强阻力 − 收盘)÷ R;上方无阻力 → inf。"""
    stop, _ = stop_of(ind, p)
    px = ind["close"]
    if stop is None or stop >= px:
        return None, "止损算不出"
    r1 = px - stop
    ra = ind.get("res_above")
    if not ra:
        return math.inf, f"R = ${r1:.2f};上方 252 根内无阻力(一年新高之上),走廊无上限"
    lvl, src = ra
    corr = max(lvl - px, 0.0) / r1
    return corr, f"R = ${r1:.2f};到 252 日强阻力 ${lvl:.2f}({src})的走廊 {corr:.1f}R"


def grade(ind: dict, p: dict = PARAMS, score=None) -> dict:
    """加权 500 分 + 一票否决 → {grade, points, factors:[(项, 档, 分, 说明)], form_points, vetoes:[...], text}。"""
    fg, fpts, ff = c3.form_grade(ind, c3.PARAMS, score)          # 形态用方向 C 的口径(用户:「按你当前的逻辑」)
    items = [("形态", fg, f"{fpts}/10:" + "、".join(f"{a} {b}({c})" for a, b, c in ff))]
    vp = ind.get("vp_net_63")
    items.append(("量价配合", c3._tier(vp, c3.VP_MIN), f"近 {c3.LOOKBACK} 天净 {vp:+d} 次" if vp is not None else "日线不足,算不出"))
    df = ind.get("defense_63")
    items.append(("抗跌", c3._tier(df[0], c3.DEF_MIN) if df else None,
                  f"标普下跌 {df[1]} 天里 {df[0]} 天不跌" if df else "没有基准日线,算不出"))
    corr, corr_txt = corridor(ind, p)
    items.append(("盈亏比", c3._tier(corr, c3.RR_MIN), (f"走廊 {corr:.1f}R" if corr != math.inf else "走廊无上限") + f"({corr_txt})"
                  if corr is not None else corr_txt))
    md, mw = bool(ind.get("macd_d")), bool(ind.get("macd_w"))
    g5 = "S" if md and mw else "A" if mw else "B" if md else "C"
    items.append(("MACD 金叉", g5, "日线 + 周线" if md and mw else "只有周线" if mw else "只有日线" if md else "没有金叉"))
    factors = [(name, t or "D", round(c3.SUB_POINTS[t or "D"] * WEIGHT[name]), txt) for name, t, txt in items]
    total = sum(x[2] for x in factors)
    g = c3._tier(total, GRADE_MIN)
    vetoes = []
    if FORM_RANK[fg] < FORM_RANK[p["form_min"]]:
        vetoes.append(f"形态 {fg} 级低于 {p['form_min']}")
    if score is None or score < p["rs_min"]:
        vetoes.append(f"RS {score:.0f} 低于 {p['rs_min']}" if score is not None else "RS 缺")
    if corr is not None and corr < p["corridor_min"]:
        vetoes.append(f"走廊 {corr:.1f}R 不足 {p['corridor_min']:.1f}R")
    corr_half = corr is not None and p["corridor_min"] <= corr < p["corridor_full"]     # 1.5~2R:短线,半仓
    stop, capped = stop_of(ind, p)
    if capped:
        vetoes.append(f"止损距离 {(1 - stop / ind['close']) * 100:.1f}% 超过 {p['max_stop_pct'] * 100:.0f}%")
    return {"grade": g, "points": total, "factors": factors, "form_points": fpts, "vetoes": vetoes, "corridor_half": corr_half,
            "text": f"{g} 级({total}/500 加权):" + "、".join(f"{a} {b}{c}({d})" for a, b, c, d in factors)
                    + (";否决:" + "、".join(vetoes) if vetoes else "") + (f";走廊不足 {p['corridor_full']:.0f}R 半仓" if corr_half else "")}


def entry_checks(ind: dict, p: dict = PARAMS) -> list[dict]:
    f = entry_flags(ind, p)
    tr = ind.get("trend")
    if tr is None:
        t2 = "日线不足 221 根,趋势模板算不出"
    else:
        bad = []
        if not tr["stack"]:
            bad.append("均线没排成 收盘 > 50 > 150 > 200")
        if not tr["rising200"]:
            bad.append("200 日线没在上行")
        if tr["near_high"] > p["near_high_pct"]:
            bad.append(f"距 52 周高点 {tr['near_high'] * 100:.0f}% > {p['near_high_pct'] * 100:.0f}%")
        t2 = "趋势模板通过" if not bad else ";".join(bad)
    ph, px = ind.get("pivot"), ind["close"]
    n, ld, lv = ind.get("contractions") or 0, ind.get("last_depth"), ind.get("low_vol_ratio")
    if ph is None:
        t3 = "筛选器没算出枢轴"
    elif not f["vcp_ok"]:
        ldc = f.get("depth_close")
        t3 = (f"形态不合格:收缩 {n} 次" + (f",末次收盘口径 {ldc:.1f}%" if ldc is not None else (f",末次盘中口径 {ld:.1f}%" if ld is not None else ""))
              + (f",低点量比 {lv:.2f}" if lv is not None else "")
              + f"(要 {p['vcp_min_contr']}~{p['vcp_max_contr']} 次、末次 ≤{p['vcp_last_depth_max']:.0f}%、量比 ≤{p['low_vol_max']:.1f})")
    elif not f["above"]:
        t3 = f"收盘 ${px:.2f} 还在枢轴 ${ph:.2f} 下方 {(1 - px / ph) * 100:.1f}%"
    elif f["watch"]:
        t3 = f"已高出枢轴 {f['dist_atr']:.2f} ATR({p['atr_half']:.2f}~{p['atr_watch']:.2f}),等回踩到 {p['atr_half']:.2f} ATR 以内再买"
    elif f["dist_atr"] is not None and f["dist_atr"] > p["atr_watch"]:
        t3 = f"已高出枢轴 {f['dist_atr']:.2f} ATR,超过 {p['atr_watch']:.2f} 放弃"
    elif not ind.get("breakout"):
        t3 = f"突破已超过 {p['breakout_window']} 天,不新鲜"
    else:
        t3 = (f"收盘 ${px:.2f} 站上枢轴 ${ph:.2f}(高出 {f['dist_atr']:.2f} ATR{',半仓' if f['half'] else ''}),"
              f"突破日 {str(ind['breakout']['date'])[5:]}")
    brk = ind.get("breakout")
    if not brk:
        t4 = "没有新鲜突破"
    else:
        t4 = ((f"突破日量 {brk['vol_ratio']:.2f}× 50 日均量" if brk["vol_ratio"] is not None else "50 日均量算不出")
              + (f" / {brk['vol_ratio20']:.2f}× 20 日均量" if brk.get("vol_ratio20") is not None else "")
              + ("" if f["vol_ok"] else f" —— 要 ≥{p['vol_boost']:.1f}×50 日或 ≥{p['vol_boost20']:.1f}×20 日")
              + (f";收盘在当日区间 {ind['close_pos'] * 100:.0f}% 处" + ("" if f["pos_ok"] else f",要在上 {p['close_pos'] * 100:.0f}% 内")))
    return [{"rule": "V-02", "ok": f["V-02"], "text": t2}, {"rule": "V-03", "ok": f["V-03"], "text": t3},
            {"rule": "V-04", "ok": f["V-04"], "text": t4}]


def watch_item(code, name, ind, held, blocked_reason, score=None) -> dict:
    it = {"symbol": code, "name": name, "score": score, "rule_id": ENTRY_RULE, "rule_text": RULES[2]["condition"]}
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
        it["gap"] = "三条全满足 —— 今日收盘触发买入"
    else:
        it["gap"] = f"{passed}/3 满足 · 还差:" + ";".join(f"{c['rule']} {c['text']}" for c in fails)
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


def _sell(pos: av.Position, qty: int, price: float, rule: str, why: str, state: dict, fills: list, n: int, want_text: bool, base: str):
    qty = max(1, min(int(qty), pos.size))
    pnl = (price - pos.avg_cost) * qty
    state["cash"] += qty * price
    if qty >= pos.size:
        state["closed_pnl"].append(pnl)
    fills.append(_fill("sell", pos, qty, price, rule, (base + why) if want_text else "",
                       pnl_abs=round(pnl, 2), pnl_pct=round((price / pos.avg_cost - 1) * 100, 2), hold_days=n))
    pos.size -= qty


def manage_position(pos: av.Position, ind: dict, state: dict, p: dict = PARAMS, want_text: bool = True) -> list[dict]:
    px, hi, lo_d = ind["close"], ind["high"], ind["low"]
    pos.bars_held += 1
    n = pos.bars_held
    ep, r1 = pos.entry_price, pos.risk
    ex = pos.extra
    pivot = ex.get("pivot")
    base = (f"买入价 ${ep:.2f},止损 ${pos.stop:.2f}(1R = ${r1:.2f}),今收 ${px:.2f}({(px / ep - 1) * 100:+.1f}%),"
            f"持有 {n} 个交易日。") if want_text else ""
    fills: list = []
    # 1. 止损:默认按收盘(收盘 < 止损 → 按收盘价出);stop_on_close=False 时按日线最低价近似盘中(第一轮口径,留作对照)
    on_close = p.get("stop_on_close", True)
    hit = (px < pos.stop) if on_close else (lo_d <= pos.stop)
    if hit:
        fill_px = px if on_close else (pos.stop if hi >= pos.stop else px)
        rule = "V-06" if pos.stop < ep - 1e-9 else ("V-09" if abs(pos.stop - ep) <= 1e-9 else "V-10")
        why = ((f"今收 ${px:.2f} 跌破止损 ${pos.stop:.2f},按收盘出 —— " if on_close else
                f"盘中最低 ${lo_d:.2f} 触及止损 ${pos.stop:.2f},按{'止损价' if fill_px == pos.stop else '收盘(跳空)'} ${fill_px:.2f} 出 —— ")
               + {"V-06": "初始止损", "V-09": "保本止损", "V-10": "跟踪止损"}[rule] + "。")
        _sell(pos, pos.size, fill_px, rule, why, state, fills, n, want_text, base)
        pos.highest = max(pos.highest, px)
        state["closed"].append(pos)
        return fills
    high_now = max(pos.highest, px)
    r_mult = (high_now - ep) / r1 if r1 else 0.0
    # 2. 时间止损
    if n >= p["time_newhigh_days"] and high_now <= ep + 1e-9 and pivot is not None and px < pivot:
        _sell(pos, pos.size, px, "V-11", f"持有 {n} 天没创新高,今收跌回枢轴 ${pivot:.2f} 下方 —— 清仓。", state, fills, n, want_text, base)
    elif n >= p["time_half_days"] and r_mult < 1.0 and not ex.get("half_done"):
        if pivot is not None and px < pivot:
            _sell(pos, pos.size, px, "V-11", f"持有 {n} 天没到 1R 且跌破枢轴 ${pivot:.2f} —— 清仓。", state, fills, n, want_text, base)
        else:
            _sell(pos, pos.size // 2, px, "V-11", f"持有 {n} 天没到 1R,仍在枢轴上方 —— 减半,余下继续拿。", state, fills, n, want_text, base)
            ex["half_done"] = True
    # 3. 高潮减仓
    elif (r_mult >= 2.0 and not ex.get("climax_done") and ind.get("vr_today") is not None
          and ind["vr_today"] >= p["climax_vol"] and ind.get("close_pos", 1.0) < 0.5 and pos.size >= 3):
        _sell(pos, pos.size // 3, px, "V-12", f"到过 2R 后放量 {ind['vr_today']:.1f}× 50 日均量却收在当日区间下半 —— 减 1/3。", state, fills, n, want_text, base)
        ex["climax_done"] = True
    if pos.size <= 0:
        pos.highest = high_now
        state["closed"].append(pos)
        return fills
    # 4. 加仓(只对 S 级):1R → level 2 加 1/4;2R → level 3 加 1/4;股数按计划(initial_size)算
    if ex.get("grade") == "S" and pos.level < 3:
        want_level = 3 if r_mult >= 2.0 else 2 if r_mult >= 1.0 else pos.level
        if want_level > pos.level:
            add = int(pos.initial_size * p["add_frac"])
            room = state["equity"] * p["heat_cap"] - state.get("open_risk", 0.0)
            risk_now = max(px - max(pos.stop, ep), 0.0)          # 加完仓止损至少在成本,新增风险按这个算
            if add > 0 and add * px <= state["cash"] and (risk_now <= 0 or add * risk_now <= room):
                state["cash"] -= add * px
                pos.avg_cost = (pos.avg_cost * pos.size + add * px) / (pos.size + add)
                pos.size += add
                pos.level = want_level
                state["open_risk"] = state.get("open_risk", 0.0) + add * risk_now
                fills.append(_fill("buy", pos, add, px, "V-08",
                                   (base + f"到 {want_level - 1}R,S 级加 1/4({add} 股)。") if want_text else "",
                                   amount=round(add * px, 2), position_pct=round(add * px / state["equity"] * 100, 2)))
    # 5. 止损上移:1R 保本 → 2R 跟 20 日线 → 3R 跟 50 日线 / 吊灯,只上不下
    new_stop = pos.stop
    if r_mult >= 1.0:
        new_stop = max(new_stop, ep)
    if r_mult >= 2.0:
        new_stop = max(new_stop, ep + r1)
        if ind.get("sma20") is not None:
            new_stop = max(new_stop, ind["sma20"])
    if r_mult >= 3.0:
        cands = [x for x in (ind.get("sma50"), (high_now - p["chandelier_atr"] * ind["atr20"]) if ind.get("atr20") else None) if x is not None]
        if cands:
            new_stop = max(new_stop, max(cands))
    if new_stop >= px:
        # 跟踪线已经在今收之上 = 今天收盘跌破了跟踪线,按收盘出,不等明天
        _sell(pos, pos.size, px, "V-10", f"今收 ${px:.2f} 跌破跟踪线 ${new_stop:.2f}(到过 {r_mult:.1f}R)—— 跟踪止损出场。", state, fills, n, want_text, base)
        pos.highest = high_now
        state["closed"].append(pos)
        return fills
    pos.stop = new_stop
    pos.highest = high_now
    return fills


def try_entry(code, name, ind, state: dict, p: dict = PARAMS, want_text: bool = True, score=None, sector=None):
    if not entry_ok(ind, p):
        return None, None
    mk = state.get("market")
    if not mk or not mk.get("ok"):
        return None, f"三条全满足,但市场逆风:{mk['text'] if mk else '没有基准日线'}(V-01)"
    if state.get("halt_reason"):
        return None, f"三条全满足,但护栏挡下:{state['halt_reason']}(V-13)"
    if len(state["positions"]) >= p["max_holdings"]:
        return None, f"三条全满足,但已持有 {len(state['positions'])} 只,达到上限 {p['max_holdings']}(V-07)"
    if sector:
        same = sum(1 for x in state["positions"] if (x.extra or {}).get("sector") == sector)
        if same >= p["max_per_sector"]:
            return None, f"三条全满足,但「{sector}」板块已持有 {same} 只,同板块上限 {p['max_per_sector']}(V-07)"
    equity, px = state["equity"], ind["close"]
    stop, capped = stop_of(ind, p)
    if stop is None or stop >= px:
        return None, "三条全满足,但止损位不低于收盘,形态不成立"
    gr = grade(ind, p, score)
    if gr["vetoes"]:
        return None, f"三条全满足,但一票否决:{'、'.join(gr['vetoes'])}(V-05)"
    risk_pct = p["risk_pct"].get(gr["grade"], 0.0)
    if risk_pct <= 0:
        return None, f"三条全满足,但评分 {gr['text']} —— D 级不买(V-05)"
    r1 = px - stop
    full = min(int(equity * risk_pct / r1), int(equity * p["max_pos_pct"] / px))
    room = equity * p["heat_cap"] - state.get("open_risk", 0.0)
    if full * r1 > room:
        full = int(room / r1) if room > 0 else 0
        if full <= 0:
            return None, (f"三条全满足({gr['grade']} 级),但组合开放风险已达 {state.get('open_risk', 0.0) / equity * 100:.1f}%,"
                          f"上限 {p['heat_cap'] * 100:.0f}%,放不下(V-07)")
    f_ = entry_flags(ind, p)
    halves = []
    if f_["half"]:
        halves.append(f"高出枢轴 {f_['dist_atr']:.2f} ATR 追高半仓")
    if gr.get("corridor_half"):
        halves.append(f"走廊不足 {p['corridor_full']:.0f}R 半仓")
    for _ in halves:
        full = full // 2
    size = int(full * p["initial_frac"]) if gr["grade"] == "S" else full
    if size <= 0:
        return None, "三条全满足,但按风险算出的股数为 0" + (f"({'、'.join(halves)})" if halves else "")
    cost = size * px
    if cost > state["cash"]:
        size = int(state["cash"] / px)
        cost = size * px
        if size <= 0:
            return None, f"三条全满足,但现金只剩 ${state['cash']:.0f},买不起 1 股"
    state["cash"] -= cost
    state["open_risk"] = state.get("open_risk", 0.0) + size * r1
    pos = av.Position(code=code, name=name or code, size=size, initial_size=full, entry_price=px,
                      entry_date=state["date"], avg_cost=px, highest=px, level=1, bars_held=0,
                      entry_rule=ENTRY_RULE, stop=stop, risk=r1,
                      extra={"pivot": ind["pivot"], "grade": gr["grade"], "sector": sector, "plan": full})
    state["positions"].append(pos)
    extra = {"amount": round(cost, 2), "position_pct": round(cost / equity * 100, 2), "grade": gr["grade"], "points": gr["points"],
             "grade_detail": gr["text"]}
    if not want_text:
        return _fill("buy", pos, size, px, ENTRY_RULE, "", **extra), None
    t = {c["rule"]: c["text"] for c in entry_checks(ind, p)}
    rationale = (f"{t['V-03']};{t['V-04']};{t['V-02']}。市场:{mk['text']}。评分 {gr['text']} → 单笔风险 {risk_pct * 100:.2f}% 总资产;"
                 f"止损 ${stop:.2f}(距收盘 {(1 - stop / px) * 100:.1f}%,{'收盘跌破即出' if p.get('stop_on_close', True) else '盘中触及即出'});计划 {full} 股"
                 + (f"({'、'.join(halves)})" if halves else "")
                 + (f",S 级首仓一半 {size} 股" if gr["grade"] == "S" else f",买入 {size} 股")
                 + f",占总资产 {cost / equity * 100:.1f}%,这一笔开放风险 ${size * r1:.0f}"
                 f"(组合合计 {state['open_risk'] / equity * 100:.1f}%,上限 {p['heat_cap'] * 100:.0f}%)。")
    return _fill("buy", pos, size, px, ENTRY_RULE, rationale, **extra), None


def run_day(date_iso: str, positions, cash: float, bars_of, watch, prev_equity, consec_losses: int,
            p: dict = PARAMS, g: dict = av.GUARDS, ind_of=None, want_text: bool = True) -> dict:
    """接口与 agent_vcp / agent_vcp3 相同。市场状态与板块表从 ind_of(MARKET_KEY) / ind_of(SECTORS_KEY) 取。"""
    state = {"date": date_iso, "cash": cash, "positions": list(positions), "closed": [], "closed_pnl": [],
             "equity": None, "halt_reason": None, "market": None}
    if ind_of is None:
        def ind_of(code):
            if code in (MARKET_KEY, SECTORS_KEY):
                return None
            return indicators(bars_of(code) or [], p)
    state["market"] = ind_of(MARKET_KEY)
    sectors = ind_of(SECTORS_KEY) or {}
    ind_cache = {pos.code: ind_of(pos.code) for pos in state["positions"]}
    mv = sum(pos.size * (ind_cache[pos.code]["close"] if ind_cache[pos.code] else pos.avg_cost) for pos in state["positions"])
    equity = cash + mv
    state["equity"] = equity
    state["open_risk"] = sum(pos.size * max((ind_cache[pos.code]["close"] if ind_cache[pos.code] else pos.avg_cost) - pos.stop, 0.0)
                             for pos in state["positions"])
    if prev_equity and (equity / prev_equity - 1) * 100 <= g["daily_loss_halt_pct"]:
        state["halt_reason"] = f"今日权益 {(equity / prev_equity - 1) * 100:+.1f}%,触及单日亏损熔断 {g['daily_loss_halt_pct']:.0f}%,今天不开新仓"
    elif consec_losses >= g["consecutive_loss_pause"]:
        state["halt_reason"] = f"此前连亏 {consec_losses} 笔,按护栏今天不开新仓"
        consec_losses = 0
    fills: list = []
    for pos in list(state["positions"]):
        if pos.extra is None:
            pos.extra = {}
        ind = ind_cache[pos.code]
        if ind is None:
            pos.bars_held += 1
            continue
        fills += manage_position(pos, ind, state, p, want_text)
    state["positions"] = [x for x in state["positions"] if x.size > 0]
    state["open_risk"] = sum(pos.size * max((ind_cache[pos.code]["close"] if ind_cache.get(pos.code) else pos.avg_cost) - pos.stop, 0.0)
                             for pos in state["positions"])
    held = {x.code for x in state["positions"]}
    watch_items = []
    for code, name, score in watch:
        ind = ind_cache[code] if code in ind_cache else ind_of(code)
        ind_cache[code] = ind
        blocked = None
        if code not in held and ind is not None:
            f, blocked = try_entry(code, name, ind, state, p, want_text, score, sectors.get(code))
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
            "consec_losses": consec_losses, "closed": state["closed"], "market": state["market"]}
