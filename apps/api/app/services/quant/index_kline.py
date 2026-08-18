"""指数日线 —— 回测基准的数据来源(`_17` §2)。

## 为什么单独一个模块

`_17` 排查发现:前端画的基准线是 `1 + i * 0.005`(一条编出来的直线),
而后端 `grep benchmark` **0 处命中**。Phase B 计划里写过要做
(`03_phase-b` §271),没落地。

做基准的前提是有指数行情,而本地 `klines` 里 `000300 / 399300`
**一条都没有**。所以先解决数据。

## 为什么塞进 klines 表而不新建一张

`klines` 的唯一键是 `(code, period, ts)`,指数用 `code='000300'` 就能存,
与个股不冲突。新建一张表要多写一套读写、多一处迁移,
而基准要的字段(ts + close)和个股完全一样。

个股代码 6 位、指数代码也 6 位,靠 `_INDEX_CODES` 白名单区分 ——
不靠"看起来像不像指数"这种猜测。
"""
from __future__ import annotations

import logging
import os
from datetime import date, timedelta

import requests

from app.services.database import get_conn

log = logging.getLogger(__name__)

# 走国内 AK 代理,不直连。
#
# 实测(2026-08-18):容器直连 AKShare 拉指数 → RemoteDisconnected。
# 而且 `index_zh_a_hist` **在国内服务器上也连不通** —— 那个接口本身坏了,
# 换代理也没用(与 `stock_individual_info_em` 同一个毛病)。
# 能用的是新浪源 `stock_zh_index_daily`,实测 5972 行到 2026-08-17。
_AK_BASE = os.getenv("AK_PROXY_URL", "http://139.199.221.232:8765")
_AK_TOKEN = os.getenv("AK_API_TOKEN", "ak-proxy-2026")

# 新浪源要带交易所前缀:sh000300 / sz399006
_SINA_PREFIX = {"000300": "sh", "000905": "sh", "000852": "sh",
                "000001": "sh", "399006": "sz"}

# 支持的基准。key = 存进 klines 的 code,value = (AKShare symbol, 显示名)
#
# AKShare 的 index_zh_a_hist 用的是**不带前缀的 6 位代码**,
# 与我们存的一致,所以两边同一个值。
INDEX_CODES: dict[str, tuple[str, str]] = {
    "000300": ("000300", "沪深 300"),
    "000905": ("000905", "中证 500"),
    "000852": ("000852", "中证 1000"),
    "399006": ("399006", "创业板指"),
    "000001": ("000001", "上证指数"),
}


def is_index(code: str) -> bool:
    return code in INDEX_CODES


def backfill(index_code: str, start: date, end: date | None = None) -> dict:
    """拉指数日线入库。返回 {code, fetched, written, range}。

    **失败返回结构化错误而不是抛** —— 调用方(定时任务 / 手工脚本)要能
    据此决定是重试还是跳过,而不是整个流程崩掉。
    """
    if index_code not in INDEX_CODES:
        return {"error": "unknown_index", "code": index_code,
                "supported": sorted(INDEX_CODES)}
    symbol, label = INDEX_CODES[index_code]
    end = end or date.today()

    sina_symbol = _SINA_PREFIX.get(index_code, "sh") + symbol
    try:
        r = requests.post(
            f"{_AK_BASE}/call",
            json={"func": "stock_zh_index_daily", "kwargs": {"symbol": sina_symbol}},
            headers={"Authorization": f"Bearer {_AK_TOKEN}"}, timeout=90,
        )
        if r.status_code != 200:
            return {"error": "proxy_error", "code": index_code,
                    "status": r.status_code, "message": r.text[:200]}
        payload = r.json()
    except Exception as e:                                    # noqa: BLE001
        log.error("[index_kline] 拉 %s(%s) 失败: %s", index_code, label, e)
        return {"error": "fetch_failed", "code": index_code, "message": str(e)[:200]}

    # 代理返回可能是 list,也可能包在 data/records 里 —— 三种都认
    raw = payload if isinstance(payload, list) else (
        payload.get("data") or payload.get("records") or [])
    if not raw:
        return {"error": "empty", "code": index_code,
                "message": f"{label} 没有返回数据"}

    # 新浪源给的是**全历史**(5900+ 行),这里按区间裁剪 ——
    # 全量入库也行,但没必要,而且首次回填会慢很多
    lo, hi = start.isoformat(), end.isoformat()
    df = [x for x in raw if lo <= str(x.get("date", ""))[:10] <= hi]
    if not df:
        return {"error": "empty_in_range", "code": index_code,
                "message": f"{label} 在 {start}~{end} 内没有数据(全量 {len(raw)} 行)"}

    # 新浪源列名固定:date / open / high / low / close / volume。
    # **找不到必需列就报错,不猜** —— 猜错了会把成交量当收盘价存进去,
    # 而那种错在回测结果里完全看不出来。
    sample = df[0]
    if "date" not in sample or "close" not in sample:
        return {"error": "unknown_columns", "code": index_code,
                "columns": list(sample.keys()),
                "message": "返回的列名不认识 —— 可能上游换了源,不猜着解析"}

    rows = []
    for x in df:
        ts = str(x.get("date"))[:10]
        c = _f(x, "close")
        if not ts or c is None:
            continue
        rows.append((index_code, "daily", ts,
                     _f(x, "open"), _f(x, "high"), _f(x, "low"), c,
                     int(_f(x, "volume") or 0)))

    if not rows:
        return {"error": "no_valid_rows", "code": index_code}

    conn = get_conn(); cur = conn.cursor()
    n = 0
    try:
        for (code, period, ts, o, h, l, c, v) in rows:
            cur.execute(
                """INSERT INTO klines (code, period, ts, open, high, low, close, volume)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (code, period, ts) DO UPDATE
                     SET close = EXCLUDED.close, open = EXCLUDED.open,
                         high = EXCLUDED.high, low = EXCLUDED.low,
                         volume = EXCLUDED.volume""",
                (code, period, ts, o, h, l, c, v),
            )
            n += cur.rowcount
        conn.commit()
    finally:
        cur.close(); conn.close()

    log.info("[index_kline] %s(%s) %s~%s · 取到 %d 行 · 写入 %d",
             index_code, label, start, end, len(rows), n)
    return {"code": index_code, "label": label,
            "fetched": len(rows), "written": n,
            "range": [rows[0][2], rows[-1][2]]}


def _f(d: dict, key: str) -> float | None:
    try:
        v = float(d.get(key))
        return None if v != v else v        # NaN → None
    except (TypeError, ValueError):
        return None


def series(index_code: str, start: date, end: date) -> list[tuple[date, float]]:
    """取一段收盘价序列 · 按日期升序。"""
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute(
            """SELECT ts, close FROM klines
               WHERE code=%s AND period='daily' AND ts BETWEEN %s AND %s
                 AND close IS NOT NULL
               ORDER BY ts""",
            (index_code, start, end),
        )
        return [(t, float(c)) for t, c in cur.fetchall()]
    finally:
        cur.close(); conn.close()


def close_on_or_before(index_code: str, d: date, lookback_days: int = 10) -> float | None:
    """取 `d` 当天或之前最近一个交易日的收盘价。

    调仓日可能是非交易日(节假日),所以要往前找。
    找不到返回 None —— 调用方据此判断"这段没有基准",
    **不要补 0 或补前值**:补出来的基准会让超额收益看起来很漂亮。
    """
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute(
            """SELECT close FROM klines
               WHERE code=%s AND period='daily' AND ts <= %s AND ts >= %s
                 AND close IS NOT NULL
               ORDER BY ts DESC LIMIT 1""",
            (index_code, d, d - timedelta(days=lookback_days)),
        )
        r = cur.fetchone()
        return float(r[0]) if r else None
    finally:
        cur.close(); conn.close()


def coverage(index_code: str) -> dict:
    """这个指数在库里覆盖了什么区间 —— 回测前判断能不能用。"""
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute(
            """SELECT count(*), min(ts), max(ts) FROM klines
               WHERE code=%s AND period='daily'""",
            (index_code,),
        )
        n, lo, hi = cur.fetchone()
        return {"code": index_code, "rows": n or 0,
                "start": lo.isoformat() if lo else None,
                "end": hi.isoformat() if hi else None}
    finally:
        cur.close(); conn.close()
