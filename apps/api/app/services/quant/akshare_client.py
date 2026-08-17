"""AKShare 数据适配层 · quant 系统专用
(2026-08-17 · Phase B B1)

设计:
- 用 stock_financial_analysis_indicator(每股 EPS/BPS/ROE 全在)
- 从本地 klines 拿 close · 组合算 PE/PB
- lru_cache 缓存 · sleep 限流(避免 AKShare 上游封 IP)
- 网络 flaky 加 3 次 retry
"""
from __future__ import annotations
import logging
import time
from datetime import date, timedelta
from functools import lru_cache

log = logging.getLogger(__name__)

_SLEEP = 0.4
_RETRY = 3


def _fetch_indicator_df(code: str, start_year: str):
    """带 15s 硬超时 · akshare requests 无超时会挂死
    ThreadPoolExecutor 不用 with · 避免 timeout 后 __exit__ 等挂死线程
    """
    import akshare as ak
    import warnings
    import concurrent.futures
    warnings.filterwarnings("ignore")

    def _do():
        return ak.stock_financial_analysis_indicator(symbol=code, start_year=start_year)

    for attempt in range(_RETRY):
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            fut = ex.submit(_do)
            df = fut.result(timeout=15)
            ex.shutdown(wait=False)   # 不等 · 已 return
            time.sleep(_SLEEP)
            return df
        except concurrent.futures.TimeoutError:
            log.warning(f"[akshare] {code} attempt {attempt+1}/{_RETRY} · TIMEOUT 15s · abandon thread")
            ex.shutdown(wait=False)   # 挂死 thread 就 daemon 化 · 别等
        except Exception as e:
            log.warning(f"[akshare] {code} attempt {attempt+1}/{_RETRY} · {e}")
            ex.shutdown(wait=False)
        if attempt < _RETRY - 1:
            time.sleep(1.5 * (attempt + 1))
    return None


@lru_cache(maxsize=2048)
def _fetch_all_periods(code: str, start_year: str):
    """按 code 缓存 · 一次拉多期 · 返 (df, 各期 dict 列表)
    (cache key 不含 trade_date · 12 期共用一次 fetch)
    """
    df = _fetch_indicator_df(code, start_year)
    if df is None or len(df) == 0:
        return None
    return df


def get_financial_summary(code: str, trade_date: date) -> dict | None:
    """按 trade_date 筛某期财报(不 cache · 走 _fetch_all_periods 拿全 · 过滤)
    45 天 buffer 避免未来函数
    """
    start_year = str(trade_date.year - 2)   # 多拉 1 年 · 保证前几期能找到
    df = _fetch_all_periods(code, start_year)
    if df is None:
        return None
    cutoff = trade_date - timedelta(days=45)
    df2 = df.copy()
    df2["_dt"] = df2["日期"].astype(str)
    df2 = df2[df2["_dt"] <= cutoff.isoformat()]
    if len(df2) == 0:
        return None
    r = df2.iloc[-1]

    def _f(k, default=None):
        v = r.get(k)
        try:
            fv = float(v)
            if fv != fv:
                return default
            return fv
        except (TypeError, ValueError):
            return default

    return {
        "report_date": str(r.get("日期")),
        "eps": _f("摊薄每股收益(元)"),
        "bps": _f("每股净资产_调整前(元)") or _f("每股净资产_调整后(元)"),
        "roe_pct": _f("净资产收益率(%)"),
        "roa_pct": _f("总资产利润率(%)"),
        "gross_margin_pct": _f("销售毛利率(%)"),
        "net_margin_pct": _f("销售净利率(%)"),
        "debt_ratio_pct": _f("资产负债率(%)"),
        "revenue_growth_pct": _f("主营业务收入增长率(%)"),
        "earnings_growth_pct": _f("净利润增长率(%)"),
    }


def get_pe_pb(code: str, trade_date: date) -> tuple[float | None, float | None]:
    from app.services.database import get_conn
    fin = get_financial_summary(code, trade_date)
    if not fin:
        return None, None
    eps = fin.get("eps")
    bps = fin.get("bps")
    conn = get_conn(); cur = conn.cursor()
    cur.execute(
        """SELECT close FROM klines
           WHERE code=%s AND period='daily' AND ts <= %s
           ORDER BY ts DESC LIMIT 1""",
        (code, trade_date),
    )
    row = cur.fetchone()
    cur.close(); conn.close()
    if not row:
        return None, None
    close = float(row[0])
    pe = close / eps if eps and eps > 0 else None
    pb = close / bps if bps and bps > 0 else None
    return pe, pb


def get_pe_ttm(code, trade_date):
    pe, _ = get_pe_pb(code, trade_date); return pe

def get_pb(code, trade_date):
    _, pb = get_pe_pb(code, trade_date); return pb

def _percent_field(field: str):
    def _fn(code, trade_date):
        fin = get_financial_summary(code, trade_date)
        if not fin: return None
        v = fin.get(field)
        return v / 100 if v is not None else None
    return _fn

get_roe = _percent_field("roe_pct")
get_roa = _percent_field("roa_pct")
get_gross_margin = _percent_field("gross_margin_pct")
get_debt_ratio = _percent_field("debt_ratio_pct")
get_revenue_growth = _percent_field("revenue_growth_pct")
get_earnings_growth = _percent_field("earnings_growth_pct")


# ═════════════════════════════════════════════════════════════════════════
# C1 · dividend_yield · 近 12M 现金分红 / 当日 close(A 股 · 派息单位 元/10股)
# ═════════════════════════════════════════════════════════════════════════

def _fetch_dividend_df(code: str):
    """15s 超时 · 3 次 retry · 参见 _fetch_indicator_df 同款模式"""
    import akshare as ak
    import warnings
    import concurrent.futures
    warnings.filterwarnings("ignore")

    def _do():
        return ak.stock_history_dividend_detail(symbol=code, indicator="分红")

    for attempt in range(_RETRY):
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            fut = ex.submit(_do)
            df = fut.result(timeout=15)
            ex.shutdown(wait=False)
            time.sleep(_SLEEP)
            return df
        except concurrent.futures.TimeoutError:
            log.warning(f"[akshare-div] {code} attempt {attempt+1}/{_RETRY} · TIMEOUT 15s")
            ex.shutdown(wait=False)
        except Exception as e:
            log.warning(f"[akshare-div] {code} attempt {attempt+1}/{_RETRY} · {e}")
            ex.shutdown(wait=False)
        if attempt < _RETRY - 1:
            time.sleep(1.5 * (attempt + 1))
    return None


@lru_cache(maxsize=2048)
def _fetch_dividends_all(code: str):
    """按 code 缓存全历史分红明细 · 12 期回填共用一次 fetch"""
    df = _fetch_dividend_df(code)
    if df is None or len(df) == 0:
        return None
    return df


def get_dividend_yield(code: str, trade_date: date) -> float | None:
    """近 12M 累计现金分红(元/股) / 当日 close · 单位 %(小数)
    - 派息列单位 · 元/10股 · 除以 10 得元/股
    - 只统计进度='实施'(排除公告未实施)
    - 除权除息日 < trade_date 才计入(避免未来函数)
    - 无分红返 None(不参与 z-score · 不写 factor_value)
    - 极端值 > 0.30(30%)视脏数据 · 返 None
    """
    from app.services.database import get_conn
    df = _fetch_dividends_all(code)
    if df is None or df.empty:
        return None
    try:
        import pandas as pd
        df2 = df.copy()
        df2["_ex_dt"] = pd.to_datetime(df2["除权除息日"], errors="coerce")
        cutoff_hi = pd.Timestamp(trade_date)
        cutoff_lo = pd.Timestamp(trade_date - timedelta(days=365))
        # 只留已实施 + 除权在 [cutoff_lo, trade_date]
        df2 = df2[(df2["_ex_dt"] >= cutoff_lo) & (df2["_ex_dt"] <= cutoff_hi)]
        if "进度" in df2.columns:
            df2 = df2[df2["进度"].astype(str).str.contains("实施", na=False)]
        if df2.empty:
            return None
        total_per_10 = 0.0
        for _, r in df2.iterrows():
            try:
                total_per_10 += float(r.get("派息") or 0)
            except (TypeError, ValueError):
                continue
        if total_per_10 <= 0:
            return None
        dps = total_per_10 / 10.0

        # 拉当日 close
        conn = get_conn(); cur = conn.cursor()
        cur.execute(
            """SELECT close FROM klines
               WHERE code=%s AND period='daily' AND ts <= %s
               ORDER BY ts DESC LIMIT 1""",
            (code, trade_date),
        )
        row = cur.fetchone()
        cur.close(); conn.close()
        if not row or not row[0]:
            return None
        close = float(row[0])
        if close <= 0:
            return None
        dy = dps / close
        if 0 < dy < 0.30:
            return dy
        return None
    except Exception as e:
        log.warning(f"[dividend_yield] {code}: {e}")
        return None


# ═════════════════════════════════════════════════════════════════════════
# C1 · main_flow · 近 5 日主力资金净流入 / 近 5 日总资金流
# 数据源:finance-data /api/v1/money_flow · AKShare 上游 stock_individual_fund_flow
# 在生产网络封禁 · 走 finance-data 网关(已鉴权 · 已缓存)· 参考 factor_engine.py
# 的老实现 _fetch_flow_sync · 只不过取多天(days=5)算比值
# ═════════════════════════════════════════════════════════════════════════

def _fetch_flow_5d(code: str, trade_date: date, days: int = 5) -> list[dict] | None:
    """走 finance-data 网关拿多天资金流 · 返 [{trade_date, super_buy, super_sell, ...}]
    - 不 cache(不同 trade_date 数据不同)
    - 用 hunter_key + saas gateway auth · 与 factor_engine 老实现一致
    """
    from app.services.finance_data_client import to_symbol, _get
    sym = to_symbol(code)
    if not sym:
        return None
    try:
        data = _get(f"/api/v1/money_flow/{sym}", {"days": max(days * 2, 10)})
        # 多拿一点 · 因 trade_date 可能不是最新交易日 · 需过滤后再切
        if not isinstance(data, list) or not data:
            return None
        # 过滤 trade_date · 只留 <= trade_date 的
        cutoff = trade_date.isoformat()
        filtered = [r for r in data if str(r.get("trade_date", ""))[:10] <= cutoff]
        # 按日期降序 · 取最近 days 天
        filtered.sort(key=lambda r: str(r.get("trade_date", "")), reverse=True)
        return filtered[:days] if len(filtered) >= days else None
    except Exception as e:
        log.warning(f"[main_flow-fetch] {code}: {e}")
        return None


def get_main_flow_ratio(code: str, trade_date: date, days: int = 5) -> float | None:
    """近 N 日主力净流入 / 近 N 日总资金流(所有方向)
    - main_net = super_buy - super_sell + big_buy - big_sell(超大 + 大单净流入)
    - total_flow = 所有方向绝对值(近似成交额 · 用于归一化)
    - ratio = main_net_sum / total_flow_sum · 截断 [-0.5, 0.5]
    - 无 N 天完整数据返 None(不写 factor_value)
    """
    rows = _fetch_flow_5d(code, trade_date, days)
    if not rows or len(rows) < days:
        return None
    try:
        main_net_sum = 0.0
        total_flow_sum = 0.0
        for r in rows:
            super_buy = float(r.get("super_buy") or 0)
            super_sell = float(r.get("super_sell") or 0)
            big_buy = float(r.get("big_buy") or 0)
            big_sell = float(r.get("big_sell") or 0)
            mid_buy = float(r.get("mid_buy") or 0)
            mid_sell = float(r.get("mid_sell") or 0)
            small_buy = float(r.get("small_buy") or 0)
            small_sell = float(r.get("small_sell") or 0)
            main_net_sum += (super_buy - super_sell) + (big_buy - big_sell)
            total_flow_sum += (super_buy + super_sell + big_buy + big_sell
                              + mid_buy + mid_sell + small_buy + small_sell)
        if total_flow_sum <= 0:
            return None
        ratio = main_net_sum / total_flow_sum
        return max(-0.5, min(0.5, ratio))
    except Exception as e:
        log.warning(f"[main_flow] {code}: {e}")
        return None
