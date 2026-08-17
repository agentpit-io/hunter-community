"""策略引擎 · 按因子权重打分 · 选 Top N
(见 doc/开源hunter-community/参考/11量化策略/quant-strategy-tech-plan.md §5)
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from app.services.database import get_conn
from app.services.quant.factor_defs import get_factor


def fetch_stock_names(codes: list[str]) -> dict[str, str]:
    """从 stocks 表拿 code → name 映射 · 缺的返 code 本身"""
    if not codes:
        return {}
    conn = get_conn(); cur = conn.cursor()
    cur.execute(
        "SELECT DISTINCT ON (code) code, name FROM stocks WHERE code = ANY(%s)",
        (codes,),
    )
    out = {c: n for c, n in cur.fetchall()}
    cur.close(); conn.close()
    return {c: out.get(c, c) for c in codes}


def _resolve_universe(universe: str, trade_date: date, user_id: str | None = None) -> list[str]:
    """E-4 · 从 index_component 表拉真 hs300/zz500/zz1000 成分股
    · 未 seed 时 fallback stocks 表(向后兼容)
    · trade_date 用于历史 · 生存者偏差防治
    """
    from app.services.quant import universe as _universe
    return _universe.resolve(universe, trade_date, user_id)


def _fetch_z_scores(factor_key: str, trade_date: date, codes: list[str]) -> dict[str, float]:
    """从 factor_value 表拿最近可用的 z_score
    lookback 45 天 · 覆盖 Phase A 月末回填的实际情况(每月 15 号)
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT DISTINCT ON (code) code, z_score FROM factor_value
           WHERE factor_key=%s AND code = ANY(%s) AND trade_date <= %s AND trade_date >= %s
             AND z_score IS NOT NULL
           ORDER BY code, trade_date DESC""",
        (factor_key, codes, trade_date, trade_date - timedelta(days=45)),
    )
    out = {c: float(z) for c, z in cur.fetchall()}
    cur.close()
    conn.close()
    return out


def score_and_select(strategy: dict, trade_date: date, user_id: str | None = None) -> list[dict]:
    """按策略配置打分 · 选 Top N
    strategy = {
      'factors': [{'key': 'pe_inv', 'weight_pct': 40}, ...],
      'config': {'universe': 'hs300', 'top_n': 20, ...},
    }
    """
    codes = _resolve_universe(strategy["config"].get("universe", "hs300"), trade_date, user_id)
    if not codes:
        return []
    scores: dict[str, float] = defaultdict(float)
    contribs: dict[str, dict] = defaultdict(dict)
    covered: dict[str, int] = defaultdict(int)   # 有效因子数(用于过滤覆盖不全的)

    for f in strategy["factors"]:
        fdef = get_factor(f["key"])
        if not fdef:
            continue
        w = f.get("weight_pct", 0) / 100.0
        if w <= 0:
            continue
        sign = -1.0 if fdef.reverse else 1.0
        zs = _fetch_z_scores(f["key"], trade_date, codes)
        for c, z in zs.items():
            contrib = sign * z * w
            scores[c] += contrib
            contribs[c][f["key"]] = contrib
            covered[c] += 1

    # 只保留至少 50% 因子有数据的
    min_cover = max(1, len([f for f in strategy["factors"] if f.get("weight_pct", 0) > 0]) // 2)
    filtered = {c: s for c, s in scores.items() if covered[c] >= min_cover}

    ranked = sorted(filtered.items(), key=lambda x: -x[1])[: strategy["config"].get("top_n", 20)]
    return [{
        "code": c,
        "score": round(s, 4),
        "factor_contrib": {k: round(v, 4) for k, v in contribs[c].items()},
    } for c, s in ranked]
