"""小鹿智能体 · 研究线(2026-09-13 用户批准的「研究台」方案,docs/agent-research-plan.md)。

「研究线」是方向(agent_opt.BRANCHES)上面的一层分组:VCP 波段线 = base / buy / sell / c 四个方向,
唐奇安突破线 = donchian 一个方向。四张表按方向分行,方向键全局唯一,所以加研究线**不迁移任何数据**。

## 状态

    idea(立项)→ backtest(全年回测)→ paper(纸上跑)→ archived(封存) / killed(淘汰)

- **封存**:优化器冻结(agent_run 里跳过 agent_opt.step),每天照常纸上跑,当所有新研究线的对照组。
  封存不是停机 —— 停了就只剩一条旧曲线,新策略没法在同一段新行情里和它比。
- **回测关**(backtest → paper):方向追到最新交易日后判一次:每笔完整周期平均净损益(已扣手续费)> 0,
  且最大回撤不超过对照组的 1.5 倍;没过 → 淘汰,数据全部保留。
- **30 笔判定**:**只数规则冻结之后的新交易**(见下),满 30 笔后平均净损益 < 0,或同一段日期的收益不如对照组 → 淘汰;
  否则判定通过(晋级不自动做,是用户的决定)。不满 30 笔一律「等」—— VCP 这轮就是在 10 笔上下反复换参数吃的亏。

## 研究台只分「运行中 / 封存」两列(2026-09-18 用户定)

原来四列:立项 → 全年回测 → 纸上跑 → 封存。用户:「纸上跑和全年回测没太大差别,立项也没有存在必要」——
两者跑的是同一个引擎、同一份日线,每晚任务本来就让回测中的线往前跑新交易日,30 笔判定也数的是全段交易,
两列只是名字不同。**状态值不迁移**(idea / backtest / paper 照旧存),只是显示合成「运行中」(STATUS_TEXT);
表单提交、还没引擎的线显示「待写引擎」,也在运行中那列。淘汰的线继续不上看板。

真正要区分的是**规则冻结之后的新数据**:全年回测是在已知历史上跑的,规则是看着这段历史定、调出来的(VCP 在同一年上改了三四版);
冻结后每天新增的交易,写规则时没人见过,才是检验。所以每条线记 `frozen_at`(最后一次改买卖规则的时间)与
`frozen_through`(那一刻数据已经到哪个交易日),研究台把结果拆成冻结前 / 冻结后,30 笔判定只数 entry_date > frozen_through 的完整交易。
**以后改任何一条研究线的买卖规则,必须同时把这两个值改成改动那一刻**(提交时间 + 当时该市场日线的最新交易日:
美股每天上海 06:30 入库、A 股 / 港股 17:30 入库,见 fin-r1 crontab);只改文案、缓存、另开方向不算改规则。
没写冻结日的线(以后的新线),过回测关那天自动以回测最后一天为冻结日。

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
# 显示名(研究台两列 + 看板提示条 + 对照表):立项 / 全年回测 / 纸上跑 合成「运行中」,状态值本身不变(见文件头)
STATUS_TEXT = {"idea": "待写引擎", "backtest": "运行中", "paper": "运行中", "archived": "封存", "killed": "淘汰"}
COLUMNS = [["running", "运行中", "规则固定 · 冻结后的新交易满 30 笔才下结论"], ["archived", "封存", "冻结优化 · 仍在跑,当对照组"]]
KILL_MIN_CYCLES = 30
GATE_DD_MULT = 1.5
KILL_TEXT = (f"回测追到最新交易日时:每笔平均净损益(已扣费)≤ 0 或最大回撤超过对照组 {GATE_DD_MULT:g} 倍 → 淘汰;"
             f"规则冻结后的新交易满 {KILL_MIN_CYCLES} 笔:每笔平均净损益 < 0,或同一段日期收益不如对照组 → 淘汰")
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
    # 2026-09-13 用户给的「Patrick Walker Style Complete Strategy」完整脚本,取名「突破买入」。
    # 回测从 2025-09-12 起(用户要最近一年),比对照组(2026-01-02 起)多四个月 —— 回撤比较口径不同,研究台文案里写明
    "breakout": {
        # breakout3y(2026-09-15):同一套规则从 2023-09-15 起跑三年;回测关判定仍按一年的 breakout(best)
        "label": "突破买入", "branches": ["breakout", "breakout3y"], "best": "breakout",
        "status": "backtest", "created_at": "2026-09-13",
        "hypothesis": "大盘向上时,整理得干净、振幅逐级收紧、量能干燥的强势股,放量站上前 21 日最高后会走出一段;加仓放大赢家,三种止损截断输家",
        "rules_draft": ("v14(2026-09-15)在 v13 上加财报风控:财报前 5 个交易日内不买不加仓;持仓离财报 2 个交易日时"
                        "浮盈不超过 10% 清仓、超过 10% 卖一半,余仓照原规则 · v13 起前一天收盘要有资金逆势买入 · "
                        "v9(入场筛选同 v4;止损同 v5:1 ATR ≤ 8% + 固定 6% + Base 低点 2%;"
                        "五项评分只记录档位(不定仓、不拦人),追高超过 4% 不买,走廊 < 1R 不买;统一仓位 20% 首次一半;"
                        "5% 保本 + 20% 减半 + 破 EMA10 再减半 / 破 EMA20 清仓)· "
                        "进:标普 > 50 日 > 200 日 + 前一天收盘过 Clean Simple Base 筛选 + 放量 1.5 倍突破密集区上沿且不超过 5% · "
                        "仓:完整仓位 20% 总资产,首次 50%,+2% / +5% 各加 30% / 20% · "
                        "出:跌破 Base 低点 2% / 亏 6% / 亏损中跌破 EMA8;浮盈 8% 暂停卖 20%;浮盈 10% 破 EMA21、20% 破 50 日线、大盘转弱清仓"),
        "pool": "突破买入预筛池(收盘 > 20 · 30 日均量 > 20 万 · 均线多头 · 距 52 周高点 20% 以内 · RS ≥ 70)",
        "compare_to": list(COMPARE_DEFAULT),
        # 用户的研究对象:回测关只给参考、不自动淘汰(2026-09-13 被自动淘汰后用户要求恢复)
        "auto_kill": False,
        "kill_text": "用户研究线:回测关与 30 笔判定照常计算,只作参考结论,不自动淘汰;是否淘汰由用户决定",
        # 规则冻结(2026-09-18 定):最后一次改买卖规则是 v14 财报风控(提交 fdb49d3,上海 09-15 13:25);
        # 之后 ecc2be5 只改文案、f310e37 / a66cd0a 是缓存提速、765440f 另开三年方向 —— 都不算改规则。
        # 那一刻美股日线到 09-14(每天上海 06:30 入库),所以 09-15 起的交易才是写规则时没见过的
        "frozen_at": "2026-09-15 13:25", "frozen_through": "2026-09-14", "frozen_note": "v14 财报风控",
    },
    # 2026-09-17 用户:第一条 A 股研究线。对照组是美股 VCP,市场、货币、基准都不同,
    # 「回撤不超过对照组 1.5 倍」「同段收益不输对照组」比不出意义 → compare_to = None,只按每笔净损益判
    "limitup": {
        # limitup_yin(2026-09-18):同一条线新开的「涨停 + 三根阴线 · 主板」方向;研究台判定仍按 best = limitup
        "label": "涨停后强势整理", "branches": ["limitup", "limitup_yin"], "best": "limitup",
        "status": "backtest", "created_at": "2026-09-17", "market": "a",
        "hypothesis": "A 股涨停后三天没再涨停、收盘都守在涨停日收盘之上,说明资金没有撤,第四天收盘买入、次日收盘卖出能赚到延续",
        "rules_draft": ("进:4 个交易日前涨停(主板 10% / 创业板科创板 20%;主板 ST 2025-07-07 前 5%)· 之后三天每天都没涨停 · 三天收盘都高于涨停日收盘"
                        " · v2(2026-09-17)加:当天收盘站上 5 日均线且 5 日均线向上 · v3(同日)加:只做创业板 / 科创板 · v4(同日)加:三天整理幅度 ≤ 15%、三天不能每天都比涨停日缩量 → 信号当天收盘买入 1 万元 · 出:次日收盘全部卖出(跌停 / 停牌顺延)· 每个信号独立,不限持仓 · 手续费按 A 股实际扣"),
        "pool": "涨停后强势整理预筛池(A 股全市场日线时间回溯,板块涨停门槛由引擎按代码判)",
        "compare_to": None,
        "auto_kill": False,
        "kill_text": "用户研究线:回测关与 30 笔判定照常计算,只作参考结论,不自动淘汰;是否淘汰由用户决定",
        # 规则冻结:v4 整理幅度 ≤ 15% + 不许三天都缩量(提交 0a880f4,上海 09-17 23:50);09-18 的 eb6e8fe 是另开「三阴 · 主板」方向,
        # 老方向默认口径不变,不算改规则。那一刻 A 股日线到 09-17(每天上海 17:30 入库),09-18 起才是新数据
        "frozen_at": "2026-09-17 23:50", "frozen_through": "2026-09-17", "frozen_note": "v4 整理幅度 + 缩量",
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
    init = ao.initial_capital(branch)
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
    rounds = ar._trade_rounds(trades, {}, market=ao.market_of(branch))
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
        "currency": ao.currency(branch),
        "_rounds": rounds,
    }


def split_after(m: dict | None, frozen_through) -> dict | None:
    """冻结后的新数据:entry_date 晚于 frozen_through 的完整交易 + 从 frozen_through 那天收盘起的净值。

    → {frozen_through, days, cycles, expectancy_net, win_rate, net, pnl_pct, first, last, equity}(形状对得上 judge 的 m)。
    没有冻结日 / 没跑过 → None。
    """
    if not m or not frozen_through:
        return None
    ft = frozen_through if isinstance(frozen_through, date) else date.fromisoformat(str(frozen_through)[:10])
    rounds = [r for r in (m.get("_rounds") or []) if str(r.get("entry_date") or "") > str(ft)]
    days = sorted(d for d in m["equity"] if d >= ft)
    cycles = len(rounds)
    net = sum(r["pnl_abs"] for r in rounds)
    wins = sum(1 for r in rounds if r["pnl_abs"] > 0)
    return {
        "frozen_through": str(ft), "days": max(len(days) - 1, 0), "cycles": cycles,
        "expectancy_net": round(net / cycles, 2) if cycles else None,
        "win_rate": round(wins / cycles * 100, 1) if cycles else None,
        "net": round(net, 2),
        "pnl_pct": round(window_return(m, days[0], days[-1]), 2) if len(days) >= 2 else None,
        "first": days[0] if days else ft, "last": m["last"], "equity": {d: m["equity"][d] for d in days},
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

def judge(stage: str, m: dict | None, cmp_: dict | None, caught_up: bool = True, unit: str = "美元") -> dict:
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
            return {"decision": "kill", "text": f"全年回测:{m['cycles']} 笔完整交易,每笔平均净损益 {exp_:+,.0f} {unit}(已扣费)≤ 0 —— 没过回测关,淘汰"}
        if cmp_ is not None and cmp_.get("max_dd_pct") is not None and m["max_dd_pct"] < cmp_["max_dd_pct"] * GATE_DD_MULT:
            return {"decision": "kill", "text": (f"全年回测:最大回撤 {m['max_dd_pct']:.2f}% 超过对照组 {cmp_['max_dd_pct']:.2f}% 的 "
                                                 f"{GATE_DD_MULT:g} 倍 —— 没过回测关,淘汰")}
        return {"decision": "pass", "text": (f"全年回测过关:{m['cycles']} 笔完整交易,每笔平均净损益 {exp_:+,.0f} {unit}(已扣费),"
                                             f"最大回撤 {m['max_dd_pct']:.2f}%")}
    if stage == "paper":
        if m["cycles"] < KILL_MIN_CYCLES:
            return {"decision": "wait", "text": f"规则冻结后已走完 {m['cycles']}/{KILL_MIN_CYCLES} 笔完整交易,不满 {KILL_MIN_CYCLES} 笔不下结论"}
        if exp_ is not None and exp_ < 0:
            return {"decision": "kill", "text": f"规则冻结后满 {m['cycles']} 笔:每笔平均净损益 {exp_:+,.0f} {unit}(已扣费)< 0 —— 淘汰"}
        if cmp_ is not None:
            start, end = max(m["first"], cmp_["first"]), min(m["last"], cmp_["last"])
            r1, r2 = window_return(m, start, end), window_return(cmp_, start, end)
            if r1 is not None and r2 is not None and r1 < r2:
                return {"decision": "kill", "text": (f"规则冻结后满 {m['cycles']} 笔:{start} → {end} 收益 {r1:+.2f}%,"
                                                     f"不如对照组同段 {r2:+.2f}% —— 淘汰")}
        return {"decision": "pass", "text": f"规则冻结后满 {m['cycles']} 笔判定通过:每笔平均净损益 {exp_:+,.0f} {unit}(已扣费),同段收益不输对照组 —— 可以开优化器或晋级主线"}
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


def decide(ln: dict, m: dict | None, cmp_: dict | None, caught_up: bool, unit: str = "美元") -> tuple[dict, dict]:
    """一条运行中的线今天的判定 → (verdict, 要落库的补丁)。纯函数(tests/test_agent_research.py 直接测)。

    1. 回测关:全段追到最新交易日才判;没过 → 淘汰(用户研究线只给参考)。
    2. 过了回测关:只看**规则冻结之后**的新交易 —— 不满 30 笔等;满了按 judge("paper") 判。
       没写冻结日的线,过关那天以回测最后一天为冻结日并落库(之后才算新数据)。
    """
    st = ln.get("status")
    patch: dict = {}
    gate = judge("backtest", m, cmp_, caught_up=caught_up, unit=unit)
    ft = ln.get("frozen_through")
    if gate["decision"] == "pass" and not ft and m:
        ft = str(m["last"])
        patch["frozen_through"] = ft
    oos = split_after(m, ft) if gate["decision"] == "pass" else None
    if gate["decision"] != "pass":
        v = gate
    elif oos["cycles"] < KILL_MIN_CYCLES:
        v = {"decision": "wait", "text": (f"{gate['text']} · 规则冻结后({ft} 之后的新数据)已走完 {oos['cycles']}/{KILL_MIN_CYCLES} 笔,"
                                          f"满 {KILL_MIN_CYCLES} 笔再下结论")}
    else:
        v = judge("paper", oos, cmp_, caught_up=True, unit=unit)
    if ln.get("auto_kill") is False:
        # 用户自己的研究对象(2026-09-13 用户:「我还没开始调整参数你就给我淘汰了」)——
        # 判定照算,只当参考写进 verdict,**状态不动**。淘汰 / 晋级由用户决定
        if v["decision"] in ("kill", "pass"):
            # 判定文案末尾的「—— 淘汰」换掉:卡片上还写着淘汰,用户会以为线又被淘汰了
            t = re.sub(r"\s*——\s*(没过回测关,)?淘汰\s*$", " —— 按回测关口径不达标(仅参考)", v["text"])
            v = {"decision": "advice", "text": "参考结论(用户研究线,不自动淘汰):" + t}
        patch["verdict"] = {"decision": v["decision"], "text": v["text"], "at": _now()}
        return v, patch
    patch["verdict"] = {"decision": v["decision"], "text": v["text"], "at": _now()}
    if v["decision"] == "kill":
        patch.update({"status": "killed", "killed_at": str(date.today()), "events": _event(ln, v["text"])})
    elif gate["decision"] == "pass" and st == "backtest":
        # 状态值 paper 只是内部记号(显示同为「运行中」),事件里写清过了回测关、从哪天起算新数据
        patch.update({"status": "paper", "events": _event(ln, f"{gate['text']} —— 规则冻结于 {ft},之后的新交易满 {KILL_MIN_CYCLES} 笔再判")})
    return v, patch


def evaluate(cur) -> list[dict]:
    """对运行中(backtest / paper)的线各判一次,状态有变就落库。→ [{key, decision, text}]"""
    # 「追到最新交易日」按市场分开算:A 股比美股早收盘一天,混在一起取 max 的话美股线永远追不上
    cur.execute("SELECT branch, max(trade_date) FROM agent_day GROUP BY branch")
    latest_of: dict = {}
    for b, d in cur.fetchall():
        if b in ao.BRANCHES:
            mk = ao.market_of(b)
            latest_of[mk] = max(latest_of.get(mk, d), d)
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
        latest = latest_of.get(ao.market_of(ln["best"]))
        v, patch = decide(ln, m, cmp_, caught_up=bool(m and latest and m["last"] >= latest), unit=ao.currency(ln["best"])["unit"])
        changed = {k for k in patch if k != "verdict"}
        if (ln.get("verdict") or {}).get("text") != v["text"] or changed:
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
            "note": "已记下,放在「运行中」一栏、标着「待写引擎」。要跑起来还得按规则草案写引擎并登记方向 —— 这一步需要开发"}


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
    from app.services.quant import agent_overfit
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
    out_lines = []
    for ln in lines:
        brs = []
        for b in ln["branches"]:
            st = ar._branch_state(cur, b)
            brs.append({"key": b, "label": ao.BRANCHES[b]["label"], "version": f"v{st['version']}"})
        best = ln.get("best") if ln["branches"] else None
        m = metrics.get(best) if best else None
        ct = ln.get("compare_to")
        ft = ln.get("frozen_through")
        oos = split_after(m, ft) if (m and ft) else None
        out_lines.append({
            "overfit": agent_overfit.public_report(m["_rounds"], sorted(m["equity"]), ln, best) if m else None,
            "key": ln["key"], "label": ln.get("label"), "status": ln.get("status"),
            "status_text": STATUS_TEXT.get(ln.get("status"), ln.get("status")),
            # 研究台只有两列:running(待写引擎 / 回测 / 纸上跑)· archived;killed 不上看板
            "column": ("archived" if ln.get("status") == "archived" else "killed" if ln.get("status") == "killed" else "running"),
            "frozen_at": ln.get("frozen_at"), "frozen_through": ft, "frozen_note": ln.get("frozen_note"),
            "oos": ({k: oos[k] for k in ("frozen_through", "days", "cycles", "expectancy_net", "win_rate", "net", "pnl_pct")}
                    | {"last": str(oos["last"])}) if oos else None,
            "custom": bool(ln.get("custom")),
            "hypothesis": ln.get("hypothesis"), "rules_draft": ln.get("rules_draft"), "pool": ln.get("pool"),
            "created_at": ln.get("created_at"), "archived_at": ln.get("archived_at"), "killed_at": ln.get("killed_at"),
            "archive_tag": ln.get("archive_tag"), "archive_reason": ln.get("archive_reason"),
            "kill_text": ln.get("kill_text"), "auto_kill": ln.get("auto_kill", True) is not False,
            "compare_text": (f"{LINES[ct[0]]['label']} · {ao.BRANCHES[ct[1]]['label']}" if ct and ct[0] in LINES and ct[1] in ao.BRANCHES else None),
            "verdict": ln.get("verdict"), "events": (ln.get("events") or [])[-5:],
            "branches": brs, "best_branch": best,
            "best_label": ao.BRANCHES[best]["label"] if best else None,
            "metrics": ({k: m[k] for k in ("pnl_pct", "benchmark_pct", "excess_pt", "max_dd_pct", "sells", "win_rate",
                                            "profit_factor", "sharpe", "cycles", "expectancy_net", "fee_total", "days")}
                        | {"first": str(m["first"]), "last": str(m["last"])}) if m else None,
            "nav": ([round((m["equity"][d] / ao.initial_capital(best) - 1) * 100, 3) if d in m["equity"] else None for d in axis]
                    if m else None),
            # 每条线自己的市场 / 货币 / 基准(A 股线是人民币 + 沪深300);表格里的金额和「超额」按这个读
            "currency": ao.currency(best) if best else None,
        })
    return {
        "common": {"start": str(axis[0]) if axis else None, "end": str(axis[-1]) if axis else None, "days": len(axis),
                   "bench_label": ar.BENCH_LABEL,
                   "bench_pct": round((bench[axis[-1]] / b0 - 1) * 100, 2) if (axis and b0 and bench.get(axis[-1])) else None},
        "status_order": STATUS_ORDER, "status_text": STATUS_TEXT, "columns": COLUMNS,
        "kill_text": KILL_TEXT, "kill_min_cycles": KILL_MIN_CYCLES,
        "dates": [str(d) for d in axis],
        "bench_nav": [round((bench[d] / b0 - 1) * 100, 3) if (b0 and bench.get(d)) else None for d in axis],
        "lines": out_lines,
    }
