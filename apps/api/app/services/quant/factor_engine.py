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
    """PE 倒数 · Phase B B1 · 用 AKShare 财务 EPS + 本地 close 算
    过滤 PE<=0(亏损)和 PE>1000(异常)· 剩下取 1/PE
    """
    from app.services.quant import akshare_client as akc
    out: dict[str, float] = {}
    for code in codes:
        pe = akc.get_pe_ttm(code, trade_date)
        if pe and 0 < pe < 1000:
            out[code] = 1.0 / pe
    return out


def _compute_roe(codes: list[str], trade_date: date) -> dict[str, float]:
    """ROE · Phase B B1 · 用 AKShare 净资产收益率(单期)
    注意:AKShare 返的是单季度 ROE(不是 TTM)· 但对截面排名不影响
    """
    from app.services.quant import akshare_client as akc
    out: dict[str, float] = {}
    for code in codes:
        roe = akc.get_roe(code, trade_date)
        if roe is not None and -1 < roe < 1:
            out[code] = roe
    return out


# ═══════════════════════════════════════════════════════════════
# B2 · 6 财务因子(akshare_client 已有 helper · 一次批发)
# ═══════════════════════════════════════════════════════════════

def _make_akshare_factor(getter_name: str):
    """工厂 · 用 akshare_client 的 getter 生成 compute · 3σ + z-score 由外层做"""
    def _compute(codes, trade_date):
        from app.services.quant import akshare_client as akc
        getter = getattr(akc, getter_name)
        out = {}
        for code in codes:
            v = getter(code, trade_date)
            if v is not None:
                out[code] = v
        return out
    return _compute


def _compute_pb_inv(codes, trade_date):
    from app.services.quant import akshare_client as akc
    out = {}
    for code in codes:
        pb = akc.get_pb(code, trade_date)
        if pb and 0 < pb < 100:
            out[code] = 1.0 / pb
    return out


def _compute_debt_ratio_inv(codes, trade_date):
    """1/(1+资产负债率) · 低杠杆分高"""
    from app.services.quant import akshare_client as akc
    out = {}
    for code in codes:
        d = akc.get_debt_ratio(code, trade_date)
        if d is not None and 0 < d < 1:
            out[code] = 1 - d
    return out


# ═══════════════════════════════════════════════════════════════
# B2 · 7 K 线因子(纯 numpy · 用现有 klines)
# ═══════════════════════════════════════════════════════════════

def _compute_momentum_1m(codes, trade_date):
    """1 月动量 · 反向(短期均值回归 · 反向 IC · factor_defs 里 reverse=True)"""
    kl = _fetch_klines_close(codes, trade_date, back_days=45)
    out = {}
    for code, series in kl.items():
        closes = [c for _, c in series if c is not None and c > 0]
        if len(closes) < 22: continue
        out[code] = closes[-1] / closes[-22] - 1
    return out


def _compute_momentum_6m(codes, trade_date):
    """6 月动量 · 近 120 交易日涨幅"""
    kl = _fetch_klines_close(codes, trade_date, back_days=180)
    out = {}
    for code, series in kl.items():
        closes = [c for _, c in series if c is not None and c > 0]
        if len(closes) < 60: continue
        n = min(120, len(closes)-1)
        out[code] = closes[-1] / closes[-n-1] - 1
    return out


def _compute_ma_align(codes, trade_date):
    """MA5>MA10>MA20>MA60 多头排列打分 0-3(3 个不等式满足数)"""
    import numpy as np
    kl = _fetch_klines_close(codes, trade_date, back_days=90)
    out = {}
    for code, series in kl.items():
        closes = [c for _, c in series if c is not None and c > 0]
        if len(closes) < 60: continue
        arr = np.array(closes[-60:])
        ma5 = arr[-5:].mean()
        ma10 = arr[-10:].mean()
        ma20 = arr[-20:].mean()
        ma60 = arr.mean()
        score = int(ma5 > ma10) + int(ma10 > ma20) + int(ma20 > ma60)
        out[code] = float(score)
    return out


def _compute_macd(codes, trade_date):
    """MACD_hist / ATR14 · 归一化"""
    import numpy as np
    kl = _fetch_klines_close(codes, trade_date, back_days=90)
    out = {}
    for code, series in kl.items():
        closes = np.array([c for _, c in series if c is not None and c > 0])
        if len(closes) < 40: continue
        # EMA
        def ema(arr, span):
            alpha = 2 / (span + 1)
            e = [arr[0]]
            for x in arr[1:]:
                e.append(alpha * x + (1 - alpha) * e[-1])
            return np.array(e)
        ema12 = ema(closes, 12)
        ema26 = ema(closes, 26)
        macd = ema12 - ema26
        signal = ema(macd, 9)
        hist = (macd - signal)[-1]
        # ATR14 简版:14 日 close 绝对变动均值
        atr = np.abs(np.diff(closes[-14:])).mean() or 1.0
        out[code] = hist / atr
    return out


def _compute_rsi(codes, trade_date):
    """RSI14 分段:超卖 30-下打高分 · 超买 70+ 打低分 · 反向指标"""
    import numpy as np
    kl = _fetch_klines_close(codes, trade_date, back_days=45)
    out = {}
    for code, series in kl.items():
        closes = np.array([c for _, c in series if c is not None and c > 0])
        if len(closes) < 15: continue
        diff = np.diff(closes[-15:])
        gain = diff[diff > 0].sum() or 0.01
        loss = -diff[diff < 0].sum() or 0.01
        rs = gain / loss
        rsi = 100 - 100 / (1 + rs)
        # 反向映射:RSI 20 → +2 · RSI 50 → 0 · RSI 80 → -2
        out[code] = (50 - rsi) / 15
    return out


def _compute_vol_20d_inv(codes, trade_date):
    """1 / 20 日收益标准差 · 低波异象 · 稳定跑赢"""
    import numpy as np
    kl = _fetch_klines_close(codes, trade_date, back_days=45)
    out = {}
    for code, series in kl.items():
        closes = np.array([c for _, c in series if c is not None and c > 0])
        if len(closes) < 20: continue
        rets = np.diff(closes[-21:]) / closes[-21:-1]
        std = rets.std()
        if std > 0:
            out[code] = 1.0 / std
    return out


def _compute_candle_5d(codes, trade_date):
    """近 5 日阳线数 / 5"""
    conn = get_conn(); cur = conn.cursor()
    cur.execute(
        """SELECT code, open, close FROM klines
           WHERE code = ANY(%s) AND period='daily' AND ts <= %s
           ORDER BY code, ts DESC""",
        (codes, trade_date))
    by_code = {}
    for c, o, cl in cur.fetchall():
        by_code.setdefault(c, []).append((float(o) if o else 0, float(cl) if cl else 0))
    cur.close(); conn.close()
    out = {}
    for code, series in by_code.items():
        recent = series[:5]
        if len(recent) < 5: continue
        up = sum(1 for o, cl in recent if cl > o)
        out[code] = up / 5.0
    return out


COMPUTERS = {
    # Phase A · 3 因子
    "pe_inv": _compute_pe_inv,
    "roe": _compute_roe,
    "momentum_12m_1m": _compute_momentum_12m_1m,
    # B2.1 · 财务 6 因子
    "pb_inv": _compute_pb_inv,
    "roa": _make_akshare_factor("get_roa"),
    "gross_margin": _make_akshare_factor("get_gross_margin"),
    "debt_ratio_inv": _compute_debt_ratio_inv,
    "revenue_growth_yoy": _make_akshare_factor("get_revenue_growth"),
    "earnings_growth_yoy": _make_akshare_factor("get_earnings_growth"),
    # B2.2 · K 线 7 因子
    "momentum_1m": _compute_momentum_1m,
    "momentum_6m": _compute_momentum_6m,
    "ma_align": _compute_ma_align,
    "macd": _compute_macd,
    "rsi": _compute_rsi,
    "vol_20d_inv": _compute_vol_20d_inv,
    "candle_5d": _compute_candle_5d,
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
    # float() 转 np.float64/np.int64 · psycopg2 不认 numpy 类型
    rows = [(trade_date, factor_key, c, "A",
             float(raw[c]),
             float(z[c]) if c in z else None,
             float(rank[c]) if c in rank else None) for c in raw]
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
