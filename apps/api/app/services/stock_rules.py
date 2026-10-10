"""TqSdk 思路的股票日线白名单；纯函数，研究与模拟交易共用。

所有输入必须是完整、同口径、日期严格递增的K线。没有模型代码执行入口。
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from statistics import pstdev

VERSION = "tq_daily_v2"
SPECS = {
    "trend_follow": dict(name="趋势跟随", stop=2.0, trail=3.0, days=0,
                         positions=4, weight=.20, exposure=.80, risk=.0075, cooldown=0),
    "mean_revert": dict(name="均值回归", stop=1.5, trail=0, days=10,
                        positions=4, weight=.15, exposure=.60, risk=.005, cooldown=3),
    "leader_break": dict(name="龙头突破", stop=2.0, trail=3.0, days=0,
                         positions=3, weight=.20, exposure=.60, risk=.0075, cooldown=0),
    "quant_rotate": dict(name="量化轮动", stop=2.5, trail=0, days=0,
                         positions=4, weight=.15, exposure=.60, risk=.005, cooldown=0),
    "contrarian": dict(name="逆向修复", stop=1.5, trail=0, days=7,
                       positions=3, weight=.10, exposure=.30, risk=.0075, cooldown=5),
    "value_hold": dict(name="价值长持", blocked="缺少时点化财务与估值数据"),
    "div_lowvol": dict(name="高股息低波", blocked="缺少时点化现金分红数据"),
    "event_driven": dict(name="事件驱动", blocked="缺少可核实事件及历史一致预期"),
}
# 冻结全部指标及信号常量；修改行为必须发布新策略键。
PARAMETERS = dict(ema_fast=10, ema_slow=30, trend_ma=120, ma_mid=20,
                  ma_environment=60, atr=14, rsi=14, boll_std=2,
                  volume_ratio=1.5, breakout=20, exit_channel=10,
                  rotation_lookback=60, rotation_skip=5, indicator="wilder_sma_seed_v1",
                  hard_position_cap=.25, cn_min_order_amount=5000,
                  foreign_min_order_amount=0)
FINGERPRINT = hashlib.sha256(json.dumps(
    [VERSION, SPECS, PARAMETERS,Path(__file__).read_text(encoding="utf-8")],
    sort_keys=True).encode()).hexdigest()


def strategy_key(family):
    if family not in SPECS:
        raise ValueError("未知策略族")
    return f"{VERSION}_{family}"


def family_for(key):
    return next((f for f in SPECS if key == strategy_key(f)), None)


def validate_bars(bars):
    previous = ""
    for bar in bars:
        stamp = str(bar["ts"])[:10]
        if stamp <= previous:
            raise ValueError("K线日期必须唯一且严格递增")
        previous = stamp
        for field in ("open", "high", "low", "close", "volume"):
            value = float(bar[field])
            if not math.isfinite(value) or value < 0 or (field != "volume" and value == 0):
                raise ValueError("K线存在缺失或异常量价")
        if float(bar["low"]) > min(float(bar["open"]), float(bar["close"])) or \
           float(bar["high"]) < max(float(bar["open"]), float(bar["close"])):
            raise ValueError("K线高低价不合法")


def _smooth(values, period, alpha):
    out = [None] * len(values)
    if len(values) < period:
        return out
    value = sum(values[:period]) / period
    out[period-1] = value
    for i in range(period, len(values)):
        value += alpha * (values[i] - value)
        out[i] = value
    return out


def indicators(bars):
    validate_bars(bars)
    c = [float(b["close"]) for b in bars]
    tr = [float(b["high"])-float(b["low"]) if i == 0 else max(
        float(b["high"])-float(b["low"]), abs(float(b["high"])-c[i-1]),
        abs(float(b["low"])-c[i-1])) for i, b in enumerate(bars)]
    delta = [c[i]-c[i-1] for i in range(1, len(c))]
    gain = _smooth([max(d, 0) for d in delta], 14, 1/14)
    loss = _smooth([max(-d, 0) for d in delta], 14, 1/14)
    rsi = [None] + [None if g is None else
        (50.0 if g == l == 0 else 100.0 if l == 0 else 100-100/(1+g/l))
        for g, l in zip(gain, loss)]
    def ma(n):
        return [None if i+1 < n else sum(c[i+1-n:i+1])/n for i in range(len(c))]
    return dict(close=c, atr=_smooth(tr, 14, 1/14), rsi=rsi,
                ema10=_smooth(c, 10, 2/11), ema30=_smooth(c, 30, 2/31),
                ma5=ma(5), ma20=ma(20), ma60=ma(60), ma120=ma(120))


def evaluate(family, bars, position=None, *, ind=None):
    spec = SPECS[family]
    if "blocked" in spec and not position:
        return dict(action="wait", reason=spec["blocked"], status="data_pending")
    if len(bars) < 131:
        return dict(action="wait", reason="完整日线不足131根", status="data_pending")
    x = ind if ind is not None else indicators(bars)
    c, r = x["close"], x["rsi"]
    t = len(bars)-1
    atr = x["atr"][t]
    if atr <= 0:
        return dict(action="wait", reason="真实波幅为零", status="data_pending")
    result = dict(action="wait", status="research", reason="尚未满足入场确认",
                  signal_date=str(bars[-1]["ts"])[:10], atr=atr,
                  close=c[t], rsi=r[t], momentum=c[t-5]/c[t-65]-1,
                  volatility=pstdev([c[i]/c[i-1]-1 for i in range(t-19,t+1)]),
                  above_trend=c[t] > x["ma120"][t])
    if position:
        opened = str(position["opened_at"])[:10]
        held = [i for i,b in enumerate(bars) if str(b["ts"])[:10] >= opened]
        if not held or opened < str(bars[0]["ts"])[:10]:
            return dict(action="wait", reason="持仓历史不完整，等待核对", status="data_pending")
        entry = held[0]
        cost = float(position["avg_cost"])
        # 初版没有新策略入场记录的存量仓位采用成本与开仓日ATR，不伪造历史订单。
        base_atr = x["atr"][entry]
        if base_atr is None:
            return dict(action="wait", reason="缺少入场日波幅", status="data_pending")
        stop = cost - spec.get("stop", 2.5) * base_atr
        if spec.get("trail"):
            stop = max([stop] + [max(c[entry:i+1])-spec["trail"]*x["atr"][i]
                                 for i in held])
        days = max(0, len(held)-1)
        reason = None
        if c[t] <= stop:
            reason = "ATR保护止损"
        elif family == "trend_follow" and x["ema10"][t] < x["ema30"][t]:
            reason = "快均线跌破慢均线"
        elif family in ("mean_revert", "contrarian") and c[t] >= x["ma20"][t]:
            reason = "修复至二十日均线"
        elif family == "leader_break" and c[t] < min(float(b["low"]) for b in bars[-11:-1]):
            reason = "跌破此前十日通道"
        elif family == "quant_rotate" and all(c[i] < x["ma120"][i] for i in range(t-2,t+1)):
            reason = "连续三日低于长期均线"
        elif spec.get("days") and days >= spec["days"]:
            reason = "达到交易日持仓期限"
        result.update(action="sell" if reason else "hold", reason=reason or "继续持仓",
                      stop_price=stop, holding_trading_days=days)
        return result
    buy = False
    if family == "trend_follow":
        buy = (x["ema10"][t-1] <= x["ema30"][t-1] and x["ema10"][t] > x["ema30"][t]
               and result["above_trend"] and x["ma120"][t] > x["ma120"][t-5])
    elif family == "mean_revert":
        p = t-1
        lower_previous = x["ma20"][p]-2*pstdev(c[p-19:p+1])
        lower = x["ma20"][t]-2*pstdev(c[t-19:t+1])
        buy = (abs(x["ma60"][t]/x["ma60"][t-10]-1) <= .03
               and c[t] >= .95*x["ma120"][t] and c[p] < lower_previous and c[t] >= lower
               and r[p] <= 30 < r[t])
    elif family == "leader_break":
        volume = sum(float(b["volume"]) for b in bars[-21:-1])/20
        height = float(bars[t]["high"])-float(bars[t]["low"])
        buy = (c[t] > max(float(b["high"]) for b in bars[-21:-1]) and volume > 0
               and float(bars[t]["volume"]) >= 1.5*volume and height > 0
               and (c[t]-float(bars[t]["low"]))/height >= .75 and c[t] > x["ma60"][t])
    elif family == "quant_rotate":
        buy = result["above_trend"] and result["volatility"] > 0
    elif family == "contrarian":
        low_seen = any(c[i] <= min(c[i-20:i]) for i in range(t-5,t))
        buy = (low_seen and min(r[t-5:t]) < 25 and r[t-1] <= 30 < r[t]
               and c[t-1] <= x["ma5"][t-1] and c[t] > x["ma5"][t]
               and c[t] >= .9*x["ma120"][t])
    result.update(action="buy" if buy else "wait", reason="日线入场条件满足" if buy else result["reason"],
                  stop_distance=spec.get("stop",2.5)*atr)
    return result


def effective_minimum(market, *, research_minimum=None):
    if market not in ("CN_A", "HK", "US"):
        raise ValueError("未知市场，不能猜订单下限")
    floor = PARAMETERS["cn_min_order_amount"] if market == "CN_A" else 0
    if research_minimum is not None:
        if market != "CN_A" or not math.isfinite(float(research_minimum)) or float(research_minimum) < 0:
            raise ValueError("研究下限只接受A股非负金额")
        floor = float(research_minimum)
    return floor


def size_order(spec, *, equity, available, exposure, symbol_value, price, distance, lot, param,
               market="CN_A", research_minimum=None):
    values = [equity, available, exposure, symbol_value, price, distance, lot]
    if any(v is None or not math.isfinite(float(v)) for v in values):
        return 0
    equity, available, exposure, symbol_value, price, distance = map(float, values[:6])
    if min(equity, available, price, distance) <= 0 or int(lot) != float(lot) or int(lot) <= 0:
        return 0
    # 缺失账户上限不猜值。费用安全垫只减少预算，实际费用仍由paper审核。
    cap = param.get("max_position_pct")
    floor = effective_minimum(market, research_minimum=research_minimum)
    if cap is None:
        return 0
    if not math.isfinite(float(cap)) or not math.isfinite(float(floor)) or float(cap)<=0 or float(floor)<0:
        return 0
    quantity = min(equity*spec["risk"]/distance,
                   max(0,equity*min(spec["weight"],float(cap),PARAMETERS["hard_position_cap"])-symbol_value)/price,
                   max(0,equity*spec["exposure"]-exposure)/price, available*.99/price)
    qty = int(quantity/int(lot))*int(lot)
    return qty if qty*price >= float(floor) else 0
