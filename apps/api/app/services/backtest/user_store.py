"""个人版回测数据层 —— 每个用户自己的股票池、判定参数与统计。

与全局 admin 版的关系(方案 §附A):
    预测数据只有一份(pred_snapshot / pred_backtest),全体共享;
    每人只是拿**自己的参数**去解读同一份数据。所以本模块不写任何预测数据,
    只做三件事:管池子、管参数、按各人参数现场算统计。

表在 financedata 库(与 pred_* 同库),见 finance-data/sql/20260805_backtest_user.sql。
"""
import logging
from datetime import date

from app.services.backtest import judge
from app.services.backtest.store import conn

log = logging.getLogger(__name__)

# 开放给个人的 8 项参数(其余属系统级, 只在 admin 后台可改)
USER_FIELDS = ("pred_len", "flat_band", "rel_err_pct", "abs_err_pp",
               "reversal_min", "strength_delta", "skip_limit", "skip_st")

USER_DEFAULTS = {
    "pred_len": 5, "flat_band": 0.5, "rel_err_pct": 20.0, "abs_err_pp": 1.5,
    "reversal_min": 1.0, "strength_delta": 1.5, "skip_limit": True, "skip_st": True,
}

FREE_QUOTA = 5     # 免费用户跟踪上限
PRO_QUOTA = 10     # Pro 会员上限
MIN_SAMPLE = 20    # 低于此样本量, 统计结果不足以说明问题(前端要显著提示)


# ── 会员额度 ─────────────────────────────────────────────

def quota_for(user_id: str) -> dict:
    """按会员状态返回可跟踪只数。会员信息在 hunter 自己的库(ax_event), 与本模块不同库。"""
    is_pro = False
    try:
        from app.services.database import get_conn
        c = get_conn(); cur = c.cursor()
        cur.execute("""SELECT member_expires_at > NOW() FROM ax_event
                       WHERE user_id = %s AND member_expires_at IS NOT NULL
                       ORDER BY member_expires_at DESC LIMIT 1""", (user_id,))
        r = cur.fetchone(); c.close()
        is_pro = bool(r and r[0])
    except Exception as e:
        log.debug("会员状态查询失败(按免费处理): %s", e)
    return {"limit": PRO_QUOTA if is_pro else FREE_QUOTA, "is_pro": is_pro}


# ── 个人参数 ─────────────────────────────────────────────

def get_user_config(user_id: str) -> dict:
    """读个人参数; 没设置过就返回默认值(不写库, 保持惰性)"""
    out = dict(USER_DEFAULTS)
    try:
        c = conn(); cur = c.cursor()
        cur.execute("SELECT " + ", ".join(USER_FIELDS) +
                    " FROM backtest_user_config WHERE user_id = %s", (user_id,))
        row = cur.fetchone(); c.close()
        if row:
            for k, v in zip(USER_FIELDS, row):
                if v is None:
                    continue
                out[k] = bool(v) if isinstance(USER_DEFAULTS[k], bool) else (
                    int(v) if isinstance(USER_DEFAULTS[k], int) else float(v))
    except Exception as e:
        log.warning("读个人回测参数失败, 用默认值: %s", e)
    return out


def save_user_config(user_id: str, patch: dict) -> dict:
    allowed = {k: v for k, v in patch.items() if k in USER_FIELDS and v is not None}
    if not allowed:
        return get_user_config(user_id)
    cols = ", ".join(allowed)
    ph = ", ".join(["%s"] * len(allowed))
    upd = ", ".join(f"{k} = EXCLUDED.{k}" for k in allowed)
    c = conn(); cur = c.cursor()
    cur.execute(f"""INSERT INTO backtest_user_config (user_id, {cols})
                    VALUES (%s, {ph})
                    ON CONFLICT (user_id) DO UPDATE SET {upd}, updated_at = NOW()""",
                [user_id] + list(allowed.values()))
    c.commit(); c.close()
    return get_user_config(user_id)


def reset_user_config(user_id: str) -> dict:
    c = conn(); cur = c.cursor()
    cur.execute("DELETE FROM backtest_user_config WHERE user_id = %s", (user_id,))
    c.commit(); c.close()
    return dict(USER_DEFAULTS)


# ── 个人股票池 ───────────────────────────────────────────

def list_user_pool(user_id: str) -> list[dict]:
    c = conn(); cur = c.cursor()
    cur.execute("""SELECT symbol, name, enabled, created_at::date
                   FROM backtest_user_pool WHERE user_id = %s
                   ORDER BY created_at""", (user_id,))
    rows = [{"symbol": r[0], "name": r[1], "enabled": r[2],
             "added_on": r[3].isoformat() if r[3] else None} for r in cur.fetchall()]
    c.close()
    return rows


def add_user_pool(user_id: str, symbol: str, name: str = "") -> tuple[bool, str]:
    """加入个人池。返回 (成功, 提示语)。超额时不静默截断, 明确告知。"""
    symbol = symbol.strip()
    if not symbol.isdigit() or len(symbol) != 6:
        return False, "股票代码应为6位数字"
    q = quota_for(user_id)
    cur_list = list_user_pool(user_id)
    if symbol in {s["symbol"] for s in cur_list}:
        return True, "已在跟踪列表中"
    if len(cur_list) >= q["limit"]:
        if q["is_pro"]:
            return False, f"已达上限 {q['limit']} 只,请先移除再添加"
        return False, f"免费版最多跟踪 {FREE_QUOTA} 只,升级 Pro 可跟踪 {PRO_QUOTA} 只"
    c = conn(); cur = c.cursor()
    cur.execute("""INSERT INTO backtest_user_pool (user_id, symbol, name)
                   VALUES (%s,%s,%s)
                   ON CONFLICT (user_id, symbol) DO UPDATE SET enabled = TRUE""",
                (user_id, symbol, (name or "").strip()))
    c.commit(); c.close()
    return True, "已加入 · 今晚 18:20 首次预测,明天可看到第一条回测"


def remove_user_pool(user_id: str, symbol: str) -> bool:
    c = conn(); cur = c.cursor()
    cur.execute("DELETE FROM backtest_user_pool WHERE user_id = %s AND symbol = %s",
                (user_id, symbol.strip()))
    n = cur.rowcount
    c.commit(); c.close()
    return n > 0


def all_user_symbols() -> list[str]:
    """全部用户池去重 —— 供每日任务与产业链池取并集。

    热门股高度重合, 去重后远小于「人数 × 只数」, 这是把 Kronos 调用量压住的关键。
    """
    try:
        c = conn(); cur = c.cursor()
        cur.execute("SELECT DISTINCT symbol FROM backtest_user_pool WHERE enabled")
        out = sorted(r[0] for r in cur.fetchall())
        c.close()
        return out
    except Exception as e:
        log.warning("汇总用户回测池失败: %s", e)
        return []


# ── 按个人参数现场统计 ───────────────────────────────────

def _fetch_rows(symbols: list[str], days: int) -> list[dict]:
    """取原始回测记录。只读客观量, 不读库里存的 dir_hit/amt_hit —— 那是按全局参数算的。"""
    if not symbols:
        return []
    c = conn(); cur = c.cursor()
    cur.execute("""SELECT symbol, pred_date, horizon, pred_change, real_change,
                          abs_error, rel_error, signal, base_date
                   FROM pred_backtest
                   WHERE symbol = ANY(%s) AND pred_date > CURRENT_DATE - %s
                     AND pred_change IS NOT NULL AND real_change IS NOT NULL
                   ORDER BY pred_date DESC, symbol""", (symbols, days))
    rows = [{"symbol": r[0], "pred_date": r[1], "horizon": r[2],
             "pred_change": float(r[3]), "real_change": float(r[4]),
             "abs_error": float(r[5]) if r[5] is not None else None,
             "rel_error": float(r[6]) if r[6] is not None else None,
             "signal": r[7], "base_date": r[8]} for r in cur.fetchall()]
    c.close()
    return rows


def _apply(rows: list[dict], cfg: dict) -> list[dict]:
    """对每行按本人参数现场判定"""
    for r in rows:
        r.update(judge.judge_row(r["pred_change"], r["real_change"], cfg))
    return rows


def _rate(rows: list[dict], key: str) -> float | None:
    return round(sum(1 for r in rows if r[key]) / len(rows) * 100, 1) if rows else None


def user_summary(user_id: str, days: int = 30) -> dict:
    """个人回测成绩单: 总命中率 + 分预测天数衰减 + 每只股票表现。"""
    cfg = get_user_config(user_id)
    pool = [s for s in list_user_pool(user_id) if s["enabled"]]
    symbols = [s["symbol"] for s in pool]
    name_map = {s["symbol"]: s["name"] for s in pool}
    rows = _apply(_fetch_rows(symbols, days), cfg)

    by_h: list[dict] = []
    for h in sorted({r["horizon"] for r in rows if r["horizon"]}):
        sub = [r for r in rows if r["horizon"] == h]
        by_h.append({"horizon": h, "n": len(sub), "hit_rate": _rate(sub, "dir_hit")})

    per_stock = []
    for s in pool:
        sub = [r for r in rows if r["symbol"] == s["symbol"]]
        per_stock.append({
            "symbol": s["symbol"], "name": name_map.get(s["symbol"], ""),
            "added_on": s["added_on"], "n": len(sub),
            "hit_rate": _rate(sub, "dir_hit"),
            "mae": round(sum(r["abs_error"] for r in sub) / len(sub), 2) if sub else None,
            "enough": len(sub) >= MIN_SAMPLE,
        })
    per_stock.sort(key=lambda x: (x["n"] == 0, -(x["hit_rate"] or 0)))

    # 稳定性: 相邻两次预测不改口的比例, 同样按本人 reversal_min 现场判
    stability, cons_n = _user_stability(symbols, cfg, days)

    # 已积累天数: 从最早加入的那只股票算起, 让用户知道"再等几天数据才够看"
    added = [s["added_on"] for s in pool if s["added_on"]]
    tracking_days = (date.today() - min(date.fromisoformat(a) for a in added)).days if added else 0

    q = quota_for(user_id)
    return {
        "sample": len(rows),
        "hit_rate": _rate(rows, "dir_hit"),
        "amt_hit_rate": _rate(rows, "amt_hit"),
        "mae": round(sum(r["abs_error"] for r in rows) / len(rows), 2) if rows else None,
        "stability": stability, "consistency_sample": cons_n,
        "by_horizon": by_h, "stocks": per_stock,
        "tracked": len(pool), "tracking_days": tracking_days,
        "quota": q["limit"], "is_pro": q["is_pro"],
        "enough_sample": len(rows) >= MIN_SAMPLE, "min_sample": MIN_SAMPLE,
        "window_days": days, "config": cfg,
    }


def _user_stability(symbols: list[str], cfg: dict, days: int) -> tuple[float | None, int]:
    if not symbols:
        return None, 0
    try:
        c = conn(); cur = c.cursor()
        cur.execute("""SELECT prev_change, curr_change FROM pred_consistency
                       WHERE symbol = ANY(%s) AND curr_run > CURRENT_DATE - %s
                         AND prev_change IS NOT NULL AND curr_change IS NOT NULL""",
                    (symbols, days))
        pairs = cur.fetchall(); c.close()
    except Exception as e:
        log.debug("个人稳定性统计失败: %s", e)
        return None, 0
    if not pairs:
        return None, 0
    rev = sum(1 for p, q_ in pairs if judge.verdict(float(p), float(q_), cfg) == "reversal")
    return round((1 - rev / len(pairs)) * 100, 1), len(pairs)


def user_stock_detail(user_id: str, symbol: str, runs: int = 5) -> dict:
    """单股详情: 近 N 次预测 + 到期结果对照 + 反转记录, 全部按本人参数判定。"""
    cfg = get_user_config(user_id)
    symbol = symbol.strip()
    c = conn(); cur = c.cursor()

    cur.execute("""SELECT DISTINCT base_date FROM pred_snapshot
                   WHERE symbol = %s ORDER BY base_date DESC LIMIT %s""", (symbol, runs))
    bases = [r[0] for r in cur.fetchall()]
    if not bases:
        c.close()
        return {"symbol": symbol, "runs": [], "reversals": [], "config": cfg,
                "hint": "该股尚无预测记录"}

    cur.execute("""SELECT base_date, pred_date, horizon, change_pct, pred_close,
                          last_close, signal, score, confidence, factors
                   FROM pred_snapshot WHERE symbol = %s AND base_date = ANY(%s)
                   ORDER BY base_date DESC, horizon""", (symbol, bases))
    by_base: dict = {}
    matrix: dict = {}       # {目标日: {基准日: 预测涨跌%}} —— 看模型对同一天有没有改口
    for (bd, pd_, hz, chg, pc, lc, sig, score, conf, fac) in cur.fetchall():
        e = by_base.setdefault(bd, {
            "base_date": bd.isoformat(), "signal": sig,
            "score": float(score) if score is not None else None,
            "confidence": float(conf) if conf is not None else None,
            "last_close": float(lc) if lc is not None else None,
            "factors": fac or {}, "preds": [],
        })
        chgf = float(chg) if chg is not None else None
        e["preds"].append({"pred_date": pd_.isoformat(), "horizon": hz,
                           "change_pct": chgf,
                           "pred_close": float(pc) if pc is not None else None})
        matrix.setdefault(pd_.isoformat(), {})[bd.isoformat()] = chgf

    # 到期结果: 只取客观量, 命中与否现场算
    cur.execute("""SELECT base_date, pred_date, pred_change, real_change
                   FROM pred_backtest WHERE symbol = %s AND base_date = ANY(%s)""",
                (symbol, bases))
    real_map = {}
    for bd, pd_, pchg, rchg in cur.fetchall():
        if pchg is None or rchg is None:
            continue
        j = judge.judge_row(float(pchg), float(rchg), cfg)
        real_map[(bd.isoformat(), pd_.isoformat())] = {
            "real_change": round(float(rchg), 2),
            "dir_hit": j["dir_hit"], "amt_hit": j["amt_hit"],
            "abs_error": round(j["abs_error"], 2),
        }
    for bd, e in by_base.items():
        for p in e["preds"]:
            p.update(real_map.get((e["base_date"], p["pred_date"]), {}))

    cur.execute("""SELECT pred_date, prev_base, curr_base, prev_change, curr_change,
                          top_driver, driver_share
                   FROM pred_consistency WHERE symbol = %s
                   ORDER BY curr_base DESC, pred_date LIMIT 40""", (symbol,))
    revs = []
    for (pd_, pb, cb, pv, cv, drv, share) in cur.fetchall():
        if pv is None or cv is None:
            continue
        vd = judge.verdict(float(pv), float(cv), cfg)
        if vd != "reversal":
            continue        # 是否算反转取决于本人的 reversal_min
        revs.append({"pred_date": pd_.isoformat(),
                     "prev_base": pb.isoformat() if pb else None,
                     "curr_base": cb.isoformat() if cb else None,
                     "prev_change": round(float(pv), 2), "curr_change": round(float(cv), 2),
                     "top_driver": drv,
                     "driver_share": float(share) if share is not None else None})
    c.close()

    done = [p for e in by_base.values() for p in e["preds"] if "dir_hit" in p]
    # 演变矩阵只保留被两次以上预测覆盖的目标日 —— 只被预测过一次的没有对比价值
    matrix = {k: v for k, v in matrix.items() if len(v) > 1}
    return {
        "symbol": symbol,
        "runs": [by_base[b] for b in bases if b in by_base],
        "matrix": matrix,
        "base_dates": [b.isoformat() for b in bases],
        "reversals": revs, "config": cfg,
        "sample": len(done),
        "hit_rate": round(sum(1 for p in done if p["dir_hit"]) / len(done) * 100, 1) if done else None,
        "enough_sample": len(done) >= MIN_SAMPLE, "min_sample": MIN_SAMPLE,
    }


# ── 一致性自检(验收用) ──────────────────────────────────

def parity_check(days: int = 90) -> dict:
    """对照「入库时按全局参数存的 dir_hit/amt_hit」与「现在按同一套全局参数算的」。

    判定逻辑从 jobs.py 抽到 judge.py 后, 两者必须逐行相同 —— 这是"搬家没搬丢东西"的证明。
    不一致的行会列出来供排查。
    """
    from app.services.backtest.config import get_config
    cfg = get_config()
    c = conn(); cur = c.cursor()
    cur.execute("""SELECT symbol, pred_date, pred_change, real_change, dir_hit, amt_hit
                   FROM pred_backtest
                   WHERE pred_date > CURRENT_DATE - %s
                     AND pred_change IS NOT NULL AND real_change IS NOT NULL""", (days,))
    rows = cur.fetchall(); c.close()
    mismatch = []
    for sym, pd_, pchg, rchg, stored_dir, stored_amt in rows:
        j = judge.judge_row(float(pchg), float(rchg), cfg)
        if j["dir_hit"] != stored_dir or j["amt_hit"] != stored_amt:
            mismatch.append({"symbol": sym, "pred_date": pd_.isoformat(),
                             "pred_change": float(pchg), "real_change": float(rchg),
                             "stored": [stored_dir, stored_amt],
                             "computed": [j["dir_hit"], j["amt_hit"]]})
    return {"checked": len(rows), "mismatch": len(mismatch),
            "ok": not mismatch, "samples": mismatch[:20],
            "note": "checked=0 表示还没有到期回测数据, 无法验证" if not rows else ""}
