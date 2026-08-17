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
    """当前成分股 · effective_to IS NULL"""
    conn = get_conn(); cur = conn.cursor()
    cur.execute(
        """SELECT stock_code FROM index_component
           WHERE index_code=%s AND effective_to IS NULL
           ORDER BY stock_code""",
        (index_code,),
    )
    codes = [r[0] for r in cur.fetchall()]
    cur.close(); conn.close()
    return codes


def query_active_at(index_code: str, on_date: date) -> list[str]:
    """指定日期的成分股(生存者偏差防治)· effective_from <= on_date AND (to IS NULL OR to > on_date)"""
    conn = get_conn(); cur = conn.cursor()
    cur.execute(
        """SELECT stock_code FROM index_component
           WHERE index_code=%s
             AND effective_from <= %s
             AND (effective_to IS NULL OR effective_to > %s)
           ORDER BY stock_code""",
        (index_code, on_date, on_date),
    )
    codes = [r[0] for r in cur.fetchall()]
    cur.close(); conn.close()
    return codes


def seed_current(index_code: str) -> int:
    """从 AKShare 拉当前成分 · 首次填 index_component · 返回入库行数"""
    import akshare as ak
    try:
        df = ak.index_stock_cons_csindex(symbol=index_code)
    except Exception as e:
        log.error(f"[universe] AKShare 拉 {index_code} 失败: {e}")
        return 0
    if df is None or df.empty:
        return 0
    # 兼容列名(AKShare 版本差异)
    code_col = None
    for candidate in ("成分券代码", "code", "constituent_code", "stock_code"):
        if candidate in df.columns:
            code_col = candidate
            break
    if not code_col:
        log.error(f"[universe] {index_code} · 找不到 code 列 · cols={list(df.columns)}")
        return 0
    codes = [str(c).zfill(6) for c in df[code_col].tolist()]

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
    import akshare as ak
    try:
        df = ak.index_stock_cons_csindex(symbol=index_code)
    except Exception as e:
        log.error(f"[universe] AKShare 拉 {index_code} 失败: {e}")
        return {"error": str(e)}
    if df is None or df.empty:
        return {"error": "empty"}
    code_col = None
    for cand in ("成分券代码", "code"):
        if cand in df.columns:
            code_col = cand
            break
    if not code_col:
        return {"error": "no code column"}
    new_set = set(str(c).zfill(6) for c in df[code_col].tolist())
    current = set(query_current(index_code))
    added = new_set - current
    removed = current - new_set
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
