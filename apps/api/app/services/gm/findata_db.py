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


# ── 网关回落 ──────────────────────────────────────────────────
#
# 这个文件原来只有一条路:直连 financedata 库。开源版拿不到 FINDATA_DB_URL,
# 于是美股查询要么 404、要么返回 200 + 空数组(装作成功)。
#
# 2026-08-15 finance-data 新增了 /api/v1/{us,hk}/* 端点,hunter 网关也放行了,
# 所以现在多一条路:**库优先 · 库不可用时走网关**。
#   · 私有部署配了 FINDATA_DB_URL → 行为完全不变,仍走直连(更快、覆盖更全)
#   · 开源版用户 → 一把 hunt_tools_ key 走网关
#
# 为什么不干脆全走网关:直连能拿到网关没暴露的东西(财报日历、全市场排行、
# ETF 榜),而且私有部署走内网直连本来就更快。留两条路的成本只是这几十行。
#
# 网关侧**没有**的:财报日历 · 港股新闻/公告/财报 · 全市场分析师排行 · ETF 榜
# (对应 _15 方案里的 C 组,量小,先没做)。这些函数在开源版仍返回空 ——
# 但那是"上游确实没这个接口",不是静默降级,数据源注册表里如实标着。

def _db_available() -> bool:
    return bool(FINDATA_DB_URL)


def _gw_get(path: str, params: dict | None = None):
    """经 hunter 网关取数。失败一律返回 None,由调用方决定怎么办。

    凭证走 finance_data_auth 这个唯一入口 —— 之前这套 fallback 抄在四个文件里,
    结果网页填的 key 喂不到深度分析且不报错。
    """
    try:
        import httpx
        from app.services import finance_data_auth as _auth
        r = httpx.get(f"{_auth.data_url()}{path}", params=params or {},
                      headers=_auth.data_headers(), timeout=20.0)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning("gm gateway %s failed: %s", path, e)
        return None


def us_kline(symbol: str, period: str = "1d", limit: int = 250) -> list[dict]:
    """美股K线。period: 1m/5m/1d。返回时间升序 [{ts,open,high,low,close,volume}]"""
    symbol = symbol.upper()
    limit = min(max(limit, 1), 2000)
    if not _db_available():
        d = _gw_get(f"/api/v1/us/kline/{symbol}", {"period": period, "limit": limit})
        return (d or {}).get("bars") or []
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
    if not _db_available():
        return _us_quote_via_gateway(symbol)
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


def _us_quote_via_gateway(symbol: str) -> dict | None:
    """网关版美股快照 —— 用日线 + 主表拼,比 Yahoo 那条路多出中文名/交易所/ETF 标识。

    与直连版的差别(如实记在这里,不假装等价):
      · 没有 `earnings_in_days` —— 网关侧没暴露财报日历(us_earnings_calendar)
      · 没有盘前/盘后分离 —— 那要 1 分钟线的最新 bar,为一个字段拉一次分钟线不划算
    拿不到就返回 None,调用方(gm/quote.py)会继续回落到 Yahoo。
    """
    d = _gw_get(f"/api/v1/us/kline/{symbol}", {"period": "1d", "limit": 2})
    bars = (d or {}).get("bars") or []
    if not bars:
        return None
    last = bars[-1]
    prev = float(bars[-2]["close"]) if len(bars) > 1 else float(last["close"])
    price = float(last["close"])
    m = _gw_get("/api/v1/us/master", {"symbol": symbol}) or {}
    return {
        "code": symbol, "market": "US", "currency": "USD",
        "name": m.get("name_cn") or m.get("name_en") or symbol,
        "name_en": m.get("name_en") or "",
        "exchange": m.get("exchange") or "",
        "is_etf": bool(m.get("is_etf")),
        "price": price, "prev_close": prev,
        "change_pct": round((price - prev) / prev * 100, 2) if prev else None,
        "regular_price": price, "ext_price": None, "ext_label": None,
        "earnings_in_days": None,
        "dual_hk": DUAL_LISTED.get(symbol),
        "ts": last["ts"], "delayed": True,
    }


# 港股主表 CSV(`_24` §8.2⑤)· 由 scripts/gen_hk_master_csv.py 从港交所官方
# ListOfSecurities 生成,随仓库分发,挂载在 /opt/hunter-data。
#
# 它把 hk.master 这条从「平台自建」降级成「仓库里的一个文件」——
# 原来它走我们的网关或我们的库,开源用户拿不到,而它其实只是一张
# 静态对照表:不是服务、不需要 key、不需要实时。
_HK_CSV = os.getenv("HUNTER_HK_MASTER_CSV", "/opt/hunter-data/hk_master.csv")
_hk_csv_cache: dict[str, dict] | None = None


def _hk_from_csv(code: str) -> dict | None:
    """从 CSV 查。文件不存在或读不出来返回 None(由调用方回落)。

    整份读进内存缓存:2800 行、88KB,一次读完比每次扫文件划算得多,
    而且这张表在进程生命周期内不会变(要更新得重跑生成脚本)。
    """
    global _hk_csv_cache
    if _hk_csv_cache is None:
        _hk_csv_cache = {}
        try:
            import csv as _csv
            with open(_HK_CSV, encoding="utf-8") as f:
                # 前三行是 # 注释(来源与生成日期)—— 过滤掉再交给 DictReader,
                # 否则表头会被解析成第一条数据
                rows = _csv.DictReader(
                    (ln for ln in f if not ln.startswith("#")))
                for r in rows:
                    c = (r.get("code") or "").strip()
                    if c:
                        _hk_csv_cache[c] = r
            log.info("[hk_master] 载入 CSV %s · %d 条", _HK_CSV, len(_hk_csv_cache))
        except FileNotFoundError:
            log.info("[hk_master] 没有 CSV(%s)· 回落到库/网关", _HK_CSV)
        except Exception as e:                                 # noqa: BLE001
            log.warning("[hk_master] 读 CSV 失败: %s", e)
    r = _hk_csv_cache.get(code)
    if not r:
        return None
    lot = (r.get("lot_size") or "").strip()
    return {
        "code": r["code"],
        # ⚠️ 港交所公开数据里**只有英文名**。原来那张表的中文名是我们
        # 自己补的,不在公开数据里 —— 这里不臆造,name 直接给英文名。
        # 宁可显示英文,也不要从别处凑一份可能对不上的翻译:
        # 代码-名称对错一个,用户看到的就是另一家公司。
        "name": r.get("name_en") or "",
        "name_trad": "",
        "category": r.get("category") or "",
        "lot_size": int(lot) if lot.isdigit() else None,
    }


def hk_master(code: str) -> dict | None:
    code = code.zfill(5)
    # **先查 CSV。**开源版没有我们的库也没有网关,这是唯一能走通的路;
    # 有库的部署里 CSV 也够用(它就是一张代码对照表),少一次查询
    hit = _hk_from_csv(code)
    if hit:
        return hit
    # CSV 里没有 = 真的没有。**不回落到我们的网关。**
    #
    # CSV 收了股票/ETF/REITs 共 3226 条,覆盖用户会分析的全部标的;
    # 剩下的是当天发行当天到期的权证和牛熊证,查它们的"主表"没有意义。
    #
    # 回落到网关会让开源用户在查一个不存在的代码时,悄悄打一次
    # hunter.agentpit.io —— 那正是这次要去掉的依赖,而且它还会失败得很慢。
    if _hk_csv_cache:
        return None
    # CSV 没载入成功(文件缺失/读失败)才走老路 —— 这条是给
    # 有我们的库或网关的部署留的,开源版走不到
    if not _db_available():
        d = _gw_get("/api/v1/hk/master", {"code": code})
        if not d or "code" not in d:
            return None
        return {"code": d["code"], "name": d.get("name") or "",
                "name_trad": d.get("name_trad") or "",
                "category": d.get("category") or "",
                "lot_size": d.get("lot_size")}
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
    if not _db_available():
        d = _gw_get("/api/v1/us/news", {"symbol": symbol.upper(), "limit": limit})
        return [{"title": i.get("headline") or "", "source": i.get("source") or "",
                 "url": i.get("url") or "", "ts": i.get("published_at") or "", "lang": "en"}
                for i in ((d or {}).get("items") or [])]
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
    if not _db_available():
        d = _gw_get(f"/api/v1/us/filings/{symbol.upper()}", {"limit": limit})
        return [{"form": i.get("form") or "",
                 "title": labels.get(i.get("form"), i.get("form") or ""),
                 "date": i.get("filed_date") or "", "url": i.get("url") or ""}
                for i in ((d or {}).get("items") or [])]
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
    if not _db_available():
        if period not in ("1d", "5m"):        # 网关侧港股只有这两档
            return []
        d = _gw_get(f"/api/v1/hk/kline/{code.zfill(5)}", {"period": period, "limit": limit})
        return (d or {}).get("bars") or []
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
    if not _db_available():
        d = _gw_get(f"/api/v1/hk/filings/{code.zfill(5)}", {"limit": limit})
        return [{"title": i.get("title") or "", "url": i.get("url") or "",
                 "date": (i.get("filed_at") or "")[:10]}
                for i in ((d or {}).get("items") or [])]
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
    if not _db_available():
        d = _gw_get(f"/api/v1/hk/financial/{code.zfill(5)}", {"limit": limit})
        return [{"report_date": i.get("report_date") or "",
                 "eps": i.get("basic_eps"), "bps": i.get("bps"),
                 # 上游给原始单位,这里换算成亿 —— 与直连版保持同一口径
                 "oi": (i.get("oi") or 0) / 1e8 if i.get("oi") else None,
                 "net_profit": (i.get("net_profit") or 0) / 1e8 if i.get("net_profit") else None}
                for i in ((d or {}).get("items") or [])]
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
    if not _db_available():
        d = _gw_get(f"/api/v1/us/analysts/{symbol.upper()}", {"limit": limit})
        return [{"date": i.get("rate_date") or "", "firm": i.get("firm") or "",
                 "action": i.get("action") or "", "from_grade": i.get("from_grade") or "",
                 "to_grade": i.get("to_grade") or ""}
                for i in ((d or {}).get("items") or [])]
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
