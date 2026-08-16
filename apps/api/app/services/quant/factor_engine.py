"""因子计算引擎 · Phase A 最简版(3 因子)
(见 doc/开源hunter-community/参考/11量化策略/quant-strategy-tech-plan.md §4)

数据流:
  finance-data(TTM 净利润 / 归母权益 / K 线) + klines 表(本地缓存)
  → numpy 向量化计算 → 3σ winsorize + z-score → factor_value 表 upsert

Phase A 简化:
- 不做增量 · 每日重算(hs300 规模够小)
- 缺失值不补 · 直接跳过(v2 补中位数)
- 不算 IC(供前端 factors.html 静态显示 · IC 走 factor_ic 表 · Phase B 加)
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from statistics import median

from app.services.database import get_conn
from app.services.quant.factor_defs import enabled_factors, get_factor

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 通用工具
# ═══════════════════════════════════════════════════════════════

def _winsorize_zscore(values: dict[str, float]) -> tuple[dict[str, float], dict[str, float]]:
    """3σ winsorize + z-score · 返 (z_score, pct_rank)"""
    if not values:
        return {}, {}
    vals = sorted(values.values())
    n = len(vals)
    # 简版 winsorize:去 1% 尾部
    lo = vals[int(n * 0.01)]
    hi = vals[int(n * 0.99)] if n > 100 else vals[-1]
    clipped = {c: max(lo, min(hi, v)) for c, v in values.items()}
    mean = sum(clipped.values()) / n
    var = sum((v - mean) ** 2 for v in clipped.values()) / n
    std = var ** 0.5 or 1.0
    z = {c: (v - mean) / std for c, v in clipped.items()}
    sorted_codes = sorted(values.keys(), key=lambda c: values[c])
    rank = {c: (i + 1) / n for i, c in enumerate(sorted_codes)}
    return z, rank


def _fetch_klines_close(codes: list[str], trade_date: date, back_days: int) -> dict[str, list[tuple[date, float]]]:
    """从 klines 表拿 close · 按 code 分组 · 按 ts 升序"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT code, ts, close FROM klines
           WHERE code = ANY(%s) AND period='daily' AND ts <= %s AND ts >= %s
           ORDER BY code, ts""",
        (codes, trade_date, trade_date - timedelta(days=back_days + 30)),
    )
    out: dict[str, list] = {}
    for code, ts, close in cur.fetchall():
        out.setdefault(code, []).append((ts, float(close) if close else None))
    cur.close()
    conn.close()
    return out


# ═══════════════════════════════════════════════════════════════
# 单因子计算 · Phase A · 3 个
# ═══════════════════════════════════════════════════════════════

def _compute_momentum_12m_1m(codes: list[str], trade_date: date) -> dict[str, float]:
    """12M-1M 动量 · 剔除最近 1 月的 11 月涨幅
    历史充足时用 close[-22]/close[-243] · 不足 243 时降级为 close[-22]/close[-min(120,len-22)]
    (Phase A · 数据回填有限 · 降级保证有数据 · v2 全 243 严格)
    """
    kl = _fetch_klines_close(codes, trade_date, back_days=400)
    out: dict[str, float] = {}
    for code, series in kl.items():
        closes = [c for _, c in series if c is not None and c > 0]
        if len(closes) < 60:
            continue
        recent_idx = min(-22, -(len(closes) // 3))
        past_idx = -min(len(closes) - abs(recent_idx) - 1, 243)
        recent = closes[recent_idx]
        past = closes[past_idx]
        if past > 0:
            out[code] = recent / past - 1
    return out


def _compute_pe_inv(codes: list[str], trade_date: date) -> dict[str, float]:
    """PE 倒数 · 需要 TTM 净利润 + 总市值
    Phase A 简化:走 finance_data_client · 单只票 HTTP 拉 · 慢但先跑通
    """
    from app.services import finance_data_client as fd
    out: dict[str, float] = {}
    for code in codes:
        try:
            fin = fd.get_governance(code)  # 简版:治理接口带 basic 财务(hackish · v2 换正规 endpoint)
        except Exception:
            fin = None
        # 简版兜底 · 大部分票拿不到就跳过
        if not fin:
            continue
        pe = fin.get("pe_ttm") or fin.get("pe")
        if pe and 0 < pe < 1000:
            out[code] = 1.0 / pe
    return out


def _compute_roe(codes: list[str], trade_date: date) -> dict[str, float]:
    """ROE · 从 finance-data 拿 TTM 净利润 + 归母权益
    Phase A 简化:同 pe_inv · 走 finance_data_client
    """
    from app.services import finance_data_client as fd
    out: dict[str, float] = {}
    for code in codes:
        try:
            fin = fd.get_governance(code)
        except Exception:
            fin = None
        if not fin:
            continue
        roe = fin.get("roe_ttm") or fin.get("roe")
        if roe is not None and -100 < roe < 100:
            out[code] = roe / 100 if abs(roe) > 1 else roe
    return out


COMPUTERS = {
    "pe_inv": _compute_pe_inv,
    "roe": _compute_roe,
    "momentum_12m_1m": _compute_momentum_12m_1m,
}


# ═══════════════════════════════════════════════════════════════
# 落库
# ═══════════════════════════════════════════════════════════════

def _bulk_upsert(trade_date: date, factor_key: str,
                 raw: dict[str, float], z: dict[str, float], rank: dict[str, float]) -> int:
    if not raw:
        return 0
    conn = get_conn()
    cur = conn.cursor()
    rows = [(trade_date, factor_key, c, "A", raw[c], z.get(c), rank.get(c)) for c in raw]
    cur.executemany(
        """INSERT INTO factor_value (trade_date, factor_key, code, market, raw_value, z_score, pct_rank)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (trade_date, factor_key, code) DO UPDATE
             SET raw_value = EXCLUDED.raw_value,
                 z_score = EXCLUDED.z_score,
                 pct_rank = EXCLUDED.pct_rank,
                 updated_at = NOW()""",
        rows,
    )
    conn.commit()
    n = len(rows)
    cur.close()
    conn.close()
    return n


# ═══════════════════════════════════════════════════════════════
# 顶层 API
# ═══════════════════════════════════════════════════════════════

def compute_and_store(factor_key: str, codes: list[str], trade_date: date) -> int:
    """计算单因子 + 落库 · 返回 upsert 行数"""
    fd = get_factor(factor_key)
    if not fd or not fd.enabled:
        log.warning("[factor_engine] 因子 %s 未启用 · 跳过", factor_key)
        return 0
    computer = COMPUTERS.get(factor_key)
    if not computer:
        log.warning("[factor_engine] 因子 %s 无 computer · 跳过", factor_key)
        return 0
    raw = computer(codes, trade_date)
    if not raw:
        log.warning("[factor_engine] 因子 %s 无数据 · 可能上游未准备好", factor_key)
        return 0
    z, rank = _winsorize_zscore(raw)
    n = _bulk_upsert(trade_date, factor_key, raw, z, rank)
    log.info("[factor_engine] %s @ %s · upsert %d 行", factor_key, trade_date, n)
    return n


def compute_daily(codes: list[str], trade_date: date) -> dict[str, int]:
    """算全部启用因子 · 供 APScheduler 每日调"""
    result = {}
    for fd in enabled_factors():
        result[fd.key] = compute_and_store(fd.key, codes, trade_date)
    return result
