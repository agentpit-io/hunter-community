"""小鹿智能体 · 快速模拟器(给优化器用,纯计算)。

优化器每天要拿几十组参数把「从起点到今天」整段重跑一遍。慢的地方只有指标(EMA / ATR 逐根递推),
而指标和策略参数无关(枢轴周期固定 20),所以按 (code, date) 算一次缓存起来,之后每组参数的模拟
只剩规则判断:约 200 个交易日 × 每天 50 只 ≈ 1 万次规则判断,几十毫秒一组。

模拟和实盘(agent_run.run_date)跑的是同一个 agent_vcp.run_day,只是指标来自缓存 —— 两边不会漂移。
"""
from __future__ import annotations

from datetime import date

from app.services.quant import agent_vcp as av


def pooled_watch(dates: list[date], screen_of: dict, pool_days: int) -> dict:
    """每天的观察池 = 最近 pool_days 个交易日筛选结果的并集 → {date: [(code, name, score, since)]}。
    和 agent_run 里实盘的拼法同一口径(今天的在前,老的按首次入选日)。"""
    out: dict = {}
    for i, d in enumerate(dates):
        seen: dict = {}
        for dd in dates[max(0, i - pool_days + 1):i + 1][::-1]:      # 今天 → 往前
            for code, name, score in screen_of.get(dd, []):
                if code not in seen:
                    seen[code] = (name, score, dd)
                else:
                    seen[code] = (seen[code][0], seen[code][1], dd)   # since = 最早那天
        out[d] = [(c, v[0], v[1], v[2]) for c, v in seen.items()]
    return out


def build_cache(dates: list[date], pool: dict, bars_of, engine=av, bench: dict | None = None) -> dict:
    """{(code, date): 指标 | None}。每只票从首次进池那天算到最后一天(持仓可能拿到很久以后)。"""
    first: dict = {}
    for d in dates:
        for code, _n, _s, _since in pool.get(d, []):
            first.setdefault(code, d)
    cache: dict = {}
    for code, d0 in first.items():
        bars = bars_of(code)                     # 整段(到最后一天)
        if not bars:
            continue
        idx = {b[0]: i for i, b in enumerate(bars)}
        for d in dates:
            if d < d0:
                continue
            i = idx.get(d)
            cache[(code, d)] = engine.indicators(bars[:i + 1], bench=bench) if i is not None else None
    return cache


def simulate(engine, p: dict, g: dict, dates: list[date], pool: dict, cache: dict,
             start_cash: float | None = None, positions=None, consec: int = 0,
             prev_equity: float | None = None) -> dict:
    """从 dates[0] 跑到 dates[-1]。engine = agent_vcp | agent_vcp3(同一接口)。
    → {equity: [(date, eq)], trades: [...], positions, cycles, metrics}"""
    cash = float(start_cash if start_cash is not None else g["initial_capital"])
    positions = list(positions or [])
    equity: list[tuple] = []
    trades: list[dict] = []
    for d in dates:
        watch = [(c, n, s) for c, n, s, _since in pool.get(d, [])]
        r = engine.run_day(str(d), positions, cash, lambda c: [], watch, prev_equity, consec, p, g,
                           ind_of=lambda c, _d=d: cache.get((c, _d)), want_text=False)
        for f in r["fills"]:
            f["date"] = str(d)
        trades += r["fills"]
        positions, cash, consec = r["positions"], r["cash"], r["consec_losses"]
        prev_equity = r["equity"]
        equity.append((d, r["equity"]))
    return {"equity": equity, "trades": trades, "positions": positions, "cash": cash, "consec": consec,
            "metrics": metrics(equity, trades, positions, g["initial_capital"])}


def metrics(equity: list[tuple], trades: list[dict], open_positions, initial: float) -> dict:
    """净值 / 回撤 / 完整持仓周期的胜率与盈亏比。周期 = 同一 code + entry_date 的所有卖出加总,
    还没清仓的周期不算(算了会把浮盈浮亏当成结论)。"""
    eq = [e for _, e in equity] or [initial]
    peak, dd = eq[0], 0.0
    for e in eq:
        peak = max(peak, e)
        dd = min(dd, (e / peak - 1) * 100)
    cyc: dict = {}
    for t in trades:
        if t["side"] == "sell":
            k = (t["symbol"], t.get("entry_date"))
            cyc[k] = cyc.get(k, 0.0) + (t.get("pnl_abs") or 0.0)
    open_keys = {(x.code, x.entry_date) for x in (open_positions or [])}
    done = [v for k, v in cyc.items() if k not in open_keys]
    wins = [v for v in done if v > 0]
    losses = [-v for v in done if v < 0]
    return {"final": eq[-1], "pnl": eq[-1] - initial, "pnl_pct": (eq[-1] / initial - 1) * 100,
            "max_dd_pct": dd, "cycles": len(done), "wins": len(wins),
            "win_rate": (len(wins) / len(done) * 100) if done else None,
            "profit_factor": (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else None,
            "expectancy": (sum(done) / len(done)) if done else None,
            "sells": sum(1 for t in trades if t["side"] == "sell")}
