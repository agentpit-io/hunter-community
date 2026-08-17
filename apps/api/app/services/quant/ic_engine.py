"""IC 引擎 · Information Coefficient(Spearman 秩相关)
(Phase D · D-2 · 2026-08-17)

IC = Spearman(今日因子 z-score · 未来 N 日累计收益率)
   · > 0.05  · 因子有效
   · > 0.10  · 强因子
   · IR = IC 均值 / IC 标准差 · 稳定性

用法:
  from app.services.quant import ic_engine
  ic_engine.compute_and_store('pe_inv', date.today(), 'hs300', horizon=5)

APScheduler 每日 17:30 CST 触发全 factor × 3 horizon 重算(见 scheduler.py)
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from app.services.database import get_conn

log = logging.getLogger(__name__)


def _fetch_z_scores(factor_key: str, trade_date: date, universe: str = "hs300") -> dict[str, float]:
    """拉 trade_date 当日或最近可用的 z-score(45 天 lookback)"""
    from app.services.quant.strategy_engine import _resolve_universe
    codes = _resolve_universe(universe, trade_date, None)
    if not codes:
        return {}
    conn = get_conn(); cur = conn.cursor()
    cur.execute(
        """SELECT DISTINCT ON (code) code, z_score FROM factor_value
           WHERE factor_key=%s AND code = ANY(%s)
             AND trade_date <= %s AND trade_date >= %s
             AND z_score IS NOT NULL
           ORDER BY code, trade_date DESC""",
        (factor_key, codes, trade_date, trade_date - timedelta(days=45)),
    )
    out = {c: float(z) for c, z in cur.fetchall()}
    cur.close(); conn.close()
    return out


def _fetch_forward_return(codes: list[str], trade_date: date, horizon: int) -> dict[str, float]:
    """算未来 horizon 交易日累计收益率 · 从 klines close 拿"""
    if not codes:
        return {}
    conn = get_conn(); cur = conn.cursor()
    # 取 trade_date 后第一个交易日的 close 作起点 · horizon 后 close 作终点
    cur.execute(
        """SELECT code, ts, close FROM klines
           WHERE code = ANY(%s) AND period='daily'
             AND ts >= %s AND ts <= %s
           ORDER BY code, ts""",
        (codes, trade_date, trade_date + timedelta(days=int(horizon * 1.7) + 5)),
    )
    by_code: dict[str, list] = {}
    for c, t, cl in cur.fetchall():
        by_code.setdefault(c, []).append((t, float(cl) if cl else None))
    cur.close(); conn.close()

    out = {}
    for c, series in by_code.items():
        prices = [p for _, p in series if p and p > 0]
        if len(prices) < horizon + 1:
            continue
        p0, p1 = prices[0], prices[horizon]
        if p0 > 0:
            out[c] = p1 / p0 - 1
    return out


def _spearman(x: list[float], y: list[float]) -> float | None:
    """无 scipy · 手算 Spearman 秩相关(小样本足够)"""
    n = len(x)
    if n < 3 or n != len(y):
        return None

    def _rank(a):
        idx = sorted(range(n), key=lambda i: a[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and a[idx[j + 1]] == a[idx[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[idx[k]] = avg
            i = j + 1
        return r

    rx = _rank(x); ry = _rank(y)
    mx = sum(rx) / n; my = sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    denx = sum((r - mx) ** 2 for r in rx) ** 0.5
    deny = sum((r - my) ** 2 for r in ry) ** 0.5
    if denx == 0 or deny == 0:
        return 0.0
    return num / (denx * deny)


def compute_ic(factor_key: str, trade_date: date, universe: str = "hs300", horizon: int = 5) -> float | None:
    """单期 IC · 无 factor_value 或未来 K 线不足时返 None"""
    z_map = _fetch_z_scores(factor_key, trade_date, universe)
    if len(z_map) < 10:
        return None
    fwd_ret = _fetch_forward_return(list(z_map.keys()), trade_date, horizon)
    common = [c for c in z_map if c in fwd_ret]
    if len(common) < 10:
        return None
    zs = [z_map[c] for c in common]
    rs = [fwd_ret[c] for c in common]
    return _spearman(zs, rs)


def compute_ic_ir(factor_key: str, trade_date: date, universe: str = "hs300",
                  horizon: int = 5, window: int = 30) -> float | None:
    """近 window 天 IC 均值 / std · 30 日窗口"""
    conn = get_conn(); cur = conn.cursor()
    cur.execute(
        """SELECT ic FROM factor_ic
           WHERE factor_key=%s AND universe=%s AND horizon_days=%s
             AND trade_date <= %s AND trade_date >= %s
             AND ic IS NOT NULL
           ORDER BY trade_date""",
        (factor_key, universe, horizon, trade_date, trade_date - timedelta(days=window * 2)),
    )
    ics = [float(r[0]) for r in cur.fetchall()]
    cur.close(); conn.close()
    if len(ics) < 3:
        return None
    mean = sum(ics) / len(ics)
    var = sum((x - mean) ** 2 for x in ics) / len(ics)
    std = var ** 0.5
    return mean / std if std > 0 else 0.0


def compute_and_store(factor_key: str, trade_date: date,
                       universe: str = "hs300", horizon: int = 5) -> int:
    """算 IC + IR + upsert · 返回 1(写入)或 0(跳过)"""
    ic = compute_ic(factor_key, trade_date, universe, horizon)
    if ic is None:
        return 0
    ic_ir = compute_ic_ir(factor_key, trade_date, universe, horizon)
    conn = get_conn(); cur = conn.cursor()
    cur.execute(
        """INSERT INTO factor_ic (factor_key, trade_date, universe, horizon_days, ic, ic_ir)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (factor_key, trade_date, universe, horizon_days)
           DO UPDATE SET ic = EXCLUDED.ic, ic_ir = EXCLUDED.ic_ir, updated_at = NOW()""",
        (factor_key, trade_date, universe, horizon, float(ic),
         float(ic_ir) if ic_ir is not None else None),
    )
    conn.commit(); cur.close(); conn.close()
    log.info("[ic] %s @ %s · universe=%s · horizon=%d · ic=%.4f · ir=%s",
             factor_key, trade_date, universe, horizon, ic,
             f"{ic_ir:.3f}" if ic_ir is not None else "None")
    return 1


def compute_daily(trade_date: date, universe: str = "hs300",
                   horizons: list[int] = [5, 10, 20]) -> dict[str, int]:
    """算全启用因子 × horizons 组合 · 供 APScheduler 调"""
    from app.services.quant.factor_defs import enabled_factors
    result = {}
    for fd in enabled_factors():
        for h in horizons:
            k = f"{fd.key}_h{h}"
            result[k] = compute_and_store(fd.key, trade_date, universe, h)
    return result
