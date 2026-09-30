"""受限个人策略的确定性日线回测；信号在前、成交在后，不执行 AI 代码。"""
from __future__ import annotations

import math
from bisect import bisect_right
from datetime import date


def average(bars, n, ema=False):
    values = [r[0] for r in (bars if ema else bars[-n:])]
    if len(values) < n or not all(math.isfinite(v) for v in values): return None
    if not ema: return sum(values[-n:])/n
    value = sum(values[:n])/n
    for px in values[n:]: value += 2/(n+1)*(px-value)
    return value


def exit_candidates(rules, bars, pos):
    """返回所有命中，硬止损固定第一，其他按用户优先级、全清优先和稳定序号。"""
    from app.services.quant.agent_builder import SCHEMA, SELL
    px = bars[-1][0]
    out = []
    for i, rule in enumerate(rules):
        kind,p = rule['type'],rule['params']
        if kind not in SELL or i in pos.get('fired',set()): continue
        hit = (kind == 'stop' and px <= pos['entry']*(1-p['pct']/100)
               or kind == 'take_profit' and px >= pos['entry']*(1+p['pct']/100)
               or kind == 'trailing' and px <= pos['high']*(1-p['pct']/100)
               or kind == 'time_exit' and pos['age'] >= p['days']
               or kind == 'breakeven' and pos['high'] >= pos['entry']*(1+p['trigger_pct']/100) and px <= pos['entry']
               or kind == 'reduce_loss' and px <= pos['entry']*(1-p['pct']/100)
               or kind == 'reduce_profit' and px >= pos['entry']*(1+p['pct']/100))
        if kind in ('ma_exit','ma_exit_offset'): hit = indicator_rule(rule,bars) is True
        if hit:
            fraction = p['size_pct']/100 if kind in ('reduce_loss','reduce_profit') else 1
            out.append(dict(rule_index=i, fraction=fraction, priority=0 if kind=='stop' else rule.get('priority',10),
                            reason=SCHEMA[kind][0], type=kind))
    return sorted(out,key=lambda x:(x['type']!='stop',x['priority'],x['fraction']<1,x['rule_index']))


def indicator_rule(rule, bars):
    """bars 每行是收、高、低、量、开，仅可用信号日及此前数据。"""
    kind, p = rule["type"], rule["params"]
    n = p.get("days", 0)
    if not bars or not math.isfinite(bars[-1][0]): return None
    close = bars[-1][0]
    if kind in ("ema_compare", "sma_compare", "market_trend"):
        fast, slow = average(bars,p["fast"],kind=="ema_compare"), average(bars,p["slow"],kind=="ema_compare")
        if fast is None or slow is None: return None
        return fast > slow and (kind != "market_trend" or close > fast)
    if kind == "price_ema":
        value = average(bars,n,True)
        return close > value if value is not None else None
    if kind in ("breakout", "breakout_distance"):
        vals = [r[1] for r in bars[-n-1:-1]]
        if len(vals) != n or not all(math.isfinite(v) for v in vals) or max(vals) <= 0: return None
        pivot = max(vals)
        return close > pivot and (kind == "breakout" or close <= pivot*(1+p["max_pct"]/100))
    if kind in ("ma_above", "ma_exit", "ma_exit_offset"):
        vals = [r[0] for r in bars[-n:]]
        if len(vals) != n or not all(math.isfinite(v) for v in vals): return None
        return close > sum(vals)/n if kind == "ma_above" else close < sum(vals)/n*(1-p.get("pct",0)/100)
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
                size = min(pos['size'], max(1, int(pos['size']*order.get('fraction',1))))
                fee = commission.fee_for(size, fill, before)
                entry_fee = pos['fee']*size/pos['size']
                cash += size*fill-fee
                pnl = (fill-pos['entry'])*size-fee-entry_fee
                pos['realized'] += pnl
                pos['fee'] -= entry_fee
                pos['size'] -= size
                if 'rule_index' in order: pos['fired'].add(order['rule_index'])
                risk = pos['entry']*config['stop']['pct']/100*pos['initial_size']
                trades.append(dict(code=code, side="sell", date=str(day), signal_date=order["date"], price=fill,
                                   shares=size, fee=fee, reason=order["reason"], pnl=pnl, r=pnl/risk,
                                   rule_index=order.get('rule_index'), matched_rules=order.get('matched_rules',[]), remaining=pos['size']))
                monthly[ym] = before+size
                if pos['size'] == 0:
                    cycles.append(pos['realized'])
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
                    positions[code] = dict(size=size, initial_size=size, realized=0.0, fired=set(), entry=fill, high=fill, age=0, fee=fee, last=px, entry_date=str(day))
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
            exits = exit_candidates(rules, bars[code], pos)
            if exits and code not in orders:
                chosen = exits[0]
                orders[code] = dict(chosen,side="sell", date=str(day), matched_rules=[x['reason'] for x in exits])
        candidates, missing = pool_of(day)
        unknown += missing
        market_checks = [indicator_rule(r,bars.get('__benchmark__',[])) for r in rules if r['type']=='market_trend']
        if any(x is None for x in market_checks): unknown += 1
        for code in sorted(candidates):
            if market_checks and not all(x is True for x in market_checks): continue
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
            if i and ds[i-1] == day: out[code] = arr[:i].tolist()
        bench_days = [d for d in days if d <= day]
        out['__benchmark__'] = [[float(store['bench'][d])]*3+[math.nan,float(store['bench'][d])] for d in bench_days]
        return out
    def pool_of(day):
        historical, info = sa.build_rows("us", day, compiled.fields, rows, perf)
        if info.get("unavailable"): raise ValueError("股票池包含缺少历史数据的字段")
        hits, missing = dsl.evaluate(compiled, historical, cache)
        return {r["_code"] for r in hits}, missing
    result = simulate(snapshot, dates, day_data, pool_of, progress)
    result["data_last"] = str(store["last"])
    return result
