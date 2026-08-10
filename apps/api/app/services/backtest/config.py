"""回测配置与股票池管理。

配置存 financedata.backtest_config(单行), 判定阈值不再硬编码,
admin 在后台改完立即生效。
股票池支持四种模式 + 自定义增删。
"""
import logging
from functools import lru_cache

from app.services.backtest.store import conn

log = logging.getLogger(__name__)

DEFAULTS = {
    "pool_mode": "core", "pred_len": 5, "run_hour": 16, "run_minute": 40,
    "concurrency": 12, "enabled": True,
    "flat_band": 0.5, "strict_dir": True,
    "rel_err_pct": 20.0, "abs_err_pp": 1.5,
    "reversal_min": 1.0, "strength_delta": 1.5, "driver_min_share": 30.0,
    "model_ver": "pro-v1",
    # P0 数据质量过滤
    "skip_suspended": True, "skip_limit": True, "skip_st": True,
    "min_list_days": 60, "min_amount_wan": 5000, "adjust_mode": "qfq",
    # P1 基准与极端值
    "benchmark_code": "000300.SH", "max_pred_pct": 11.0, "outlier_mode": "clip",
    # P2 运维
    "retain_days": 365, "kronos_retry": 2, "kronos_timeout": 300,
    "alert_hit_rate": 50.0,
}

_FIELDS = list(DEFAULTS.keys())


def get_config() -> dict:
    """读配置, 失败时回退默认值(保证作业不因配置读不到而中断)"""
    try:
        c = conn(); cur = c.cursor()
        cur.execute("SELECT " + ", ".join(_FIELDS) + " FROM backtest_config WHERE id = 1")
        row = cur.fetchone(); c.close()
        if not row:
            return dict(DEFAULTS)
        out = {}
        for k, v in zip(_FIELDS, row):
            out[k] = float(v) if isinstance(v, (int, float)) and isinstance(DEFAULTS[k], float) else v
        # numeric 类型 psycopg2 返回 Decimal, 统一转 float
        for k in ("flat_band", "rel_err_pct", "abs_err_pp", "reversal_min",
                  "strength_delta", "driver_min_share", "max_pred_pct", "alert_hit_rate"):
            if out.get(k) is not None:
                out[k] = float(out[k])
        return out
    except Exception as e:
        log.warning("读回测配置失败, 用默认值: %s", e)
        return dict(DEFAULTS)


def save_config(patch: dict, user: str = "") -> dict:
    """局部更新配置, 只接受白名单字段"""
    allowed = {k: v for k, v in patch.items() if k in _FIELDS and v is not None}
    if not allowed:
        return get_config()
    sets = ", ".join(f"{k} = %s" for k in allowed)
    c = conn(); cur = c.cursor()
    cur.execute(f"UPDATE backtest_config SET {sets}, updated_at = NOW(), updated_by = %s WHERE id = 1",
                list(allowed.values()) + [user])
    c.commit(); c.close()
    return get_config()


# ── 股票池 ────────────────────────────────────────────────

def chain_stocks(rep_only: bool = True) -> list[dict]:
    """从产业链定义提取股票。rep_only=True 只取每条链的代表股(约70只),
    False 取全部相关股(约103只)。"""
    from app.routers.discover import CHAIN_DEFS
    seen: dict[str, dict] = {}
    for ch in CHAIN_DEFS:
        name = ch.get("chain", "")
        if rep_only:
            for s in ch.get("rep_stocks") or []:
                code = s.get("symbol")
                if code and code not in seen:
                    seen[code] = {"symbol": code, "name": s.get("name", ""), "chain": name}
        else:
            reps = {s.get("symbol"): s.get("name", "") for s in (ch.get("rep_stocks") or [])}
            for code in ch.get("symbols") or []:
                if code and code not in seen:
                    seen[code] = {"symbol": code, "name": reps.get(code, ""), "chain": name}
    return list(seen.values())


def custom_pool(enabled_only: bool = True) -> list[dict]:
    """读自定义池"""
    c = conn(); cur = c.cursor()
    sql = "SELECT symbol, name, source, chain, enabled FROM backtest_pool"
    if enabled_only:
        sql += " WHERE enabled = TRUE"
    sql += " ORDER BY created_at DESC"
    cur.execute(sql)
    rows = [{"symbol": r[0], "name": r[1], "source": r[2], "chain": r[3], "enabled": r[4]}
            for r in cur.fetchall()]
    c.close()
    return rows


def add_to_pool(symbol: str, name: str = "", chain: str = "",
                source: str = "manual", user: str = "") -> bool:
    c = conn(); cur = c.cursor()
    cur.execute("""INSERT INTO backtest_pool (symbol, name, source, chain, added_by)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (symbol) DO UPDATE SET
                     enabled = TRUE, name = COALESCE(NULLIF(EXCLUDED.name,''), backtest_pool.name)""",
                (symbol.strip(), name.strip(), source, chain, user))
    n = cur.rowcount
    c.commit(); c.close()
    return n > 0


def remove_from_pool(symbol: str, hard: bool = False) -> bool:
    c = conn(); cur = c.cursor()
    if hard:
        cur.execute("DELETE FROM backtest_pool WHERE symbol = %s", (symbol.strip(),))
    else:
        cur.execute("UPDATE backtest_pool SET enabled = FALSE WHERE symbol = %s", (symbol.strip(),))
    n = cur.rowcount
    c.commit(); c.close()
    return n > 0


def import_chains_to_pool(rep_only: bool = True, user: str = "") -> int:
    """把产业链股票一次性导入自定义池(供 admin 一键初始化)"""
    stocks = chain_stocks(rep_only)
    n = 0
    for s in stocks:
        if add_to_pool(s["symbol"], s.get("name", ""), s.get("chain", ""), "chain", user):
            n += 1
    return n


def resolve_pool(cfg: dict | None = None, with_users: bool = True) -> list[str]:
    """按配置解析出本次要跑的股票代码列表。

    with_users=True 时并上全部用户的个人回测池(去重)。这是个人版的基础:
    预测本身是公用的, 同一只股票只调一次 Kronos, 结果所有用户共享;
    差异只体现在各人用自己的参数解读同一份数据(见 user_store)。
    """
    cfg = cfg or get_config()
    mode = cfg.get("pool_mode", "core")
    if mode == "custom":
        codes = {s["symbol"] for s in custom_pool(enabled_only=True)}
    elif mode == "chain_all":
        codes = {s["symbol"] for s in chain_stocks(rep_only=False)}
    elif mode == "watchlist":
        from app.services.backtest.scheduler import collect_symbols
        codes = set(collect_symbols("watchlist"))
    else:
        # core: 29链代表股 + 自定义池里手工加的(去重)
        codes = {s["symbol"] for s in chain_stocks(rep_only=True)}
        for s in custom_pool(enabled_only=True):
            if s.get("source") == "manual":
                codes.add(s["symbol"])
        # 自定义池里被显式关掉的要剔除
        disabled = {s["symbol"] for s in custom_pool(enabled_only=False) if not s.get("enabled")}
        codes -= disabled

    if with_users:
        try:
            from app.services.backtest.user_store import all_user_symbols
            codes |= set(all_user_symbols())
        except Exception as e:      # 用户池读不到不能拖垮主任务
            log.warning("并入用户回测池失败, 本次只跑全局池: %s", e)
    return sorted(codes)
