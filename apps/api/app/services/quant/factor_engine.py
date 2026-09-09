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

def _compute_momentum_12m_1m(codes: list[str], trade_date: date, params=None) -> dict[str, float]:
    """12M-1M 动量 · 剔除最近 1 月的 11 月涨幅
    历史充足时用 close[-(skip+1)]/close[-window] · 不足时降级(数据回填有限 · 降级保证有数据)
    回看窗口与剔除天数可配(默认 243 / 21)。
    """
    p = params_of("momentum_12m_1m", params)
    win, skip = int(p["window"]), int(p["skip"])
    kl = _fetch_klines_close(codes, trade_date, back_days=max(400, int(win * 1.6)))
    out: dict[str, float] = {}
    for code, series in kl.items():
        closes = [c for _, c in series if c is not None and c > 0]
        if len(closes) < 60:
            continue
        # 降级只在**历史真的不够**时发生。
        #
        # 原来这里写的是 `min(-22, -(len//3))` —— min 取的是更负的那个,
        # 所以只要有 66 根以上 K 线,它永远选 -(len//3):270 根历史时
        # "剔除最近 1 月"变成了"剔除最近 90 个交易日",算出来的是
        # 「90 天前往前推 89 天的涨幅」,和因子名写的完全不是一回事。
        # 更糟的是 skip 参数在这个式子下几乎永远不生效 —— 用户把
        # 「剔除近期」从 21 调到 0,结果一个数都不变,又是一个假参数。
        skip_eff = skip if len(closes) > skip + 30 else max(1, len(closes) // 3)
        recent_idx = -(skip_eff + 1)
        past_idx = -min(len(closes) - abs(recent_idx) - 1, win)
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

def _compute_momentum_1m(codes, trade_date, params=None):
    """1 月动量 · 反向(短期均值回归 · 反向 IC · factor_defs 里 reverse=True)
    回看窗口可配(默认 21 个交易日 ≈ 1 个月)。
    """
    n = int(params_of("momentum_1m", params)["window"])
    kl = _fetch_klines_close(codes, trade_date, back_days=max(45, n * 2))
    out = {}
    for code, series in kl.items():
        closes = [c for _, c in series if c is not None and c > 0]
        if len(closes) < n + 1: continue
        out[code] = closes[-1] / closes[-(n + 1)] - 1
    return out


def _compute_momentum_6m(codes, trade_date, params=None):
    """6 月动量 · 近 N 交易日涨幅(默认 120)· 历史不足时用现有全部(降级)"""
    n0 = int(params_of("momentum_6m", params)["window"])
    kl = _fetch_klines_close(codes, trade_date, back_days=max(180, int(n0 * 1.6)))
    out = {}
    for code, series in kl.items():
        closes = [c for _, c in series if c is not None and c > 0]
        if len(closes) < 60: continue
        n = min(n0, len(closes) - 1)
        out[code] = closes[-1] / closes[-n - 1] - 1
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
#
# ── 加参数之前必须知道的三条(2026-09-09 补)────────────────────────
#
# 1. **线性变换的参数是假参数。** 打分链路最后要过 `_winsorize_zscore`,
#    而 z-score 会把 `a·x + b`(a>0)重新归一成和 x 完全一样的分布 ——
#    截面排名一个位置都不变。所以「把 RSI 超卖线从 30 调到 20」如果只是
#    改了归一化的分母,用户调完选出来的还是同一批票,回测数字一模一样。
#    参数要真的生效,必须**非线性**:截断(clip)、门槛(剔除/并档)、
#    改窗口长度、改取数口径。新增参数时先问自己"它改的是排序还是刻度",
#    只改刻度的不要放进来 —— 那是假功能,比没有更糟。
#
# 2. **只给 LOCAL_ONLY 的因子加参数。** 调参会走 `compute_z_live` 实时重算,
#    基本面因子(AKSHARE_ONLY)每只票要打一次 AKShare、300 只是分钟级,
#    用户在工作台拖一下参数就把 /scan 拖到超时。启动自检 `_check_params()`
#    会拦这种情况。
#
# 3. **默认值必须与"没有这个参数时"的老口径完全等价。** 定时任务用默认参数
#    算完落 `factor_value` 表,默认值一变,库里的历史值就和新口径对不上了
#    (回测跨着这条线会出现无法解释的断层)。要变口径就明说,并重算该因子。
#
# 没登记的因子 = 没有可调参数(比如 dividend_yield 就是分红除以股价,
# 没什么可调的;pe_inv / pb_inv 这些能调的其实是异常值上限,但它们是
# AKSHARE_ONLY,受第 2 条约束不放进来)。
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
    # 动量三兄弟 · 「回看多少天」本来写死在函数里,界面上看不见也改不了
    "momentum_1m": [
        {"key": "window", "label": "回看窗口", "default": 21,
         "min": 5, "max": 60, "step": 1, "unit": "交易日",
         "hint": "拿今天的收盘价和多少个交易日前比。21 ≈ 1 个月。这是反向因子,涨多了反而扣分。"},
    ],
    "momentum_6m": [
        {"key": "window", "label": "回看窗口", "default": 120,
         "min": 20, "max": 250, "step": 5, "unit": "交易日",
         "hint": "拿今天的收盘价和多少个交易日前比。120 ≈ 6 个月。历史不足时自动用现有全部。"},
    ],
    "momentum_12m_1m": [
        {"key": "window", "label": "回看窗口", "default": 243,
         "min": 120, "max": 500, "step": 1, "unit": "交易日",
         "hint": "总共回看多少个交易日。243 ≈ 12 个月。"},
        {"key": "skip", "label": "剔除近期", "default": 21,
         "min": 0, "max": 60, "step": 1, "unit": "交易日",
         "hint": "掐掉最近多少个交易日不算 —— 学术上这一段是短期反转,"
                 "留着会把动量效应抵消掉。设 0 就退化成普通 12 月动量。"},
    ],
    "candle_5d": [
        {"key": "window", "label": "统计窗口", "default": 5,
         "min": 2, "max": 30, "step": 1, "unit": "日",
         "hint": "数最近多少天里有几根阳线。窗口越短越像「这两天的情绪」。"},
    ],
    # F-1 · 纯日线 7 因子
    "vol_ratio_20": [
        {"key": "window", "label": "均量窗口", "default": 20,
         "min": 5, "max": 120, "step": 1, "unit": "日",
         "hint": "今日成交量除以之前多少天的平均成交量。"},
    ],
    "high52_prox": [
        {"key": "window", "label": "回看窗口", "default": 250,
         "min": 60, "max": 500, "step": 5, "unit": "日",
         "hint": "在多少天里找最高价。250 日 ≈ 52 周,120 日 ≈ 半年。"},
        {"key": "near_pct", "label": "贴近阈值", "type": "float", "default": 100.0,
         "min": 1.0, "max": 100.0, "step": 0.5, "unit": "%",
         "hint": "只在「距高点 X% 以内」这一段里区分强弱。设 10 = 距高点 10% 以内的才算"
                 "临近新高,再远的按下面那项处理。默认 100 = 不设阈值,"
                 "退化成直接比 收盘/最高(和以前完全一样)。"},
        {"key": "outside", "label": "超出阈值的", "type": "select", "default": "floor",
         "options": [{"value": "floor", "label": "并到最低分"},
                     {"value": "drop", "label": "不打分(当筛选条件用)"}],
         "unit": "",
         "hint": "「并到最低分」= 距高点太远的一律 0 分,仍参与排名,只是分不出高下;"
                 "「不打分」= 这只票在本因子上视同没有数据 —— 相当于把因子当筛选条件,"
                 "但注意有数据的因子不足一半的股票会被整体剔除,阈值卡太紧可能一只都选不出来。"},
    ],
    "turnover_20": [
        {"key": "window", "label": "换手窗口", "default": 20,
         "min": 5, "max": 120, "step": 1, "unit": "日",
         "hint": "平均多少天的成交股数再除以总股本。"},
        {"key": "max_turnover_pct", "label": "换手率上限", "type": "float", "default": 20.0,
         "min": 1.0, "max": 50.0, "step": 0.5, "unit": "%",
         "hint": "日均换手率超过这个数的直接丢掉(不打分)。它挡的是上游财务字段算错股本"
                 "导致的离谱值 —— 沪深 300 / 中证 500 成分股真实日均换手极少超过 15%。"},
    ],
    "size_inv": [
        {"key": "mcap_min_yi", "label": "市值下限", "default": 20,
         "min": 1, "max": 2000, "step": 1, "unit": "亿元",
         "hint": "总市值低于这个数的不打分。既是小盘的下界,也挡掉股本估错的异常值。"},
        {"key": "mcap_max_yi", "label": "市值上限", "default": 50000,
         "min": 100, "max": 200000, "step": 100, "unit": "亿元",
         "hint": "总市值高于这个数的不打分。想只在中小盘里选,把它调到 500 这一档。"},
    ],
    "amihud_20": [
        {"key": "window", "label": "窗口", "default": 20,
         "min": 5, "max": 120, "step": 1, "unit": "日",
         "hint": "平均多少天的 |日收益| / 成交额。"},
    ],
    "beta_60": [
        {"key": "window", "label": "回归窗口", "default": 60,
         "min": 20, "max": 250, "step": 5, "unit": "日",
         "hint": "用多少天的日收益对沪深 300 做回归。短了噪音大,长了滞后。"},
    ],
    "ret_skew_60": [
        {"key": "window", "label": "窗口", "default": 60,
         "min": 20, "max": 250, "step": 5, "unit": "日",
         "hint": "用多少天的日收益算偏度。少于 60 天的偏度很不稳定。"},
    ],
}


def param_type(p: dict) -> str:
    """一项参数是什么类型 · 老条目没写 type 就按默认值推断(int / float)"""
    t = p.get("type")
    if t:
        return t
    return "int" if isinstance(p["default"], int) else "float"


def params_of(key: str, override: dict | None = None) -> dict:
    """默认参数 + 用户覆盖。

    **只认注册表里登记过的键** —— 野字段直接忽略,不让它穿进计算。
    越界的值夹到 [min, max] 而不是报错:参数是给人调的,
    调过头给他一个最接近的合法值,比弹错误框好。

    select 型(如 high52_prox 的「超出阈值的怎么办」)不在选项里的值
    **回落到默认**,不夹取 —— 枚举没有"最接近"这回事,猜错了会让
    用户以为自己选的那种行为生效了。
    """
    spec = FACTOR_PARAMS.get(key) or []
    out = {p["key"]: p["default"] for p in spec}
    if not override:
        return out
    for p in spec:
        v = override.get(p["key"])
        if v is None:
            continue
        t = param_type(p)
        if t == "select":
            allowed = {o["value"] for o in p.get("options", [])}
            if v in allowed:
                out[p["key"]] = v
            continue
        try:
            v = int(v) if t == "int" else float(v)
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

    打分:以中位线为 0,越超卖分越高、越超买分越低,**在超买/超卖线处封顶**
    (分段映射,与 factor_defs 里写的「RSI14 分段映射」一致)。

    ⚠ 封顶这一步不是美化,是让参数真的生效。
    原来只写 `(mid - rsi) / half`,那是个线性变换 —— 后面 `_winsorize_zscore`
    会把线性变换重新归一成同一个分布,**截面排名一个位置都不变**。
    也就是说用户把超卖线从 30 调到 20,选出来的还是同一批票、
    回测数字一模一样,而界面告诉他"已自定义"。
    截断之后,超卖线以下的股票一律 +1 并列、超买线以上一律 −1 并列,
    区分只发生在两线之间 —— 线一动,谁进谁出就真的变了。

    这也意味着默认参数下的 rsi 值与 2026-09-09 之前落库的口径不同
    (以前不封顶)。历史 factor_value 要重算才能和新值对齐。
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
        # 中位 → 0 · 到超卖线 → +1 · 到超买线 → −1 · 两线之外封顶并列
        out[code] = float(max(-1.0, min(1.0, (mid - rsi) / half)))
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


def _compute_candle_5d(codes, trade_date, params=None):
    """近 N 日阳线数 / N(默认 5)"""
    n = int(params_of("candle_5d", params)["window"])
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
        recent = series[:n]
        if len(recent) < n: continue
        up = sum(1 for o, cl in recent if cl > o)
        out[code] = up / float(n)
    return out


# ═══════════════════════════════════════════════════════════════
# F-1 · 纯日线 7 因子(2026-09-09 · 点名:量比 / 52 周高点 / 换手与 Amihud / 规模 / 贝塔 / 偏度)
# ═══════════════════════════════════════════════════════════════
#
# 全部只读本地表(klines + financial_metric),不联网,归 LOCAL_ONLY。
# 两条口径说明 —— 写在这里,也写在 factor_defs 的 desc 里让界面看得到:
#
#   · klines 没有成交额字段。Amihud 里的"成交额"用 volume × 100 × close 近似
#     (腾讯源 volume 单位是**手**,1 手 = 100 股;实测 600519 日 volume 约 2 万,
#     对应 200 万股,量级吻合)。这是近似不是编造,且只影响绝对值,截面排序不变;
#     但因子 desc 必须说清楚,不能叫"成交额"。
#   · klines / financial_metric 都没有股本。总股本 ≈ 总资产 × (1 − 负债率) / 每股净资产
#     (= 净资产 / BPS),三项都在 financial_metric,取各自 trade_date 之前最近一期。
#     任一项缺或非正 → 该股票**不在返回里**(不打分),不用默认值凑。
#     对比 _compute_ev_ebitda_inv 里"5 亿股近似"那种做法 —— 那个会让所有股票市值
#     同比例失真,这里宁可少算几只。CLAUDE.md:空的比假的好。
#   · beta_60 依赖 klines 里的沪深 300(code='000300',由 index_kline 落库)。
#     指数历史不足窗口时**整个因子返回空**(界面标灰),不拿别的东西凑。

_LOT = 100.0   # 腾讯源 volume 单位为「手」· 1 手 = 100 股


def _shares_traded(code: str, volume: float) -> float:
    """把 klines.volume 换成「股」。

    2026-09-09 实测(库里 2026-09-08 这天各板块 volume 中位数):
    主板 / 创业板 约 12 万 ~ 53 万,**科创板(688)约 1470 万** —— 差两个量级。
    对照真实成交量:600519 约 175 万股/日、688981 约 2800 万股/日,
    说明腾讯源对 **688 给的是「股」,其他板块给的是「手」**。local_kline 没做归一。
    不归一的后果:688 的换手率被高估 100 倍(实测中芯国际日换手 1112%),
    Amihud 被低估 100 倍。这里统一换成股,在因子层修正,不动 klines 存量。
    """
    return volume if code.startswith("688") else volume * _LOT


# 估算股本 / 市值 / 换手的合理性边界 —— 越界说明上游财务字段错了(实测 600941 的 bps 给成 3.02,
# 真值约 65;688981 的 bps 给成 659),按 CLAUDE.md「空的比假的好」丢掉该股票,而不是让它进截面排名
_BPS_RANGE = (0.3, 300.0)          # 每股净资产(元)
# ⚠ 下面两个现在是 FACTOR_PARAMS 里 size_inv / turnover_20 那几项的**默认值来源**
#   (用户可以在工作台改,但改的是他自己那次计算;定时任务落库仍走这里的默认)。
#   两处必须一致,`_check_params()` 会在启动时比对 —— 不一致意味着默认口径
#   悄悄变了,而 factor_value 表里的历史值还是按老常量算的。
_MCAP_RANGE = (2e9, 5e12)          # 总市值(元):20 亿 ~ 5 万亿(工商银行约 2.9 万亿)
_TURNOVER_MAX = 0.2                # 日均换手率上限 20%(hs300/zz500 成分股真实值极少超 15%;
                                   # 实测 002027 因 bps 错成 42.6 算出 44.8%,0.5 挡不住,收到 0.2)


def _fetch_klines_ohlcv(codes, trade_date, back_days):
    """klines 取 (ts, high, close, volume) · 按 code 分组 · ts 升序"""
    conn = get_conn(); cur = conn.cursor()
    cur.execute(
        """SELECT code, ts, high, close, volume FROM klines
           WHERE code = ANY(%s) AND period='daily' AND ts <= %s AND ts >= %s
           ORDER BY code, ts""",
        (codes, trade_date, trade_date - timedelta(days=back_days + 30)))
    out = {}
    for code, ts, hi, cl, vol in cur.fetchall():
        out.setdefault(code, []).append((
            ts,
            float(hi) if hi is not None else None,
            float(cl) if cl is not None else None,
            float(vol) if vol is not None else None,
        ))
    cur.close(); conn.close()
    return out


def _estimate_shares(codes, trade_date):
    """总股本(股)≈ 总资产 × (1 − 负债率%) / 每股净资产 · 三项来自 financial_metric

    任一项缺或非正 → 该股票不在返回里。三项各取 trade_date 之前最近一期,
    绝大多数情况是同一份财报;偶尔错期(某项那期为 nan)误差在个位数百分比,
    对 −ln(市值) 和换手率的截面排序影响可忽略。
    """
    from app.services.quant import financial_store as fs
    ta = fs.read_metric(codes, "total_asset", trade_date)
    dr = fs.read_metric(codes, "debt_ratio", trade_date)
    bps = fs.read_metric(codes, "bps", trade_date)
    out = {}
    for c in codes:
        a, d, b = ta.get(c), dr.get(c), bps.get(c)
        if a is None or d is None or b is None:
            continue
        if a <= 0 or b <= 0 or not (0 <= d < 100):
            continue
        if not (_BPS_RANGE[0] <= b <= _BPS_RANGE[1]):
            continue                                   # bps 离谱 = 上游字段错,丢
        equity = a * (1 - d / 100.0)
        if equity <= 0:
            continue
        out[c] = equity / b
    return out


def _compute_vol_ratio_20(codes, trade_date, params=None):
    """量比 · 最近 1 日成交量 / 之前 N 日平均成交量(默认 20)"""
    n = int(params_of("vol_ratio_20", params)["window"])
    kl = _fetch_klines_ohlcv(codes, trade_date, back_days=max(45, n * 2))
    out = {}
    for code, series in kl.items():
        vols = [v for _, _, _, v in series if v is not None and v > 0]
        if len(vols) < n + 1: continue
        base = sum(vols[-(n + 1):-1]) / n
        if base > 0:
            out[code] = vols[-1] / base
    return out


def _compute_high52_prox(codes, trade_date, params=None):
    """52 周高点距离 · 越接近高点越强势。三个参数(回看窗口 / 贴近阈值 / 超阈处理)。

    距离用「差多少个百分点」表示:gap = 1 − 收盘 / 窗口内最高价,
    gap=0 就是站在新高上,gap=0.08 就是离高点还差 8%。

    打分(near = 贴近阈值 / 100):

        gap ≤ near → 1 − gap / near      # 站在高点 = 1 · 正好在阈值上 = 0
        gap > near → 并到 0 分,或者不打分(看 outside)

    near = 1(默认 100%)时 gap 恒 ≤ near,式子化简成 1 − gap = 收盘/最高 ——
    **和 2026-09-09 之前完全一样**,所以默认口径没变、库里的历史值仍然对得上。

    把阈值调小才是用户真正要的那件事:比如设 5%,那么"离高点 6%"和
    "离高点 40%"一样都是 0 分,排名只在距高点 5% 以内的股票之间发生。
    这是个截断(非线性),z-score 吃不掉它 —— 阈值一动,选出来的票真的会变。

    历史不足 N 日但 ≥ 120 日时用现有全部(降级,同 momentum_12m_1m 的做法)。
    """
    p = params_of("high52_prox", params)
    n = int(p["window"])
    near = float(p["near_pct"]) / 100.0
    outside = p["outside"]
    kl = _fetch_klines_ohlcv(codes, trade_date, back_days=int(n * 1.6))
    out = {}
    for code, series in kl.items():
        rows = [(h, c) for _, h, c, _ in series
                if h is not None and c is not None and h > 0 and c > 0]
        if len(rows) < 120: continue
        window = rows[-n:]
        hi = max(h for h, _ in window)
        if hi <= 0:
            continue
        gap = 1.0 - window[-1][1] / hi        # 距高点几个百分点(0 = 就在高点上)
        # 1e-9 是给浮点留的余量:用户设 5%、某只票算出来 0.050000000000000004,
        # 落在阈值外会让"距高点正好 5%"这只票凭空消失,而界面上完全解释不了
        if gap <= near + 1e-9:
            out[code] = 1.0 - gap / near
        elif outside == "floor":
            out[code] = 0.0
        # outside == "drop":这只票在本因子上视同没有数据,不进 out
    return out


def _compute_turnover_20(codes, trade_date, params=None):
    """N 日平均换手率 · mean(成交量 × 100) / 估算总股本(见 _estimate_shares)
    上限可配(默认 20%,即 _TURNOVER_MAX)· 越界丢该股票,不截断到边界。
    """
    p = params_of("turnover_20", params)
    n = int(p["window"])
    t_max = float(p["max_turnover_pct"]) / 100.0
    shares = _estimate_shares(codes, trade_date)
    if not shares:
        return {}
    kl = _fetch_klines_ohlcv(list(shares), trade_date, back_days=max(45, n * 2))
    out = {}
    for code, series in kl.items():
        vols = [v for _, _, _, v in series if v is not None and v > 0]
        if len(vols) < n: continue
        traded = _shares_traded(code, sum(vols[-n:]) / n)
        t = traded / shares[code]
        if 0 < t <= t_max:
            out[code] = t
    return out


def _compute_amihud_20(codes, trade_date, params=None):
    """Amihud 非流动性 · mean(|日收益| / 日成交额) · 成交额 ≈ volume × 100 × close(见块头说明)

    × 1e9 只是让数字可读(每十亿元成交推动的收益),z-score 后没有影响。
    值越大越难成交 → factor_defs 里 reverse=True(流动性好的分高)。
    """
    n = int(params_of("amihud_20", params)["window"])
    kl = _fetch_klines_ohlcv(codes, trade_date, back_days=max(45, n * 2))
    out = {}
    for code, series in kl.items():
        rows = [(c, v) for _, _, c, v in series
                if c is not None and v is not None and c > 0 and v > 0]
        if len(rows) < n + 1: continue
        rows = rows[-(n + 1):]
        vals = []
        for (c0, _v0), (c1, v1) in zip(rows[:-1], rows[1:]):
            amt = _shares_traded(code, v1) * c1
            if amt > 0:
                vals.append(abs(c1 / c0 - 1) / amt)
        if len(vals) >= n // 2:
            out[code] = sum(vals) / len(vals) * 1e9
    return out


def _compute_size_inv(codes, trade_date, params=None):
    """规模(反向)· −ln(总市值) · 市值 = close × 估算总股本 · 小市值分高
    市值上下限可配(默认 20 亿 ~ 5 万亿,即 _MCAP_RANGE)· 越界不打分。
    把上限调到 500 亿就是"只在中小盘里选"。
    """
    import math
    p = params_of("size_inv", params)
    lo = float(p["mcap_min_yi"]) * 1e8
    hi = float(p["mcap_max_yi"]) * 1e8
    shares = _estimate_shares(codes, trade_date)
    if not shares:
        return {}
    kl = _fetch_klines_ohlcv(list(shares), trade_date, back_days=15)
    out = {}
    for code, series in kl.items():
        closes = [c for _, _, c, _ in series if c is not None and c > 0]
        if not closes: continue
        mcap = closes[-1] * shares[code]
        if lo <= mcap <= hi:
            out[code] = -math.log(mcap)
    return out


def _compute_beta_60(codes, trade_date, params=None):
    """市场贝塔 · 个股日收益对沪深 300(klines code='000300')日收益的回归斜率 · N 日窗口

    指数历史不足 N+1 日 → 整个因子返回空(界面标灰),不用别的指数凑。
    个股与指数按 ts 对齐,只用两边都有的交易日。
    """
    import numpy as np
    n = int(params_of("beta_60", params)["window"])
    idx = _fetch_klines_ohlcv(["000300"], trade_date, back_days=int(n * 1.6)).get("000300", [])
    idx_close = {ts: c for ts, _, c, _ in idx if c is not None and c > 0}
    if len(idx_close) < n + 1:
        log.warning("[factor_engine] beta_60: 沪深 300 只有 %d 日 K 线(需 ≥ %d)· 本次不产出"
                    " · 跑 index_kline.backfill('000300', ...) 补指数历史", len(idx_close), n + 1)
        return {}
    kl = _fetch_klines_ohlcv(codes, trade_date, back_days=int(n * 1.6))
    out = {}
    for code, series in kl.items():
        pairs = [(ts, c) for ts, _, c, _ in series if c is not None and c > 0 and ts in idx_close]
        if len(pairs) < n + 1: continue
        pairs = pairs[-(n + 1):]
        ci = np.array([c for _, c in pairs])
        cm = np.array([idx_close[ts] for ts, _ in pairs])
        r_i = np.diff(ci) / ci[:-1]
        r_m = np.diff(cm) / cm[:-1]
        var = r_m.var()
        # 指数日收益方差正常在 1e-5 ~ 1e-3 量级;小于 1e-10 说明指数序列几乎不动
        # (数据异常或被填充),此时 cov/var 是噪音放大器,不产出
        if var > 1e-10:
            out[code] = float(np.cov(r_i, r_m, bias=True)[0, 1] / var)
    return out


def _compute_ret_skew_60(codes, trade_date, params=None):
    """收益偏度 · N 日日收益分布的三阶标准化矩 · 负偏 = 暴跌尾部风险"""
    import numpy as np
    n = int(params_of("ret_skew_60", params)["window"])
    kl = _fetch_klines_ohlcv(codes, trade_date, back_days=int(n * 1.6))
    out = {}
    for code, series in kl.items():
        closes = np.array([c for _, _, c, _ in series if c is not None and c > 0])
        if len(closes) < n + 1: continue
        rets = np.diff(closes[-(n + 1):]) / closes[-(n + 1):-1]
        sd = rets.std()
        if sd > 0:
            out[code] = float(((rets - rets.mean()) ** 3).mean() / sd ** 3)
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
    # F-1 · 纯日线 7 因子(turnover_20 / size_inv 还读本地 financial_metric,仍不联网)
    "vol_ratio_20", "high52_prox", "turnover_20", "amihud_20",
    "size_inv", "beta_60", "ret_skew_60",
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
    # F-1 · 纯日线 7 因子
    "vol_ratio_20": _compute_vol_ratio_20,
    "high52_prox": _compute_high52_prox,
    "turnover_20": _compute_turnover_20,
    "amihud_20": _compute_amihud_20,
    "size_inv": _compute_size_inv,
    "beta_60": _compute_beta_60,
    "ret_skew_60": _compute_ret_skew_60,
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


def _check_params() -> list[str]:
    """参数注册表的三条硬约束 —— 违反了就是**假参数**,启动时直接 ERROR。

    界面上「已自定义」四个字是一句承诺:用户调了,选股就该跟着变。
    下面每一条不满足,这句承诺就是假的,而且从界面上完全看不出来:

      1. computer 不接 `params` → `compute_z_live` 捕到 TypeError 静默回退查表,
         调了等于没调。
      2. 因子不在 `LOCAL_ONLY` → 调参会触发实时重算,基本面因子每只票打一次
         AKShare,300 只是分钟级,/scan 直接超时。
      3. spec 字段不全(数值型缺 min/max/step、select 缺 options)→ 前端画不出
         输入框,或者画出一个夹不住的框。

    注意:**"参数是线性变换"这一条查不出来**(见 FACTOR_PARAMS 头注第 1 条),
    那个只能靠 review 和 selfcheck_factor_params.py 里的排序断言。
    """
    import inspect
    problems: list[str] = []
    for key, spec in FACTOR_PARAMS.items():
        fn = COMPUTERS.get(key)
        if not fn:
            problems.append(f"{key}: 注册了参数但没有 computer")
        elif "params" not in inspect.signature(fn).parameters:
            problems.append(f"{key}: computer 不接 params —— 用户调了不生效(假参数)")
        if key not in LOCAL_ONLY:
            problems.append(f"{key}: 不在 LOCAL_ONLY,调参会走实时重算打爆上游")
        for p in spec:
            t = param_type(p)
            miss = [f for f in ("key", "label", "default", "hint") if f not in p]
            if t == "select":
                if not p.get("options"):
                    miss.append("options")
                elif p["default"] not in {o["value"] for o in p["options"]}:
                    problems.append(f"{key}.{p.get('key')}: 默认值不在 options 里")
            else:
                miss += [f for f in ("min", "max", "step") if f not in p]
                if "min" in p and "max" in p and not (p["min"] <= p["default"] <= p["max"]):
                    problems.append(f"{key}.{p.get('key')}: 默认值不在 [min, max] 内")
            if miss:
                problems.append(f"{key}.{p.get('key')}: 缺字段 {miss}")

    # 参数默认值必须等于原来写死的常量 —— 不然默认口径悄悄变了,
    # 而 factor_value 表里的历史值是按老常量算的,回测跨这条线会出现断层
    for pkey, want, got in (
        ("turnover_20.max_turnover_pct", _TURNOVER_MAX * 100,
         params_of("turnover_20")["max_turnover_pct"]),
        ("size_inv.mcap_min_yi", _MCAP_RANGE[0] / 1e8, params_of("size_inv")["mcap_min_yi"]),
        ("size_inv.mcap_max_yi", _MCAP_RANGE[1] / 1e8, params_of("size_inv")["mcap_max_yi"]),
    ):
        if abs(float(want) - float(got)) > 1e-9:
            problems.append(f"{pkey}: 默认值 {got} 与常量 {want} 不一致")

    if problems:
        log.error("[factor_engine] 因子参数注册表有问题(界面会显示可调但实际不生效):%s",
                  problems)
    return problems


_check_coverage()
_check_params()

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
        # 该因子还没接参数 —— 不假装成功,交回查表。
        # 这条只该在开发期出现:`_check_params()` 启动时就会把它报成 ERROR。
        # 走到这里说明界面显示"已自定义"而结果用的是默认口径 —— 假参数。
        log.error("[factor_engine] %s 的 computer 不接 params · 回退默认口径"
                  " —— 用户以为调了,实际没调,去修 COMPUTERS 签名", factor_key)
        return {}
    if not raw:
        return {}
    z, _rank = _winsorize_zscore(raw)

    if len(_LIVE_CACHE) >= _LIVE_CACHE_MAX:
        _LIVE_CACHE.clear()
    _LIVE_CACHE[ck] = z
    return z
