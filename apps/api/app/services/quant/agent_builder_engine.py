"""受限个人策略的确定性日线回测；信号在前、成交在后，不执行 AI 代码。"""
from __future__ import annotations

import math
from bisect import bisect_right
from datetime import date


def indicator_rule(rule, bars):
    """bars 每行是收、高、低、量、开，仅可用信号日及此前数据。"""
    kind, p = rule["type"], rule["params"]
    n = p.get("days", 0)
    if not bars or not math.isfinite(bars[-1][0]): return None
    close = bars[-1][0]
    if kind == "breakout":
        vals = [r[1] for r in bars[-n-1:-1]]
        return close > max(vals) if len(vals) == n and all(math.isfinite(v) for v in vals) else None
    if kind in ("ma_above", "ma_exit"):
        vals = [r[0] for r in bars[-n:]]
        if len(vals) != n or not all(math.isfinite(v) for v in vals): return None
        return close > sum(vals)/n if kind == "ma_above" else close < sum(vals)/n
    if kind == "volume":
        vals = [r[3] for r in bars[-n-1:-1]]
        if len(vals) != n or not all(math.isfinite(v) for v in vals) or sum(vals) <= 0 or not math.isfinite(bars[-1][3]): return None
        return bars[-1][3] >= sum(vals)/n*p["ratio"]
    raise ValueError("不支持的指标规则")


def simulate(snapshot, dates, day_data, pool_of, progress=lambda p: None):
    from app.services.quant import commission
    from app.services.quant.agent_builder import BUY
    rules = snapshot["rules"]
    config = {r["type"]: r["params"] for r in rules}
    cash = float(snapshot["initial"])
    peak, dd, positions, orders = cash, 0.0, {}, {}
    trades, nav, cycles, monthly = [], [], [], {}
    signals = unknown = 0
    slip = snapshot["slippage_bps"]/10000
    for index, day in enumerate(dates):
        bars = day_data(day)
        def mark(code):
            data = bars.get(code)
            return data[-1][0] if data and math.isfinite(data[-1][0]) else None
        # 先执行前一日已知卖出信号，再执行前一日买入信号；绝不用今日收盘选今日买入。
        for code, order in sorted(list(orders.items()), key=lambda x: (x[1]["side"] != "sell", x[0])):
            px = mark(code)
            if px is None or px <= 0: continue
            ym = str(day)[:7]
            before = monthly.get(ym, 0)
            if order["side"] == "sell":
                pos = positions.get(code)
                if not pos: orders.pop(code); continue
                fill = px*(1-slip)
                fee = commission.fee_for(pos["size"], fill, before)
                cash += pos["size"]*fill-fee
                pnl = (fill-pos["entry"])*pos["size"]-fee-pos["fee"]
                risk = pos["entry"]*config["stop"]["pct"]/100*pos["size"]
                trades.append(dict(code=code, side="sell", date=str(day), signal_date=order["date"], price=fill,
                                   shares=pos["size"], fee=fee, reason=order["reason"], pnl=pnl, r=pnl/risk))
                cycles.append(pnl)
                monthly[ym] = before+pos["size"]
                del positions[code]
            else:
                if code in positions or len(positions) >= config["holdings"]["count"]:
                    orders.pop(code); continue
                equity = cash+sum(p["size"]*(mark(c) or p["last"]) for c, p in positions.items())
                fill = px*(1+slip)
                risk_dist = fill*config["stop"]["pct"]/100
                size = int(min(equity*config["position"]["pct"]/100/fill,
                               equity*config["risk"]["pct"]/100/risk_dist, cash/fill))
                while size > 0 and size*fill+commission.fee_for(size, fill, before) > cash: size -= 1
                if size > 0:
                    fee = commission.fee_for(size, fill, before)
                    cash -= size*fill+fee
                    positions[code] = dict(size=size, entry=fill, high=fill, age=0, fee=fee, last=px, entry_date=str(day))
                    trades.append(dict(code=code, side="buy", date=str(day), signal_date=order["date"],
                                       price=fill, shares=size, fee=fee, reason="股票池入选且全部买入规则满足"))
                    monthly[ym] = before+size
            orders.pop(code)
        for code, pos in positions.items():
            px = mark(code)
            if px is None: continue
            pos["last"] = px
            pos["high"] = max(pos["high"], px)
            # 开仓当日不算已持有一日；只数该股票实际有行情的交易日。
            if pos["entry_date"] != str(day):
                pos["age"] += 1
            exits = []
            for r in rules:
                kind, p = r["type"], r["params"]
                hit = (kind == "stop" and px <= pos["entry"]*(1-p["pct"]/100)
                       or kind == "take_profit" and px >= pos["entry"]*(1+p["pct"]/100)
                       or kind == "trailing" and px <= pos["high"]*(1-p["pct"]/100)
                       or kind == "time_exit" and pos["age"] >= p["days"])
                if kind == "ma_exit": hit = indicator_rule(r, bars[code]) is True
                if hit:
                    from app.services.quant.agent_builder import SCHEMA
                    exits.append(SCHEMA[kind][0])
            if exits and code not in orders:
                orders[code] = dict(side="sell", date=str(day), reason="；".join(exits))
        candidates, missing = pool_of(day)
        unknown += missing
        for code in sorted(candidates):
            if code in positions or code in orders or code not in bars: continue
            checks = [indicator_rule(r, bars[code]) for r in rules if r["type"] in BUY]
            if any(x is None for x in checks): unknown += 1
            if checks and all(x is True for x in checks):
                signals += 1
                orders[code] = dict(side="buy", date=str(day))
        equity = cash+sum(p["size"]*p["last"] for p in positions.values())
        peak = max(peak, equity)
        dd = min(dd, (equity/peak-1)*100)
        nav.append(dict(date=str(day), equity=round(equity, 2)))
        if index % 5 == 0 or index == len(dates)-1: progress(round((index+1)/len(dates)*100, 1))
    wins = [p for p in cycles if p > 0]
    return dict(initial=snapshot["initial"], final=round(equity, 2), return_pct=(equity/snapshot["initial"]-1)*100,
                max_drawdown_pct=dd, closed=len(cycles), win_rate=len(wins)/len(cycles)*100 if cycles else None,
                signals=signals, unknown=unknown, open=len(positions), pending_orders=len(orders),
                nav=nav, trades=trades, start=str(dates[0]), end=str(dates[-1]),
                note="历史研究结果，非实盘。期末持仓按最后可用收盘估值，未强制平仓；未成交订单不算交易。股票池使用当前入库证券范围，存在幸存者偏差；复权修正使用当前锚点，不是完整时点数据库。")


def calculate(snapshot, progress):
    from app.services.quant import agent_run as ar, screen_asof as sa, screen_dsl as dsl
    start, end = date.fromisoformat(snapshot["start"]), date.fromisoformat(snapshot["end"])
    periods = list(range(2, 253))
    args = (lambda f: sa.need_bars(f) is not None, periods, periods, periods)
    compiled = dsl.compile_script(snapshot["pool"]["script"], *args)
    if compiled.series:
        raise ValueError("此股票池包含序列脚本，首版个人策略回测暂不支持；请选择普通历史字段筛选策略")
    if any(sa.need_bars(f) is None for f in compiled.fields):
        raise ValueError("股票池包含无法还原历史的字段，不能使用当前快照代替")
    cache = dsl.build_resolver_cache(compiled, *args)
    rows, perf = ar._snapshot("us")
    store = sa.get_store("us", perf)
    days = sorted(store["bench"])
    if not days or start < days[0] or end > days[-1]: raise ValueError("回测日期超出已入库基准日线范围，请调整日期或补齐行情")
    dates = [d for d in days if start <= d <= end]
    if len(dates) < 2: raise ValueError("回测至少需要两个交易日")
    def day_data(day):
        out = {}
        for code, (ds, arr) in store["codes"].items():
            i = bisect_right(ds, day)
            if i and ds[i-1] == day: out[code] = arr[max(0, i-253):i].tolist()
        return out
    def pool_of(day):
        historical, info = sa.build_rows("us", day, compiled.fields, rows, perf)
        if info.get("unavailable"): raise ValueError("股票池包含缺少历史数据的字段")
        hits, missing = dsl.evaluate(compiled, historical, cache)
        return {r["_code"] for r in hits}, missing
    result = simulate(snapshot, dates, day_data, pool_of, progress)
    result["data_last"] = str(store["last"])
    return result
