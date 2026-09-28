"""个人研究规则与手动回测。版本快照隔离，绝不重写公共纸上交易账本。"""
from __future__ import annotations

import math
import time
import uuid
from bisect import bisect_right
from datetime import date
from app.services.quant import agent_run as ar, agent_limitup as engine

BRANCHES = {"limitup", "limitup_yin"}
LOCK = 719280061
FIELDS = {"amount", "hold_days", "growth_only", "main_only", "require_ma5", "amp_max", "no_all_shrink"}


def validate(params):
    if not isinstance(params, dict) or set(params) != FIELDS:
        raise ValueError("规则字段不完整或含有不支持的字段")
    p = dict(params)
    for key in ("growth_only", "main_only", "require_ma5", "no_all_shrink"):
        if type(p[key]) is not bool:
            raise ValueError("开关必须为布尔值")
    for key, lo, hi in (("amount", 100, 100000), ("hold_days", 1, 60), ("amp_max", 0.1, 100)):
        v = p[key]
        if key == "amp_max" and v is None:
            continue
        if type(v) not in (int, float) or not math.isfinite(v) or not lo <= v <= hi:
            raise ValueError(f"{key} 超出允许范围 {lo}～{hi}")
    if p["hold_days"] != int(p["hold_days"]):
        raise ValueError("持有天数必须为整数")
    p["hold_days"] = int(p["hold_days"])
    if p["main_only"] and p["growth_only"]:
        raise ValueError("不能同时只选主板和双创")
    return p


def key_for(uid, branch):
    if branch not in BRANCHES:
        raise ValueError("这个方向暂未开放规则编辑")
    return f"manual-rule:{uid}:{branch}"


def initial(cur, branch):
    p = dict(ar._branch_state(cur, branch)["params"])
    p.setdefault("main_only", branch == "limitup_yin")
    p.setdefault("require_ma5", branch != "limitup_yin")
    return {"version": 0, "params": p, "runs": []}


def transaction(fn):
    conn = ar._conn()
    try:
        with conn.cursor() as cur:
            result = fn(cur)
        conn.commit()
        return result
    finally:
        conn.close()


def read(uid, branch):
    key = key_for(uid, branch)
    def get(cur):
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (key,))
        cfg = ar._meta_get(cur, key) or initial(cur, branch)
        out = dict(cfg)
        out["rules"] = engine.rules_for(cfg["params"])
        out["editable"] = {k: cfg["params"].get(k) for k in FIELDS}
        from app.services.quant import rs_history
        cur.execute("SELECT MIN(trade_date), MAX(trade_date) FROM rs_daily WHERE market=%s AND code=%s",
                    ("a", rs_history.BENCH_CODE))
        first, last = cur.fetchone()
        out["data_first"] = str(first) if first else None
        out["data_last"] = str(last) if last else None
        # 工作进程重启会释放数据库锁，刷新时明确告知中断，而不是永久转圈。
        runs = list(out.get("runs", []))
        if runs and runs[0]["status"] in ("queued", "running"):
            cur.execute("SELECT pg_try_advisory_xact_lock(%s)", (LOCK,))
            if cur.fetchone()[0]:
                runs[0] = dict(runs[0], status="failed", error="回测进程已中断，请重新运行")
                cfg["runs"] = runs
                ar._meta_set(cur, key, cfg)
        out["runs"] = runs
        return out
    return transaction(get)


def save(uid, branch, body):
    key = key_for(uid, branch)
    values = validate(body.get("params"))
    if branch == "limitup_yin" and (not values["main_only"] or values["growth_only"]):
        raise ValueError("涨停三阴方向只支持主板")
    def update(cur):
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (key,))
        cfg = ar._meta_get(cur, key) or initial(cur, branch)
        if type(body.get("version")) is not int or body["version"] != cfg["version"]:
            raise ValueError("规则版本已变化，请重新打开编辑器")
        cfg["params"] = dict(cfg["params"], **values)
        cfg["version"] += 1
        cfg["saved_at"] = time.time()
        ar._meta_set(cur, key, cfg)
        return {"version": cfg["version"]}
    return transaction(update)


def submit(uid, branch, body):
    key = key_for(uid, branch)
    try:
        start, end = date.fromisoformat(body["start"]), date.fromisoformat(body["end"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("请填写有效的开始、结束日期")
    if end < start or (end - start).days > 366 or end > date.today():
        raise ValueError("回测区间需为过去的一年以内")
    conn = ar._conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (LOCK,))
            if not cur.fetchone()[0]:
                raise ValueError("已有手动回测正在运行，请完成后再试")
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (key,))
            cfg = ar._meta_get(cur, key) or initial(cur, branch)
            if not cfg["version"] or body.get("version") != cfg["version"]:
                raise ValueError("请先保存规则，并按保存的版本回测")
            run = {"id": uuid.uuid4().hex, "version": cfg["version"], "params": dict(cfg["params"]),
                   "start": str(start), "end": str(end), "status": "queued", "progress": 0,
                   "created_at": time.time(), "rules": engine.rules_for(cfg["params"])}
            cfg["runs"] = [run] + cfg.get("runs", [])[:9]
            ar._meta_set(cur, key, cfg)
        conn.commit()
        return conn, key, run
    except Exception:
        conn.close()
        raise


def persist(conn, key, run):
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (key,))
        cfg = ar._meta_get(cur, key)
        cfg["runs"] = [dict(run) if x["id"] == run["id"] else x for x in cfg["runs"]]
        ar._meta_set(cur, key, cfg)
    conn.commit()


def calculate(ctx, p, start, end, progress):
    from app.services.quant import screen_asof, commission
    dates = sorted(d for d in ctx.store["bench"] if start <= d <= end)
    all_days = sorted(ctx.store["bench"])
    if not dates or start < all_days[0] or end > ctx.store["last"]:
        raise ValueError("区间超出已入库日线，请按数据日期调整")
    codes = [c for c in ctx.store["codes"] if engine.is_main(c) or engine.is_growth(c)]
    if not codes:
        raise ValueError("没有可用于回测的 A 股日线")
    # 逐日全池精确计算，不复用固定预筛池；否则放宽规则会静默漏掉股票。
    positions, cash, previous, consec = [], engine.INITIAL_CAPITAL, None, 0
    nav, trades, net_cycles = [], [], []
    peak, max_dd = cash, 0.0
    for day_index, d in enumerate(dates):
        inds, watch = {}, []
        for code in codes:
            ds, arr = ctx.store["codes"][code]
            i = bisect_right(ds, d)
            if not i or ds[i - 1] != d:
                continue
            j = max(0, i - 6)
            bars = screen_asof._tuples(ds[j:i], arr[j:i], i - j, True)
            ind = engine.indicators(bars, p)
            inds[code] = ind
            if ind is not None:
                name = ctx.snap.get(code, {}).get("description") or code
                if engine.entry_checks(ind, code, name, p)["ok"]:
                    watch.append((code, name, None))
        # 稳定代码顺序，现金不足时也可重复得到同一结果。
        watch.sort(key=lambda x: x[0])
        before = {(x.code, x.entry_date): float(x.extra.get("buy_fee", 0)) for x in positions}
        res = engine.run_day(str(d), positions, cash, lambda c: [], watch, previous, consec, p,
                             ind_of=lambda c: inds.get(c), want_text=False)
        for f in res["fills"]:
            f["date"] = str(d)
            if f["side"] == "sell":
                f["net_pnl"] = round(f["pnl_abs"] - commission.a_share_fee("sell", f["shares"], f["price"])
                                     - before.get((f["symbol"], f["entry_date"]), 0), 2)
                net_cycles.append(f["net_pnl"])
            trades.append(f)
        positions, cash, consec = res["positions"], res["cash"], res["consec_losses"]
        previous = res["equity"]
        peak = max(peak, previous)
        max_dd = min(max_dd, (previous / peak - 1) * 100)
        nav.append({"date": str(d), "equity": round(previous, 2)})
        if day_index % 5 == 0 or day_index == len(dates) - 1:
            progress(round((day_index + 1) / len(dates) * 100, 1))
    return {"start": str(dates[0]), "end": str(dates[-1]), "data_last": str(ctx.store["last"]),
            "initial": engine.INITIAL_CAPITAL, "final": round(previous, 2),
            "net_pnl": round(previous - engine.INITIAL_CAPITAL, 2),
            "return_pct": (previous / engine.INITIAL_CAPITAL - 1) * 100,
            "closed": len(net_cycles), "open": len(positions),
            "win_rate": sum(x > 0 for x in net_cycles) / len(net_cycles) * 100 if net_cycles else None,
            "max_drawdown_pct": max_dd, "nav": nav, "trades": trades,
            "note": "收益已扣费用，含未平仓市值；收盘价成交、无滑点、按整股模拟。股票池与 ST 名称使用当前数据，有幸存者偏差。"}


def run_job(conn, key, run):
    import logging
    try:
        run["status"] = "running"
        persist(conn, key, run)
        ctx = ar.Ctx("a")
        def progress(value):
            run["progress"] = value
            persist(conn, key, run)
        run["result"] = calculate(ctx, run["params"], date.fromisoformat(run["start"]),
                                  date.fromisoformat(run["end"]), progress)
        run["status"] = "done"
    except Exception:
        logging.getLogger(__name__).exception("个人规则回测失败")
        run["status"] = "failed"
        run["error"] = "回测未完成，请检查日期是否在已入库日线范围内；仍失败请联系管理员。"
        conn.rollback()
    finally:
        try:
            persist(conn, key, run)
        finally:
            conn.close()  # 同时释放全局资源锁
