"""智能炒股 · 复核（复盘）回路 · 服务层（第四段 R3 · `plan/R3.md` §一.1）。

收盘之后（各市场时段末点 + 可配延迟）跑一次复盘：**读当日的账本读数 → 让模型只想文字
→ 把结论写成经验**。三件事分别在三个端点 / 三个 Activity 里（口径见 `routers/fin_review.py`）：

| 步骤 | 干什么 | 碰库吗 |
|---|---|---|
| `collect()` | 取当日 `fin_report`（含 `self_review` 三问）+ `fin_report_fact` + 当日 `fin_trade` | **只读** |
| `propose()` | 模型只写文字（`statement` / `applicability` / `invalidation_condition`）；**数字一律以 `fact` 引用出现** | 只读（不写经验） |
| `append`（在 fin-worker） | 走 `POST /internal/fin/memory/evidence` 写经验 | 经 Memory Service 唯一入口 |

**这一层不写经验**：写经验只有一个入口（`services/fin/memory.py`），复核服务只**产出候选**，
由 fin-worker 的 `review_append` 活动经内网口令通道提交。这样「唯一入口」在复核这条链路上也没破。

## 三条口径（都对得起「不许编数字」这条铁律）

1. **模型只写文字**。返回体里 `statement` 的每一个数字都要能落到 `fin_report_fact` 的某一行
   （复用 `report.validate_numbers` 的回读校验口径）。落不到 → **整条候选作废**，
   不是「删掉那个数字继续用」。
2. **证据只能是当日真实存在的引用**。`fact` 引用必须命中本次收集到的事实行，
   `trade` 引用必须命中本次收集到的成交 —— 模型编一个 id 出来，那条候选直接丢。
3. **`kind` 只允许 `fact` / `hypothesis`**。`verified` 要求 `method` + `sample_size`（样本笔数），
   而这两个数**只能来自真实的验证过程**，模型无从取得 —— 让它「随便给一个样本数」
   就是编数字。所以复核回路**不产出 `verified`**（那是后面阶段验证器的事）。

模型不可用 / 输出不可解析 → **降级为规则文案**（`deterministic_candidates`），
照样只复述真实引用、不下结论；降级在返回体里以 `used_fallback` **明示**，不伪装成 AI 复盘。
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

import psycopg2
import psycopg2.extras
from loguru import logger

from app.services.fin import memory as memory_svc
from app.services.fin import report as report_svc

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://hunter:hunter@localhost:5432/hunter")

SHANGHAI = timezone(timedelta(hours=8))

# 复核回路只产出这两类（见模块文档第 3 条）。`verified` 的样本数只能来自真实验证过程。
REVIEW_KINDS = ("fact", "hypothesis")

# 一轮复盘最多写几条经验 —— 不是「越多越好」：一天的账本读数撑不起十条独立结论。
MAX_CANDIDATES = 8

# 规则 6 同款判据（`statement` 里出现阿拉伯数字即作废）。**不另写一份正则** ——
# 直接借 `memory.statement_has_numbers`，两处判据只有一个真值。
_has_numbers = memory_svc.statement_has_numbers


def get_conn():
    """与 `report.py` / `memory.py` 同口径：psycopg2 直连 `DATABASE_URL`，无连接池。"""
    return psycopg2.connect(DATABASE_URL)


def _iso(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


# ════════════════════════════════════════════════════════════════════════
# 一 · 收集（只读）
# ════════════════════════════════════════════════════════════════════════

def _report_id(project_id: str, trade_date: str, market: Optional[str]) -> str:
    return report_svc.report_id_for(project_id, trade_date, market)


def collect(conn, project_id: str, trade_date: str,
            market: Optional[str] = None) -> dict:
    """取某项目某交易日（某市场）的**当日账本读数** —— 报告 + 事实行 + 成交。**只读。**

    `market` 不给时不加市场过滤（一期单市场语义），与 `report.collect` 同口径。
    当日成交按**上海时间的自然日**取（`traded_at` 是 TIMESTAMPTZ，`AT TIME ZONE` 换算后再比日期）——
    `fin_report` 的 `trade_date` 就是这个口径。

    报告不存在时 `report=None`、`facts=[]` —— **不报错**：复核可以在「只有成交、还没报告」
    的时点跑（报告生成失败 / 时点没跑到），这时它只复盘成交，不假装读过报告。
    """
    mfilter = " AND market = %s" if market else ""
    margs = (market,) if market else ()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT project_id, user_id, tier FROM fin_project WHERE project_id = %s",
                    (project_id,))
        project = cur.fetchone()
        if not project:
            raise LookupError(f"项目不存在：{project_id}")

        report_id = _report_id(project_id, trade_date, market)
        loaded = report_svc.load_report(conn, report_id)
        report = None
        facts: list[dict] = []
        if loaded:
            raw = loaded["report"]
            report = {
                "report_id": raw.get("report_id"),
                "trade_date": _iso(raw.get("trade_date")),
                "market": raw.get("market"),
                "status": raw.get("status"),
                "analysis_text": raw.get("analysis_text"),
                "self_review": raw.get("self_review") or {},
                "valuation_as_of": _iso(raw.get("valuation_as_of")),
                "used_fallback": bool(raw.get("used_fallback")),
            }
            facts = [_fact_row(f) for f in loaded["facts"]]

        # 当日成交（含人机归因：`fin_trade.source` + `fin_order.actor/intent_ref`）。
        cur.execute(
            "SELECT t.trade_id, t.code, t.side, t.qty, t.price, t.amount, t.total_fee, "
            "       t.source, t.market, t.traded_at, o.actor, o.intent_ref, o.decline_reason "
            "FROM fin_trade t JOIN fin_order o ON o.order_id = t.order_id "
            "WHERE t.project_id = %s "
            f"  AND (t.traded_at AT TIME ZONE 'Asia/Shanghai')::date = %s"
            + (" AND t.market = %s" if market else "") +
            " ORDER BY t.traded_at ASC",
            (project_id, trade_date, *margs),
        )
        trades = [{
            "trade_id": r["trade_id"],
            "code": r["code"],
            "side": r["side"],
            "qty": r["qty"],
            "price": str(r["price"]) if r["price"] is not None else None,
            "amount": str(r["amount"]) if r["amount"] is not None else None,
            "total_fee": str(r["total_fee"]) if r["total_fee"] is not None else None,
            "source": r["source"],
            "actor": r.get("actor"),
            "market": r.get("market"),
            "traded_at": _iso(r["traded_at"]),
        } for r in cur.fetchall()]

    return {
        "project_id": project_id,
        "trade_date": trade_date,
        "market": market,
        "tier": (project.get("tier") if isinstance(project, dict) else None),
        "report": report,
        "facts": facts,
        "trades": trades,
        "codes": sorted({str(t["code"]) for t in trades}),
    }


def _fact_row(f: dict) -> dict:
    """一条事实行 → 复核用得到的那几个字段（值 / 单位 / 中文名 / 引用）。"""
    key = str(f.get("metric_key") or "")
    label, unit = report_svc.METRIC_SPEC.get(key, (None, None))
    value = f.get("value")
    return {
        "metric_key": key,
        "label_cn": label or key,
        "value": (str(value) if value is not None else None),
        "unit": f.get("unit") or unit,
        "currency": f.get("currency"),
        "ref_id": f"{f.get('report_id')}:{key}",     # memory.query 的 `fact` 引用形态
        "source_ref": f.get("source_ref"),
    }


# ════════════════════════════════════════════════════════════════════════
# 二 · 候选经验（纯函数 —— 可在单测里直接喂数据测）
# ════════════════════════════════════════════════════════════════════════

def fact_refs(collected: dict) -> set[str]:
    return {f["ref_id"] for f in collected.get("facts") or []}


def trade_ids(collected: dict) -> set[str]:
    return {t["trade_id"] for t in collected.get("trades") or []}


def validate_candidate(cand: dict, collected: dict) -> tuple[bool, str]:
    """一条候选经验能不能落库。**不过就不落**（不是「修一修改一改」）。

    四道：`kind` 在允许枚举内 · `statement` 非空且无阿拉伯数字（规则 6）·
    `evidence` 至少一行且**每个引用都真实存在于本次收集里**（防编造引用）·
    `hypothesis` 的 `status` 由服务端强制（这里不看）。
    """
    if not isinstance(cand, dict):
        return False, "候选不是对象"
    kind = str(cand.get("kind") or "").strip()
    if kind not in REVIEW_KINDS:
        return False, f"复核回路只产出 {REVIEW_KINDS}，收到 {kind!r}"
    statement = str(cand.get("statement") or "").strip()
    if not statement:
        return False, "statement 为空"
    if _has_numbers(statement):
        return False, "statement 里出现阿拉伯数字（数字一律走 evidence_kind='fact' 引用）"
    evidence = cand.get("evidence") or []
    if not isinstance(evidence, list) or not evidence:
        return False, "evidence 至少一行"
    ok_facts, ok_trades = fact_refs(collected), trade_ids(collected)
    seen: set[tuple[str, str]] = set()
    for ev in evidence:
        if not isinstance(ev, dict):
            return False, "evidence 行不是对象"
        ekind = str(ev.get("evidence_kind") or "").strip()
        ref_id = str(ev.get("ref_id") or "").strip()
        if ekind == "fact" and ref_id not in ok_facts:
            return False, f"fact 引用不在当日事实行里：{ref_id}"
        if ekind == "trade" and ref_id not in ok_trades:
            return False, f"trade 引用不在当日成交里：{ref_id}"
        if ekind not in ("fact", "trade"):
            return False, f"复核回路只引用 fact / trade，收到 {ekind!r}"
        if (ekind, ref_id) in seen:
            return False, f"证据重复：{ekind} {ref_id}"
        seen.add((ekind, ref_id))
    return True, ""


def deterministic_candidates(collected: dict) -> list[dict]:
    """模型不可用时的**规则候选**：只复述「当日发生了哪一类成交」这个事实，不下结论。

    三类，各自带**真实存在**的引用：
    · 有 AI 成交 → `fact`，证据 = 那几笔成交；
    · 有**人工**成交 → 单独一条（服务端会据证据真值把它记成 `human_mixed`，与 AI 自主经验分开累计）；
    · 没有成交但有报告 → `fact`，证据 = 报告的一条事实行（对账结论）。

    都没有 → **空列表**（没有可复盘的事实就什么都不写 —— 「空的比假的好」）。
    """
    trades = collected.get("trades") or []
    facts = collected.get("facts") or []
    out: list[dict] = []

    ai_trades = [t for t in trades if t.get("source") == "ai"]
    human_trades = [t for t in trades if t.get("source") in memory_svc.HUMAN_TRADE_SOURCES]
    if ai_trades:
        out.append({
            "kind": "fact",
            "statement": "本交易日执行了策略自主发起的成交，逐笔明细见证据引用",
            "applicability": f"{collected.get('market') or '本市场'} · 策略自主成交",
            "invalidation_condition": "成交口径或账本归因口径变更后需重新核对",
            "evidence": [{"evidence_kind": "trade", "ref_id": t["trade_id"]} for t in ai_trades],
            "basis": "rule:ai_trades",
        })
    if human_trades:
        out.append({
            "kind": "fact",
            "statement": "本交易日发生了人工介入的成交，与策略自主成交分开累计",
            "applicability": f"{collected.get('market') or '本市场'} · 人工成交",
            "invalidation_condition": "账本的人机归因口径变更后需重新核对",
            "evidence": [{"evidence_kind": "trade", "ref_id": t["trade_id"]} for t in human_trades],
            "basis": "rule:human_trades",
        })
    if not trades and facts:
        recon = next((f for f in facts if f["metric_key"] == "recon_passed"), None) or facts[0]
        out.append({
            "kind": "fact",
            "statement": "本交易日没有成交，账本自洽性以对账结论为准",
            "applicability": f"{collected.get('market') or '本市场'} · 无成交交易日",
            "invalidation_condition": "对账结论或账本口径变更后需重新核对",
            "evidence": [{"evidence_kind": "fact", "ref_id": recon["ref_id"]}],
            "basis": "rule:no_trades",
        })
    return out[:MAX_CANDIDATES]


# ════════════════════════════════════════════════════════════════════════
# 三 · 模型提议（只写文字）
# ════════════════════════════════════════════════════════════════════════

_SYS_PROMPT = (
    "你是「猎鹿人」智能交易的复盘助手。你要指出**哪条认知被今天的账本事实证实、"
    "哪条被推翻、需要新增什么假设**，写成可以长期留存的「经验」。\n"
    "硬性要求：\n"
    "1. **正文里不许出现任何阿拉伯数字**。数字一律通过 evidence_fact_keys / evidence_trade_ids "
    "引用下面给出的真实数据行 —— 系统会用回读校验核对，写了数字的条目直接作废。\n"
    "2. 只用中文；专业缩写（PE、T+1、MACD）可保留。\n"
    "3. 只输出一个 JSON 对象，不要任何解释性前后缀：\n"
    '{"experiences":[{"kind":"fact 或 hypothesis","statement":"一句话结论（无数字）",'
    '"applicability":"适用边界（市场 / 板块 / 时点 / 标的形态）",'
    '"invalidation_condition":"什么情况该重验",'
    '"evidence_fact_keys":["nav"],"evidence_trade_ids":["trd_..."]}]}\n'
    "4. 每条经验**至少引用一条真实存在的事实行或成交**。宁可少写，也不要凑数。\n"
    "5. `kind` 只能是 fact（今天的账本事实说明的）或 hypothesis（待验证的推断）；"
    "不要写 verified —— 验证结论要真的做过验证，不是复盘说一句就算。"
)


def _review_payload(collected: dict) -> str:
    """给模型看的当日读数（**只给真实数据**，不给任何编造的占位）。"""
    lines = [f"交易日：{collected.get('trade_date')}",
             f"市场：{collected.get('market') or '（未指定）'}"]
    rep = collected.get("report")
    if rep:
        sr = rep.get("self_review") or {}
        lines += ["", "当日自我复盘（模型写的定性总结）：",
                  f"  做对了什么：{sr.get('did_well') or '—'}",
                  f"  做得不好：{sr.get('did_bad') or '—'}",
                  f"  明天改什么：{sr.get('change_tomorrow') or '—'}"]
    lines += ["", "当日事实行（key 就是引用用的 evidence_fact_keys）：", "| key | 含义 | 值 | 单位 |",
              "|---|---|---|---|"]
    for f in collected.get("facts") or []:
        lines.append(f"| {f['metric_key']} | {f['label_cn']} | {f['value']} | {f['unit'] or '—'} |")
    lines += ["", "当日成交（trade_id 就是引用用的 evidence_trade_ids）：",
              "| trade_id | 代码 | 方向 | 数量 | 价 | 归因 |", "|---|---|---|---|---|---|"]
    for t in collected.get("trades") or []:
        lines.append(f"| {t['trade_id']} | {t['code']} | {t['side']} | {t['qty']} | "
                     f"{t['price']} | {t['source']} |")
    if not collected.get("facts") and not collected.get("trades"):
        lines.append("（当天既没有报告事实行也没有成交 —— 没有可复盘的数据。）")
    return "\n".join(lines)


def _parse_experiences(content: str) -> list[dict]:
    """从模型输出里抠出 experiences 数组。抠不出 → 抛（由调用方降级）。"""
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"```\s*$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型输出里没有 JSON 对象")
    obj = json.loads(text[start:end + 1])
    items = obj.get("experiences")
    if not isinstance(items, list) or not items:
        raise ValueError("模型输出缺少 experiences 数组")
    return [i for i in items if isinstance(i, dict)]


def _to_candidate(raw: dict, collected: dict) -> Optional[dict]:
    """模型的一条 → 候选经验（只做**形状**搬运；合法性由 `validate_candidate` 判）。

    引用从 `evidence_fact_keys` / `evidence_trade_ids` 翻成 `(kind, ref_id)`；
    事实 key 翻成 `"<report_id>:<metric_key>"`（Memory Service 的 `fact` 引用形态）。
    """
    report = collected.get("report") or {}
    report_id = report.get("report_id")
    if not report_id:
        return None
    ev: list[dict] = []
    for key in raw.get("evidence_fact_keys") or []:
        ev.append({"evidence_kind": "fact", "ref_id": f"{report_id}:{str(key).strip()}"})
    for tid in raw.get("evidence_trade_ids") or []:
        ev.append({"evidence_kind": "trade", "ref_id": str(tid).strip()})
    return {
        "kind": str(raw.get("kind") or "").strip(),
        "statement": raw.get("statement"),
        "applicability": raw.get("applicability"),
        "invalidation_condition": raw.get("invalidation_condition"),
        "evidence": ev,
        "basis": "llm",
    }


async def propose(collected: dict, *, analyzer: Optional[Callable] = None) -> dict:
    """产出候选经验：**优先模型，失败降级规则文案**。

    返回 `{"candidates": [...], "used_fallback": bool, "reason": str|None, "llm_model": str,
    "rejected": [{"statement":..., "reason":...}]}`。

    `rejected` 如实列出被回读校验 / 引用校验丢掉的候选 —— **不是静默丢弃**：
    「模型写错了」和「今天没什么可写」是两件事，运维要能分辨。
    """
    fallback = deterministic_candidates(collected)
    if analyzer is None:
        analyzer = _analyze_with_llm
    try:
        raw_items = await analyzer(collected)
    except Exception as exc:  # noqa: BLE001 —— 降级要留痕，不能让一次复盘失败打挂整轮
        reason = f"{exc.__class__.__name__}: {str(exc)[:160]}"
        logger.warning("[fin.review] 模型不可用，降级为规则文案：{}", reason)
        return {"candidates": fallback, "used_fallback": True, "reason": reason,
                "llm_model": "none", "rejected": []}

    candidates: list[dict] = []
    rejected: list[dict] = []
    for raw in raw_items:
        cand = _to_candidate(raw, collected)
        if cand is None:
            rejected.append({"statement": str(raw.get("statement") or "")[:120],
                             "reason": "当日没有报告事实行，无法建立 fact 引用"})
            continue
        ok, why = validate_candidate(cand, collected)
        if ok:
            candidates.append(cand)
        else:
            rejected.append({"statement": str(cand.get("statement") or "")[:120], "reason": why})
    if not candidates:
        # 模型答了但一条都不能用 —— 退回规则文案（并且 reason 说明是模型的问题，不是没数据）。
        reason = f"模型候选全部未通过校验（{len(rejected)} 条）"
        logger.warning("[fin.review] {}，降级为规则文案", reason)
        return {"candidates": fallback, "used_fallback": True, "reason": reason,
                "llm_model": "llm", "rejected": rejected}
    return {"candidates": candidates[:MAX_CANDIDATES], "used_fallback": False, "reason": None,
            "llm_model": "llm", "rejected": rejected}


async def _analyze_with_llm(collected: dict) -> list[dict]:
    """真正调模型。形状与 `report.analyze_with_llm` 一致（同一个 `get_llm()`）。"""
    from app.providers.llm import get_llm

    llm = get_llm()
    user = (
        f"以下是今天的真实账本读数（唯一可引用的数据来源）：\n\n"
        f"{_review_payload(collected)}\n\n"
        "请写出今天值得沉淀的经验（没有就写空数组）。"
    )
    resp = await llm.chat(
        [{"role": "system", "content": _SYS_PROMPT}, {"role": "user", "content": user}],
        temperature=0.2, max_tokens=1200,
    )
    return _parse_experiences((resp or {}).get("content") or "")


# ════════════════════════════════════════════════════════════════════════
# 四 · 编排：一次复核（collect → propose）
# ════════════════════════════════════════════════════════════════════════

async def run_review(project_id: str, trade_date: str, *, market: Optional[str] = None,
                     conn=None, analyzer: Optional[Callable] = None) -> dict:
    """一次复核的**只读**部分：收集 + 产出候选。**不写经验**（写走 Memory Service 唯一入口）。"""
    own = conn is None
    conn = conn or get_conn()
    try:
        collected = collect(conn, project_id, trade_date, market)
        proposed = await propose(collected, analyzer=analyzer)
        return {
            "project_id": project_id,
            "trade_date": trade_date,
            "market": market,
            "has_report": collected.get("report") is not None,
            "trade_count": len(collected.get("trades") or []),
            "fact_count": len(collected.get("facts") or []),
            **proposed,
        }
    finally:
        if own:
            conn.close()
