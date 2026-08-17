"""E-3 · 因子相关性引擎 · Pearson / Spearman
(Phase E · 2026-08-18)

用法:
  from app.services.quant import correlation_engine as ce
  r = ce.compute_pairwise_corr(['pe_inv', 'roe', 'momentum_12m_1m'])
  # → {factors: [...], matrix: [[1.0, 0.32, -0.05], ...], n_codes: 289}

高相关(|r| > 0.7)提示用户"权重被稀释"。
"""
from __future__ import annotations

import logging
from datetime import date

log = logging.getLogger(__name__)


def _pearson(x: list[float], y: list[float]) -> float:
    n = len(x)
    if n < 2:
        return 0.0
    mx = sum(x) / n
    my = sum(y) / n
    num = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    dx = sum((v - mx) ** 2 for v in x) ** 0.5
    dy = sum((v - my) ** 2 for v in y) ** 0.5
    if dx == 0 or dy == 0:
        return 0.0
    return num / (dx * dy)


def _rank(a: list[float]) -> list[float]:
    n = len(a)
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


def _spearman(x: list[float], y: list[float]) -> float:
    return _pearson(_rank(x), _rank(y))


def compute_pairwise_corr(
    factor_keys: list[str],
    universe: str = "hs300",
    trade_date: date | None = None,
    method: str = "pearson",
) -> dict:
    """N × N 因子对之间的相关矩阵(单期截面 z-score)
    · trade_date=None 用今天(strategy_engine 会 lookback 45 天)
    · common_codes < 10 时返 error
    """
    from app.services.quant.strategy_engine import _resolve_universe, _fetch_z_scores

    trade_date = trade_date or date.today()
    codes = _resolve_universe(universe, trade_date, None)
    if not codes:
        return {"error": "universe_empty"}

    # 拉每因子 z-score
    z_matrix = {k: _fetch_z_scores(k, trade_date, codes) for k in factor_keys}

    # 只留全部因子都覆盖的 code
    key_sets = [set(z_matrix[k].keys()) for k in factor_keys]
    common_codes = sorted(set.intersection(*key_sets)) if key_sets else []
    if len(common_codes) < 10:
        return {
            "error": "insufficient_common_codes",
            "n_common": len(common_codes),
            "coverage_per_factor": {k: len(z_matrix[k]) for k in factor_keys},
        }

    # 构造 (n_common × n_factors) 矩阵
    rows = [[z_matrix[k][c] for k in factor_keys] for c in common_codes]

    # 相关矩阵
    n = len(factor_keys)
    matrix = [[0.0] * n for _ in range(n)]
    corr_fn = _spearman if method == "spearman" else _pearson
    for i in range(n):
        for j in range(n):
            if i == j:
                matrix[i][j] = 1.0
            elif i < j:
                col_i = [r[i] for r in rows]
                col_j = [r[j] for r in rows]
                matrix[i][j] = round(corr_fn(col_i, col_j), 4)
            else:
                matrix[i][j] = matrix[j][i]

    # 高相关警告
    high_pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            if abs(matrix[i][j]) > 0.7:
                high_pairs.append({
                    "a": factor_keys[i], "b": factor_keys[j],
                    "corr": matrix[i][j],
                })

    return {
        "factors": factor_keys,
        "method": method,
        "universe": universe,
        "trade_date": trade_date.isoformat(),
        "n_codes": len(common_codes),
        "matrix": matrix,
        "high_pairs": high_pairs,
    }
