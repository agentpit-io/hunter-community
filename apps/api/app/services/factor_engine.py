"""factor_engine.py — Pro 多因子 K 线预测引擎。

计算 8 个因子并返回带综合评分的 Pro 预测结果。
所有因子输出范围 [-1, +1]，加权求和后得到综合评分。
"""
import os
import math
import asyncio
from datetime import datetime
import httpx
from loguru import logger
from app.services import finance_data_auth as _auth


# ---------------------------------------------------------------------------
# 权重配置
# ---------------------------------------------------------------------------

WEIGHTS = {
    'kronos':    0.10,
    'ma_align':  0.10,
    'macd':      0.08,
    'rsi':       0.07,
    'main_flow': 0.35,
    'flow_div':  0.15,
    'candle_5d': 0.05,
    'pe':        0.10,
}

FACTOR_LABELS = {
    'kronos':    'Kronos技术',
    'ma_align':  '均线趋势',
    'macd':      'MACD动量',
    'rsi':       'RSI超买卖',
    'main_flow': '主力净流入',
    'flow_div':  '筹码分布',
    'candle_5d': '近5日K线',
    'pe':        'PE估值',
}


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _ema(values: list[float], n: int) -> list[float]:
    k = 2 / (n + 1)
    r = [values[0]]
    for v in values[1:]:
        r.append(v * k + r[-1] * (1 - k))
    return r


# ---------------------------------------------------------------------------
# 因子函数（均返回 [-1, +1]）
# ---------------------------------------------------------------------------

def factor_ma_align(bars: list[dict]) -> float:
    """A3: MA5/10/20/60 多头排列得分。"""
    closes = [b['close'] for b in bars]
    if len(closes) < 60:
        return 0.0
    ma5  = sum(closes[-5:])  / 5
    ma10 = sum(closes[-10:]) / 10
    ma20 = sum(closes[-20:]) / 20
    ma60 = sum(closes[-60:]) / 60
    p = closes[-1]
    score = sum([p > ma5, ma5 > ma10, ma10 > ma20, ma20 > ma60]) / 4
    return score * 2 - 1  # [0,1] → [-1,1]


def factor_macd(bars: list[dict]) -> float:
    """A4: MACD bar / ATR14，裁剪到 [-1, 1]。"""
    closes = [b['close'] for b in bars]
    if len(closes) < 35:
        return 0.0
    e12 = _ema(closes, 12)
    e26 = _ema(closes, 26)
    macd_line = [e12[i] - e26[i] for i in range(len(closes))]
    signal = _ema(macd_line[25:], 9)
    bar_val = macd_line[-1] - signal[-1]
    # ATR14
    atrs = []
    for i in range(1, min(15, len(bars))):
        h  = bars[-i]['high']
        l  = bars[-i]['low']
        pc = bars[-i - 1]['close']
        atrs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = sum(atrs) / len(atrs) if atrs else closes[-1] * 0.02
    return max(-1.0, min(1.0, bar_val / atr))


def factor_rsi(bars: list[dict]) -> float:
    """A5: RSI14 超买超卖信号。"""
    closes = [b['close'] for b in bars]
    if len(closes) < 15:
        return 0.0
    diffs  = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [max(0,  d) for d in diffs[-14:]]
    losses = [max(0, -d) for d in diffs[-14:]]
    ag = sum(gains)  / 14
    al = sum(losses) / 14
    rsi = 100 - 100 / (1 + ag / al) if al > 0 else 100
    if rsi < 20: return  1.0
    if rsi < 30: return  0.6 + (30 - rsi) / 10 * 0.4
    if rsi < 50: return  (50 - rsi) / 20 * 0.6
    if rsi < 70: return -(rsi - 50) / 20 * 0.5
    if rsi < 80: return -0.7
    return -1.0


def factor_candle_ratio(bars: list[dict]) -> float:
    """C4: 近5日阳线比例。"""
    recent  = bars[-5:]
    bullish = sum(1 for b in recent if b['close'] >= b['open'])
    return {5: 0.8, 4: 0.5, 3: 0.1, 2: -0.3, 1: -0.7, 0: -0.7}[bullish]


def factor_main_flow(flow_5d: list[dict]) -> float:
    """B3: 5日主力净流入占比。"""
    total_main = sum(f.get('main_net', 0) for f in flow_5d)
    total_abs  = sum(
        abs(f.get('main_net', 0)) + abs(f.get('mid_net', 0)) + abs(f.get('small_net', 0))
        for f in flow_5d
    )
    if total_abs == 0:
        return 0.0
    ratio = total_main / total_abs  # 近似 [-1, 1]
    return max(-1.0, min(1.0, ratio * 2))


def factor_flow_divergence(flow_5d: list[dict]) -> float:
    """B5: 主力 vs 散户方向背离信号。"""
    main  = sum(f.get('main_net',  0) for f in flow_5d)
    small = sum(f.get('small_net', 0) for f in flow_5d)
    if abs(main) < 1:
        return 0.0
    main_dir  = 1 if main  > 0 else -1
    small_dir = 1 if small > 0 else -1
    if main_dir != small_dir:
        return main_dir * 0.6   # 背离：跟随主力
    return main_dir * 0.2       # 同向：信号较弱


def factor_pe(code: str, last_close: float = 0.0) -> float:
    """D1: 用 stock_financial_analysis_indicator 的年度 EPS 反算 PE 打分。

    缓存: 24h Redis · key=kpred:pe:{code}:{yyyymmdd} · 命中率接近 100%
      (PE 依赖年报 EPS · 一天内绝不会变 · last_close 用当日 close 天然带日期维度)
    """
    from app.services import kpred_cache as _cache
    bare = code.split('.')[0] if '.' in code else code
    _cache_key = f"kpred:pe:{bare}:{datetime.now().strftime('%Y%m%d')}"
    _cached = _cache.get(_cache_key)
    if _cached is not None and isinstance(_cached, dict) and "score" in _cached:
        logger.debug("[factor:pe:{}] cache hit · score={}", bare, _cached["score"])
        return float(_cached["score"])
    def _compute() -> float:
        try:
            if last_close <= 0:
                return 0.0
            import akshare as ak
            # 只拉去年一年财务数据(最新年报) · akshare 会按年分页串行拉取
            # 拉 4 年 = 4 次 HTTP × 2.5s = 10s · 但只用 iloc[-1] 一行(浪费 3 年)
            # 改成只拉 1 年 · factor_pe 从 ~10s 降到 ~2.5s
            start_year = str(datetime.now().year - 1)
            df = ak.stock_financial_analysis_indicator(symbol=bare, start_year=start_year)
            if df is None or df.empty:
                return 0.0
            annual = df[df['日期'].astype(str).str.endswith('12-31')]
            if annual.empty:
                annual = df   # 没有年报则退而用最新季报
            raw = str(annual.iloc[-1]['摊薄每股收益(元)']).replace(',', '').strip()
            if raw in ('', '-', '--', 'None', 'nan', 'NaN'):
                return 0.0
            eps = float(raw)
            if eps <= 0:
                return -0.3   # 亏损股
            pe_val = last_close / eps
            if pe_val < 12: return  0.9
            if pe_val < 20: return  0.5
            if pe_val < 30: return  0.1
            if pe_val < 45: return -0.3
            if pe_val < 70: return -0.6
            return -0.9
        except Exception:
            return 0.0

    score = _compute()
    _cache.set(_cache_key, {"score": score}, 24 * 3600)
    return score


def factor_kronos(pred_return: float, sigma: float, pred_len: int) -> float:
    """A1: Kronos 预测收益率归一化评分。"""
    expected_vol = sigma * (pred_len ** 0.5)
    if expected_vol == 0:
        return 0.0
    score = pred_return / expected_vol
    # 极端预测惩罚
    if abs(pred_return) > 0.20:
        score *= 0.5
    return max(-1.0, min(1.0, score))


# ---------------------------------------------------------------------------
# 合成函数
# ---------------------------------------------------------------------------

def get_daily_limit(code: str) -> float:
    """返回该股票的日涨跌幅限制（小数）。"""
    bare = code.split('.')[0] if '.' in code else code
    if bare.startswith(('300', '301', '688')):
        return 0.20   # 创业板 / 科创板
    if bare.startswith('8') or bare.startswith('43'):
        return 0.30   # 北交所
    return 0.10       # 主板（ST 暂按主板，不做名称查询）


def apply_daily_limit(
    predictions: list[dict],
    last_close: float,
    code: str,
) -> list[dict]:
    """
    等比例缩放每日涨跌幅，使最大单日变化 ≤ 涨跌停限制。
    保留每根 K 线的内部形态（上下影线比例不变），避免出现连续涨跌停。
    """
    limit = get_daily_limit(code)
    if not predictions:
        return predictions

    # 计算每根 K 线相对前一收盘的日涨跌幅
    closes = [last_close] + [b['close'] for b in predictions]
    daily_returns = [
        (closes[i] - closes[i - 1]) / closes[i - 1] if closes[i - 1] != 0 else 0
        for i in range(1, len(closes))
    ]

    max_abs = max(abs(r) for r in daily_returns) if daily_returns else 0

    # 未超限直接返回
    if max_abs <= limit:
        return predictions

    # 等比例缩放因子：最大日变化压缩到刚好等于限制
    scale = limit / max_abs

    result = []
    prev_c = last_close
    for i, bar in enumerate(predictions):
        new_close = round(prev_c * (1 + daily_returns[i] * scale), 2)
        # 保留 K 线内部形态（O/H/L 相对收盘的偏移量不变）
        new_open = round(new_close + (bar['open']  - bar['close']), 2)
        new_high = round(new_close + (bar['high']  - bar['close']), 2)
        new_low  = round(new_close + (bar['low']   - bar['close']), 2)
        new_high = max(new_high, new_open, new_close)
        new_low  = min(new_low,  new_open, new_close)
        result.append({**bar, 'open': new_open, 'high': new_high, 'low': new_low, 'close': new_close})
        prev_c = new_close

    return result


def scale_predictions(
    predictions: list[dict],
    last_close: float,
    factor_return: float,
) -> list[dict]:
    """保留 Kronos 形态，调整终点幅度（40% Kronos + 60% 因子）。"""
    if not predictions:
        return predictions
    kronos_end    = predictions[-1]['close']
    kronos_return = (kronos_end - last_close) / last_close if last_close > 0 else 0

    if abs(kronos_end - last_close) < last_close * 0.001:
        ratio = 1.0
    else:
        ratio = factor_return / kronos_return if abs(kronos_return) > 0.001 else 1.0

    blend_ratio = 0.4 + 0.6 * ratio

    scaled = []
    for bar in predictions:
        scaled.append({
            **bar,
            'open':  round(last_close + (bar['open']  - last_close) * blend_ratio, 2),
            'high':  round(last_close + (bar['high']  - last_close) * blend_ratio, 2),
            'low':   round(last_close + (bar['low']   - last_close) * blend_ratio, 2),
            'close': round(last_close + (bar['close'] - last_close) * blend_ratio, 2),
        })
    return scaled


def compute_composite(
    factors_dict: dict[str, float],
    weights: dict[str, float],
) -> tuple[float, str, str, str]:
    """加权求和 → score, rating, confidence, conflict_level。"""
    score = sum(factors_dict.get(k, 0) * w for k, w in weights.items())
    score = max(-1.0, min(1.0, score))

    directions = [
        1  if v >  0.05 else
        (-1 if v < -0.05 else 0)
        for v in factors_dict.values()
    ]
    bullish = directions.count(1)
    bearish = directions.count(-1)
    total   = len(directions)

    if bullish >= total * 0.7:
        conflict, confidence = '低', 82
    elif bearish >= total * 0.7:
        conflict, confidence = '低', 82
    elif abs(bullish - bearish) >= 2:
        conflict, confidence = '中', 62
    else:
        conflict, confidence = '高', 42

    if   score < -0.5:  rating = '强烈看空'
    elif score < -0.15: rating = '偏空'
    elif score <  0.15: rating = '中性观望'
    elif score <  0.5:  rating = '偏多'
    else:               rating = '强烈看多'

    return score, rating, f'{confidence}%', conflict


# ---------------------------------------------------------------------------
# 同步辅助：获取5日资金流向
# ---------------------------------------------------------------------------

def _parse_flow_rows(data: list[dict]) -> list[dict]:
    result = []
    for row in data:
        main_net  = (float(row.get('super_buy',  0) or 0)
                     - float(row.get('super_sell', 0) or 0)
                     + float(row.get('big_buy',   0) or 0)
                     - float(row.get('big_sell',  0) or 0))
        mid_net   = (float(row.get('mid_buy',    0) or 0)
                     - float(row.get('mid_sell',  0) or 0))
        small_net = (float(row.get('small_buy',  0) or 0)
                     - float(row.get('small_sell', 0) or 0))
        result.append({'main_net': main_net, 'mid_net': mid_net, 'small_net': small_net})
    return result


def _parse_xtick_flow(item: dict) -> dict:
    return {
        'main_net':  (float(item.get('buyMostAmount',  0) or 0)
                      - float(item.get('sellMostAmount', 0) or 0)
                      + float(item.get('buyBigAmount',   0) or 0)
                      - float(item.get('sellBigAmount',  0) or 0)),
        'mid_net':   (float(item.get('buyMediumAmount', 0) or 0)
                      - float(item.get('sellMediumAmount', 0) or 0)),
        'small_net': (float(item.get('buySmallAmount',  0) or 0)
                      - float(item.get('sellSmallAmount', 0) or 0)),
    }


def _sym_with_exchange(bare: str) -> str:
    """300308 → 300308.SZ，600519 → 600519.SH，供 finance-data 查询用。"""
    if bare.startswith(('6', '9')):
        return f"{bare}.SH"
    if bare.startswith(('8', '43')):
        return f"{bare}.BJ"
    return f"{bare}.SZ"


def _fetch_flow_sync(sym: str, url: str = "", token: str = "") -> list[dict]:
    """获取5日资金流向：优先 finance-data PG（watchlist 内已缓存），fallback 直接调 XTick REST。

    url/token 为向后兼容保留 · 空值时自动走 finance_data_auth 统一入口
    (Community 用户在网页填的 hunter key 也会自动生效 · 与 K 线/Kronos 一致)。

    缓存: 15 min Redis · key=kpred:flow:{sym}:5d
      · 5 日资金流盘中每分钟微调,15 min 平衡新鲜度与命中率
    """
    import io, zipfile, json as _json, os
    from app.services import kpred_cache as _cache
    # 1. finance-data(网关或直连)· 空 url/token 时走统一入口(与 K 线一致)
    bare = sym.split('.')[0] if '.' in sym else sym
    full_sym = _sym_with_exchange(bare)
    _cache_key = f"kpred:flow:{full_sym}:5d"
    _cached = _cache.get(_cache_key)
    if isinstance(_cached, list) and _cached:
        logger.debug("[factor:flow] cache hit · {} · rows={}", full_sym, len(_cached))
        return _cached
    base_url = url or _auth.data_url()
    if url and token:
        headers = {'X-Finance-Token': token}  # 显式直连 · 用旧签名
    else:
        headers = _auth.data_headers()        # 统一入口 · 网关走 Bearer / 直连走 X-Finance-Token
    try:
        r = httpx.get(
            f"{base_url}/api/v1/money_flow/{full_sym}",
            params={'days': 5},
            headers=headers,
            timeout=8.0,
        )
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and data:
                _rows = _parse_flow_rows(data)
                if _rows:
                    _cache.set(_cache_key, _rows, 900)   # TTL 15 min
                return _rows
        else:
            logger.debug("[factor:flow] {} money_flow non-200: status={}", full_sym, r.status_code)
    except Exception as e:
        logger.debug("[factor:flow] {} money_flow error: {}", full_sym, e)

    # 2. XTick REST 直连（任意 A 股，不受 watchlist 限制）
    try:
        from datetime import datetime, timedelta
        xtick_token = os.getenv('XTICK_TOKEN', '')
        xtick_url   = os.getenv('XTICK_API_URL', 'http://api.xtick.top')
        if not xtick_token:
            return []
        bare = sym.split('.')[0] if sym else ''
        if not bare or not bare.isdigit():
            return []
        xt_type    = 2 if bare.startswith(('6', '9')) else 1
        end_date   = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=14)).strftime('%Y-%m-%d')
        resp = httpx.get(
            f"{xtick_url}/doc/hot/money",
            params={'token': xtick_token, 'type': xt_type, 'code': bare,
                    'startDate': start_date, 'endDate': end_date},
            timeout=12.0,
        )
        resp.raise_for_status()
        content = resp.content
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                with z.open('data.json') as f:
                    raw = _json.load(f)
        except zipfile.BadZipFile:
            raw = _json.loads(content)
        if isinstance(raw, list) and raw:
            _rows = [_parse_xtick_flow(item) for item in raw[-5:]]
            if _rows:
                _cache.set(_cache_key, _rows, 900)   # TTL 15 min
            return _rows
    except Exception:
        pass
    return []


# ---------------------------------------------------------------------------
# 主异步编排函数
# ---------------------------------------------------------------------------

async def compute_pro_prediction(
    code: str,
    kronos_result: dict,
    pred_len: int,
    custom_weights: dict | None = None,
) -> dict:
    """给定 Kronos 结果，计算多因子综合评分并返回 Pro 预测结果。custom_weights 覆盖默认权重。"""
    from app.services.finance_data_client import get_kline_with_fallback as get_kline, to_symbol

    sym = to_symbol(code)

    loop = asyncio.get_event_loop()

    # last_close 从 kronos_result 直接取，不需要等 K 线
    _lc = kronos_result.get('last_close', 0.0)

    # 并发获取 K 线（80日）、5日资金流向、PE 评分
    # _fetch_flow_sync 不再传 url/token · 走 finance_data_auth 统一入口
    # (旧代码硬读 env 导致 Community 用户 hunter key 拿不到 · 三个因子恒为 0)
    kline_bars, flow_5d, pe_score = await asyncio.gather(
        loop.run_in_executor(None, lambda: get_kline(code, period='daily', limit=80)),
        loop.run_in_executor(None, lambda: _fetch_flow_sync(sym or code)),
        loop.run_in_executor(None, lambda: factor_pe(code, _lc)),
    )

    bars   = kline_bars
    closes = [b['close'] for b in bars]

    # 诊断 log · bars<60 导致 ma_align=0 · flow_5d=[] 导致 main_flow/flow_div=0
    logger.info("[factor:{}] bars={} flow_5d={} pe_score={} last_close={}",
                code, len(bars), len(flow_5d), pe_score, _lc)

    # 历史波动率（20日）
    if len(closes) >= 21:
        returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
        sigma   = (sum(r ** 2 for r in returns[-20:]) / 20) ** 0.5
    else:
        sigma = 0.02  # 默认 2% 日波动率

    last_close  = kronos_result.get('last_close', closes[-1] if closes else 1)
    predictions = kronos_result.get('predictions', [])
    kronos_end  = predictions[-1]['close'] if predictions else last_close
    kronos_ret  = (kronos_end - last_close) / last_close if last_close > 0 else 0

    factors: dict[str, float] = {
        'kronos':    factor_kronos(kronos_ret, sigma, pred_len),
        'ma_align':  factor_ma_align(bars)    if len(bars) >= 60 else 0.0,
        'macd':      factor_macd(bars)        if len(bars) >= 35 else 0.0,
        'rsi':       factor_rsi(bars)         if len(bars) >= 15 else 0.0,
        'main_flow': factor_main_flow(flow_5d)       if flow_5d else 0.0,
        'flow_div':  factor_flow_divergence(flow_5d) if flow_5d else 0.0,
        'candle_5d': factor_candle_ratio(bars) if len(bars) >= 5 else 0.0,
        'pe':        pe_score,
    }

    weights = custom_weights if custom_weights else WEIGHTS
    composite, rating, confidence, conflict = compute_composite(factors, weights)

    # 因子预期收益率
    factor_return = composite * sigma * math.sqrt(pred_len)

    # 调整 Kronos 预测
    adjusted_predictions = scale_predictions(predictions, last_close, factor_return)
    adjusted_predictions = apply_daily_limit(adjusted_predictions, last_close, code)

    # 因子明细（按贡献绝对值排序）
    factor_list = [
        {
            'key':          k,
            'label':        FACTOR_LABELS[k],
            'score':        round(factors[k], 3),
            'weight':       weights[k],
            'contribution': round(factors[k] * weights[k], 3),
        }
        for k in weights
    ]
    factor_list.sort(key=lambda x: abs(x['contribution']), reverse=True)

    adj_return_pct = (
        (adjusted_predictions[-1]['close'] - last_close) / last_close * 100
        if adjusted_predictions else 0
    )

    # 复赛 §3.C · 概率分布输出 · 复用 Day4 calibration 模块
    # 严禁 mock 兜底 · 样本 < 30 一律 None · 前端显"数据不足"
    distribution = _build_distribution(code, adj_return_pct)

    return {
        **kronos_result,
        'predictions': adjusted_predictions,
        'pro': {
            'composite_score':      round(composite, 3),
            'factor_return_pct':    round(factor_return * 100, 2),
            'adj_return_pct':       round(adj_return_pct, 2),
            'kronos_raw_return_pct': round(kronos_ret * 100, 2),
            'rating':               rating,
            'confidence':           confidence,
            'conflict_level':       conflict,
            'factors':              factor_list,
            'sigma_daily_pct':      round(sigma * 100, 2),
            'distribution':         distribution,   # 复赛 §3.C · 见 backtest/calibration.py
        },
    }


def _build_distribution(code: str, point_pct: float) -> dict:
    """§3.C 概率分布 · 拿 calibration 模块两个方法组合 · 拉不到就每字段 None.

    · interval_from_residuals(code, days=90, horizon=1) → p80 / p95
    · class_prob_from_history(code, point_pct, days=180) → up/flat/down
    · 严禁 mock 兜底 · 任何一步样本不够都 None · note 字段区分原因
    """
    try:
        from app.services.backtest import calibration
    except Exception as e:
        logger.warning("[factor:{}] calibration import failed: {}", code, e)
        return {
            "point_pct": round(point_pct, 4),
            "interval_80": None, "interval_95": None, "prob": None,
            "sample": 0, "note": "calibration_unavailable",
        }

    # 需要 symbol 带交易所后缀(pred_backtest 用 600519.SH 格式)· kpred 传的 code 也带
    # 复赛 seed 的 3 只:600519.SH / 000001.SZ / 600276.SH
    symbol = code if "." in code else _normalize_a_symbol(code)

    iv = None
    pb = None
    try:
        iv = calibration.interval_from_residuals(symbol, days=90, horizon=1)
    except Exception as e:
        logger.warning("[factor:{}] interval failed: {}", symbol, e)
    try:
        pb = calibration.class_prob_from_history(symbol, point_pct, days=180)
    except Exception as e:
        logger.warning("[factor:{}] prob failed: {}", symbol, e)

    note = None
    if iv is None and pb is None:
        note = "sample_small"
    elif iv is None:
        note = "interval_sample_small"
    elif pb is None:
        note = "prob_sample_small"

    return {
        "point_pct":   round(point_pct, 4),
        "interval_80": iv["p80"] if iv else None,
        "interval_95": iv["p95"] if iv else None,
        "residual_std": iv["residual_std"] if iv else None,
        "prob":        {"up": pb["up"], "flat": pb["flat"], "down": pb["down"]} if pb else None,
        "prob_bucket": pb["bucket"] if pb else None,
        "prob_sample_in_bucket": pb["sample_in_bucket"] if pb else None,
        "sample":      iv["sample"] if iv else (pb["sample_total"] if pb else 0),
        "note":        note,
    }


def _normalize_a_symbol(bare: str) -> str:
    """把 600519 → 600519.SH · 000001 → 000001.SZ · 沪深首字判断"""
    if not bare or "." in bare:
        return bare
    if bare.startswith(("6", "5", "9")):
        return f"{bare}.SH"
    return f"{bare}.SZ"
