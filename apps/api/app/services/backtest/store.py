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


def _dsn() -> str:
    """FINDATA_DB_URL → DATABASE_URL 兜底。

    ## 为什么要兜底(2026-08-31 修)

    原来这里只认 `FINDATA_DB_URL`,不配就 `raise`。而 FINDATA_DB_URL 指的是
    **我们自己的 finance-data 服务器** —— 开源版用户 clone 下来根本没有这个
    变量,也不该有(那是我们的私有库)。

    后果是:凡是走 backtest 数据层的接口对开源用户**全部 503**——

        GET /api/backtest/calibration    概率校准(评委建议 C)
        GET /api/backtest/interval/{c}   预测区间
        GET /api/backtest/prob/{c}       三类概率
        GET /api/backtest/stock/{c}      单股预测评估(评委建议 A)

    这四个恰好是复赛评委点名要看的功能。默认配置下点进去只有一句
    "校准数据暂不可用",看起来像功能没做 —— 实际上代码全在,只是连不上库。

    三张表(pred_snapshot / pred_backtest / pred_consistency)的建表语句在
    `db/migrations/0004_pred_backtest.sql`,本地主库里就有,所以退到
    `DATABASE_URL` 是可行的、也是开源版的正确默认。

    生产仍优先用 FINDATA_DB_URL —— 那边数据全,这个改动不影响线上取数路径。
    """
    return FINDATA_DB_URL or os.getenv("DATABASE_URL", "")


def conn():
    dsn = _dsn()
    if not dsn:
        raise RuntimeError("FINDATA_DB_URL / DATABASE_URL 均未配置")
    return psycopg2.connect(dsn, connect_timeout=10)

def resolve_symbol(code: str) -> str:
    """把裸代码补成带交易所后缀的 symbol。

    ## 起因(2026-08-31)

    两张表的代码格式不一样:

        stocks.code       600519       ← 自选股表 · 裸码
        pred_snapshot.symbol  600519.SH   ← 预测表 · 带后缀

    前端自选股卡片把 `stock.code` 原样传给 /api/backtest/accuracy,
    于是 `symbol=600519` 一行都匹配不上:

        symbol=600519      sample=0    hit_rate=null    ← 界面显示"暂无"
        symbol=600519.SH   sample=320  hit_rate=54.7%

    结果是**每只自选股的「预测评估」框都显示暂无**,看起来像功能没做,
    实际上数据一直都在。而且不报错、不告警 —— sample=0 是个合法响应。

    ## 为什么在后端补,而不是前端拼后缀

    调用点不止一处:预测评估框、概率校准框、/evaluation 页、/calibration 页、
    存证分享。每处都拼一次,就有每处都拼错的机会。
    在数据层补一次,所有调用方一起修好。

    ## 为什么查库而不是按规则推

    "6 开头是沪市"这类规则有例外(北交所 8/4 开头、科创 688、退市代码),
    推错了会静默查不到 —— 跟现在这个 bug 一模一样。
    直接问库里实际存的是什么,查不到就原样返回(调用方照旧拿到空结果)。
    """
    c = (code or "").strip()
    if not c or "." in c:
        return c
    try:
        conn_ = conn(); cur = conn_.cursor()
        cur.execute("SELECT symbol FROM pred_snapshot WHERE symbol LIKE %s LIMIT 1",
                    (c + ".%",))
        row = cur.fetchone()
        cur.close(); conn_.close()
        if row:
            return row[0]
    except Exception as e:
        log.debug("[store] resolve_symbol(%s) 查库失败: %s", c, e)
    return c




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
    symbol = resolve_symbol(symbol)   # 裸码补后缀 · 见 resolve_symbol 的说明
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

    # 这批样本是哪个模型版本产生的 —— **必须回传给前端**。
    #
    # 起因(2026-09-01):自选股卡片上写着「命中 63.1%」,而这三只票的
    # 570×3 条记录 model_ver 全是 'demo-v1'(真实收盘 + 高斯噪声合成的
    # 演示数据)。前端本来做了「·演示」徽标,但这个接口不返回 model_ver,
    # isDemo 永远是 false —— **等于把合成数据当成真实模型表现展示给评委**。
    #
    # 老板已定:复赛演示就用 demo 数据(时间来不及跑够真样本)。
    # 正因为如此,标记才更不能省 —— 用 demo 可以,不标就是另一回事了。
    #
    # 混合的情况取占比最高的那个;真样本一旦超过 demo,徽标自然消失。
    cur.execute(f"""SELECT model_ver, count(*) FROM pred_backtest WHERE {where}
                    GROUP BY model_ver ORDER BY count(*) DESC LIMIT 1""", args)
    _mv = cur.fetchone()
    c.close()
    return {
        "sample": n or 0,
        # ⚠️ 这几个 hit_rate 已经是**百分数**(×100 过了)。
        # 前端拿到 63.1 直接显示 "63.1%",别再乘一次 —— 界面上曾出现过
        # "命中 6310%",就是又乘了一遍 100。
        "hit_rate": round(float(hit) * 100, 1) if hit else None,
        "amt_hit_rate": round(float(amt) * 100, 1) if amt else None,
        "mae": round(float(mae), 2) if mae else None,
        "by_horizon": by_h, "by_signal": by_sig, "window_days": days,
        "model_ver": _mv[0] if _mv else None,
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
    symbol = resolve_symbol(symbol)
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
    symbol = resolve_symbol(symbol)
    c = conn(); cur = c.cursor()
    cur.execute("""SELECT pred_date, run_date, change_pct, signal
                   FROM pred_snapshot WHERE symbol = %s
                   ORDER BY pred_date DESC, run_date DESC LIMIT %s""", (symbol, limit))
    out = [{"pred_date": r[0].isoformat(), "run_date": r[1].isoformat(),
            "change_pct": float(r[2]) if r[2] is not None else None, "signal": r[3]}
           for r in cur.fetchall()]
    c.close()
    return out


# ═══════════════════════════════════════════════════════════════
# 预测存证分享 · 方案见 doc/开源hunter-community/04开源比赛/
#                    2026-08-31_预测存证分享页_方案.md
# ═══════════════════════════════════════════════════════════════

def mint_share_token(symbol: str) -> dict | None:
    """给某只股票**已存在的最近一次快照**发一个公开 token。

    ## 为什么是"已存在的",不是"现在跑一条"

    原方案写的是 `from-live` —— 用户点分享,立刻跑一遍模型,存快照,返 token。
    这里有意偏离,理由在方案 §2:

    存证要证明的是「这条预测在 T 时刻就已经存在」。如果预测是点击那一刻才
    生成的,它的诞生时间就晚于它所预测的日期 —— **这样的链接证明不了任何事**,
    拿今天的数据算今天的"预测"当然准。

    分享一条早就躺在库里、run_date 明写在页面上的旧预测,
    它的价值恰恰在于是旧的。

    ## 一次快照一个 token · token 落在"锚点行"上

    方案原文写的是「同一 base_date 的多个 horizon 共用一个 token」,
    实现时撞了库:

        idx_pred_snap_share_token  UNIQUE (share_token) WHERE share_token IS NOT NULL

    **share_token 是全表唯一的**,同一个值不可能写进 5 个 horizon 行 ——
    UPDATE 直接 UniqueViolation。

    改法:token 只写在 horizon 最小的那一行(锚点),读取时由锚点找出
    (symbol, base_date) 下的全部 horizon。对外行为完全一致 ——
    一个链接看全 1/2/3/5/10 天 —— 只是存法不同,而且不用动索引
    (生产库规则禁 DROP/ALTER 约束)。

    重复调用返回原 token,不重新发:多个链接指向同一条预测,
    存证的唯一性就没了。
    """
    symbol = resolve_symbol(symbol)
    import secrets

    c = conn(); cur = c.cursor()
    try:
        # 最近一次快照的 base_date
        cur.execute("""
            SELECT base_date FROM pred_snapshot
             WHERE symbol = %s
             ORDER BY base_date DESC, run_date DESC
             LIMIT 1
        """, (symbol,))
        row = cur.fetchone()
        if not row:
            return None
        base_date = row[0]

        # 这次快照下已经有人发过 token 了吗(任一 horizon 上有就算)
        cur.execute("""
            SELECT share_token FROM pred_snapshot
             WHERE symbol = %s AND base_date = %s AND share_token IS NOT NULL
             LIMIT 1
        """, (symbol, base_date))
        hit = cur.fetchone()
        if hit:
            log.info("[share] %s base=%s 已有 token · 复用", symbol, base_date)
            return {"token": hit[0], "symbol": symbol,
                    "base_date": base_date.isoformat(), "reused": True}

        # token_urlsafe(9) → 12 字符 ≈ 71 bit · 猜不动(列宽 varchar(16))
        # 只写 horizon 最小的那一行 —— share_token 全表唯一,写不进多行
        token = secrets.token_urlsafe(9)
        cur.execute("""
            UPDATE pred_snapshot SET share_token = %s
             WHERE symbol = %s AND base_date = %s
               AND horizon = (SELECT MIN(horizon) FROM pred_snapshot
                               WHERE symbol = %s AND base_date = %s)
        """, (token, symbol, base_date, symbol, base_date))
        if cur.rowcount != 1:
            c.rollback()
            log.warning("[share] %s base=%s 锚点行更新影响 %d 行(预期 1)· 放弃",
                        symbol, base_date, cur.rowcount)
            return None
        c.commit()
        log.info("[share] %s base=%s 发 token(锚点 horizon)", symbol, base_date)
        return {"token": token, "symbol": symbol,
                "base_date": base_date.isoformat(), "reused": False}
    finally:
        cur.close(); c.close()


def get_by_share_token(token: str) -> dict | None:
    """按 token 取整条存证 —— 公开只读,无需登录。

    **已验证和未验证的都返回。** outcome=None 表示还没到验证日,
    前端必须照样显示 —— 只挑已验证的给看,就又变成挑好的了。
    """
    c = conn(); cur = c.cursor()
    try:
        # ① 锚点行(token 只落在 horizon 最小的那行 · 见 mint_share_token)
        cur.execute("""
            SELECT symbol, base_date FROM pred_snapshot WHERE share_token = %s
        """, (token,))
        anchor = cur.fetchone()
        if not anchor:
            return None
        symbol, base_date = anchor

        # ② 由锚点取出这次快照的全部 horizon
        cur.execute("""
            SELECT symbol, run_date, base_date, pred_date, horizon,
                   last_close, pred_close, change_pct, direction,
                   signal, confidence, factors, model_ver
              FROM pred_snapshot
             WHERE symbol = %s AND base_date = %s
             ORDER BY horizon
        """, (symbol, base_date))
        rows = cur.fetchall()
        if not rows:
            return None

        run_date = rows[0][1]

        # 事后真实结果 —— 对得上就带上,对不上留 None
        cur.execute("""
            SELECT horizon, real_change, dir_hit, abs_error, pred_date
              FROM pred_backtest
             WHERE symbol = %s AND base_date = %s
        """, (symbol, base_date))
        outcomes = {
            r[0]: {
                "real_change": float(r[1]) if r[1] is not None else None,
                "dir_hit": r[2],
                "abs_error": float(r[3]) if r[3] is not None else None,
                "pred_date": r[4].isoformat() if r[4] else None,
            } for r in cur.fetchall()
        }
    finally:
        cur.close(); c.close()

    def _f(v):
        return float(v) if v is not None else None

    model_ver = rows[0][12]
    return {
        "token": token,
        "symbol": symbol,
        "run_date": run_date.isoformat(),
        "base_date": base_date.isoformat(),
        "model_ver": model_ver,
        # demo-v1 是 seed 合成的演示数据 —— 分享页是最容易被截图转发的一页,
        # 这个标记必须传到前端(方案 §5)
        "is_demo": model_ver == "demo-v1",
        "factors": rows[0][11] or {},
        "predictions": [{
            "horizon": r[4],
            "pred_date": r[3].isoformat(),
            "last_close": _f(r[5]),
            "pred_close": _f(r[6]),
            "change_pct": _f(r[7]),
            "direction": r[8],
            "signal": r[9],
            "confidence": _f(r[10]),
            "outcome": outcomes.get(r[4]),
        } for r in rows],
    }


def mint_tokens_for_run(base_date, limit: int = 1000) -> int:
    """给某一次 snapshot 里**还没有 token** 的股票批量发存证链接。

    ## 为什么要自动发

    原来 token 只能靠 `POST /api/backtest/share/{code}` 手动发一条。
    结果是:每天流水线跑出 306 只票的预测,却一个链接都没有 ——
    存证功能存在,但没有任何一条预测真的可被外部核验。

    存证的价值恰恰在于**预测作出的当时就已经有链接了**。事后想起来
    才补发,链接的诞生时间晚于预测,证明力就弱了一层
    (虽然 run_date 写在页面上,但"为什么这条补了那条没补"是说不清的)。
    所以改成:流水线存完快照,顺手把这一批全发了。

    ## 一次快照一个 token · 落在锚点行

    `idx_pred_snap_share_token` 是全表唯一索引,同一个 token 写不进
    多个 horizon 行。所以只写 horizon 最小的那一行作为锚点,
    读取时由锚点找出同 (symbol, base_date) 的全部 horizon
    —— 见 `mint_share_token` / `get_by_share_token`。

    ## 幂等

    只处理"这一批里一个 token 都没有"的 symbol。同一天重复跑
    (流水线重试、手动补跑)不会产生第二个链接 —— 一条预测一个链接,
    这是存证的唯一性要求。

    返回新发放的数量。
    """
    import secrets

    c = conn(); cur = c.cursor()
    minted = 0
    try:
        # 这一批里还没有任何 token 的 symbol,取其锚点 horizon
        cur.execute("""
            SELECT symbol, MIN(horizon)
              FROM pred_snapshot
             WHERE base_date = %s
             GROUP BY symbol
            HAVING count(share_token) = 0
             LIMIT %s
        """, (base_date, limit))
        targets = cur.fetchall()

        for symbol, anchor_h in targets:
            # 撞唯一索引的概率约 0(71 bit),但真撞上时重试比抛异常好 ——
            # 一只票发失败不该让整批 306 只都回滚
            for _attempt in range(3):
                token = secrets.token_urlsafe(9)
                try:
                    cur.execute("""
                        UPDATE pred_snapshot SET share_token = %s
                         WHERE symbol = %s AND base_date = %s AND horizon = %s
                    """, (token, symbol, base_date, anchor_h))
                    c.commit()
                    minted += 1
                    break
                except Exception as e:                       # noqa: BLE001
                    c.rollback()
                    log.debug("[share] %s 发 token 重试(%s)", symbol, str(e)[:60])
        if minted:
            log.info("[share] base=%s 自动发放存证链接 %d 条", base_date, minted)
    except Exception as e:                                   # noqa: BLE001
        # **不阻塞流水线** —— 发链接失败只是少了个分享入口,
        # 预测数据本身已经存好了,不该因此让整条流水线报失败
        log.warning("[share] 批量发 token 失败(非致命): %s", e)
    finally:
        cur.close(); c.close()
    return minted
