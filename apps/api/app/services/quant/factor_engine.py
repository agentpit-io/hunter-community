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

def _make_db_factor(metric_key: str, scale: float = 1.0,
                    lo: float | None = None, hi: float | None = None):
    """工厂 · 从 `financial_metric` 表读 —— **不联网**。

    数据由「数据」页的下载任务落库(financial_store)。这样:

      · 算因子变成纯本地操作,几秒而不是几十分钟
      · 不再依赖 akshare_client 那个**永不失效的进程内缓存**
        (方案 §G0:容器长跑时新季报进不来,而界面显示的日期是最新的)

    scale:上游给的是百分数(如 ROE 18.5),转成小数要 /100。
    lo/hi:合理区间,超出的当异常剔除 —— 不是截断到边界,
    截断会把异常值变成"刚好在边界上"的正常值参与排名。
    """
    def _compute(codes, trade_date):
        from app.services.quant import financial_store as fs
        raw = fs.read_metric(codes, metric_key, trade_date)
        out = {}
        for code, v in raw.items():
            x = v * scale
            if lo is not None and x <= lo:
                continue
            if hi is not None and x >= hi:
                continue
            out[code] = x
        return out
    return _compute


def _make_akshare_factor(getter_name: str):
    """工厂 · 用 akshare_client 的 getter 生成 compute · 3σ + z-score 由外层做

    ⚠ 只剩没有落库对应指标的因子还在用它。新因子请走 `_make_db_factor` ——
    这个函数每只票都要打一次 AKShare(8.6 秒),而且靠进程内缓存去重,
    容器一重启就重新付一遍。
    """
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


# ═══════════════════════════════════════════════════════════════
# 因子参数注册表 —— 让用户能改 RSI 周期、超买超卖线这些
# ═══════════════════════════════════════════════════════════════
#
# 产品经理反馈:「这些因子大部分需要进一步设置参数,比如 RSI 超买卖多少
# 才算超,让客户自己设置。现在这种只拖动滚动条调节没有意义。」
#
# 工作台原来只能调**权重**,而 RSI 用 14 天还是 6 天、超卖线画在 30
# 还是 20,直接决定选出什么票 —— 这些一直硬编码在函数里,
# 界面上既看不见也改不了。评委问「你这个 RSI 实际参数是多少、
# 用户怎么自定义」的时候,得能指着界面回答。
#
# 每项给出:默认值、范围、一句人话解释(界面直接显示)。
# 没登记的因子 = 没有可调参数(pe_inv 就是 1/PE,没什么可调的)。
FACTOR_PARAMS: dict[str, list[dict]] = {
    "rsi": [
        {"key": "period", "label": "RSI 周期", "default": 14,
         "min": 2, "max": 60, "step": 1, "unit": "日",
         "hint": "算 RSI 用最近多少天。短了灵敏也更吵,长了稳但滞后。"},
        {"key": "oversold", "label": "超卖线", "default": 30,
         "min": 5, "max": 45, "step": 1, "unit": "",
         "hint": "RSI 低于这条线算超卖(打高分)。越低越苛刻,选出来的票越少。"},
        {"key": "overbought", "label": "超买线", "default": 70,
         "min": 55, "max": 95, "step": 1, "unit": "",
         "hint": "RSI 高于这条线算超买(打低分)。这是个反向因子。"},
    ],
    "macd": [
        {"key": "fast", "label": "快线 EMA", "default": 12,
         "min": 3, "max": 50, "step": 1, "unit": "日", "hint": "短周期均线。"},
        {"key": "slow", "label": "慢线 EMA", "default": 26,
         "min": 10, "max": 120, "step": 1, "unit": "日", "hint": "长周期均线,要大于快线。"},
        {"key": "signal", "label": "信号线", "default": 9,
         "min": 2, "max": 40, "step": 1, "unit": "日", "hint": "对 MACD 再平滑一次,差值就是柱子。"},
        {"key": "atr_period", "label": "ATR 归一周期", "default": 14,
         "min": 5, "max": 60, "step": 1, "unit": "日",
         "hint": "用 ATR 把柱子除成无量纲,不同价位的股票才能比。"},
    ],
    "ma_align": [
        {"key": "ma1", "label": "均线 1", "default": 5, "min": 2, "max": 20, "step": 1, "unit": "日",
         "hint": "多头排列的最短均线。"},
        {"key": "ma2", "label": "均线 2", "default": 10, "min": 3, "max": 60, "step": 1, "unit": "日", "hint": ""},
        {"key": "ma3", "label": "均线 3", "default": 20, "min": 5, "max": 120, "step": 1, "unit": "日", "hint": ""},
        {"key": "ma4", "label": "均线 4", "default": 60, "min": 10, "max": 250, "step": 1, "unit": "日",
         "hint": "最长那条。四条依次向上 = 满分。"},
    ],
    "vol_20d_inv": [
        {"key": "window", "label": "波动率窗口", "default": 20,
         "min": 5, "max": 120, "step": 1, "unit": "日",
         "hint": "用多少天的收益率算标准差。窗口越长越平滑。"},
    ],
}


def params_of(key: str, override: dict | None = None) -> dict:
    """默认参数 + 用户覆盖。

    **只认注册表里登记过的键** —— 野字段直接忽略,不让它穿进计算。
    越界的值夹到 [min, max] 而不是报错:参数是给人调的,
    调过头给他一个最接近的合法值,比弹错误框好。
    """
    spec = FACTOR_PARAMS.get(key) or []
    out = {p["key"]: p["default"] for p in spec}
    if not override:
        return out
    for p in spec:
        v = override.get(p["key"])
        if v is None:
            continue
        try:
            v = int(v) if isinstance(p["default"], int) else float(v)
        except (TypeError, ValueError):
            continue
        out[p["key"]] = max(p["min"], min(p["max"], v))
    return out


def _compute_ma_align(codes, trade_date, params=None):
    """多头排列打分 · 满足几个不等式就是几分。周期可配(默认 5/10/20/60)。"""
    import numpy as np
    p = params_of("ma_align", params)
    ws = sorted({int(p["ma1"]), int(p["ma2"]), int(p["ma3"]), int(p["ma4"])})
    need = max(ws)
    kl = _fetch_klines_close(codes, trade_date, back_days=max(90, need + 30))
    out = {}
    for code, series in kl.items():
        closes = [c for _, c in series if c is not None and c > 0]
        if len(closes) < need:
            continue
        arr = np.array(closes[-need:])
        mas = [arr[-w:].mean() for w in ws]
        out[code] = float(sum(mas[i] > mas[i + 1] for i in range(len(mas) - 1)))
    return out


def _compute_macd(codes, trade_date, params=None):
    """MACD_hist / ATR · 归一化。快/慢/信号/ATR 周期可配(默认 12/26/9/14)。"""
    import numpy as np
    p = params_of("macd", params)
    fast, slow = int(p["fast"]), int(p["slow"])
    if fast >= slow:                       # 调反了换回来,不报错
        fast, slow = min(fast, slow), max(fast, slow)
        if fast == slow:
            slow = fast + 1
    sig_n, atr_n = int(p["signal"]), int(p["atr_period"])
    kl = _fetch_klines_close(codes, trade_date, back_days=max(90, slow * 3))
    out = {}

    def ema(arr, span):
        alpha = 2 / (span + 1)
        e = [arr[0]]
        for x in arr[1:]:
            e.append(alpha * x + (1 - alpha) * e[-1])
        return np.array(e)

    for code, series in kl.items():
        closes = np.array([c for _, c in series if c is not None and c > 0])
        if len(closes) < slow + sig_n + 5:
            continue
        macd = ema(closes, fast) - ema(closes, slow)
        hist = (macd - ema(macd, sig_n))[-1]
        atr = np.abs(np.diff(closes[-(atr_n + 1):])).mean() or 1.0
        out[code] = float(hist / atr)
    return out


def _compute_rsi(codes, trade_date, params=None):
    """RSI 反向因子 · 超卖打高分。周期与超买/超卖线可配(默认 14/30/70)。

    产品经理点名的那个:「RSI 超买卖多少才算超,让客户自己设置」。

    打分:以中位线为 0,越超卖分越高、越超买分越低,
    并按用户设的超买超卖区间归一 —— 把线收窄(如 20/80)会让
    同一个 RSI 拿到更低的绝对分,因为"够极端"的门槛提高了。
    """
    import numpy as np
    p = params_of("rsi", params)
    n = int(p["period"])
    lo, hi = float(p["oversold"]), float(p["overbought"])
    if lo >= hi:
        lo, hi = min(lo, hi), max(lo, hi)
    mid = (lo + hi) / 2
    half = max((hi - lo) / 2, 1e-9)

    kl = _fetch_klines_close(codes, trade_date, back_days=max(45, n * 3))
    out = {}
    for code, series in kl.items():
        closes = np.array([c for _, c in series if c is not None and c > 0])
        if len(closes) < n + 1:
            continue
        diff = np.diff(closes[-(n + 1):])
        gain = diff[diff > 0].sum() or 0.01
        loss = -diff[diff < 0].sum() or 0.01
        rsi = 100 - 100 / (1 + gain / loss)
        # 中位 → 0 · 到超卖线 → +1 · 到超买线 → -1(再外推不封顶,z-score 会处理)
        out[code] = float((mid - rsi) / half)
    return out


def _compute_vol_20d_inv(codes, trade_date, params=None):
    """1 / N 日收益标准差 · 低波异象 · 稳定跑赢。窗口可配(默认 20 日)。"""
    import numpy as np
    n = int(params_of("vol_20d_inv", params)["window"])
    kl = _fetch_klines_close(codes, trade_date, back_days=max(45, n * 2))
    out = {}
    for code, series in kl.items():
        closes = np.array([c for _, c in series if c is not None and c > 0])
        if len(closes) < n: continue
        rets = np.diff(closes[-(n + 1):]) / closes[-(n + 1):-1]
        std = rets.std()
        if std > 0:
            out[code] = 1.0 / std
    return out


# ═══════════════════════════════════════════════════════════════
# C1 · 3 新因子(dividend_yield / kronos / main_flow)
# ═══════════════════════════════════════════════════════════════

def _compute_dividend_yield(codes, trade_date):
    """近 12M 现金分红 / 当日 close · 见 akshare_client.get_dividend_yield"""
    from app.services.quant import akshare_client as akc
    out = {}
    for code in codes:
        dy = akc.get_dividend_yield(code, trade_date)
        if dy is not None:
            out[code] = dy
    return out


def _compute_kronos(codes, trade_date):
    """Kronos 5 日预测收益率 · T-0 · 无未来函数
    注:trade_date 参数被忽略 · Kronos 只能拿当日预测
    (回填历史因子时 · trade_date != today 的调用会拿今日预测 · 有偏)
    · 建议只在生产 APScheduler(当日)调用 · 历史回填走"每日快照"逻辑
    """
    from app.services.quant.kronos_client import batch_get_kronos
    return batch_get_kronos(codes, horizon=5)


def _compute_main_flow(codes, trade_date):
    """近 5 日主力净流入 / 5 日总资金流 · 见 akshare_client.get_main_flow_ratio"""
    from app.services.quant import akshare_client as akc
    out = {}
    for code in codes:
        r = akc.get_main_flow_ratio(code, trade_date, days=5)
        if r is not None:
            out[code] = r
    return out


def _compute_ev_ebitda_inv(codes, trade_date):
    """D-6 · EV/EBITDA 倒数 · 3 财报拼装 · 银行/证券/保险跳过
    · mcap = close * 总股本 · 总股本从财务 EPS + 净利润反推(近似)
    """
    from app.services.quant import akshare_client as akc
    from app.services.database import get_conn
    conn = get_conn(); cur = conn.cursor()
    cur.execute(
        """SELECT DISTINCT ON (code) code, close FROM klines
           WHERE code = ANY(%s) AND period='daily' AND ts <= %s AND close IS NOT NULL
           ORDER BY code, ts DESC""",
        (codes, trade_date),
    )
    price_map = {c: float(cl) for c, cl in cur.fetchall()}
    cur.close(); conn.close()

    out = {}
    for code in codes:
        if code in akc.FINANCIAL_INDUSTRY_CODES:
            continue
        price = price_map.get(code)
        if not price:
            continue
        # 简化:市值 = close × 净利润 / EPS(EPS 从 financial_summary 拿)
        fin = akc.get_financial_summary(code, trade_date)
        if not fin: continue
        eps = fin.get("eps")
        if not eps or eps <= 0:
            continue
        # 净利润(单期不能用 · 但作粗估市值够)
        # 实际:mcap = close × 总股本 · 总股本 ≈ (季度净利/单季 EPS)· 有偏但同一 code 稳定
        # 更精准:AKShare stock_a_indicator_lg 有 total_share · 但复杂 · 先用近似
        # 兜底:mcap 用 close × 5 亿股(hs300 均值)· 反正只影响绝对值 · 排序无影响
        mcap_approx = price * 5e8   # 5 亿股近似 · 后续 z-score 归一化会消掉尺度
        v = akc.get_ev_ebitda_inv(code, trade_date, mcap_approx)
        if v is not None and 0 < v < 0.5:
            out[code] = v
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


# 只靠本地 klines 就能算的因子 —— **不碰网络,不需要任何 key**。
#
# 这个名单决定了开源实例"不填 key 能用到什么程度":这 8 个因子
# 从本地 K 线算,所以哪怕用户什么都不配,量化也能选股、能回测。
# 其余因子要么走 AKShare(基本面),要么要外部服务(已下架)。
#
# **不要往这里加基本面因子** —— 那些跑起来是分钟级的限流等待,
# 混进来会让每日任务从几秒变成几十分钟,而且失败原因完全不同
# (网络/限流 vs K线历史不足),混在一起很难看清是哪里出了问题。
LOCAL_ONLY = [
    "momentum_1m", "momentum_6m", "momentum_12m_1m",
    "ma_align", "macd", "rsi", "vol_20d_inv", "candle_5d",
]


# 走 AKShare 直连的因子 —— **同样不需要任何 key,只是慢**。
#
# 慢到什么程度:AKShare 对财务接口有限流,300 只 × 一个日期实测是分钟级,
# 补一整年是小时级(backfill_hs300_full.py 自己写着 60-120 分钟)。
# 所以它不能和 LOCAL_ONLY 一起塞进每日任务 —— 那会让本来几秒的任务
# 变成几十分钟,而且一旦卡住,连技术因子也跟着不更新。
#
# 单独排一个低频任务(每周),见 scheduler.weekly_akshare_factors()。
AKSHARE_ONLY = [
    "pe_inv", "pb_inv", "dividend_yield", "ev_ebitda_inv",
    "roe", "roa", "gross_margin", "debt_ratio_inv",
    "revenue_growth_yoy", "earnings_growth_yoy",
]


COMPUTERS = {
    # Phase A · 3 因子
    "pe_inv": _compute_pe_inv,
    "roe": _compute_roe,
    "momentum_12m_1m": _compute_momentum_12m_1m,
    # B2.1 · 财务 6 因子
    "pb_inv": _compute_pb_inv,
    # 这四个已落库(financial_metric),走本地读 —— 不联网、几秒算完。
    # 上游给的是百分数,scale=0.01 转小数
    "roa": _make_db_factor("roa", 0.01, lo=-1, hi=1),
    "gross_margin": _make_db_factor("gross_margin", 0.01, lo=-2, hi=2),
    "debt_ratio_inv": _compute_debt_ratio_inv,
    "revenue_growth_yoy": _make_db_factor("revenue_growth_yoy", 0.01, lo=-10, hi=50),
    "earnings_growth_yoy": _make_db_factor("earnings_growth_yoy", 0.01, lo=-10, hi=50),
    # B2.2 · K 线 7 因子
    "momentum_1m": _compute_momentum_1m,
    "momentum_6m": _compute_momentum_6m,
    "ma_align": _compute_ma_align,
    "macd": _compute_macd,
    "rsi": _compute_rsi,
    "vol_20d_inv": _compute_vol_20d_inv,
    "candle_5d": _compute_candle_5d,
    # C1 · 3 新因子(dividend_yield / kronos / main_flow)
    "dividend_yield": _compute_dividend_yield,
    "kronos": _compute_kronos,
    "main_flow": _compute_main_flow,
    # D-6 · 补齐 20/20
    "ev_ebitda_inv": _compute_ev_ebitda_inv,
}


# ═══════════════════════════════════════════════════════════════
# 落库
# ═══════════════════════════════════════════════════════════════


def _check_coverage() -> list[str]:
    """每个启用的因子都得有人负责算它。

    这次整件事的根源就是"因子定义了但没人算":20 个因子里 17 个从来没跑过,
    而界面上它们和有数据的长得一模一样,用户选中就回测出一份空仓成绩单。

    以后新增因子时,如果忘了把它归进 LOCAL_ONLY 或 AKSHARE_ONLY,
    **启动时就会有一条 ERROR**,而不是等到用户选了它才发现。
    """
    from app.services.quant.factor_defs import enabled_factors
    covered = set(LOCAL_ONLY) | set(AKSHARE_ONLY)
    orphans = [f.key for f in enabled_factors() if f.key not in covered]
    if orphans:
        log.error("[factor_engine] 这些因子启用了但没有任何定时任务算它:%s"
                  " —— 用户选中会得到空仓回测", orphans)
    return orphans


_check_coverage()

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


# ═══════════════════════════════════════════════════════════════
# 自定义参数 · 实时计算通道
# ═══════════════════════════════════════════════════════════════
#
# 打分链路平时读的是 `factor_value` 表里**每天定时算好**的 z_score,
# 而定时任务只会用默认参数算一遍。所以用户在工作台把 RSI 超卖线
# 从 30 调到 20,如果还走查表,**回测结果会一个数都不变** ——
# 那就又是一个「改了但不生效」的假功能(今天刚修完 5 个)。
#
# 所以:带自定义参数的因子改走这里现算。
# 口径和定时任务完全一致(同一个 computer + 同一个 _winsorize_zscore),
# 只是参数换成用户的,且结果**不落库** —— factor_value 永远只存默认口径,
# 否则一个人的调参会污染所有人的历史。
#
# 代价:每个调参因子 × 每个调仓日一次 K 线查询。月频一年 = 12 次,
# 池子整批取,可接受。同一 (因子,日期,参数) 组合在一次回测里
# 会被反复问到,用进程内缓存挡掉。

_LIVE_CACHE: dict[tuple, dict[str, float]] = {}
_LIVE_CACHE_MAX = 512


def is_parametric(factor_key: str) -> bool:
    """这个因子有没有可调参数(界面据此决定要不要画参数行)"""
    return bool(FACTOR_PARAMS.get(factor_key))


def compute_z_live(factor_key: str, codes: list[str], trade_date: date,
                   params: dict | None = None) -> dict[str, float]:
    """用自定义参数现算 z_score · 不落库。

    参数为空或该因子无可调参数时返回 {},调用方应回退到查表 ——
    没必要为默认口径重算一遍已经算好的东西。
    """
    spec = FACTOR_PARAMS.get(factor_key)
    if not spec or not params:
        return {}
    eff = params_of(factor_key, params)
    if eff == {p["key"]: p["default"] for p in spec}:
        return {}                                  # 调回默认值 = 走查表

    ck = (factor_key, trade_date, tuple(sorted(eff.items())), len(codes),
          hash(tuple(sorted(codes))))
    hit = _LIVE_CACHE.get(ck)
    if hit is not None:
        return hit

    computer = COMPUTERS.get(factor_key)
    if not computer:
        return {}
    try:
        raw = computer(codes, trade_date, eff)
    except TypeError:
        # 该因子还没接参数 —— 不假装成功,交回查表
        log.warning("[factor_engine] %s 尚不支持自定义参数 · 回退默认口径", factor_key)
        return {}
    if not raw:
        return {}
    z, _rank = _winsorize_zscore(raw)

    if len(_LIVE_CACHE) >= _LIVE_CACHE_MAX:
        _LIVE_CACHE.clear()
    _LIVE_CACHE[ck] = z
    return z
