"""复赛 §3.C · 概率校准模块

四件事:
1. interval_from_residuals(symbol, days)  · 残差分位 → 80/95 预测区间
2. class_prob_from_history(symbol, pred_change, days) · 经验频率 → 三类概率(up/flat/down)
3. brier_score / reliability_curve / ece · 校准评估指标
4. get_calibration_report(days, symbol) · API 层聚合入口

严禁 mock 兜底: 样本量 < 30 一律返 None · 上层判断显示"数据不足".
数据来源: pred_backtest 表(services/backtest/store.py 已有的 3 张表之一).
"""
from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from typing import Optional

from app.services.backtest.store import conn

from app.services.backtest import store

log = logging.getLogger(__name__)

MIN_SAMPLE_INTERVAL = 30      # 残差分位所需最少样本
MIN_SAMPLE_PROB = 30          # 经验频率所需最少样本
FLAT_THRESHOLD_PCT = 0.5      # |change| ≤ 0.5% 判定为 flat


# ═══════════════════════════════════════════════════════════════
# ① 残差分位 → 预测区间
# ═══════════════════════════════════════════════════════════════

def _percentile(sorted_vals: list[float], p: float) -> float:
    """线性插值分位数 · p in [0,1] · sorted_vals 必须升序"""
    if not sorted_vals:
        return 0.0
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    idx = p * (n - 1)
    lo = int(math.floor(idx)); hi = int(math.ceil(idx))
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (idx - lo) * (sorted_vals[hi] - sorted_vals[lo])


def interval_from_residuals(
    symbol: str,
    days: int = 90,
    horizon: Optional[int] = None,
) -> Optional[dict]:
    """拿最近 days 天该股的预测残差 · 算 p10/p90 (80% 区间) 和 p2.5/p97.5 (95% 区间)

    输出:
        {p80: [-2.1, +6.3], p95: [-4.5, +8.7], sample: 305}
        None: 样本量 < 30 · 上层显"数据不足"

    残差定义: real_change - pred_change · 分位数直接加到点估计 pred_change 上即得区间.
    """
    symbol = store.resolve_symbol(symbol)   # 裸码补后缀 · 见 store.resolve_symbol
    c = conn(); cur = c.cursor()
    where = "symbol = %s AND pred_date > CURRENT_DATE - %s AND pred_change IS NOT NULL AND real_change IS NOT NULL"
    args = [symbol, days]
    if horizon is not None:
        where += " AND horizon = %s"
        args.append(horizon)
    cur.execute(f"SELECT real_change - pred_change FROM pred_backtest WHERE {where}", args)
    residuals = [float(r[0]) for r in cur.fetchall() if r[0] is not None]
    cur.close(); c.close()

    if len(residuals) < MIN_SAMPLE_INTERVAL:
        return None

    residuals.sort()
    return {
        "p80": [round(_percentile(residuals, 0.10), 4),
                round(_percentile(residuals, 0.90), 4)],
        "p95": [round(_percentile(residuals, 0.025), 4),
                round(_percentile(residuals, 0.975), 4)],
        "sample": len(residuals),
        "residual_std": round((sum(r * r for r in residuals) / len(residuals)) ** 0.5, 4),
    }


# ═══════════════════════════════════════════════════════════════
# ② 经验频率 → 三类概率
# ═══════════════════════════════════════════════════════════════

def class_prob_from_history(
    symbol: str,
    pred_change: float,
    days: int = 180,
    bucket_width_pct: float = 0.5,
) -> Optional[dict]:
    """查历史上"预测 X% 时实际方向 up/flat/down 的经验频率"

    做法:
    - 拿最近 days 天该股 pred_backtest 全部记录
    - 按 pred_change 分桶(每 bucket_width_pct%)
    - 查当前 pred_change 落在哪个桶 · 该桶内 real_change 的三类分布 = 经验概率

    输出:
        {up: 0.62, flat: 0.18, down: 0.20, bucket: '(1.0, 1.5]', sample_in_bucket: 45}
        None: 样本量 < 30 或桶内样本 < 8
    """
    symbol = store.resolve_symbol(symbol)   # 裸码补后缀 · 见 store.resolve_symbol
    c = conn(); cur = c.cursor()
    cur.execute(
        """SELECT pred_change, real_change FROM pred_backtest
           WHERE symbol = %s AND pred_date > CURRENT_DATE - %s
             AND pred_change IS NOT NULL AND real_change IS NOT NULL""",
        (symbol, days),
    )
    rows = [(float(r[0]), float(r[1])) for r in cur.fetchall()]
    cur.close(); c.close()

    if len(rows) < MIN_SAMPLE_PROB:
        return None

    # 找目标桶
    def _bucket_id(x: float) -> int:
        return int(math.floor(x / bucket_width_pct))

    target_bid = _bucket_id(pred_change)
    same_bucket = [(p, r) for (p, r) in rows if _bucket_id(p) == target_bid]

    # 桶太稀就扩到邻居 · 直到至少 8 条
    for spread in range(1, 6):
        if len(same_bucket) >= 8:
            break
        same_bucket = [(p, r) for (p, r) in rows if abs(_bucket_id(p) - target_bid) <= spread]

    if len(same_bucket) < 8:
        return None

    n = len(same_bucket)
    n_up = sum(1 for (_p, r) in same_bucket if r > FLAT_THRESHOLD_PCT)
    n_down = sum(1 for (_p, r) in same_bucket if r < -FLAT_THRESHOLD_PCT)
    n_flat = n - n_up - n_down

    lo, hi = target_bid * bucket_width_pct, (target_bid + 1) * bucket_width_pct
    return {
        "up":   round(n_up / n, 4),
        "flat": round(n_flat / n, 4),
        "down": round(n_down / n, 4),
        "bucket": f"({lo:+.2f}%, {hi:+.2f}%]",
        "sample_in_bucket": n,
        "sample_total": len(rows),
    }


# ═══════════════════════════════════════════════════════════════
# ③ 校准评估指标 · Brier / Reliability / ECE
# ═══════════════════════════════════════════════════════════════

def brier_score(preds: list[float], outcomes: list[int]) -> Optional[float]:
    """Brier score · 越低越好 · 0.25 = 抛硬币 · 0 = 完美预言
    preds: 预测概率 [0,1] · outcomes: 实际二元(0/1)
    """
    if len(preds) != len(outcomes) or not preds:
        return None
    n = len(preds)
    return round(sum((p - y) ** 2 for p, y in zip(preds, outcomes)) / n, 4)


def reliability_curve(
    preds: list[float],
    outcomes: list[int],
    bins: int = 10,
) -> Optional[list[dict]]:
    """预测概率分桶 vs 实际频率 · 用于画 reliability diagram

    输出: [{bin: [0.0, 0.1], avg_pred: 0.05, freq: 0.03, n: 42}, ...]
    """
    if len(preds) != len(outcomes) or len(preds) < MIN_SAMPLE_PROB:
        return None
    buckets: list[dict] = []
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        pairs = [(p, y) for p, y in zip(preds, outcomes) if (lo <= p < hi or (i == bins - 1 and p == 1.0))]
        if not pairs:
            buckets.append({"bin": [round(lo, 2), round(hi, 2)],
                            "avg_pred": None, "freq": None, "n": 0})
            continue
        avg_p = sum(p for p, _ in pairs) / len(pairs)
        freq = sum(y for _, y in pairs) / len(pairs)
        buckets.append({
            "bin": [round(lo, 2), round(hi, 2)],
            "avg_pred": round(avg_p, 4),
            "freq": round(freq, 4),
            "n": len(pairs),
        })
    return buckets


def ece(preds: list[float], outcomes: list[int], bins: int = 10) -> Optional[float]:
    """Expected Calibration Error · 各桶 |avg_pred - freq| 的加权平均"""
    rc = reliability_curve(preds, outcomes, bins)
    if rc is None:
        return None
    total_n = sum(b["n"] for b in rc)
    if total_n == 0:
        return None
    weighted = sum(b["n"] * abs((b["avg_pred"] or 0) - (b["freq"] or 0)) for b in rc if b["n"] > 0)
    return round(weighted / total_n, 4)


# ═══════════════════════════════════════════════════════════════
# ④ API 聚合入口
# ═══════════════════════════════════════════════════════════════

def get_calibration_report(
    days: int = 90,
    symbol: str = "",
    threshold_pct: float = FLAT_THRESHOLD_PCT,
) -> dict:
    """API 层聚合 · GET /api/backtest/calibration

    · brier: 用"P(up 预测) = sigmoid(pred_change / |threshold| * k)" 转成概率再算 Brier
      简化版:pred_change > threshold → 1(预测涨) · else 0 · 与 real_change > threshold 比
      这个是"方向命中的 Brier"(而不是概率化预测的 Brier · 需要模型输出概率才行)
    · ece: 同上
    · reliability: 用 pred_change 大小 → 经验命中率 · 分 10 桶
    · sample_size + note: 供 UI 判是否显"数据不足"
    """
    symbol = store.resolve_symbol(symbol)   # 裸码补后缀 · 见 store.resolve_symbol
    c = conn(); cur = c.cursor()
    where = "pred_date > CURRENT_DATE - %s AND pred_change IS NOT NULL AND real_change IS NOT NULL"
    args = [days]
    if symbol:
        where += " AND symbol = %s"
        args.append(symbol)
    cur.execute(f"SELECT pred_change, real_change FROM pred_backtest WHERE {where}", args)
    rows = [(float(r[0]), float(r[1])) for r in cur.fetchall()]
    cur.close(); c.close()

    sample = len(rows)
    if sample < MIN_SAMPLE_PROB:
        return {
            "sample_size": sample, "brier": None, "ece": None, "reliability": None,
            "window_days": days, "symbol": symbol or None,
            "note": f"样本量 {sample} < {MIN_SAMPLE_PROB} · 数据不足以给出校准评估",
        }

    # 把 pred_change 转成 "上涨概率" · 用 sigmoid 归一化
    # k 是尺度因子 · 让 pred_change=1% 时概率 ≈ 0.65 · pred_change=3% 时 ≈ 0.85
    def _to_prob(x: float, k: float = 0.7) -> float:
        return 1.0 / (1.0 + math.exp(-x * k))

    preds = [_to_prob(p) for (p, _r) in rows]
    outcomes = [1 if r > threshold_pct else 0 for (_p, r) in rows]

    brier = brier_score(preds, outcomes)
    ece_val = ece(preds, outcomes, bins=10)
    rc = reliability_curve(preds, outcomes, bins=10)

    return {
        "sample_size": sample,
        "brier": brier,
        "ece": ece_val,
        "reliability": rc,
        "window_days": days,
        "symbol": symbol or None,
        "note": None,
        "threshold_pct": threshold_pct,
        "prob_model": "sigmoid(pred_change * 0.7) 简化转化 · 生产模型应直接输出概率",
    }
