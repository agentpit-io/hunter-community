"""gm 端数据通道：直读 financedata 库（美股K线三表 + 港美股主表）。

数据背景（2026-07 建成，见 agentpit doc/云清每日总结/数据/）：
- us_kline_1m / us_kline_5m: 热门300只，90天/1年滚动，Alpaca源每5分钟增量
- us_kline_1d: 全市场1.2万+只近1年日线，每晚更新
- us_stock_master(1.3万) / hk_stock_master(2817): 代码+名称主表

连接：env FINDATA_DB_URL（postgresql://user:pass@host:5432/dbname）
连不上时各函数返回空值，不抛异常打崩接口。
"""
import os
import logging
import psycopg2

log = logging.getLogger(__name__)

FINDATA_DB_URL = os.getenv("FINDATA_DB_URL", "")

_KLINE_TABLE = {"1m": "us_kline_1m", "5m": "us_kline_5m"}


def _conn():
    if not FINDATA_DB_URL:
        raise RuntimeError("FINDATA_DB_URL not configured")
    return psycopg2.connect(FINDATA_DB_URL, connect_timeout=5)


def us_kline(symbol: str, period: str = "1d", limit: int = 250) -> list[dict]:
    """美股K线。period: 1m/5m/1d。返回时间升序 [{ts,open,high,low,close,volume}]"""
    symbol = symbol.upper()
    limit = min(max(limit, 1), 2000)
    try:
        conn = _conn()
        cur = conn.cursor()
        if period == "1d":
            cur.execute(
                "SELECT trade_date, open, high, low, close, volume FROM us_kline_1d "
                "WHERE symbol = %s ORDER BY trade_date DESC LIMIT %s", (symbol, limit))
            rows = cur.fetchall()
            conn.close()
            return [{"ts": r[0].isoformat(), "open": float(r[1]), "high": float(r[2]),
                     "low": float(r[3]), "close": float(r[4]), "volume": int(r[5] or 0)}
                    for r in reversed(rows)]
        table = _KLINE_TABLE.get(period)
        if not table:
            return []
        cur.execute(
            f"SELECT ts, open, high, low, close, volume FROM {table} "
            "WHERE symbol = %s ORDER BY ts DESC LIMIT %s", (symbol, limit))
        rows = cur.fetchall()
        conn.close()
        return [{"ts": r[0].isoformat(), "open": float(r[1]), "high": float(r[2]),
                 "low": float(r[3]), "close": float(r[4]), "volume": int(r[5] or 0)}
                for r in reversed(rows)]
    except Exception as e:
        log.warning("findata us_kline %s %s failed: %s", symbol, period, e)
        return []


# 中概股 美股ADR↔港股 双重上市对照(常见对)
DUAL_LISTED = {
    "BABA": "09988", "JD": "09618", "BIDU": "09888", "NTES": "09999",
    "LI": "02015", "XPEV": "09868", "NIO": "09866", "TME": "01698",
    "BILI": "09626", "YUMC": "09987", "TCOM": "09961", "ZTO": "02057",
}
DUAL_LISTED_HK = {v: k for k, v in DUAL_LISTED.items()}


def us_quote(symbol: str) -> dict | None:
    """美股快照：最新1分钟bar价 + 上一交易日收盘算涨跌。
    盘前/盘后时段额外给出 regular_price(正常时段收盘) 与 ext_price(延时价)分离。"""
    from zoneinfo import ZoneInfo
    symbol = symbol.upper()
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("SELECT ts, close FROM us_kline_1m WHERE symbol = %s ORDER BY ts DESC LIMIT 1", (symbol,))
        m = cur.fetchone()
        cur.execute(
            "SELECT trade_date, close FROM us_kline_1d WHERE symbol = %s ORDER BY trade_date DESC LIMIT 2",
            (symbol,))
        d = cur.fetchall()
        cur.execute("SELECT name_en, name_cn, exchange, is_etf FROM us_stock_master WHERE symbol = %s", (symbol,))
        master = cur.fetchone()
        cur.execute("SELECT min(cal_date) FROM us_earnings_calendar WHERE symbol = %s AND cal_date >= CURRENT_DATE",
                    (symbol,))
        next_earn = cur.fetchone()[0]
        conn.close()
        if not d:
            return None
        latest_daily_date, latest_daily_close = d[0][0], float(d[0][1])
        prev_close = float(d[1][1]) if len(d) > 1 else latest_daily_close
        ext_price = ext_label = None
        if m is not None:
            price, ts = float(m[1]), m[0]
            base = latest_daily_close if ts.date() > latest_daily_date else prev_close
            # 三段分离: 最新bar落在盘前/盘后时段时, 单独给出延时段价格
            ny = ts.astimezone(ZoneInfo("America/New_York"))
            hm = ny.hour * 60 + ny.minute
            if ny.weekday() < 5 and (hm < 570 or hm >= 960):  # 9:30=570, 16:00=960
                ext_price = price
                ext_label = "盘前" if hm < 570 else "盘后"
        else:
            price, ts, base = latest_daily_close, None, prev_close
        change_pct = round((price - base) / base * 100, 2) if base else None
        from datetime import date as _date
        return {
            "code": symbol, "market": "US", "currency": "USD",
            "name": (master[1] or master[0]) if master else symbol,
            "name_en": master[0] if master else "",
            "exchange": master[2] if master else "",
            "is_etf": bool(master[3]) if master else False,
            "price": price, "prev_close": base, "change_pct": change_pct,
            "regular_price": latest_daily_close,      # 最近正常时段收盘
            "ext_price": ext_price, "ext_label": ext_label,  # 盘前/盘后延时价(非延时段为null)
            "earnings_in_days": (next_earn - _date.today()).days if next_earn else None,
            "dual_hk": DUAL_LISTED.get(symbol),        # 美港双重上市: 对应港股代码
            "ts": ts.isoformat() if ts else latest_daily_date.isoformat(),
            "delayed": True,  # 免费档REST最近15分钟不可见
        }
    except Exception as e:
        log.warning("findata us_quote %s failed: %s", symbol, e)
        return None


def hk_master(code: str) -> dict | None:
    code = code.zfill(5)
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("SELECT code, name, lot_size FROM hk_stock_master WHERE code = %s", (code,))
        r = cur.fetchone()
        conn.close()
        if not r:
            return None
        return {"code": r[0], "name": r[1], "lot_size": r[2]}
    except Exception as e:
        log.warning("findata hk_master %s failed: %s", code, e)
        return None


# 发现页 ETF 热度榜白名单（主流指数/行业/杠杆）
ETF_HOT_LIST = [
    ("QQQ", "纳指100"), ("SPY", "标普500"), ("DIA", "道指"), ("IWM", "罗素2000"),
    ("SMH", "半导体"), ("SOXX", "费城半导体"), ("XLK", "科技"), ("XLE", "能源"),
    ("XLF", "金融"), ("ARKK", "ARK创新"), ("TQQQ", "3×纳指"), ("KWEB", "中概互联"),
    ("FXI", "富时中国"), ("GLD", "黄金"), ("TLT", "20年美债"),
]


def us_news_db(symbol: str, limit: int = 12) -> list[dict]:
    """新闻读库(us_news, 每日采集入库); 空则由调用方回退实时源"""
    try:
        conn = _conn(); cur = conn.cursor()
        cur.execute("""SELECT headline, source, url, published_at FROM us_news
                       WHERE %s = ANY(symbols) ORDER BY published_at DESC LIMIT %s""",
                    (symbol.upper(), limit))
        rows = cur.fetchall(); conn.close()
        return [{"title": r[0], "source": r[1], "url": r[2],
                 "ts": r[3].isoformat() if r[3] else "", "lang": "en"} for r in rows]
    except Exception as e:
        log.warning("us_news_db %s failed: %s", symbol, e)
        return []


def earnings_week_db() -> list[dict]:
    """财报日历读库: 今起7个自然日, 每天按市值前30"""
    wd_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    try:
        conn = _conn(); cur = conn.cursor()
        cur.execute("""SELECT cal_date, symbol, name, when_time, eps_forecast, mcap
                       FROM us_earnings_calendar
                       WHERE cal_date >= CURRENT_DATE AND cal_date < CURRENT_DATE + 8
                       ORDER BY cal_date, mcap DESC NULLS LAST""")
        rows = cur.fetchall(); conn.close()
        days: dict = {}
        for d, sym, name, when, eps, mcap in rows:
            k = d.isoformat()
            if k not in days:
                days[k] = {"date": k, "weekday": wd_cn[d.weekday()], "items": []}
            if len(days[k]["items"]) < 30:
                days[k]["items"].append({"symbol": sym, "name": name or "", "when": when or "",
                                         "eps_forecast": eps or "", "mcap": float(mcap or 0)})
        return list(days.values())
    except Exception as e:
        log.warning("earnings_week_db failed: %s", e)
        return []


def us_filings_db(symbol: str, limit: int = 10) -> list[dict]:
    """公告读库(us_filings, EDGAR每日索引入库)"""
    labels = {"8-K": "重大事件报告", "10-Q": "季度报告", "10-K": "年度报告",
              "6-K": "外国公司报告", "20-F": "外国公司年报", "S-1": "上市注册",
              "DEF 14A": "股东大会文件", "4": "内部人交易"}
    try:
        conn = _conn(); cur = conn.cursor()
        cur.execute("""SELECT form, filed_date, url FROM us_filings
                       WHERE symbol = %s ORDER BY filed_date DESC LIMIT %s""",
                    (symbol.upper(), limit))
        rows = cur.fetchall(); conn.close()
        return [{"form": r[0], "title": labels.get(r[0], r[0]),
                 "date": r[1].isoformat(), "url": r[2]} for r in rows]
    except Exception as e:
        log.warning("us_filings_db %s failed: %s", symbol, e)
        return []


def hk_kline_db(code: str, period: str = "1d", limit: int = 250) -> list[dict]:
    """港股K线读库(每日采集入库); 空则调用方回退Yahoo实时"""
    code = code.zfill(5)
    try:
        conn = _conn(); cur = conn.cursor()
        if period == "1d":
            cur.execute("""SELECT trade_date, open, high, low, close, volume FROM hk_kline_1d
                           WHERE code = %s ORDER BY trade_date DESC LIMIT %s""", (code, limit))
            rows = cur.fetchall(); conn.close()
            return [{"ts": r[0].isoformat(), "open": float(r[1]), "high": float(r[2]),
                     "low": float(r[3]), "close": float(r[4]), "volume": int(r[5] or 0)}
                    for r in reversed(rows)]
        if period == "5m":
            cur.execute("""SELECT ts, open, high, low, close, volume FROM hk_kline_5m
                           WHERE code = %s ORDER BY ts DESC LIMIT %s""", (code, limit))
            rows = cur.fetchall(); conn.close()
            return [{"ts": r[0].isoformat(), "open": float(r[1]), "high": float(r[2]),
                     "low": float(r[3]), "close": float(r[4]), "volume": int(r[5] or 0)}
                    for r in reversed(rows)]
        conn.close()
        return []
    except Exception as e:
        log.warning("hk_kline_db %s %s failed: %s", code, period, e)
        return []


def hk_news_db(code: str, limit: int = 12) -> list[dict]:
    code = code.zfill(5)
    try:
        conn = _conn(); cur = conn.cursor()
        cur.execute("""SELECT title, source, url, published_at FROM hk_news
                       WHERE code = %s ORDER BY published_at DESC NULLS LAST LIMIT %s""",
                    (code, limit))
        rows = cur.fetchall(); conn.close()
        return [{"title": r[0], "source": r[1], "url": r[2],
                 "ts": r[3].isoformat() if r[3] else "", "lang": "en"} for r in rows]
    except Exception as e:
        log.warning("hk_news_db %s failed: %s", code, e)
        return []


def hk_filings_db(code: str, limit: int = 10) -> list[dict]:
    code = code.zfill(5)
    try:
        conn = _conn(); cur = conn.cursor()
        cur.execute("""SELECT title, filed_at, url FROM hk_filings
                       WHERE code = %s ORDER BY filed_at DESC NULLS LAST LIMIT %s""",
                    (code, limit))
        rows = cur.fetchall(); conn.close()
        return [{"form": "公告", "title": r[0],
                 "date": r[1].date().isoformat() if r[1] else "", "url": r[2]} for r in rows]
    except Exception as e:
        log.warning("hk_filings_db %s failed: %s", code, e)
        return []


def hk_fin_db(code: str, limit: int = 4) -> list[dict]:
    """港股财务指标读库(hk_fin_indicator, 年度多期), 含营收/净利YoY"""
    code = code.zfill(5)
    try:
        conn = _conn(); cur = conn.cursor()
        cur.execute("""SELECT report_date, oi, net_profit, basic_eps, bps FROM hk_fin_indicator
                       WHERE code = %s ORDER BY report_date DESC LIMIT %s""", (code, limit + 1))
        rows = cur.fetchall(); conn.close()
        out = []
        for i in range(min(len(rows), limit)):
            rd, oi, np_, eps, bps = rows[i]
            nxt = rows[i + 1] if i + 1 < len(rows) else None

            def yoy(cur_v, prev_v):
                if cur_v is None or not prev_v:
                    return None
                return round((float(cur_v) - float(prev_v)) / abs(float(prev_v)) * 100, 1)
            out.append({
                "report_date": rd.isoformat(),
                "oi_yi": round(float(oi) / 1e8, 1) if oi is not None else None,
                "net_profit_yi": round(float(np_) / 1e8, 1) if np_ is not None else None,
                "oi_yoy": yoy(oi, nxt[1]) if nxt else None,
                "net_profit_yoy": yoy(np_, nxt[2]) if nxt else None,
                "basic_eps": float(eps) if eps is not None else None,
                "bps": float(bps) if bps is not None else None,
            })
        return out
    except Exception as e:
        log.warning("hk_fin_db %s failed: %s", code, e)
        return []


def analysts_top(days: int = 10, limit: int = 15) -> list[dict]:
    """近days天分析师上调/新覆盖动向(发现页'分析师动向'模块)"""
    try:
        conn = _conn(); cur = conn.cursor()
        cur.execute("""SELECT symbol, rate_date, firm, action, from_grade, to_grade
                       FROM us_analyst_ratings
                       WHERE rate_date >= CURRENT_DATE - %s AND action IN ('up','init')
                       ORDER BY rate_date DESC LIMIT %s""", (days, limit))
        rows = cur.fetchall(); conn.close()
        return [{"symbol": r[0], "date": r[1].isoformat(), "firm": r[2],
                 "action": "上调" if r[3] == "up" else "新覆盖",
                 "from_grade": r[4] or "", "to_grade": r[5] or ""} for r in rows]
    except Exception as e:
        log.warning("analysts_top failed: %s", e)
        return []


def analysts_by_symbol(symbol: str, limit: int = 10) -> list[dict]:
    try:
        conn = _conn(); cur = conn.cursor()
        cur.execute("""SELECT rate_date, firm, action, from_grade, to_grade
                       FROM us_analyst_ratings WHERE symbol = %s
                       ORDER BY rate_date DESC LIMIT %s""", (symbol.upper(), limit))
        rows = cur.fetchall(); conn.close()
        act_cn = {"up": "上调", "down": "下调", "init": "新覆盖", "main": "维持", "reit": "重申"}
        return [{"date": r[0].isoformat(), "firm": r[1], "action": act_cn.get(r[2], r[2]),
                 "from_grade": r[3] or "", "to_grade": r[4] or ""} for r in rows]
    except Exception as e:
        log.warning("analysts_by_symbol %s failed: %s", symbol, e)
        return []


def etf_hot() -> list[dict]:
    """ETF 热度榜：白名单最近两根日线算涨跌幅，按涨幅排序"""
    try:
        conn = _conn()
        cur = conn.cursor()
        out = []
        for sym, label in ETF_HOT_LIST:
            cur.execute(
                "SELECT trade_date, close FROM us_kline_1d WHERE symbol = %s "
                "ORDER BY trade_date DESC LIMIT 2", (sym,))
            rows = cur.fetchall()
            if len(rows) < 2:
                continue
            latest, prev = float(rows[0][1]), float(rows[1][1])
            out.append({
                "code": sym, "label": label, "price": latest,
                "change_pct": round((latest - prev) / prev * 100, 2),
                "date": rows[0][0].isoformat(),
            })
        conn.close()
        out.sort(key=lambda x: -x["change_pct"])
        return out
    except Exception as e:
        log.warning("findata etf_hot failed: %s", e)
        return []
