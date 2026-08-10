"""回测数据层 —— 读写 financedata 库的 pred_snapshot / pred_backtest / pred_consistency。

三张表由 finance-data/sql/20260803_pred_backtest.sql 创建。
连接复用 gm 端的 FINDATA_DB_URL。
"""
import json
import logging
import os
from datetime import date

import psycopg2
from psycopg2.extras import Json, execute_values

log = logging.getLogger(__name__)

FINDATA_DB_URL = os.getenv("FINDATA_DB_URL", "")


def conn():
    if not FINDATA_DB_URL:
        raise RuntimeError("FINDATA_DB_URL 未配置")
    return psycopg2.connect(FINDATA_DB_URL, connect_timeout=10)


# ── 预测快照 ─────────────────────────────────────────────

def save_snapshot(rows: list[dict]) -> int:
    """rows: [{symbol, run_date, pred_date, horizon, last_close, pred_close,
               change_pct, direction, score, signal, confidence, factors, model_ver}]"""
    if not rows:
        return 0
    c = conn(); cur = c.cursor()
    execute_values(cur, """
        INSERT INTO pred_snapshot
          (symbol, run_date, pred_date, horizon, base_date, last_close, pred_close,
           change_pct, direction, score, signal, confidence, factors, model_ver, clipped)
        VALUES %s
        ON CONFLICT (symbol, base_date, pred_date) DO UPDATE SET
          run_date=EXCLUDED.run_date, clipped=EXCLUDED.clipped,
          pred_close=EXCLUDED.pred_close, change_pct=EXCLUDED.change_pct,
          direction=EXCLUDED.direction, score=EXCLUDED.score,
          signal=EXCLUDED.signal, confidence=EXCLUDED.confidence,
          factors=EXCLUDED.factors, created_at=NOW()
    """, [(r["symbol"], r["run_date"], r["pred_date"], r["horizon"],
           r.get("base_date"),
           r.get("last_close"), r.get("pred_close"), r.get("change_pct"),
           r.get("direction"), r.get("score"), r.get("signal"),
           r.get("confidence"), Json(r.get("factors") or {}),
           r.get("model_ver", "pro-v1"), bool(r.get("clipped"))) for r in rows])
    n = cur.rowcount
    c.commit(); c.close()
    return n


def snapshots_for_target(pred_date: date) -> list[dict]:
    """取所有预测目标日 = pred_date 的快照(可能来自过去5个基准日)"""
    c = conn(); cur = c.cursor()
    cur.execute("""SELECT symbol, run_date, pred_date, horizon, last_close,
                          change_pct, signal, factors, model_ver, base_date
                   FROM pred_snapshot WHERE pred_date = %s""", (pred_date,))
    rows = cur.fetchall(); c.close()
    return [{"symbol": r[0], "run_date": r[1], "pred_date": r[2], "horizon": r[3],
             "last_close": float(r[4]) if r[4] is not None else None,
             "change_pct": float(r[5]) if r[5] is not None else None,
             "signal": r[6], "factors": r[7] or {}, "model_ver": r[8],
             "base_date": r[9]} for r in rows]


def two_latest_runs(base_a: date, base_b: date) -> dict:
    """取两个基准日的全部快照, 按 symbol+pred_date 索引, 供重叠对比。

    用 base_date 而非 run_date:决定预测内容的是"用哪天的收盘价算的",
    同一天可能跑多次且基准日不同(实测 15:19 跑时前后拿到 8/3 与 8/4 两种收盘),
    用 run_date 对比会把同一天的两次混在一起。
    """
    c = conn(); cur = c.cursor()
    cur.execute("""SELECT symbol, base_date, pred_date, change_pct, factors
                   FROM pred_snapshot WHERE base_date IN (%s, %s)""", (base_a, base_b))
    out: dict = {}
    for sym, base, pd_, chg, fac in cur.fetchall():
        out.setdefault((sym, pd_), {})[base] = {
            "change": float(chg) if chg is not None else None,
            "factors": fac or {},
        }
    c.close()
    return out


def latest_base_date() -> date | None:
    """最新的基准日"""
    c = conn(); cur = c.cursor()
    cur.execute("SELECT max(base_date) FROM pred_snapshot")
    r = cur.fetchone(); c.close()
    return r[0] if r and r[0] else None


def prev_run_date(before: date) -> date | None:
    """找出 before 之前最近的一个基准日(供重叠对比取上一次预测)"""
    c = conn(); cur = c.cursor()
    cur.execute("SELECT max(base_date) FROM pred_snapshot WHERE base_date < %s", (before,))
    r = cur.fetchone(); c.close()
    return r[0] if r and r[0] else None


# ── 真实收盘价 ───────────────────────────────────────────

def kronos_today(symbols: list[str]) -> dict:
    """读 finance-data 每日批量跑好的纯 Kronos 预测(避免重复调用模型服务:单次95-127秒)。
    返回 {symbol: {last_close, predictions:[{date,close},...]}}"""
    if not symbols:
        return {}
    c = conn(); cur = c.cursor()
    cur.execute("""SELECT symbol, pred_date, last_close, pred_close, run_date
                   FROM kronos_daily_pred
                   WHERE symbol = ANY(%s)
                     AND run_date = (SELECT max(run_date) FROM kronos_daily_pred)
                   ORDER BY symbol, pred_date""", (symbols,))
    out: dict = {}
    for sym, pd_, lc, pc, run in cur.fetchall():
        e = out.setdefault(sym, {"last_close": float(lc) if lc else 0.0,
                                 "run_date": run, "predictions": []})
        if pc is not None:
            e["predictions"].append({"date": pd_.isoformat(), "close": float(pc)})
    c.close()
    return out


def real_closes(symbols: list[str], d: date) -> dict:
    """取指定交易日的真实收盘价 {symbol: close}"""
    if not symbols:
        return {}
    c = conn(); cur = c.cursor()
    cur.execute("SELECT symbol, close FROM daily_close WHERE trade_date = %s AND symbol = ANY(%s)",
                (d, symbols))
    out = {r[0]: float(r[1]) for r in cur.fetchall() if r[1] is not None}
    c.close()
    return out


# ── 回测结果 ─────────────────────────────────────────────

def save_backtest(rows: list[dict]) -> int:
    if not rows:
        return 0
    c = conn(); cur = c.cursor()
    execute_values(cur, """
        INSERT INTO pred_backtest
          (symbol, run_date, pred_date, horizon, pred_change, real_change,
           abs_error, rel_error, dir_hit, amt_hit, signal, factors, model_ver, base_date)
        VALUES %s
        ON CONFLICT (symbol, base_date, pred_date) DO UPDATE SET
          real_change=EXCLUDED.real_change, abs_error=EXCLUDED.abs_error,
          rel_error=EXCLUDED.rel_error, dir_hit=EXCLUDED.dir_hit,
          amt_hit=EXCLUDED.amt_hit, created_at=NOW()
    """, [(r["symbol"], r["run_date"], r["pred_date"], r.get("horizon"),
           r.get("pred_change"), r.get("real_change"), r.get("abs_error"),
           r.get("rel_error"), r.get("dir_hit"), r.get("amt_hit"),
           r.get("signal"), Json(r.get("factors") or {}),
           r.get("model_ver"), r.get("base_date")) for r in rows])
    n = cur.rowcount
    c.commit(); c.close()
    return n


def save_consistency(rows: list[dict]) -> int:
    if not rows:
        return 0
    c = conn(); cur = c.cursor()
    execute_values(cur, """
        INSERT INTO pred_consistency
          (symbol, pred_date, prev_run, curr_run, prev_change, curr_change,
           delta, verdict, factor_delta, top_driver, driver_share, prev_base, curr_base)
        VALUES %s
        ON CONFLICT (symbol, pred_date, curr_base) DO UPDATE SET
          curr_change=EXCLUDED.curr_change, delta=EXCLUDED.delta,
          verdict=EXCLUDED.verdict, factor_delta=EXCLUDED.factor_delta,
          top_driver=EXCLUDED.top_driver, driver_share=EXCLUDED.driver_share,
          created_at=NOW()
    """, [(r["symbol"], r["pred_date"], r["prev_run"], r["curr_run"],
           r.get("prev_change"), r.get("curr_change"), r.get("delta"),
           r.get("verdict"), Json(r.get("factor_delta") or {}),
           r.get("top_driver"), r.get("driver_share"),
           r.get("prev_run"), r.get("curr_run")) for r in rows])
    n = cur.rowcount
    c.commit(); c.close()
    return n


# ── 统计查询(供 API) ────────────────────────────────────

def accuracy_stats(days: int = 30, symbol: str = "") -> dict:
    """整体/分horizon/分信号档 的方向命中率与平均误差"""
    c = conn(); cur = c.cursor()
    where = "pred_date > CURRENT_DATE - %s"
    args: list = [days]
    if symbol:
        where += " AND symbol = %s"
        args.append(symbol)

    cur.execute(f"""SELECT count(*), avg(CASE WHEN dir_hit THEN 1.0 ELSE 0 END),
                           avg(abs_error), avg(CASE WHEN amt_hit THEN 1.0 ELSE 0 END)
                    FROM pred_backtest WHERE {where}""", args)
    n, hit, mae, amt = cur.fetchone()
    cur.execute(f"""SELECT horizon, count(*), avg(CASE WHEN dir_hit THEN 1.0 ELSE 0 END),
                           avg(abs_error) FROM pred_backtest WHERE {where}
                    GROUP BY horizon ORDER BY horizon""", args)
    by_h = [{"horizon": r[0], "n": r[1], "hit_rate": round(float(r[2]) * 100, 1) if r[2] else None,
             "mae": round(float(r[3]), 2) if r[3] else None} for r in cur.fetchall()]
    cur.execute(f"""SELECT signal, count(*), avg(CASE WHEN dir_hit THEN 1.0 ELSE 0 END)
                    FROM pred_backtest WHERE {where} AND signal IS NOT NULL
                    GROUP BY signal ORDER BY count(*) DESC""", args)
    by_sig = [{"signal": r[0], "n": r[1], "hit_rate": round(float(r[2]) * 100, 1) if r[2] else None}
              for r in cur.fetchall()]
    c.close()
    return {
        "sample": n or 0,
        "hit_rate": round(float(hit) * 100, 1) if hit else None,
        "amt_hit_rate": round(float(amt) * 100, 1) if amt else None,
        "mae": round(float(mae), 2) if mae else None,
        "by_horizon": by_h, "by_signal": by_sig, "window_days": days,
    }


def consistency_stats(days: int = 30) -> dict:
    c = conn(); cur = c.cursor()
    cur.execute("""SELECT verdict, count(*) FROM pred_consistency
                   WHERE curr_run > CURRENT_DATE - %s GROUP BY verdict""", (days,))
    dist = {r[0]: r[1] for r in cur.fetchall()}
    total = sum(dist.values()) or 1
    cur.execute("""SELECT top_driver, count(*) FROM pred_consistency
                   WHERE curr_run > CURRENT_DATE - %s AND verdict = 'reversal'
                     AND top_driver IS NOT NULL
                   GROUP BY top_driver ORDER BY count(*) DESC LIMIT 8""", (days,))
    drivers = [{"factor": r[0], "n": r[1]} for r in cur.fetchall()]
    c.close()
    return {
        "distribution": dist, "sample": total,
        "stability": round((1 - dist.get("reversal", 0) / total) * 100, 1),
        "top_reversal_drivers": drivers, "window_days": days,
    }


def reversals(run_date: date | None = None, limit: int = 20) -> list[dict]:
    c = conn(); cur = c.cursor()
    if run_date:
        cur.execute("""SELECT symbol, pred_date, prev_run, curr_run, prev_change, curr_change,
                              delta, top_driver, driver_share, factor_delta
                       FROM pred_consistency WHERE verdict='reversal' AND curr_run = %s
                       ORDER BY abs(delta) DESC LIMIT %s""", (run_date, limit))
    else:
        cur.execute("""SELECT symbol, pred_date, prev_run, curr_run, prev_change, curr_change,
                              delta, top_driver, driver_share, factor_delta
                       FROM pred_consistency WHERE verdict='reversal'
                         AND curr_run = (SELECT max(curr_run) FROM pred_consistency)
                       ORDER BY abs(delta) DESC LIMIT %s""", (limit,))
    out = [{"symbol": r[0], "pred_date": r[1].isoformat(), "prev_run": r[2].isoformat(),
            "curr_run": r[3].isoformat(), "prev_change": float(r[4]) if r[4] is not None else None,
            "curr_change": float(r[5]) if r[5] is not None else None,
            "delta": float(r[6]) if r[6] is not None else None,
            "top_driver": r[7], "driver_share": float(r[8]) if r[8] is not None else None,
            "factor_delta": r[9] or {}} for r in cur.fetchall()]
    c.close()
    return out


def symbol_detail(symbol: str, runs: int = 5) -> dict:
    """单股完整回测详情:近 N 次预测 + 实际结果对照 + 因子 + 反转记录。

    返回结构:
      runs:   [{run_date, signal, score, confidence, factors,
                preds:[{pred_date, horizon, change_pct, real_change, dir_hit, amt_hit}]}]
      matrix: 用于画"同一目标日的历次预测"对比 {pred_date: {run_date: change_pct}}
      reversals: 该股的反转记录
    """
    c = conn(); cur = c.cursor()

    # 近 N 次预测发起日
    cur.execute("""SELECT DISTINCT run_date FROM pred_snapshot
                   WHERE symbol = %s ORDER BY run_date DESC LIMIT %s""", (symbol, runs))
    run_dates = [r[0] for r in cur.fetchall()]
    if not run_dates:
        c.close()
        return {"symbol": symbol, "runs": [], "matrix": {}, "reversals": [], "name": ""}

    cur.execute("""SELECT run_date, pred_date, horizon, change_pct, pred_close, last_close,
                          signal, score, confidence, factors
                   FROM pred_snapshot
                   WHERE symbol = %s AND run_date = ANY(%s)
                   ORDER BY run_date DESC, horizon""", (symbol, run_dates))
    by_run: dict = {}
    matrix: dict = {}
    for (rd, pd_, hz, chg, pc, lc, sig, score, conf, fac) in cur.fetchall():
        e = by_run.setdefault(rd, {
            "run_date": rd.isoformat(), "signal": sig,
            "score": float(score) if score is not None else None,
            "confidence": float(conf) if conf is not None else None,
            "last_close": float(lc) if lc is not None else None,
            "factors": fac or {}, "preds": [],
        })
        chgf = float(chg) if chg is not None else None
        e["preds"].append({
            "pred_date": pd_.isoformat(), "horizon": hz, "change_pct": chgf,
            "pred_close": float(pc) if pc is not None else None,
        })
        matrix.setdefault(pd_.isoformat(), {})[rd.isoformat()] = chgf

    # 已到期预测的实际结果
    cur.execute("""SELECT run_date, pred_date, real_change, dir_hit, amt_hit, abs_error
                   FROM pred_backtest WHERE symbol = %s AND run_date = ANY(%s)""",
                (symbol, run_dates))
    real_map = {(r[0].isoformat(), r[1].isoformat()): {
        "real_change": float(r[2]) if r[2] is not None else None,
        "dir_hit": r[3], "amt_hit": r[4],
        "abs_error": float(r[5]) if r[5] is not None else None} for r in cur.fetchall()}
    for rd, e in by_run.items():
        for p in e["preds"]:
            p.update(real_map.get((e["run_date"], p["pred_date"]), {}))

    # 反转记录
    cur.execute("""SELECT pred_date, prev_run, curr_run, prev_change, curr_change,
                          delta, verdict, top_driver, driver_share
                   FROM pred_consistency WHERE symbol = %s
                   ORDER BY curr_run DESC, pred_date LIMIT 40""", (symbol,))
    revs = [{"pred_date": r[0].isoformat(), "prev_run": r[1].isoformat(),
             "curr_run": r[2].isoformat(),
             "prev_change": float(r[3]) if r[3] is not None else None,
             "curr_change": float(r[4]) if r[4] is not None else None,
             "delta": float(r[5]) if r[5] is not None else None,
             "verdict": r[6], "top_driver": r[7],
             "driver_share": float(r[8]) if r[8] is not None else None}
            for r in cur.fetchall()]
    c.close()
    return {
        "symbol": symbol,
        "runs": [by_run[rd] for rd in run_dates if rd in by_run],
        "matrix": matrix, "reversals": revs,
    }


def symbol_evolution(symbol: str, limit: int = 40) -> list[dict]:
    """单股的预测演变(轻量版, 兼容旧调用)"""
    c = conn(); cur = c.cursor()
    cur.execute("""SELECT pred_date, run_date, change_pct, signal
                   FROM pred_snapshot WHERE symbol = %s
                   ORDER BY pred_date DESC, run_date DESC LIMIT %s""", (symbol, limit))
    out = [{"pred_date": r[0].isoformat(), "run_date": r[1].isoformat(),
            "change_pct": float(r[2]) if r[2] is not None else None, "signal": r[3]}
           for r in cur.fetchall()]
    c.close()
    return out
