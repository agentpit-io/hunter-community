"""E-4 · universe 解析 · 从 index_component 表拉真成分股
(Phase E · 2026-08-18)

数据流:
- 首次:AKShare `index_stock_cons_csindex(symbol='000300')` → seed index_component
- 每月:APScheduler 1 号 09:00 CST · reconcile diff · 加新 · 关闭旧
- 平时:strategy_engine._resolve_universe → _query_index_current(index_code)
"""
from __future__ import annotations

import logging
from datetime import date

from app.services.database import get_conn

log = logging.getLogger(__name__)


INDEX_MAP = {
    "hs300":  ("000300", "沪深 300"),
    "zz500":  ("000905", "中证 500"),
    "zz1000": ("000852", "中证 1000"),
}


def query_current(index_code: str) -> list[str]:
    """当前成分股 · effective_to IS NULL

    同 `query_active_at`:表不存在时返回空而不是抛 —— 让 `resolve()` 的
    fallback 分支能真的兜住。
    """
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute(
            """SELECT stock_code FROM index_component
               WHERE index_code=%s AND effective_to IS NULL
               ORDER BY stock_code""",
            (index_code,),
        )
        codes = [r[0] for r in cur.fetchall()]
    except Exception as e:                                    # noqa: BLE001
        log.warning("[universe] 查当前成分失败(回落到 stocks 表): %s", e)
        codes = []
    finally:
        cur.close(); conn.close()
    return codes


def query_active_at(index_code: str, on_date: date) -> list[str]:
    """指定日期的成分股(生存者偏差防治)· effective_from <= on_date AND (to IS NULL OR to > on_date)

    **表不存在时返回空而不是抛异常。** `resolve()` 的注释写的是
    「未 seed 时 fallback」,但它只处理"查到空",不处理"表都还没建" ——
    于是任何没跑 `sql/20260818_index_component.sql` 的部署,
    一跑回测就 500(psycopg2.errors.UndefinedTable)。实测踩到。

    开源版用户 clone 下来第一次跑就会撞上这个,而错误信息是一句
    SQL 报错,他不会想到是缺一个迁移。
    """
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute(
            """SELECT stock_code FROM index_component
               WHERE index_code=%s
                 AND effective_from <= %s
                 AND (effective_to IS NULL OR effective_to > %s)
               ORDER BY stock_code""",
            (index_code, on_date, on_date),
        )
        codes = [r[0] for r in cur.fetchall()]
    except Exception as e:                                    # noqa: BLE001
        log.warning("[universe] 查历史成分失败(回落到 stocks 表): %s", e)
        codes = []
    finally:
        cur.close(); conn.close()
    return codes


def _fetch_cons(index_code: str) -> list[str]:
    """拉指数成分股 —— **走国内 AK 代理,不直连**。

    容器里 `import akshare` 直接调会 RemoteDisconnected(2026-08-18 实测,
    与指数日线同一个问题)。代理在 139.199.221.232,
    `index_stock_cons_csindex` 已加入白名单。

    拿不到返回空列表 —— 上层据此跳过,而不是把空当成"这个指数没有成分股"
    写进库(那会把整个股票池清空)。
    """
    import os
    import requests
    base = os.getenv("AK_PROXY_URL", "http://139.199.221.232:8765")
    token = os.getenv("AK_API_TOKEN", "ak-proxy-2026")
    try:
        r = requests.post(
            f"{base}/call",
            json={"func": "index_stock_cons_csindex", "kwargs": {"symbol": index_code}},
            # 300 秒:代理那边是同步调 AKShare,沪深300 的 300 只实测要 100+ 秒,
            # 偶尔更久。超时太短的表现是"有时成功有时 seed 0 行",
            # 而 seed 0 行不报错,只是股票池悄悄回落到 stocks 表
            headers={"Authorization": f"Bearer {token}"}, timeout=300,
        )
        if r.status_code != 200:
            log.error("[universe] 代理拉 %s 失败 HTTP %s: %s",
                      index_code, r.status_code, r.text[:200])
            return []
        d = r.json()
    except Exception as e:                                    # noqa: BLE001
        log.error("[universe] 拉 %s 失败: %s", index_code, e)
        return []

    rows = d if isinstance(d, list) else (d.get("data") or d.get("records") or [])
    if not rows:
        return []
    # 列名兼容。**找不到就返回空并报错,不猜** —— 猜错了会把指数代码
    # 当成分股写进去,而那种错要等回测选出一堆不存在的票才会暴露
    key = next((k for k in ("成分券代码", "code", "constituent_code", "stock_code")
                if k in rows[0]), None)
    if not key:
        log.error("[universe] %s 找不到 code 列 · keys=%s", index_code, list(rows[0])[:10])
        return []
    return [str(x[key]).zfill(6) for x in rows if x.get(key)]


def seed_current(index_code: str) -> int:
    """拉当前成分 · 首次填 index_component · 返回入库行数"""
    codes = _fetch_cons(index_code)
    if not codes:
        return 0

    conn = get_conn(); cur = conn.cursor()
    n = 0
    for c in codes:
        cur.execute(
            """INSERT INTO index_component (index_code, stock_code, effective_from)
               VALUES (%s, %s, %s)
               ON CONFLICT (index_code, stock_code, effective_from) DO NOTHING""",
            (index_code, c, date.today()),
        )
        if cur.rowcount > 0:
            n += 1
    conn.commit(); cur.close(); conn.close()
    log.info(f"[universe] seeded {index_code} · +{n} rows")
    return n


def reconcile_current(index_code: str) -> dict:
    """diff 当前 vs AKShare 最新 · 加入变动 · 关闭旧
    返回 {added: [...], removed: [...]}
    """
    # 与 seed_current 共用 _fetch_cons —— 两处各写一份取数逻辑,
    # 改了一处忘另一处就会出现"首次能 seed、每月 reconcile 却一直失败"
    codes = _fetch_cons(index_code)
    if not codes:
        return {"error": "fetch_failed_or_empty", "index": index_code}
    new_set = set(codes)
    current = set(query_current(index_code))
    added = new_set - current
    removed = current - new_set

    # ⚠️ **拉到的成分数明显偏少时不动库**。
    # 上游偶发返回半截数据(比如只回 30 只),照单执行会把另外 270 只
    # 全部 effective_to=今天 —— 等于一次性伪造一场"指数大调整",
    # 而历史成分表是不可逆的:下个月 reconcile 会以为它们本来就不在。
    if current and len(new_set) < len(current) * 0.7:
        log.error("[universe] %s 只拉到 %d 只(库里 %d 只)· 疑似上游返回不全 · 本次不更新",
                  index_code, len(new_set), len(current))
        return {"error": "suspicious_shrink", "index": index_code,
                "fetched": len(new_set), "current": len(current)}
    today = date.today()

    conn = get_conn(); cur = conn.cursor()
    for c in added:
        cur.execute(
            """INSERT INTO index_component (index_code, stock_code, effective_from)
               VALUES (%s, %s, %s)
               ON CONFLICT (index_code, stock_code, effective_from) DO NOTHING""",
            (index_code, c, today),
        )
    for c in removed:
        cur.execute(
            """UPDATE index_component SET effective_to=%s
               WHERE index_code=%s AND stock_code=%s AND effective_to IS NULL""",
            (today, index_code, c),
        )
    conn.commit(); cur.close(); conn.close()
    log.info(f"[universe] reconcile {index_code} · +{len(added)} -{len(removed)}")
    return {"added": sorted(added), "removed": sorted(removed)}


def resolve(universe_key: str, on_date: date | None = None, user_id: str | None = None) -> list[str]:
    """统一入口 · strategy_engine._resolve_universe 调用
    · hs300/zz500/zz1000 · 从 index_component 拉
    · my_watch · 从 stocks WHERE user_id 拉
    · a_all / a_ex_st · 从 stocks 拉
    · 未 seed 时 fallback stocks 表
    """
    if universe_key in INDEX_MAP:
        index_code, _ = INDEX_MAP[universe_key]
        codes = query_active_at(index_code, on_date) if on_date else query_current(index_code)
        if codes:
            return codes
        # fallback · 未 seed 时
    return _fallback_stocks(universe_key, user_id)


def _fallback_stocks(universe_key: str, user_id: str | None) -> list[str]:
    conn = get_conn(); cur = conn.cursor()
    if universe_key == "my_watch" and user_id:
        cur.execute("SELECT code FROM stocks WHERE user_id=%s AND enabled AND market='A'", (user_id,))
    else:
        cur.execute("SELECT DISTINCT code FROM stocks WHERE enabled AND market='A'")
    codes = [r[0] for r in cur.fetchall()]
    cur.close(); conn.close()
    return codes


def quality_at(universe_key: str, on_date: date) -> dict:
    """这个日期的股票池**成色**怎么样(`_17` §5)。

    `query_active_at()` 按 effective_from <= on_date 查历史成分,逻辑是对的。
    但 Phase E 的设计是「首次 seed 时 effective_from = today,之后每月累积变更」
    (`15_phase-e` §3.3)—— 所以**未来一到两年内查任何历史日期都返回空**,
    回落到 stocks 表(= 今天还活着的股票)。

    也就是说 `01` §10.3 要求的「不用今日成分套 3 年前」,现在恰恰在这么做。

    这不是 bug,是设计的必然阶段。但用户看不出来 —— 他拿到一个漂亮的回测,
    不知道里面藏着幸存者偏差。**让他知道成色,比修好它更急。**

    返回给回测结果带上,前端据此显示一句提示。
    """
    if universe_key not in INDEX_MAP:
        # 自选股/全 A —— 本来就没有历史成分的概念,不存在这个问题
        return {"survivorship_ok": True, "source": "stocks",
                "note": ""}

    index_code, label = INDEX_MAP[universe_key]
    hist = query_active_at(index_code, on_date)
    if hist:
        return {"survivorship_ok": True, "source": "index_component",
                "count": len(hist), "note": ""}

    cur_codes = query_current(index_code)
    return {
        "survivorship_ok": False,
        "source": "stocks_fallback",
        "count": len(cur_codes),
        "note": (
            f"{label} 在 {on_date} 没有历史成分记录,用的是**当前**股票池 —— "
            f"退市和被调出的股票不在里面,回测收益会偏高(幸存者偏差)。"
            f"历史成分表从 2026-08 起按月累积,届时这条会自动消失。"
        ),
    }
