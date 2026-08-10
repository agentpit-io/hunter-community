"""Hunter-compatible aggregate market-data client.

Historically hits `finance-data.agentpit.io`; in Community it's opt-in via
`FINANCE_DATA_URL` + `FINANCE_DATA_TOKEN` env. When those are empty (the
default), calls fall back to `app.providers.data_source` (akshare for A-shares,
yfinance for US/HK · whichever `DATA_SOURCE_PROVIDER` picks).

symbol 格式映射：hermes code "002595" + exchange "SZ" → "002595.SZ"
"""
import asyncio
import os
import httpx
from loguru import logger
from app.config import STOCK_MAP

FINANCE_DATA_URL   = os.getenv("FINANCE_DATA_URL",   "").rstrip("/")
FINANCE_DATA_TOKEN = os.getenv("FINANCE_DATA_TOKEN", "")

_HEADERS = {"X-Finance-Token": FINANCE_DATA_TOKEN} if FINANCE_DATA_TOKEN else {}
_USE_SAAS = bool(FINANCE_DATA_URL)


def _provider_get_quote_sync(code: str) -> dict | None:
    """Sync bridge to providers.data_source · caller of this module is sync."""
    try:
        from app.providers.data_source import get_data_source
        ds = get_data_source()
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Called from within an async request handler · spawn thread
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    fut = pool.submit(asyncio.run, ds.get_quote(code))
                    return fut.result(timeout=15)
        except RuntimeError:
            pass  # no loop, fall through
        return asyncio.run(ds.get_quote(code))
    except Exception as e:
        logger.warning("[finance_data_client] provider fallback failed for {}: {}", code, e)
        return None

# 动态扩展缓存（通过 register_stocks 注入，随 DB 内容增长）
_dynamic_map: dict = {}


def register_stocks(stocks: list[dict]):
    """将 DB stocks 列表注入动态缓存，供 to_symbol/get_quote 使用。"""
    _dynamic_map.clear()
    _dynamic_map.update({s["code"]: s for s in stocks})


def _a_prefix_exchange(bare: str) -> str | None:
    """A 股/ETF/可转债标准前缀 → 权威交易所（可覆盖 watchlist 里的脏数据）。"""
    if bare.startswith(("60", "68", "11", "51", "52")): return "SH"
    if bare.startswith(("00", "30")):                    return "SZ"
    if bare.startswith(("43", "83", "87", "88")):        return "BJ"
    return None


def to_symbol(code: str) -> str | None:
    """hermes code → finance-data symbol。
    A 股/ETF/可转债按代码前缀权威推断（覆盖 watchlist 中的脏数据）。
    港股/美股/公募走 watchlist / STOCK_MAP。
    """
    bare = code.split(".")[0]
    suffix = code.split(".")[1] if "." in code else None
    s = STOCK_MAP.get(bare) or _dynamic_map.get(bare)

    # A 股/ETF/可转债/北交所 · 前缀 override（防 watchlist 脏数据 e.g. 002138 被登记成 SH）
    prefix_exch = _a_prefix_exchange(bare)
    if prefix_exch:
        return f"{bare}.{prefix_exch}"

    # 港股/美股/公募 · 走 map · 无 map 则用 suffix
    if s:
        market = s.get("market") or "A"
        exchange = s.get("exchange") or ""
        if exchange in ("HK", "US", "OF"):
            return f"{bare}.{exchange}"
        if market == "HK":  return f"{bare}.HK"
        if market == "US":  return f"{bare}.US"
        if market == "FUND": return f"{bare}.OF"
    if suffix and suffix in ("HK", "US", "OF"):
        return code
    return None


def subscribe(code: str, name: str, market: str, exchange: str, asset_type: str = "stock") -> dict:
    """通知 finance-data 开始采集新标的（股票 / ETF / 公募基金）。"""
    try:
        r = httpx.post(
            f"{FINANCE_DATA_URL}/api/v1/watchlist/subscribe",
            json={
                "code": code, "name": name,
                "market": market, "exchange": exchange,
                "asset_type": asset_type,
            },
            headers=_HEADERS,
            timeout=10.0,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"ok": False, "message": str(e)}


def _get(path: str, params: dict = None) -> dict | list | None:
    try:
        r = httpx.get(
            f"{FINANCE_DATA_URL}{path}",
            params=params or {},
            headers=_HEADERS,
            timeout=10.0,
        )
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def get_quote(code: str) -> dict | None:
    """实时报价快照（含五档盘口）· SaaS or provider fallback."""
    # No SaaS URL configured → go straight to providers.data_source
    if not _USE_SAAS:
        base = _provider_get_quote_sync(code)
        if not base:
            return None
        stock = STOCK_MAP.get(code) or _dynamic_map.get(code) or {"name": base.get("name") or code, "market": base.get("market") or "A"}
        # Shape-adapt: providers return simple dict · fill bid/ask with zeros
        return {
            "code":       code,
            "name":       base.get("name") or stock["name"],
            "price":      _f(base.get("price")),
            "change_pct": _f(base.get("change_pct")),
            "change_amt": _f(base.get("change_amt")),
            "open":       _f(base.get("open")),
            "high":       _f(base.get("high")),
            "low":        _f(base.get("low")),
            "prev_close": _f(base.get("prev_close")),
            "volume":     int(base.get("volume") or 0),
            "amount":     _f(base.get("amount")),
            **{f"bid{i}": None for i in range(1, 6)},
            **{f"bid{i}v": 0 for i in range(1, 6)},
            **{f"ask{i}": None for i in range(1, 6)},
            **{f"ask{i}v": 0 for i in range(1, 6)},
            "ts":     base.get("ts", ""),
            "market": stock["market"],
            "asset_type": stock.get("asset_type", "stock"),
        }

    sym = to_symbol(code)
    if not sym:
        return None
    data = _get(f"/api/v1/quote/{sym}")
    if not data:
        return None
    stock = STOCK_MAP.get(code) or _dynamic_map.get(code) or {"name": code, "market": "A"}
    # 统一成 hermes collector 写 Redis 的格式
    return {
        "code":       code,
        "name":       stock["name"],
        "price":      _f(data.get("price")),
        "change_pct": _f(data.get("change_pct")),
        "change_amt": _f(data.get("change_amt")),
        "open":       _f(data.get("open")),
        "high":       _f(data.get("high")),
        "low":        _f(data.get("low")),
        "prev_close": _f(data.get("pre_close")),
        "volume":     int(data.get("volume") or 0),
        "amount":     _f(data.get("amount")),
        "bid1":  _f(data.get("bid1_price")), "bid1v": int(data.get("bid1_vol") or 0),
        "bid2":  _f(data.get("bid2_price")), "bid2v": int(data.get("bid2_vol") or 0),
        "bid3":  _f(data.get("bid3_price")), "bid3v": int(data.get("bid3_vol") or 0),
        "bid4":  _f(data.get("bid4_price")), "bid4v": int(data.get("bid4_vol") or 0),
        "bid5":  _f(data.get("bid5_price")), "bid5v": int(data.get("bid5_vol") or 0),
        "ask1":  _f(data.get("ask1_price")), "ask1v": int(data.get("ask1_vol") or 0),
        "ask2":  _f(data.get("ask2_price")), "ask2v": int(data.get("ask2_vol") or 0),
        "ask3":  _f(data.get("ask3_price")), "ask3v": int(data.get("ask3_vol") or 0),
        "ask4":  _f(data.get("ask4_price")), "ask4v": int(data.get("ask4_vol") or 0),
        "ask5":  _f(data.get("ask5_price")), "ask5v": int(data.get("ask5_vol") or 0),
        "ts":    data.get("updated_at") or data.get("ts", ""),
        "market": stock["market"],
        "asset_type": stock.get("asset_type", "stock"),
    }


# ═════════════════════════════════════════════════════════════════
# UZI 5 维数据（Sprint 3 P2 · chat 深度分析 tool 消费）
# 端点在 finance-data /api/routers/uzi_dims.py · 5 张表 seed 前多为空
# ═════════════════════════════════════════════════════════════════

def _unwrap(data, list_key: str) -> list[dict]:
    """finance-data 端点常见两种形态：
       · 直接 list         → 原样返回
       · {"...": [...], detail?} → 取 list_key 字段（如 holders / reports）
       · {"detail": "..."} → 无数据 · 返回 []
    """
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if "detail" in data and list_key not in data:
            return []
        v = data.get(list_key)
        return v if isinstance(v, list) else []
    return []


def get_lhb(code: str, days: int = 30) -> list[dict]:
    """龙虎榜 · 近 N 日。返回 list of records。"""
    sym = to_symbol(code)
    if not sym:
        return []
    return _unwrap(_get(f"/api/v1/lhb/{sym}", {"days": days}), "records")


def get_fund_holders(code: str) -> list[dict]:
    """十大流通股东（最新季度）。返回 list of holders。"""
    sym = to_symbol(code)
    if not sym:
        return []
    return _unwrap(_get(f"/api/v1/fund_holders/{sym}"), "holders")


def get_governance(code: str) -> dict | None:
    """治理指标。无数据返 None。"""
    sym = to_symbol(code)
    if not sym:
        return None
    data = _get(f"/api/v1/governance/{sym}")
    if not isinstance(data, dict):
        return None
    if "detail" in data and len(data) == 1:
        return None  # 无数据的 error 包装
    return data


def get_peers(code: str) -> dict | None:
    """同业股票 + 申万一二三级行业。无数据返 None。"""
    sym = to_symbol(code)
    if not sym:
        return None
    data = _get(f"/api/v1/peers/{sym}")
    if not isinstance(data, dict):
        return None
    if "detail" in data and len(data) == 1:
        return None
    return data


def get_research_reports(code: str, limit: int = 10) -> list[dict]:
    """研报（近 N 篇）。返回 list of reports。"""
    sym = to_symbol(code)
    if not sym:
        return []
    return _unwrap(_get(f"/api/v1/research/{sym}", {"limit": limit}), "reports")


def get_reliable_close(code: str, today: str) -> dict | None:
    """当 quote ts 不是今天时，从 kline 取当日收盘价。
    V2：finance-data kline 无今日数据时，用 akshare 兜底（防上游对个别股票停更）。"""
    sym = to_symbol(code)
    if not sym:
        return None
    data = _get(f"/api/v1/kline/{sym}", {"tf": "1d", "range": "7d"})
    today_row = None
    if isinstance(data, list) and len(data) >= 1:
        today_row = next((r for r in data if (r.get("ts") or "")[:10] == today), None)

    # ── akshare 兜底：finance-data 无今日 kline，且看起来数据陈旧 ────────
    if not today_row:
        bare = code.split(".")[0] if "." in code else code
        try:
            fresh = _get_kline_akshare(bare, limit=5)
            if fresh:
                # 找今日或最新一日
                today_row = next((r for r in fresh if (r.get("ts") or "")[:10] == today), None)
                if not today_row:
                    today_row = fresh[-1]  # 最新一日（可能是刚过的交易日）
                # 用 akshare 结果重组返回；prev_close 用倒数第二日
                prev = fresh[-2] if len(fresh) >= 2 else None
                stock = STOCK_MAP.get(code) or _dynamic_map.get(code) or {"name": code, "market": "A"}
                return {
                    "code":       code,
                    "name":       stock["name"],
                    "price":      _f(today_row.get("close")),
                    "change_pct": (
                        round((_f(today_row.get("close")) - _f(prev.get("close"))) /
                              _f(prev.get("close")) * 100, 2)
                        if prev and _f(prev.get("close")) else 0.0
                    ),
                    "change_amt": (
                        round(_f(today_row.get("close")) - _f(prev.get("close")), 2)
                        if prev else 0.0
                    ),
                    "open":       _f(today_row.get("open")),
                    "high":       _f(today_row.get("high")),
                    "low":        _f(today_row.get("low")),
                    "prev_close": _f(prev.get("close")) if prev else 0.0,
                    "volume":     int(today_row.get("volume") or 0),
                    "amount":     0.0,
                    "bid1": 0.0, "bid1v": 0, "bid2": 0.0, "bid2v": 0,
                    "bid3": 0.0, "bid3v": 0, "bid4": 0.0, "bid4v": 0,
                    "bid5": 0.0, "bid5v": 0,
                    "ask1": 0.0, "ask1v": 0, "ask2": 0.0, "ask2v": 0,
                    "ask3": 0.0, "ask3v": 0, "ask4": 0.0, "ask4v": 0,
                    "ask5": 0.0, "ask5v": 0,
                    "ts":         (today_row.get("ts") or "")[:10],
                    "market":     stock["market"],
                    "asset_type": stock.get("asset_type", "stock"),
                }
        except Exception:
            pass

    if not today_row:
        return None
    if not isinstance(data, list) or len(data) < 2:
        return None
    prev_close_row = None
    for r in data:
        if (r.get("ts") or "")[:10] < today:
            prev_close_row = r
    if not prev_close_row:
        return None
    close = _f(today_row.get("close"))
    prev_close = _f(prev_close_row.get("close"))
    change_amt = close - prev_close
    change_pct = (change_amt / prev_close * 100) if prev_close else 0.0
    stock = STOCK_MAP.get(code) or _dynamic_map.get(code) or {"name": code, "market": "A"}
    return {
        "code": code, "name": stock["name"],
        "price": close, "change_pct": change_pct, "change_amt": change_amt,
        "open": _f(today_row.get("open")), "high": _f(today_row.get("high")),
        "low": _f(today_row.get("low")), "prev_close": prev_close,
        "volume": int(today_row.get("volume") or 0), "amount": 0.0,
        "bid1": 0.0, "bid1v": 0, "bid2": 0.0, "bid2v": 0,
        "bid3": 0.0, "bid3v": 0, "bid4": 0.0, "bid4v": 0,
        "bid5": 0.0, "bid5v": 0, "ask1": 0.0, "ask1v": 0,
        "ask2": 0.0, "ask2v": 0, "ask3": 0.0, "ask3v": 0,
        "ask4": 0.0, "ask4v": 0, "ask5": 0.0, "ask5v": 0,
        "ts": today + "T07:00:00+00:00",
        "market": stock["market"], "asset_type": stock.get("asset_type", "stock"),
    }


def get_kline(code: str, period: str = "daily", limit: int = 120) -> list[dict]:
    """K线数据。period: daily/weekly/monthly → tf=1d。"""
    sym = to_symbol(code)
    if not sym:
        return []
    tf_map = {"daily": "1d", "weekly": "1d", "monthly": "1d",
              "day": "1d", "week": "1d", "month": "1d"}
    range_map = {"daily": "1y", "day": "1y",
                 "weekly": "all", "week": "all",
                 "monthly": "all", "month": "all"}
    tf    = tf_map.get(period, "1d")
    range_ = range_map.get(period, "1y")
    data = _get(f"/api/v1/kline/{sym}", {"tf": tf, "range": range_, "fq": "front"})
    if not isinstance(data, list):
        return []
    rows = data[-limit:] if limit else data
    return [{"ts": r.get("ts", "")[:10], "open": _f(r.get("open")),
             "high": _f(r.get("high")), "low": _f(r.get("low")),
             "close": _f(r.get("close")), "volume": int(r.get("volume") or 0)}
            for r in rows]


def get_timeshare(code: str) -> list[dict]:
    """分时图（今日 1 分钟 K 线）。"""
    sym = to_symbol(code)
    if not sym:
        return []
    data = _get(f"/api/v1/kline/{sym}", {"tf": "1m", "range": "1d", "fq": "none"})
    if not isinstance(data, list):
        return []
    return [{"ts": r.get("ts", ""), "close": _f(r.get("close")),
             "volume": int(r.get("volume") or 0)}
            for r in data]


def get_orderbook(code: str) -> dict | None:
    """五档盘口。"""
    sym = to_symbol(code)
    if not sym:
        return None
    return _get(f"/api/v1/orderbook/{sym}")


def get_money_flow(code: str) -> dict | None:
    """资金流向（今日）。"""
    sym = to_symbol(code)
    if not sym:
        return None
    data = _get(f"/api/v1/money_flow/{sym}", {"days": 1})
    if not isinstance(data, list) or not data:
        return None
    r = data[0]
    super_net = _f(r.get("super_buy")) - _f(r.get("super_sell"))
    big_net   = _f(r.get("big_buy"))   - _f(r.get("big_sell"))
    mid_net   = _f(r.get("mid_buy"))   - _f(r.get("mid_sell"))
    small_net = _f(r.get("small_buy")) - _f(r.get("small_sell"))
    return {
        "main_net":  super_net + big_net,
        "big_net":   super_net,
        "large_net": big_net,
        "mid_net":   mid_net,
        "small_net": small_net,
        "date":      str(r.get("trade_date", "")),
    }


def get_news(code: str, limit: int = 20) -> list[dict]:
    """个股新闻。"""
    sym = to_symbol(code)
    if not sym:
        return []
    data = _get("/api/v1/news", {"symbols": sym, "limit": limit})
    if not isinstance(data, list):
        return []
    return [{"id": i, "code": code, "title": r.get("title", ""),
             "source": r.get("source", ""), "url": r.get("url", ""),
             "published_at": r.get("published_at", "")}
            for i, r in enumerate(data)]


def get_all_news_bulk(limit: int = 100) -> list[dict]:
    """所有自选股新闻，单次请求（不按 symbol 过滤）。"""
    data = _get("/api/v1/news", {"limit": limit})
    if not isinstance(data, list):
        return []
    return [
        {
            "id":           i,
            "code":         r.get("symbol") or "",
            "title":        r.get("title", ""),
            "source":       r.get("source", ""),
            "url":          r.get("url", ""),
            "published_at": r.get("published_at", ""),
        }
        for i, r in enumerate(data)
    ]


def save_analysis_report(report: dict) -> int:
    """保存在线分析报告到 finance-data DB，返回 report id。"""
    try:
        r = httpx.post(
            f"{FINANCE_DATA_URL}/api/v1/analysis/report",
            json={
                "stock_code":       report.get("stock_code", ""),
                "stock_name":       report.get("stock_name", ""),
                "thesis_status":    report.get("thesis_status", "HOLD"),
                "confidence":       report.get("confidence"),
                "final_conclusion": report.get("final_conclusion", {}),
                "duration_ms":      report.get("duration_ms"),
            },
            headers=_HEADERS,
            timeout=10.0,
        )
        r.raise_for_status()
        return r.json()["id"]
    except Exception as e:
        raise RuntimeError(f"save_analysis_report failed: {e}") from e


def list_analysis_reports(limit: int = 20, offset: int = 0,
                          stock_code: str | None = None) -> list[dict]:
    """列出历史分析报告。"""
    params: dict = {"limit": limit, "offset": offset}
    if stock_code:
        params["stock_code"] = stock_code
    data = _get("/api/v1/analysis/reports", params)
    return (data or {}).get("items", [])


def get_analysis_report(report_id: int) -> dict | None:
    """读取单份完整报告。"""
    return _get(f"/api/v1/analysis/report/{report_id}")


def _f(v, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


# ── akshare 兜底（非 watchlist 股票数据陈旧时自动补充） ──────────────────────

def _get_kline_akshare(bare: str, limit: int = 120) -> list[dict]:
    """从 akshare 拉任意 A 股 K 线（不依赖 watchlist，前复权日线）。"""
    try:
        import akshare as ak
        from datetime import datetime, timedelta
        end = datetime.now()
        start = end - timedelta(days=limit * 2)
        df = ak.stock_zh_a_hist(
            symbol=bare, period="daily",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adjust="qfq",
        )
        if df is None or df.empty:
            return []
        rows = df.tail(limit)
        return [
            {
                "ts":     str(r.get("日期", ""))[:10],
                "open":   _f(r.get("开盘")),
                "high":   _f(r.get("最高")),
                "low":    _f(r.get("最低")),
                "close":  _f(r.get("收盘")),
                "volume": int(r.get("成交量") or 0),
            }
            for _, r in rows.iterrows()
        ]
    except Exception:
        return []


def _subscribe_async(bare: str):
    """Fire-and-forget：把股票订阅到 finance-data watchlist（下次调用将有实时数据）。"""
    import threading
    if bare.startswith(("60", "68", "11", "51", "52")):
        exchange, market = "SH", "A"
    elif bare.startswith(("43", "83", "87", "88")):
        exchange, market = "BJ", "A"
    else:
        exchange, market = "SZ", "A"

    def _do():
        try:
            subscribe(bare, bare, market, exchange, "stock")
        except Exception:
            pass
    threading.Thread(target=_do, daemon=True).start()


def get_kline_with_fallback(code: str, period: str = "daily", limit: int = 120) -> list[dict]:
    """优先取 finance-data K 线；如数据陈旧（>3 自然日）立即用 akshare 补充，
    并后台触发 watchlist 订阅，确保下次调用时 finance-data 已有实时数据。"""
    from datetime import date
    bars = get_kline(code, period, limit)

    # 检查是否陈旧
    if bars:
        last_ts = bars[-1].get("ts", "")[:10]
        try:
            if (date.today() - date.fromisoformat(last_ts)).days <= 3:
                return bars  # 数据新鲜，直接返回
        except Exception:
            pass

    # 数据陈旧或为空 → akshare 实时拉取
    bare = code.split(".")[0] if "." in code else code
    fresh = _get_kline_akshare(bare, limit)
    if fresh:
        _subscribe_async(bare)  # 异步订阅 watchlist，下次自动走 finance-data
        return fresh

    return bars  # akshare 也失败则返回原有数据（至少有历史形态）
