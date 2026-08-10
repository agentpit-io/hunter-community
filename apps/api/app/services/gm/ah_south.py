"""港股发现页数据(gm端): 南向资金汇总 + AH溢价榜。

南向: akshare stock_hsgt_fund_flow_summary_em(经232代理, 已在白名单)
AH溢价: 静态AH对照表(24对主流) + A股价(finance-data中台) + H股价(Yahoo延迟)
        + HKDCNY汇率(Yahoo) → 溢价% = A价/(H价*汇率) - 1
Redis缓存: 南向10分钟 / AH榜10分钟
"""
import os
import logging
import requests

from app.services.gm.yahoo_hk import _cache_get, _cache_set, hk_quote, _fetch_chart
from app.services.finance_data_client import get_quote as fd_quote

log = logging.getLogger(__name__)

_AK_BASE = os.getenv("AK_PROXY_URL", "http://139.199.221.232:8765")
_AK_TOKEN = os.getenv("AK_API_TOKEN", "ak-proxy-2026")

# (A股代码, H股代码, 名称) 主流AH对照 —— 成分极少变动, 静态维护
AH_PAIRS = [
    ("601398", "01398", "工商银行"), ("601288", "01288", "农业银行"),
    ("601988", "03988", "中国银行"), ("601939", "00939", "建设银行"),
    ("601328", "03328", "交通银行"), ("600036", "03968", "招商银行"),
    ("601998", "00998", "中信银行"), ("600016", "01988", "民生银行"),
    ("601818", "06818", "光大银行"), ("601318", "02318", "中国平安"),
    ("601628", "02628", "中国人寿"), ("601601", "02601", "中国太保"),
    ("601336", "01336", "新华保险"), ("600028", "00386", "中国石化"),
    ("601857", "00857", "中国石油"), ("601088", "01088", "中国神华"),
    ("600030", "06030", "中信证券"), ("000063", "00763", "中兴通讯"),
    ("600585", "00914", "海螺水泥"), ("601766", "01766", "中国中车"),
    ("601390", "00390", "中国中铁"), ("601186", "01186", "中国铁建"),
    ("601800", "01800", "中国交建"), ("600011", "00902", "华能国际"),
]


def southbound_summary() -> dict | None:
    """南向(港股通)资金今日汇总, 单位亿元"""
    key = "gm:southbound:summary"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    try:
        r = requests.post(f"{_AK_BASE}/call",
                          json={"func": "stock_hsgt_fund_flow_summary_em", "kwargs": {}},
                          headers={"Authorization": f"Bearer {_AK_TOKEN}"}, timeout=30)
        r.raise_for_status()
        body = r.json()
        if not body.get("ok"):
            return None
        rows = body.get("data") or []
        # 行含 交易状况/资金方向/板块 等列: 取 南向 的 沪股通+深股通 净流入合计
        south_net, date = 0.0, ""
        for row in rows:
            if str(row.get("资金方向", "")) == "南向":
                try:
                    south_net += float(row.get("成交净买额", 0) or 0)
                except (TypeError, ValueError):
                    pass
                date = str(row.get("交易日", ""))[:10] or date
        out = {"net_buy_yi": round(south_net, 2), "date": date}
        _cache_set(key, out, 600)
        return out
    except Exception as e:
        log.warning("southbound summary failed: %s", e)
        return None


def _hkdcny() -> float | None:
    key = "gm:fx:hkdcny"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    try:
        chart = _fetch_chart_raw("HKDCNY=X")
        rate = (chart.get("meta") or {}).get("regularMarketPrice")
        if rate:
            _cache_set(key, float(rate), 3600)
            return float(rate)
    except Exception as e:
        log.warning("fx hkdcny failed: %s", e)
    return None


def _fetch_chart_raw(symbol: str) -> dict:
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                     params={"interval": "1d", "range": "5d"},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    r.raise_for_status()
    result = (r.json().get("chart") or {}).get("result") or []
    return result[0] if result else {}


def ah_premium(limit: int = 15) -> list[dict]:
    """AH溢价榜(溢价高→低)。溢价% = A价(CNY) / (H价(HKD)*HKDCNY) - 1"""
    key = "gm:ah:premium"
    cached = _cache_get(key)
    if cached is not None:
        return cached[:limit]
    fx = _hkdcny()
    if not fx:
        return []
    out = []
    for a_code, h_code, name in AH_PAIRS:
        try:
            aq = fd_quote(a_code)
            a_price = float(aq.get("price") or 0) if aq else 0
            hq = hk_quote(h_code)
            h_price = float(hq.get("price") or 0) if hq else 0
            if a_price <= 0 or h_price <= 0:
                continue
            premium = (a_price / (h_price * fx) - 1) * 100
            out.append({
                "name": name, "a_code": a_code, "h_code": h_code,
                "a_price": round(a_price, 2), "h_price": round(h_price, 2),
                "premium_pct": round(premium, 1),
            })
        except Exception:
            continue
    out.sort(key=lambda x: -x["premium_pct"])
    _cache_set(key, out, 600)
    return out[:limit]
