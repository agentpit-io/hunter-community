"""回测引擎 · 向量化 · 月频等权 · Sharpe/Sortino/MaxDD/Calmar
(见 doc/开源hunter-community/参考/11量化策略/quant-strategy-tech-plan.md §6)

Phase A 最简版:
- 只支持 A 股
- rebalance 只支持 M(月频)
- 等权持仓
- 用 stocks 表 · 未来加真 hs300 成分股(v2)
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.services.database import get_conn
from app.services.quant.strategy_engine import score_and_select

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# spec hash
# ═══════════════════════════════════════════════════════════════

def compute_spec_hash(strategy: dict, start: date, end: date) -> str:
    canonical = json.dumps({
        "factors": sorted(strategy["factors"], key=lambda f: f["key"]),
        "config": strategy["config"],
        "start": start.isoformat(),
        "end": end.isoformat(),
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


# ═══════════════════════════════════════════════════════════════
# rebalance 日历
# ═══════════════════════════════════════════════════════════════

def _rebalance_dates(start: date, end: date, freq: str = "M") -> list[date]:
    """生成 rebalance 日期 · Phase A 只支持 M(每月第一个交易日)"""
    if freq != "M":
        # v2 加 W / Q / H
        freq = "M"
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT DISTINCT ts FROM klines
           WHERE period='daily' AND ts >= %s AND ts <= %s
           ORDER BY ts""",
        (start, end),
    )
    all_days = [r[0] for r in cur.fetchall()]
    cur.close()
    conn.close()
    if not all_days:
        return []
    # 每月第一个交易日
    seen = set()
    result = []
    for d in all_days:
        key = (d.year, d.month)
        if key not in seen:
            seen.add(key)
            result.append(d)
    return result


# ═══════════════════════════════════════════════════════════════
# 收益计算
# ═══════════════════════════════════════════════════════════════

def _period_return(codes: list[str], dt0: date, dt1: date) -> float:
    """等权持仓 · 从 dt0 到 dt1 的收益率"""
    if not codes or dt0 >= dt1:
        return 0.0
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT code, ts, close FROM klines
           WHERE code = ANY(%s) AND period='daily' AND ts IN (
             (SELECT MAX(ts) FROM klines WHERE code = ANY(%s) AND period='daily' AND ts <= %s),
             (SELECT MAX(ts) FROM klines WHERE code = ANY(%s) AND period='daily' AND ts <= %s)
           )""",
        (codes, codes, dt0, codes, dt1),
    )
    by_code: dict[str, list] = {}
    for c, t, close in cur.fetchall():
        by_code.setdefault(c, []).append((t, float(close) if close else None))
    cur.close()
    conn.close()

    rets = []
    for c, series in by_code.items():
        if len(series) < 2:
            continue
        series.sort(key=lambda x: x[0])
        p0, p1 = series[0][1], series[-1][1]
        if p0 and p1 and p0 > 0:
            rets.append(p1 / p0 - 1)
    return sum(rets) / len(rets) if rets else 0.0


# ═══════════════════════════════════════════════════════════════
# 指标
# ═══════════════════════════════════════════════════════════════

def _calc_metrics(nav: list[float]) -> dict:
    if len(nav) < 2:
        return {"ann_ret": 0, "sharpe": 0, "sortino": 0, "max_dd": 0, "calmar": 0, "vol": 0}
    rets = [nav[i] / nav[i-1] - 1 for i in range(1, len(nav))]
    n = len(rets)
    # 假设月频 · 12 段/年
    ann_ret = (nav[-1] / nav[0]) ** (12.0 / n) - 1 if n > 0 else 0
    mean_r = sum(rets) / n
    var = sum((r - mean_r) ** 2 for r in rets) / n
    ann_vol = math.sqrt(var * 12)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    downside = [r for r in rets if r < 0]
    downside_std = math.sqrt(sum(r*r for r in downside) / len(downside)) * math.sqrt(12) if downside else 0
    sortino = ann_ret / downside_std if downside_std > 0 else 0
    peak = nav[0]
    max_dd = 0.0
    for v in nav:
        if v > peak: peak = v
        dd = v / peak - 1
        if dd < max_dd: max_dd = dd
    calmar = ann_ret / abs(max_dd) if max_dd < 0 else 0
    return {
        "ann_ret": round(ann_ret, 4),
        "vol": round(ann_vol, 4),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "max_dd": round(max_dd, 4),
        "calmar": round(calmar, 3),
    }


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════

def _calc_turnover(prev: list[str], curr: list[str]) -> float:
    """单边换手率 · 简版:出仓比例"""
    if not prev:
        return 1.0
    prev_set = set(prev)
    curr_set = set(curr)
    out = len(prev_set - curr_set) / len(prev_set)
    return out


def run_backtest(strategy: dict, start: date, end: date, user_id: str | None = None) -> dict:
    """执行回测 · 返回完整结果"""
    t0 = time.time()
    schedule = _rebalance_dates(start, end)
    if len(schedule) < 2:
        return {"error": "no_dates", "message": f"起止时间内无 rebalance 日 · start={start} end={end}"}

    cost_bps = strategy["config"].get("cost_bps", 15)
    nav = [1.0]
    nav_series = []
    positions_hist = []
    turnover_hist = []
    for i in range(len(schedule) - 1):
        dt0, dt1 = schedule[i], schedule[i+1]
        picks = score_and_select(strategy, dt0, user_id)
        codes = [p["code"] for p in picks]
        # 换手成本
        prev_codes = positions_hist[-1] if positions_hist else []
        turnover = _calc_turnover(prev_codes, codes)
        cost = turnover * cost_bps / 10000
        # 期间收益
        period_ret = _period_return(codes, dt0, dt1)
        nav.append(nav[-1] * (1 - cost) * (1 + period_ret))
        positions_hist.append(codes)
        turnover_hist.append(turnover)
        nav_series.append({"date": dt0.isoformat(), "nav": round(nav[-1], 4)})

    metrics = _calc_metrics(nav)
    metrics["turnover"] = round(sum(turnover_hist) / len(turnover_hist), 3) if turnover_hist else 0

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "metrics": metrics,
        "nav_series": nav_series,
        "positions": [{"code": c, "weight": 1.0/len(positions_hist[-1])} for c in positions_hist[-1]] if positions_hist else [],
        "cost_used": cost_bps * sum(turnover_hist),
        "duration_ms": int((time.time() - t0) * 1000),
    }
