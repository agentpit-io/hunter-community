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
    import akshare as ak
    import warnings
    warnings.filterwarnings("ignore")
    for attempt in range(_RETRY):
        try:
            df = ak.stock_financial_analysis_indicator(symbol=code, start_year=start_year)
            time.sleep(_SLEEP)
            return df
        except Exception as e:
            log.warning(f"[akshare] {code} attempt {attempt+1}/{_RETRY} · {e}")
            if attempt < _RETRY - 1:
                time.sleep(1.5 * (attempt + 1))
    return None


@lru_cache(maxsize=2048)
def get_financial_summary(code: str, trade_date: date) -> dict | None:
    """一次拿全 · 返 dict
    trade_date 用于选择哪个季报(避免未来函数 · 45 天 buffer)
    """
    start_year = str(trade_date.year - 1)
    df = _fetch_indicator_df(code, start_year)
    if df is None or len(df) == 0:
        return None
    cutoff = trade_date - timedelta(days=45)
    df["_dt"] = df["日期"].astype(str)
    df = df[df["_dt"] <= cutoff.isoformat()]
    if len(df) == 0:
        return None
    r = df.iloc[-1]

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
