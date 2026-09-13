"""小鹿智能体 · 研究线(2026-09-13 用户批准的「研究台」方案,docs/agent-research-plan.md)。

「研究线」是方向(agent_opt.BRANCHES)上面的一层分组:VCP 波段线 = base / buy / sell / c 四个方向,
唐奇安突破线 = donchian 一个方向。四张表按方向分行,方向键全局唯一,所以加研究线**不迁移任何数据**。

## 状态

    idea(立项)→ backtest(全年回测)→ paper(纸上跑)→ archived(封存) / killed(淘汰)

- **封存**:优化器冻结(agent_run 里跳过 agent_opt.step),每天照常纸上跑,当所有新研究线的对照组。
  封存不是停机 —— 停了就只剩一条旧曲线,新策略没法在同一段新行情里和它比。
- **回测关**(backtest → paper):方向追到最新交易日后判一次:每笔完整周期平均净损益(已扣手续费)> 0,
  且最大回撤不超过对照组的 1.5 倍;没过 → 淘汰,数据全部保留。
- **30 笔判定**(paper):完整周期满 30 笔后,平均净损益 < 0,或同一段日期的收益不如对照组 → 淘汰;
  否则判定通过(晋级不自动做,是用户的决定)。不满 30 笔一律「等」—— VCP 这轮就是在 10 笔上下反复换参数吃的亏。

「期望值」用**每笔完整周期的平均净损益(美元,已扣手续费)**判正负。方案里写的是 0R;
R 需要每笔的初始风险,成交表里没有这一列,美元口径是现在能如实算出来的那个。

## 存哪

静态定义在 LINES(随代码走);会变的状态(status / verdict / 封存时间 / 事件)存 agent_meta 的 `line:<key>`,
合并时覆盖静态值。用户在研究台新建的立项整条存在 `line:<key>` 里(custom=True),**淘汰线在立项时写定、之后没有接口能改**
—— 防的是看过回测结果再回头改标准。
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone

from app.services.quant import agent_opt as ao

STATUS_ORDER = ["idea", "backtest", "paper", "archived", "killed"]
STATUS_TEXT = {"idea": "立项", "backtest": "全年回测", "paper": "纸上跑", "archived": "封存", "killed": "淘汰"}
KILL_MIN_CYCLES = 30
GATE_DD_MULT = 1.5
KILL_TEXT = (f"回测追到最新交易日时:每笔平均净损益(已扣费)≤ 0 或最大回撤超过对照组 {GATE_DD_MULT:g} 倍 → 淘汰;"
             f"纸上跑满 {KILL_MIN_CYCLES} 笔完整交易后:每笔平均净损益 < 0,或同一段日期收益不如对照组 → 淘汰")
COMPARE_DEFAULT = ("vcp", "c")

LINES: dict = {
    "vcp": {
        "label": "VCP 波段线", "branches": ["base", "buy", "sell", "c"], "best": "c",
        "status": "archived", "created_at": "2026-09-09", "archived_at": "2026-09-13", "archive_tag": "vcp-archive-v3",
        "hypothesis": "收缩后的放量突破会延续:波动逐级收紧、贴着枢轴的票,放量突破之后大概率走出一段",
        "rules_draft": "四个方向:基准 v1(用户的 Backtrader 策略)· A · SEPA 优化 · B · 调卖出 · C · 三段式",
        "pool": "筛选器「VCP 波段收缩」(方向 A 用 SEPA 趋势模板池)",
        "archive_reason": "迭代到 v3、4 个方向,最好的方向 C 全年仍落后标普 500;冻结优化、照常纸上跑,当新研究线的对照组",
        "compare_to": None, "kill_text": None,
    },
    "donchian": {
        "label": "唐奇安突破线", "branches": ["donchian"], "best": "donchian",
        "status": "backtest", "created_at": "2026-09-13",
        "hypothesis": "趋势一旦形成会延续:收盘第一次突破 55 日最高的票,赢的时候赚得比输的时候亏得多",
        "rules_draft": "进:收盘第一次突破前 55 日最高 · 出:跌破前 20 日最低或进场价 − 2 ATR · 仓位:单笔风险 1% 总资产",
        "pool": "唐奇安预筛池(离 3 个月高点 3% 以内 · 日均成交额 ≥ 2000 万美元 · RS 排序前 300)",
        "compare_to": list(COMPARE_DEFAULT), "kill_text": KILL_TEXT,
    },
}


def _meta():
    from app.services.quant import agent_run as ar     # 延迟导入:agent_run 也会延迟导入本模块
    return ar


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ═══════════════════════════════════════════════════════════════
# 读
# ═══════════════════════════════════════════════════════════════

def all_lines(cur) -> list[dict]:
    cur.execute("SELECT key, value FROM agent_meta WHERE key LIKE 'line:%'")
    stored = {k[5:]: (v if isinstance(v, dict) else json.loads(v)) for k, v in cur.fetchall()}
    out = []
    for key, base in LINES.items():
        ln = dict(base)
        ln.update(stored.get(key) or {})
        ln["key"] = key
        ln["branches"] = [b for b in base["branches"] if b in ao.BRANCHES]      # 静态定义说了算,存储改不了归属
        out.append(ln)
    custom = [dict(v, key=k) for k, v in stored.items() if k not in LINES and v.get("custom")]
    custom.sort(key=lambda x: x.get("created_at") or "")
    for ln in custom:
        ln["branches"] = []
    return out + custom


def line_of_branch(cur, branch: str) -> dict | None:
    for ln in all_lines(cur):
        if branch in ln["branches"]:
            return ln
    return None


def is_frozen(cur, branch: str) -> bool:
    ln = line_of_branch(cur, branch)
    return bool(ln and ln.get("status") == "archived")


def branch_metrics(cur, branch: str) -> dict | None:
    """一个方向的全段指标 + 净值序列(按日期)。没跑过 → None。"""
    ar = _meta()
    cur.execute("SELECT trade_date, equity, bench_close FROM agent_day WHERE branch=%s ORDER BY trade_date", (branch,))
    days = cur.fetchall()
    if not days:
        return None
    init = ar.av.GUARDS["initial_capital"]
    eq = [r[1] for r in days]
    b0 = next((r[2] for r in days if r[2]), None)
    pnl_pct = (eq[-1] / init - 1) * 100
    bench_pct = (days[-1][2] / b0 - 1) * 100 if (b0 and days[-1][2]) else None
    cur.execute("SELECT trade_date, seq, side, code, name, shares, price, amount, position_pct, pnl_abs, pnl_pct, "
                "hold_days, rule_id, rule_name, rationale, entry_date, level, followup, grade FROM agent_trade "
                "WHERE branch=%s ORDER BY trade_date, seq", (branch,))
    trades = cur.fetchall()
    sells = [t for t in trades if t[2] == "sell"]
    wins = [t for t in sells if t[9] is not None and t[9] > 0]
    gains = sum(t[9] for t in wins)
    losses = -sum(t[9] for t in sells if t[9] is not None and t[9] < 0)
    rounds = ar._trade_rounds(trades, {})
    cycles = len(rounds)
    net = sum(r["pnl_abs"] for r in rounds)
    return {
        "branch": branch, "first": days[0][0], "last": days[-1][0], "days": len(days),
        "equity": {r[0]: r[1] for r in days},
        "pnl_pct": round(pnl_pct, 2),
        "benchmark_pct": round(bench_pct, 2) if bench_pct is not None else None,
        "excess_pt": round(pnl_pct - bench_pct, 2) if bench_pct is not None else None,
        "max_dd_pct": round(ar._max_dd(eq)[0], 2),
        "sells": len(sells), "win_rate": round(len(wins) / len(sells) * 100, 1) if sells else None,
        "profit_factor": round(gains / losses, 2) if losses > 0 else None,
        "sharpe": ar._sharpe(eq),
        "cycles": cycles,
        "expectancy_net": round(net / cycles, 2) if cycles else None,
        "fee_total": round(sum(r["fee"] for r in rounds), 2),
    }


def window_return(m: dict, start: date, end: date) -> float | None:
    """同一段日期(两个方向都有净值的日子)里的收益 %。"""
    ds = sorted(d for d in m["equity"] if start <= d <= end)
    if len(ds) < 2:
        return None
    return (m["equity"][ds[-1]] / m["equity"][ds[0]] - 1) * 100


# ═══════════════════════════════════════════════════════════════
# 判定(纯函数,tests/test_agent_research.py 直接测)
# ═══════════════════════════════════════════════════════════════

def judge(stage: str, m: dict | None, cmp_: dict | None, caught_up: bool = True) -> dict:
    """→ {decision: wait | pass | kill, text}。m / cmp_ 是 branch_metrics 的结果。

    stage = backtest:追到最新交易日才判(没追完 = wait);期望 ≤ 0 或回撤超过对照组 1.5 倍 → kill;否则 pass(进纸上跑)。
    stage = paper:完整周期不满 30 笔 = wait;期望 < 0 或同段收益不如对照组 → kill;否则 pass(判定通过,状态不变)。
    """
    if m is None:
        return {"decision": "wait", "text": "还没跑过"}
    exp_ = m.get("expectancy_net")
    if stage == "backtest":
        if not caught_up:
            return {"decision": "wait", "text": f"全年回测进行中:已跑到 {m['last']}"}
        if exp_ is None:
            return {"decision": "kill", "text": "全年回测跑完,一笔完整交易都没有 —— 没法证明假设,淘汰"}
        if exp_ <= 0:
            return {"decision": "kill", "text": f"全年回测:{m['cycles']} 笔完整交易,每笔平均净损益 {exp_:+,.0f} 美元(已扣费)≤ 0 —— 没过回测关,淘汰"}
        if cmp_ is not None and cmp_.get("max_dd_pct") is not None and m["max_dd_pct"] < cmp_["max_dd_pct"] * GATE_DD_MULT:
            return {"decision": "kill", "text": (f"全年回测:最大回撤 {m['max_dd_pct']:.2f}% 超过对照组 {cmp_['max_dd_pct']:.2f}% 的 "
                                                 f"{GATE_DD_MULT:g} 倍 —— 没过回测关,淘汰")}
        return {"decision": "pass", "text": (f"全年回测过关:{m['cycles']} 笔完整交易,每笔平均净损益 {exp_:+,.0f} 美元(已扣费),"
                                             f"最大回撤 {m['max_dd_pct']:.2f}% —— 进入纸上跑,满 {KILL_MIN_CYCLES} 笔再判")}
    if stage == "paper":
        if m["cycles"] < KILL_MIN_CYCLES:
            return {"decision": "wait", "text": f"已走完 {m['cycles']}/{KILL_MIN_CYCLES} 笔完整交易,不满 {KILL_MIN_CYCLES} 笔不下结论"}
        if exp_ is not None and exp_ < 0:
            return {"decision": "kill", "text": f"满 {m['cycles']} 笔:每笔平均净损益 {exp_:+,.0f} 美元(已扣费)< 0 —— 淘汰"}
        if cmp_ is not None:
            start, end = max(m["first"], cmp_["first"]), min(m["last"], cmp_["last"])
            r1, r2 = window_return(m, start, end), window_return(cmp_, start, end)
            if r1 is not None and r2 is not None and r1 < r2:
                return {"decision": "kill", "text": (f"满 {m['cycles']} 笔:{start} → {end} 收益 {r1:+.2f}%,"
                                                     f"不如对照组同段 {r2:+.2f}% —— 淘汰")}
        return {"decision": "pass", "text": f"满 {m['cycles']} 笔判定通过:每笔平均净损益 {exp_:+,.0f} 美元(已扣费),同段收益不输对照组 —— 可以开优化器或晋级主线"}
    return {"decision": "wait", "text": STATUS_TEXT.get(stage, stage)}


# ═══════════════════════════════════════════════════════════════
# 写
# ═══════════════════════════════════════════════════════════════

def _save(cur, key: str, patch: dict) -> None:
    ar = _meta()
    cur.execute("SELECT value FROM agent_meta WHERE key=%s", (f"line:{key}",))
    r = cur.fetchone()
    cur_v = (r[0] if isinstance(r[0], dict) else json.loads(r[0])) if r else {}
    cur_v.update(patch)
    ar._meta_set(cur, f"line:{key}", cur_v)


def _event(ln: dict, text: str) -> list:
    ev = list(ln.get("events") or [])
    ev.append({"at": _now(), "text": text})
    return ev[-30:]


def evaluate(cur) -> list[dict]:
    """对 backtest / paper 的线各判一次,状态有变就落库。→ [{key, decision, text}]"""
    cur.execute("SELECT max(trade_date) FROM agent_day")
    latest = cur.fetchone()[0]
    lines = all_lines(cur)
    by_key = {ln["key"]: ln for ln in lines}
    out = []
    for ln in lines:
        st = ln.get("status")
        if st not in ("backtest", "paper") or not ln["branches"]:
            continue
        m = branch_metrics(cur, ln["best"])
        cmp_ = None
        ct = ln.get("compare_to")
        if ct and ct[0] in by_key:
            cmp_ = branch_metrics(cur, ct[1])
        v = judge(st, m, cmp_, caught_up=bool(m and latest and m["last"] >= latest))
        patch = {"verdict": {"decision": v["decision"], "text": v["text"], "at": _now()}}
        if v["decision"] == "kill":
            patch.update({"status": "killed", "killed_at": str(date.today()), "events": _event(ln, v["text"])})
        elif v["decision"] == "pass" and st == "backtest":
            patch.update({"status": "paper", "events": _event(ln, v["text"])})
        if (ln.get("verdict") or {}).get("text") != v["text"] or "status" in patch:
            _save(cur, ln["key"], patch)
        out.append({"key": ln["key"], **v})
    return out


_SLUG = re.compile(r"[^0-9a-z]+")


def create_idea(cur, uid, body: dict) -> dict:
    """立项。只记录假设、规则草案、股票池;淘汰线用固定口径并锁定。没有引擎就跑不了回测 —— 这点在返回里说清楚。"""
    def s(k, n):
        v = str((body or {}).get(k) or "").strip()
        if len(v) > n:
            raise ValueError(f"「{k}」太长(上限 {n} 字)")
        return v
    label, hyp = s("label", 30), s("hypothesis", 400)
    rules, pool = s("rules_draft", 600), s("pool", 200)
    if not label:
        raise ValueError("研究线要有名称")
    if not hyp:
        raise ValueError("要写一句话假设 —— 说不清在验证什么,回测结果怎么读都对")
    names = {ln["label"] for ln in all_lines(cur)}
    if label in names:
        raise ValueError(f"已经有一条叫「{label}」的研究线")
    key = "idea-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    v = {"custom": True, "label": label, "hypothesis": hyp, "rules_draft": rules, "pool": pool,
         "status": "idea", "created_at": str(date.today()), "created_by": str(uid),
         "compare_to": list(COMPARE_DEFAULT), "kill_text": KILL_TEXT, "kill_locked": True,
         "events": [{"at": _now(), "text": "立项:淘汰线已锁定"}]}
    _meta()._meta_set(cur, f"line:{key}", v)
    return {"key": key, "status": "idea",
            "note": "立项已记下。要跑回测,还得按规则草案写一个引擎文件并登记方向 —— 这一步需要开发,研究台上会一直显示「立项」"}


def set_archived(cur, key: str, archived: bool, uid) -> dict:
    lines = {ln["key"]: ln for ln in all_lines(cur)}
    ln = lines.get(key)
    if not ln:
        raise ValueError("没有这条研究线")
    if not ln["branches"]:
        raise ValueError("这条线还没有引擎和方向,没有可封存的运行")
    if archived:
        if ln.get("status") == "archived":
            return {"key": key, "status": "archived"}
        patch = {"status": "archived", "archived_at": str(date.today()),
                 "events": _event(ln, f"封存(操作人 {uid}):优化器冻结,照常纸上跑")}
    else:
        if ln.get("status") != "archived":
            raise ValueError("这条线没有封存")
        patch = {"status": "paper", "unarchived_at": str(date.today()),
                 "events": _event(ln, f"解除封存(操作人 {uid}):回到纸上跑,优化器恢复")}
    _save(cur, key, patch)
    return {"key": key, "status": patch["status"]}


# ═══════════════════════════════════════════════════════════════
# 研究台接口的返回体
# ═══════════════════════════════════════════════════════════════

def board(cur) -> dict:
    ar = _meta()
    lines = all_lines(cur)
    metrics = {}
    for ln in lines:
        for b in ln["branches"]:
            metrics[b] = branch_metrics(cur, b)
    ref = metrics.get(LINES["vcp"]["best"])
    axis = sorted(ref["equity"]) if ref else []
    cur.execute("SELECT trade_date, bench_close FROM agent_day WHERE branch=%s ORDER BY trade_date", (LINES["vcp"]["best"],))
    bench = {r[0]: r[1] for r in cur.fetchall()}
    b0 = next((bench[d] for d in axis if bench.get(d)), None)
    init = ar.av.GUARDS["initial_capital"]
    out_lines = []
    for ln in lines:
        brs = []
        for b in ln["branches"]:
            st = ar._branch_state(cur, b)
            brs.append({"key": b, "label": ao.BRANCHES[b]["label"], "version": f"v{st['version']}"})
        best = ln.get("best") if ln["branches"] else None
        m = metrics.get(best) if best else None
        ct = ln.get("compare_to")
        out_lines.append({
            "key": ln["key"], "label": ln.get("label"), "status": ln.get("status"),
            "status_text": STATUS_TEXT.get(ln.get("status"), ln.get("status")),
            "custom": bool(ln.get("custom")),
            "hypothesis": ln.get("hypothesis"), "rules_draft": ln.get("rules_draft"), "pool": ln.get("pool"),
            "created_at": ln.get("created_at"), "archived_at": ln.get("archived_at"), "killed_at": ln.get("killed_at"),
            "archive_tag": ln.get("archive_tag"), "archive_reason": ln.get("archive_reason"),
            "kill_text": ln.get("kill_text"),
            "compare_text": (f"{LINES[ct[0]]['label']} · {ao.BRANCHES[ct[1]]['label']}" if ct and ct[0] in LINES and ct[1] in ao.BRANCHES else None),
            "verdict": ln.get("verdict"), "events": (ln.get("events") or [])[-5:],
            "branches": brs, "best_branch": best,
            "best_label": ao.BRANCHES[best]["label"] if best else None,
            "metrics": ({k: m[k] for k in ("pnl_pct", "benchmark_pct", "excess_pt", "max_dd_pct", "sells", "win_rate",
                                            "profit_factor", "sharpe", "cycles", "expectancy_net", "fee_total", "days")}
                        | {"first": str(m["first"]), "last": str(m["last"])}) if m else None,
            "nav": ([round((m["equity"][d] / init - 1) * 100, 3) if d in m["equity"] else None for d in axis] if m else None),
        })
    return {
        "common": {"start": str(axis[0]) if axis else None, "end": str(axis[-1]) if axis else None, "days": len(axis),
                   "bench_label": ar.BENCH_LABEL,
                   "bench_pct": round((bench[axis[-1]] / b0 - 1) * 100, 2) if (axis and b0 and bench.get(axis[-1])) else None},
        "status_order": STATUS_ORDER, "status_text": STATUS_TEXT,
        "kill_text": KILL_TEXT, "kill_min_cycles": KILL_MIN_CYCLES,
        "dates": [str(d) for d in axis],
        "bench_nav": [round((bench[d] / b0 - 1) * 100, 3) if (b0 and bench.get(d)) else None for d in axis],
        "lines": out_lines,
    }
