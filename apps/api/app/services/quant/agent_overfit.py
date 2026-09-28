"""过拟合证据检查：纯计算，不调用大模型，不把检查分解释成概率。"""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import date

VERSION = "evidence-v1"


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def fingerprint(params):
    return hashlib.sha256(json.dumps(params, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), default=str).encode()).hexdigest()


def assess(rounds, *, days=0, hypothesis=None, frozen_through=None, trials=None,
           audited=False, blockers=(), scope="历史探索", as_of=None):
    """只给实际完成的检查计分。缺失项不折算；阈值是产品规则，不是统计显著性。

    rounds 必须是完整平仓周期，pnl_abs 必须扣费。冻结日期只作描述，
    没有规则/数据快照审计时，不把冻结后交易冒充独立样本外证据。
    """
    rows = [r for r in rounds if finite(r.get("pnl_abs"))]
    checks = []

    def add(key, label, weight, state, text, action):
        checks.append(dict(key=key, label=label, weight=weight, status=state,
                           points=weight if state == "pass" else 0,
                           detail=text, next_step=action))

    n = len(rows)
    add("sample", "交易和时间是否够用", 10,
        "pass" if n >= 30 and days >= 120 else "insufficient",
        f"有效完整交易 {n} 笔，交易日 {days} 个；初筛门槛为 30 笔且 120 日。相关交易不等于独立样本。",
        "冻结规则，继续观察新交易；不要为了凑笔数放宽条件。")
    months = defaultdict(float)
    dated = 0
    for r in rows:
        try:
            month = date.fromisoformat(str(r.get("exit_date"))[:10]).strftime("%Y-%m")
        except (TypeError, ValueError):
            continue
        months[month] += r["pnl_abs"]
        dated += 1
    positive = sum(v > 0 for v in months.values())
    monthly_ready = len(months) >= 6 and dated == n
    add("periods", "收益是否跨时段出现", 10,
        ("pass" if positive / len(months) >= 2 / 3 else "warn") if monthly_ready else "insufficient",
        f"按平仓月份汇总：{len(months)} 个有交易月份，{positive} 个净盈利。只看已平仓损益，不是月净值或样本外检验。",
        "核对不同市场环境下的表现，不能挑掉亏损月份。")
    profits = sorted((r["pnl_abs"] for r in rows if r["pnl_abs"] > 0), reverse=True)
    net = sum(r["pnl_abs"] for r in rows)
    remainder = net - sum(profits[:3]) if n else None
    add("concentration", "是否依赖少数幸运交易", 10,
        ("pass" if remainder > 0 else "warn") if n >= 30 else "insufficient",
        (f"去掉最大三笔盈利后的已平仓净损益为 {remainder:.2f}（本策略币种）。" if n else "暂无完整交易。")
        + "这是集中度检查，不是重新回测。",
        "检查是否由少数股票或事件贡献大部分收益，不据此删除亏损交易。")
    add("hypothesis", "是否写明策略为什么有效", 5, "pass" if hypothesis else "missing",
        str(hypothesis)[:2000] if hypothesis else "没有记录策略假设。",
        "运行前写下原因、适用条件和什么结果会推翻它；事后解释不能充当预先约定。")
    add("trials", "是否记录全部试验", 10, "pass" if audited else "missing",
        (f"可追溯试跑至少 {trials} 次（含失败及重复运行）。" if trials is not None else "历史试验总数未知。")
        + "人工及其他工具的尝试未核实，不能声称完整。",
        "保留所有成功与失败的版本；停止在同一段历史上反复选最好结果，另留未看过的数据。")
    after = [r for r in rows if frozen_through and str(r.get("entry_date") or "") > str(frozen_through)]
    add("unseen", "是否在未见过的数据验证", 25, "missing",
        (f"记录的冻结界线为 {frozen_through}，之后进场且已平仓 {len(after)} 笔。" if frozen_through else "没有本方向独立冻结界线。")
        + "尚未审计规则版本、历史数据快照及查看记录，不认定为独立验证。",
        "锁定策略及判定标准，从锁定后新到的数据开始观察；看过并用于改规则的结果归回研究集。")
    add("parameters", "参数稍变是否仍有效", 10, "missing", "尚未运行邻近参数对照。",
        "预先固定参数邻域，一次运行全部对照，不从中另挑赢家重新打分。")
    add("costs", "成本增加是否还能承受", 10, "missing", "尚未按更高费用和滑点重新运行完整组合。",
        "按同一资金与成交约束重跑成本压力测试；不能只从总收益中随意减一个百分比。")
    add("integrity", "数据与成交是否可信", 10, "blocked" if blockers else "missing",
        "；".join(blockers) if blockers else "尚未完成历史股票池、时点数据、信号与成交时序审计。",
        "先修复数据或成交假设，再研究调参；高回测收益不能抵消这一项。")
    score = sum(c["points"] for c in checks) if n else None
    return dict(version=VERSION, score=score, max_score=100, scope=scope, as_of=str(as_of) if as_of else None,
                label="存在回测限制" if blockers else "证据不足", checks=checks,
                completed_checks=sum(c["status"] in ("pass", "warn", "blocked") for c in checks),
                total_checks=len(checks), known_trials=trials,
                warning="验证充分度，不是收益评分、通过率或过拟合概率；不能据此认定策略可实盘。",
                next_step="先处理回测限制，再冻结规则观察新数据；保留修改方向、证据不足、放弃假设三种结论。",
                diagnostics=dict(closed=n, invalid_cycles=len(rounds)-n, net_without_top3=remainder,
                                 profitable_months=positive, active_months=len(months)))


def public_report(history, days, line, branch):
    # 一条研究线可以有多个方向，不能把主方向的冻结日套到新分支。
    line = line or {}
    return assess(history, days=len(days), hypothesis=line.get("hypothesis"),
                  frozen_through=line.get("frozen_through") if line.get("best") == branch else None,
                  as_of=days[-1] if days else None, scope="公共研究 · " + branch,
                  blockers=("历史股票池与时点数据尚未完成审计", "信号与成交时序、滑点尚未完成独立验证"))


def manual_report(run):
    result = run.get("result") or {}
    # 当前手动引擎每个信号独立持仓，一次卖出完成周期；未来分批卖出必须改成完整周期汇总。
    rounds = [dict(pnl_abs=t.get("net_pnl"), entry_date=t.get("entry_date"), exit_date=t.get("date"))
              for t in result.get("trades", []) if t.get("side") == "sell"]
    report = assess(rounds, days=len(result.get("nav", [])), hypothesis=run.get("hypothesis"),
                    trials=run.get("trial_number"), as_of=result.get("end"), scope="个人历史回测",
                    blockers=("收盘信号按同日收盘价成交，尚未验证可成交性", "没有滑点模型", "股票池与 ST 名称使用当前数据"))
    report.update(rule_hash=fingerprint(run.get("params", {})), run_id=run.get("id"),
                  failure_rule=run.get("failure_rule"), trial_budget=run.get("trial_budget"))
    if run.get("trial_number", 0) >= run.get("trial_budget", 20):
        report["next_step"] = "已达到本方向的试验预算，建议暂停调参，复核假设或收集新数据。预算不自动重置。"
    return report


def record_submit(cur, key, cfg, run, body):
    """与任务提交同一事务保存，不受界面只保留十次的限制。旧记录只能算下界。"""
    from app.services.quant import agent_run as ar
    for field in ("hypothesis", "failure_rule"):
        text = body.get(field, "")
        if not isinstance(text, str) or len(text) > 2000:
            raise ValueError("策略假设和放弃条件必须是最多 2000 字的文本")
        run[field] = text.strip()
    counter_key = "overfit-count:" + key
    counter = ar._meta_get(cur, counter_key) or {"count": len(cfg.get("runs", [])), "budget": 20}
    counter["count"] += 1
    run["trial_number"] = counter["count"]
    run["trial_budget"] = counter["budget"]
    run["rule_hash"] = fingerprint(run["params"])
    # 指纹覆盖执行文件；它支持追溯，不等价于完成时点数据审计。
    from pathlib import Path
    root = Path(__file__).parent
    files = ("agent_manual.py", "agent_limitup.py", "agent_rule_editor.py", "commission.py")
    run["implementation_hash"] = fingerprint({name: hashlib.sha256((root/name).read_bytes()).hexdigest()
                                             for name in files if (root/name).exists()})
    ar._meta_set(cur, counter_key, counter)
    ar._meta_set(cur, "overfit-run:" + key + ":" + run["id"], run)


def record_result(cur, key, run):
    from app.services.quant import agent_run as ar
    if run.get("status") == "done":
        run["overfit"] = manual_report(run)
    # 进度轮询不反复复制整份账本，只记录结果和失败；queued 已在提交时持久化。
    if run.get("status") in ("done", "failed"):
        ar._meta_set(cur, "overfit-run:" + key + ":" + run["id"], run)
