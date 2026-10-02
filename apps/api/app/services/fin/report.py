"""智能炒股 · 每日报告（M5，对应 `05 §3.2` M-17 / `05 §3.4` M-32）。

**三层分离**（`01方案 §九`）—— 这是本模块存在的全部理由：

| 层 | 谁做 | 落哪 |
|---|---|---|
| 事实层 | **确定性指标代码**（下面 `build_facts`，AI 一个字节都碰不到） | `fin_report_fact` 每行 `metric_key/value/unit/source_ref/computed_by` |
| 分析层 | AI 基于事实表写 | `fin_report.analysis_text` / `self_review` |
| 表达层 | 风格化渲染（一期一种风格：`render_html`） | HTML 产物（`hunter_artifacts`）/ 报告正文 |

**四条不可退让的约束：**

1. **数字只能来自事实层。** 指标算不出（没有估值、只有一根 nav 点、缺价…）就写
   `None` —— 报告里渲染成 `—`，**绝不拿 0 或成本价顶替**（`总控规则 §六-1`）。
2. **生成后必过回读校验**（`validate_numbers`）：把分析文字里所有数字抽出来逐个比对
   `fin_report_fact`；对不上 → `status='failed'`、**不出产物、不写发布回执**。
   这条铁律在别的项目上出过事故（`-4.57%` 被写成 `+8.74%`），所以校验是**逐行比对**，
   不是让模型自查。
3. **`source_ref` 与 `computed_by` 非空**是硬要求：每一行都要能回答「这个数从哪来、
   哪段代码算的」。
4. **三种口径不许拼成一条收益曲线**（`01方案 §2.2`）。本期只有实盘模拟（live paper）
   一种口径 —— 事实行 `return_caliber_count` 把它写成 1，且**所有收益类事实的
   `source_ref` 只指向 `fin_*` 账本**，`assert_single_caliber` 会盯着这件事：
   任何一行引用了回测 / 前向模拟 / 影子的来源即判失败。

模块里 **纯函数**（`build_facts` / `extract_numbers` / `validate_numbers` /
`render_html`）与 **IO**（`collect` / `persist` / `validate_stored`）分开，
前者可以在 `tests/test_fin_report.py` 里不连库直接测。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable, Optional

import psycopg2
import psycopg2.extras

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://hunter:hunter@localhost:5432/hunter")

SHANGHAI = timezone(timedelta(hours=8))

# 代码位置 + 算法版本。`computed_by` 写的就是它 —— 「这个数哪段代码算的」。
CODE_VERSION = "fin.report.build_facts@m5.1"
PROMPT_VERSION = "fin-report-zh@1.0"

# 三个自我总结栏目的稳定 key（`09 §4.8` fin_report.self_review）。
SELF_REVIEW_KEYS = ("did_well", "did_bad", "change_tomorrow")

# 一期只有实盘模拟一种口径（`01方案 §2.2`）。回测 / 前向模拟 / 影子**不在**这条曲线里。
RETURN_CALIBER = "live_paper"
# 任何事实行的 source_ref 命中这些词 = 把别的口径掺进来了 → 校验失败。
_FORBIDDEN_SOURCES = ("backtest", "pred_backtest", "shadow", "replay_sim", "前向模拟", "影子", "回测")

# ── 指标规格：key → (中文名, 单位) ─────────────────────────────────────────
# 渲染与提示词都从这里取标签，**不另抄一份**（`CLAUDE.md`「同一件事写在多处」）。
METRIC_SPEC: dict[str, tuple[str, str]] = {
    "initial_capital":    ("初始本金", "CNY"),
    "total_assets":       ("总资产", "CNY"),
    "cash_available":     ("可用资金", "CNY"),
    "cash_frozen":        ("冻结资金", "CNY"),
    "market_value":       ("持仓市值", "CNY"),
    "nav":                ("净值", "倍"),
    "return_pct":         ("累计收益率", "%"),
    "daily_return_pct":   ("当日收益率", "%"),
    "max_drawdown":       ("最大回撤", "%"),
    "fee_total":          ("累计费用", "CNY"),
    "fee_today":          ("当日费用", "CNY"),
    "trade_count_today":  ("当日成交笔数", "笔"),
    "trade_count_total":  ("累计成交笔数", "笔"),
    "position_count":     ("持仓数", "只"),
    "open_order_count":   ("未成交挂单", "笔"),
    "recon_passed":       ("对账通过", ""),
    "data_quality_ok":    ("估值数据完整", ""),
    "days_traded":        ("已运行交易日", "日"),
    "return_caliber_count": ("收益口径数", "种"),
}


# ════════════════════════════════════════════════════════════════════════
# 一 · 事实层：确定性指标（纯函数，不连库）
# ════════════════════════════════════════════════════════════════════════

def _q(value: Any, places: str) -> Optional[Decimal]:
    """把一个数按指定精度量化。`None` 原样返回 —— 算不出就是算不出。"""
    if value is None:
        return None
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not d.is_finite():
        return None
    return d.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def _money(value: Any) -> Optional[Decimal]:
    return _q(value, "0.0001")


def _ratio(value: Any) -> Optional[Decimal]:
    return _q(value, "0.000001")


def _pct(ratio: Optional[Decimal]) -> Optional[Decimal]:
    """把小数比例转成百分数（0.004 → 0.4）。`None` 原样返回。"""
    if ratio is None:
        return None
    return _q(ratio * Decimal(100), "0.0001")


def facts_map(facts: list[dict]) -> dict[str, dict]:
    return {f["metric_key"]: f for f in facts}


def build_facts(ctx: dict) -> list[dict]:
    """纯函数：从账本读数算出全部事实行。

    `ctx` 的每一项都是**已经读出来的账本数据**（见 `collect`）。本函数不连库、不算网络，
    因此可以在单测里直接喂构造数据。**每个数算不出就 `None`**，绝不兜底成 0。
    """
    project = ctx["project"]
    valuation = ctx.get("valuation_latest")
    series = ctx.get("valuation_series") or []
    trades = ctx.get("trades") or []
    trade_date = ctx["trade_date"]

    def _row(key: str, value: Optional[Decimal], source_ref: str) -> dict:
        label, unit = METRIC_SPEC[key]
        return {
            "metric_key": key, "label_cn": label, "value": value, "unit": unit,
            "source_ref": source_ref, "computed_by": CODE_VERSION,
        }

    facts: list[dict] = []

    # 本金：项目的登记值（不是流水推算 —— 它是账本的起点，写入后不可改）。
    initial = _money(project.get("initial_capital"))
    facts.append(_row("initial_capital", initial, f"fin_project:{project['project_id']}"))

    # 估值三项 + 净值：来自最近一次估值（没有估值 = 报告日缺数，全 None）。
    as_of = valuation.get("as_of") if valuation else None
    vref = f"fin_valuation:{_day(as_of) or 'unknown'}"
    facts.append(_row("total_assets", _money(valuation.get("total_assets")) if valuation else None, vref))
    facts.append(_row("cash_available", _money(valuation.get("cash_available")) if valuation else None, vref))
    facts.append(_row("cash_frozen", _money(valuation.get("cash_frozen")) if valuation else None, vref))
    facts.append(_row("market_value", _money(valuation.get("market_value")) if valuation else None, vref))
    nav = _ratio(valuation.get("nav")) if valuation else None
    facts.append(_row("nav", nav, vref))

    # 累计收益率 = nav - 1（口径：以本金为分母，与估值同源）。
    return_ratio = (nav - Decimal(1)) if nav is not None else None
    facts.append(_row("return_pct", _pct(return_ratio), vref))

    # 当日收益率 = 今天 nav / 上一根 nav - 1。**不足两根估值点算不出**（写 None）。
    daily = None
    if len(series) >= 2 and series[-1].get("nav") is not None and series[-2].get("nav") not in (None, 0):
        prev = Decimal(str(series[-2]["nav"]))
        cur = Decimal(str(series[-1]["nav"]))
        if prev != 0:
            daily = _pct((cur / prev) - Decimal(1))
    facts.append(_row("daily_return_pct", daily, f"fin_valuation:{_day(as_of) or 'unknown'}"))

    # 最大回撤：对整段净值序列取峰谷最大跌幅。不足两根算不出。
    mdd = None
    if len(series) >= 2:
        peak: Optional[Decimal] = None
        worst = Decimal(0)
        ok = True
        for row in series:
            v = row.get("nav")
            if v is None:
                ok = False
                break
            v = Decimal(str(v))
            peak = v if peak is None else max(peak, v)
            if peak and peak != 0:
                worst = min(worst, (v - peak) / peak)
        if ok and peak is not None:
            mdd = _pct(worst)
    facts.append(_row("max_drawdown", mdd, f"fin_valuation:*（{len(series)} 根净值）"))

    # 费用：来自成交表（每笔成交的手续费 / 印花税 / 过户费之和）。
    fee_total = _money(sum((Decimal(str(t.get("total_fee") or 0)) for t in trades), Decimal(0)))
    facts.append(_row("fee_total", fee_total, "fin_trade:*"))
    today_trades = [t for t in trades if _day(t.get("traded_at")) == trade_date]
    fee_today = _money(sum((Decimal(str(t.get("total_fee") or 0)) for t in today_trades), Decimal(0)))
    facts.append(_row("fee_today", fee_today, f"fin_trade:{trade_date}"))

    facts.append(_row("trade_count_today", Decimal(len(today_trades)), f"fin_trade:{trade_date}"))
    facts.append(_row("trade_count_total", Decimal(len(trades)), "fin_trade:*"))

    positions = [p for p in (ctx.get("positions") or []) if int(p.get("qty") or 0) > 0]
    facts.append(_row("position_count", Decimal(len(positions)), "fin_position:*"))
    facts.append(_row("open_order_count", Decimal(int(ctx.get("open_order_count") or 0)), "fin_order:pending"))

    recon = ctx.get("recon_latest")
    facts.append(_row(
        "recon_passed",
        None if recon is None else (Decimal(1) if recon.get("passed") else Decimal(0)),
        "fin_recon_log:*",
    ))
    # 数据质量：估值 quality=='ok' 且未标 missing → 1，否则 0；没有估值 → None。
    quality = None
    if valuation is not None:
        quality = Decimal(1) if (valuation.get("quality") == "ok" and not valuation.get("missing_flag")) else Decimal(0)
    facts.append(_row("data_quality_ok", quality, vref))

    facts.append(_row("days_traded", Decimal(len(series)), "fin_valuation:*"))

    # 三种口径不拼接：本期只产出 1 种（实盘模拟）。这一行是**口径声明的事实依据**，
    # 与 `assert_single_caliber` 一起把「不许把回测/前向/影子拼进来」变成可校验的事实。
    facts.append(_row("return_caliber_count", Decimal(1),
                      f"const:caliber={RETURN_CALIBER}·01方案§2.2"))
    return facts


def assert_single_caliber(facts: list[dict]) -> list[str]:
    """返回违规说明列表（空 = 通过）。

    两件事一起保证「三种口径不拼成一条收益曲线」（`01方案 §2.2`）：
    - `return_caliber_count` 必须存在且等于 1；
    - **任何事实行的 `source_ref` 都不许指向回测 / 前向模拟 / 影子** ——
      本期收益只认 `fin_*` 账本这一份。
    """
    problems: list[str] = []
    m = facts_map(facts)
    caliber = m.get("return_caliber_count")
    if caliber is None or caliber.get("value") != Decimal(1):
        problems.append("缺少「收益口径数=1」的事实行：无法证明本期只有实盘模拟一种口径")
    for f in facts:
        ref = str(f.get("source_ref") or "")
        for bad in _FORBIDDEN_SOURCES:
            if bad.lower() in ref.lower():
                problems.append(
                    f"{f['metric_key']} 的 source_ref 指向另一种口径（{bad}）：{ref}")
    return problems


# ════════════════════════════════════════════════════════════════════════
# 二 · 回读校验：把分析文字里的每个数字拉回事实层
# ════════════════════════════════════════════════════════════════════════

# 先掩掉「不是数字主张」的东西：日期 / 时刻 / 形如 T+1 · MA20 的标识 / 版本号。
_MASK_PATTERNS = (
    re.compile(r"\d{4}-\d{2}-\d{2}"),             # ISO 日期
    re.compile(r"\d{1,2}:\d{2}(?::\d{2})?"),      # 时刻
    re.compile(r"[A-Za-z]{1,4}\s*[+\-]\s*\d+"),   # T+1 / MA20 / v2
    re.compile(r"v\d+(?:\.\d+)+"),                # 版本号
)
_NUM_TOKEN_RE = re.compile(r"[+\-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*[%％]?")
_FULLWIDTH_PCT = "％"


class NumberToken:
    """正文里抽到的一个数字主张。"""

    __slots__ = ("raw", "value", "is_pct", "decimals")

    def __init__(self, raw: str, value: Decimal, is_pct: bool, decimals: int):
        self.raw, self.value, self.is_pct, self.decimals = raw, value, is_pct, decimals


def extract_numbers(text: str) -> list[NumberToken]:
    """抽出正文里的全部数字主张。日期 / 时刻 / 代码形态的标识先被掩掉。"""
    if not text:
        return []
    masked = text
    for pat in _MASK_PATTERNS:
        masked = pat.sub(" ", masked)

    out: list[NumberToken] = []
    for m in _NUM_TOKEN_RE.finditer(masked):
        raw = m.group(0).strip()
        is_pct = raw.endswith("%") or raw.endswith(_FULLWIDTH_PCT)
        body = raw.rstrip("%" + _FULLWIDTH_PCT).replace(",", "").strip()
        if not body:
            continue
        try:
            value = Decimal(body)
        except InvalidOperation:
            continue
        decimals = len(body.split(".")[1]) if "." in body else 0
        out.append(NumberToken(raw, value, is_pct, decimals))
    return out


def _matches_fact(tok: NumberToken, facts: list[dict]) -> Optional[dict]:
    """一个数字主张能不能落到某条事实上。

    比对是**精度感知**的：事实按主张的小数位数四舍五入后相等即算命中
    （nav 1.000000 与正文里的 `1.00` 是同一个数）。带 `%` 的主张只认 `%` 单位的事实，
    反之亦然 —— 单位错了就是错了。
    """
    for f in facts:
        v = f.get("value")
        if v is None:
            continue
        unit = f.get("unit") or ""
        if tok.is_pct != (unit == "%"):
            continue
        q = _q(v, "0.000001" if tok.decimals >= 6 else "1" if tok.decimals == 0 else "0." + "0" * tok.decimals)
        if q is not None and q == tok.value:
            return f
    return None


def validate_numbers(text: str, facts: list[dict], *, codes: Optional[set[str]] = None) -> dict:
    """回读校验：`text` 里每一个数字都要能在 `facts` 里找到对应行。

    `codes` 是本项目涉及的股票代码（持仓 + 成交）。**裸的 6 位代码**（如 `601398`）
    是标的标识不是数字主张，命中 `codes` 才忽略 —— 不无脑掩 6 位数，
    否则 10 万档的本金 `100000` 会被一起掩掉。
    """
    codes = codes or set()
    checked = matched = 0
    violations: list[dict] = []
    for tok in extract_numbers(text):
        if tok.raw.rstrip("%" + _FULLWIDTH_PCT).replace(",", "").lstrip("+-") in codes:
            continue  # 股票代码，不是数字主张
        checked += 1
        hit = _matches_fact(tok, facts)
        if hit:
            matched += 1
        else:
            violations.append({
                "token": tok.raw.strip(),
                "reason": "正文里的这个数字在 fin_report_fact 里找不到对应行",
            })
    return {"ok": not violations, "checked": checked, "matched": matched, "violations": violations}


def validate_report(report: dict, facts: list[dict], *, codes: Optional[set[str]] = None) -> dict:
    """把整份报告的**全部 AI 文字**逐字段过一遍回读校验。

    包含 `analysis_text` 与 `self_review` 三栏 —— 「AI 编的数字」在哪都不许有。
    """
    codes = codes or set()
    fields: list[tuple[str, str]] = [("analysis_text", report.get("analysis_text") or "")]
    sr = report.get("self_review") or {}
    for k in SELF_REVIEW_KEYS:
        fields.append((f"self_review.{k}", sr.get(k) or ""))

    total_checked = total_matched = 0
    violations: list[dict] = []
    for name, text in fields:
        res = validate_numbers(text, facts, codes=codes)
        total_checked += res["checked"]
        total_matched += res["matched"]
        for v in res["violations"]:
            violations.append({**v, "field": name})
    problems = assert_single_caliber(facts)
    for p in problems:
        violations.append({"token": "-", "field": "caliber", "reason": p})
    return {
        "ok": not violations,
        "checked": total_checked,
        "matched": total_matched,
        "violations": violations,
    }


# ════════════════════════════════════════════════════════════════════════
# 三 · 表达层：渲染（一种风格）
# ════════════════════════════════════════════════════════════════════════

def format_value(value: Optional[Decimal], unit: str) -> str:
    """渲染一个事实值。`None` → `—`（**算不出就写 —，绝不编数**）。"""
    if value is None:
        return "—"
    if unit == "CNY":
        return f"{value:,.2f}"
    if unit == "%":
        return f"{value:+.2f}%"
    if unit == "倍":
        return f"{value:.4f}"
    if value == value.to_integral_value():
        return f"{int(value)}"
    return f"{value}"


def render_html(report: dict, facts: list[dict], *, project: dict) -> str:
    """表达层：把事实与文字渲染成 HTML 产物。**只改措辞与版面，不改数字。**"""
    fm = facts_map(facts)
    trade_date = report["trade_date"]

    def g(key: str) -> str:
        f = fm.get(key)
        return format_value(f.get("value") if f else None, (f or {}).get("unit", ""))

    kpi = [
        ("净值", g("nav")), ("累计收益率", g("return_pct")), ("当日收益率", g("daily_return_pct")),
        ("最大回撤", g("max_drawdown")), ("总资产", g("total_assets")), ("持仓市值", g("market_value")),
        ("可用资金", g("cash_available")), ("累计费用", g("fee_total")),
    ]
    kpi_html = "".join(
        f"<div class='kpi'><div class='k'>{k}</div><div class='v'>{v}</div></div>" for k, v in kpi)

    sr = report.get("self_review") or {}
    review_html = "".join(
        f"<div class='rev'><h4>{title}</h4><p>{_esc(sr.get(key) or '—')}</p></div>"
        for key, title in (("did_well", "做对了什么"), ("did_bad", "没做好什么"),
                           ("change_tomorrow", "明天怎么改")))

    facts_rows = "".join(
        "<tr>"
        f"<td>{_esc(f['metric_key'])}</td>"
        f"<td class='n'>{format_value(f.get('value'), f.get('unit') or '')}</td>"
        f"<td>{_esc(f.get('unit') or '—')}</td>"
        f"<td class='ref'>{_esc(f['source_ref'])}</td>"
        f"<td class='ref'>{_esc(f['computed_by'])}</td>"
        "</tr>"
        for f in sorted(facts, key=lambda x: x["metric_key"]))

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>每日报告 · {_esc(trade_date)}</title>
<style>
 body{{font:14px/1.7 -apple-system,"PingFang SC",sans-serif;color:#1d2129;max-width:860px;margin:24px auto;padding:0 16px}}
 h1{{font-size:20px}} h3{{margin-top:28px;border-left:3px solid #165dff;padding-left:8px}}
 .kpi{{display:inline-block;min-width:120px;margin:6px 10px 6px 0;padding:8px 12px;background:#f7f8fa;border-radius:8px}}
 .kpi .k{{color:#86909c;font-size:12px}} .kpi .v{{font-size:18px;font-weight:600}}
 .rev{{background:#f7f8fa;border-radius:8px;padding:10px 14px;margin:8px 0}} .rev h4{{margin:0 0 4px}}
 table{{border-collapse:collapse;width:100%;font-size:12px}} th,td{{border:1px solid #e5e6eb;padding:4px 6px;text-align:left}}
 td.n{{text-align:right;font-variant-numeric:tabular-nums}} td.ref{{color:#86909c;font-size:11px}}
 .warn{{background:#fff7e8;border:1px solid #ffd591;border-radius:6px;padding:8px 12px}}
 .caliber{{background:#e8f3ff;border-radius:6px;padding:8px 12px}}
</style></head><body>
<h1>每日报告 · {_esc(trade_date)}</h1>
<p>项目 <code>{_esc(project['project_id'])}</code> · 档位 {_esc(str(project.get('tier')))} ·
估值时点 {_esc(str(report.get('valuation_as_of')))} · 状态 <b>{_esc(report.get('status'))}</b></p>

<h3>收益与净值</h3>
<div>{kpi_html}</div>
<p class="caliber">收益口径：<b>实盘模拟（{g('return_caliber_count')} 种）</b>。
本报告的收益只来自这一条模拟账本；<b>回测 / 前向模拟 / 影子运行不与之拼成同一条曲线</b>（01方案 §2.2）。</p>

<h3>今日操作与费用</h3>
<p>当日成交 <b>{g('trade_count_today')}</b> 笔，累计成交 <b>{g('trade_count_total')}</b> 笔；
当日费用 <b>{g('fee_today')}</b>，累计费用 <b>{g('fee_total')}</b>。
持仓 <b>{g('position_count')}</b> 只，未成交挂单 <b>{g('open_order_count')}</b> 笔。</p>

<h3>账本与数据质量</h3>
<p>对账通过：<b>{g('recon_passed')}</b>（1 = 通过）；估值数据完整：<b>{g('data_quality_ok')}</b>（1 = 完整）。
已运行交易日 <b>{g('days_traded')}</b> 天。</p>

<h3>今天的自我总结</h3>
{review_html}

<h3>分析</h3>
<p>{_esc(report.get('analysis_text') or '—').replace(chr(10), '<br>')}</p>

<h3>事实表（报告里每个数字的来源）</h3>
<table><thead><tr><th>metric_key</th><th>值</th><th>单位</th><th>source_ref</th><th>computed_by</th></tr></thead>
<tbody>{facts_rows}</tbody></table>
<p><small>模型 {_esc(str(report.get('llm_model') or '—'))} · 提示词版本 {_esc(str(report.get('prompt_version') or '—'))} ·
生成于 {_esc(str(report.get('created_at') or '—'))}</small></p>
</body></html>"""


def _esc(text: Any) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


# ════════════════════════════════════════════════════════════════════════
# 四 · 分析层：AI 只写文字（数字受事实表约束）
# ════════════════════════════════════════════════════════════════════════

_SYS_PROMPT = (
    "你是「猎鹿人」智能交易的复盘助手。你只能**解释**事实表里已经算好的数字，"
    "**绝对不许自己生成、估算或修改任何数字**。\n"
    "硬性要求：\n"
    "1. 正文里出现的每一个数字，都必须逐字来自下面给出的事实表；不得使用任何别的数字"
    "（不要写序号、不要写日期、不要写百分比之外的整数）。\n"
    "2. 只用中文；专业缩写（PE、MACD、T+1）可保留。\n"
    "3. 事实表里为「—」的指标表示**没算出来**，要如实说「该项本期算不出」，不要补一个数。\n"
    "4. 只输出一个 JSON 对象，不要任何解释性前后缀：\n"
    '{"analysis_text": "3~5 段中文分析", '
    '"self_review": {"did_well": "…", "did_bad": "…", "change_tomorrow": "…"}}\n'
    "5. self_review 三栏尽量用定性描述，能不用数字就不用。"
)


def fact_table_text(facts: list[dict]) -> str:
    lines = ["| metric_key | 含义 | 值 | 单位 |", "|---|---|---|---|"]
    for f in facts:
        lines.append(
            f"| {f['metric_key']} | {f.get('label_cn','')} | "
            f"{format_value(f.get('value'), f.get('unit') or '')} | {f.get('unit') or '—'} |")
    return "\n".join(lines)


async def analyze_with_llm(facts: list[dict], ctx: dict) -> dict:
    """调用配置里的 LLM 产出分析层文字。**数字受事实表约束**（见 `_SYS_PROMPT`）。"""
    from app.providers.llm import get_llm

    llm = get_llm()
    user = (
        f"交易日：{ctx['trade_date']}\n"
        f"项目档位：{ctx['project'].get('tier')}\n\n"
        f"事实表（唯一可用的数字来源）：\n{fact_table_text(facts)}\n\n"
        "请基于以上事实写今天的复盘。"
    )
    resp = await llm.chat(
        [{"role": "system", "content": _SYS_PROMPT}, {"role": "user", "content": user}],
        temperature=0.3, max_tokens=1400,
    )
    content = (resp or {}).get("content") or ""
    parsed = _parse_analysis_json(content)
    model = (resp or {}).get("model") or ""
    return {
        "analysis_text": parsed["analysis_text"],
        "self_review": parsed["self_review"],
        "llm_provider": "llm", "llm_model": model or "unknown", "used_fallback": False,
    }


def _parse_analysis_json(content: str) -> dict:
    """从模型输出里抠出 JSON。抠不出就报错（由调用方降级为规则文案）。"""
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"```\s*$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型输出里没有 JSON 对象")
    obj = json.loads(text[start:end + 1])
    analysis = str(obj.get("analysis_text") or "").strip()
    sr = obj.get("self_review") or {}
    review = {k: str(sr.get(k) or "").strip() for k in SELF_REVIEW_KEYS}
    if not analysis or not any(review.values()):
        raise ValueError("模型输出缺少 analysis_text 或 self_review")
    return {"analysis_text": analysis, "self_review": review}


def fallback_analysis(facts: list[dict], ctx: dict, reason: str) -> dict:
    """LLM 不可用时的**规则文案**：只复述事实表，不新增任何数字、不下结论。

    与「净化不出中文 → 落只陈述真实数字的规则文案」同一条思路：降级要如实标注，
    不能伪装成 AI 分析。
    """
    fm = facts_map(facts)

    def g(key: str) -> str:
        f = fm.get(key)
        return format_value(f.get("value") if f else None, (f or {}).get("unit", ""))

    text = (
        f"（未启用 AI 分析：{reason}。以下为按事实表生成的自动摘要，只复述数字。）\n"
        f"本交易日账本读数：净值 {g('nav')}，累计收益率 {g('return_pct')}，"
        f"当日收益率 {g('daily_return_pct')}，最大回撤 {g('max_drawdown')}。"
        f"当日成交 {g('trade_count_today')} 笔，持仓 {g('position_count')} 只。"
        f"累计费用 {g('fee_total')}，对账通过 {g('recon_passed')}。"
    )
    review = {
        "did_well": "本期按既定规则运行，账本自洽性以对账结果为准（口径见事实表）。",
        "did_bad": "未启用模型分析，无法给出定性复盘；请检查大模型配置后重跑。",
        "change_tomorrow": "补齐大模型配置，让分析层基于事实表产出归因与改进项。",
    }
    return {"analysis_text": text, "self_review": review,
            "llm_provider": "fallback", "llm_model": "none", "used_fallback": True}


# ════════════════════════════════════════════════════════════════════════
# 五 · 账本读取（报告侧只读，不写账本）
# ════════════════════════════════════════════════════════════════════════

def get_conn():
    return psycopg2.connect(DATABASE_URL)


def _day(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(SHANGHAI).date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def collect(conn, project_id: str, trade_date: str) -> dict:
    """读一个项目在 `trade_date` 这天的全部账本读数（**只读**）。

    报告的事实层**只认账本**（`fin_*`），不读回测 / 前向模拟 / 影子的任何表。
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM fin_project WHERE project_id = %s", (project_id,))
        project = cur.fetchone()
        if not project:
            raise LookupError(f"项目不存在：{project_id}")

        cur.execute(
            "SELECT as_of, nav, total_assets, quality, missing_flag FROM fin_valuation "
            "WHERE project_id = %s ORDER BY as_of ASC", (project_id,))
        all_series = [dict(r) for r in cur.fetchall()]

        # 取**这一交易日**的收盘估值；没有 → 无报告日（不看「最近一次」，
        # 否则给过去某天出报告会拿今天的估值冒充，也就读进了未来）。
        valuation = None
        for row in all_series:
            if _day(row.get("as_of")) == trade_date:
                valuation = row

        # 序列截到估值那一刻：净值曲线与回撤只许用当日及以前的点（**不许前视**）。
        cutoff = valuation["as_of"] if valuation else None
        series = [r for r in all_series if cutoff is None or r["as_of"] <= cutoff]

        cur.execute(
            "SELECT trade_id, code, side, qty, price, amount, total_fee, traded_at "
            "FROM fin_trade WHERE project_id = %s ORDER BY traded_at ASC", (project_id,))
        trades = [dict(t) for t in cur.fetchall()]
        if cutoff is not None:
            trades = [t for t in trades if t["traded_at"] <= cutoff]

        cur.execute("SELECT code, qty FROM fin_position WHERE project_id = %s", (project_id,))
        positions = cur.fetchall()

        cur.execute(
            "SELECT count(*) AS n FROM fin_order WHERE project_id = %s "
            "AND status IN ('pending','accepted','partially_filled')", (project_id,))
        open_orders = int(cur.fetchone()["n"])

        cur.execute(
            "SELECT passed FROM fin_recon_log WHERE project_id = %s ORDER BY id DESC LIMIT 1",
            (project_id,))
        recon = cur.fetchone()

    return {
        "project": dict(project),
        "trade_date": trade_date,
        "valuation_latest": dict(valuation) if valuation else None,
        "valuation_series": [dict(r) for r in series],
        "trades": [dict(t) for t in trades],
        "positions": [dict(p) for p in positions],
        "open_order_count": open_orders,
        "recon_latest": dict(recon) if recon else None,
    }


def report_id_for(project_id: str, trade_date: str) -> str:
    """报告 id 从业务身份推导（**不含随机数**）：同项目同日重跑拿到同一行。"""
    digest = hashlib.sha256(f"{project_id}:{trade_date}".encode("utf-8")).hexdigest()[:24]
    return f"rpt_{digest}"


def code_set(ctx: dict) -> set[str]:
    codes = {str(p["code"]) for p in ctx.get("positions") or []}
    codes |= {str(t["code"]) for t in ctx.get("trades") or []}
    return codes


# ════════════════════════════════════════════════════════════════════════
# 六 · 落库（api 侧；账本本身仍只归 paper）
# ════════════════════════════════════════════════════════════════════════

# 随代码走的幂等 DDL（`CLAUDE.md` 铁律：db/migrations 里的 .sql 对已有部署不生效）。
#   · fin_report.artifact_ref —— M5 新增：指向报告 HTML 产物。
#   · hunter_artifacts.published_artifact 的 html 两列 —— 复用现仓产物表存 HTML
#     报告；老库这个表是「只有 markdown」的版本（本机实测确认），不补列 insert 会报错。
_DDL = """
ALTER TABLE fin_report ADD COLUMN IF NOT EXISTS artifact_ref TEXT
"""

_ARTIFACT_DDL = """
ALTER TABLE hunter_artifacts.published_artifact
  ADD COLUMN IF NOT EXISTS artifact_type TEXT NOT NULL DEFAULT 'markdown',
  ADD COLUMN IF NOT EXISTS content_html TEXT;
ALTER TABLE hunter_artifacts.published_artifact
  ALTER COLUMN content_md DROP NOT NULL;
"""


def ensure_columns(conn) -> None:
    """幂等补列（不 DROP、不改类型、不重命名）。产物表不存在时跳过它的补列。"""
    with conn.cursor() as cur:
        cur.execute(_DDL)
        cur.execute("SELECT to_regclass('hunter_artifacts.published_artifact')")
        if cur.fetchone()[0] is not None:
            cur.execute(_ARTIFACT_DDL)
    conn.commit()


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def persist(conn, ctx: dict, facts: list[dict], analysis: dict) -> dict:
    """把事实层 / 分析层落库，跑回读校验，按结果决定状态与是否出产物。

    校验通过 → `status='validated'` + HTML 产物 + 一条 in_app 发布回执；
    对不上 → `status='failed'`，**不出产物、不写发布回执**（`01方案 §9.2`）。
    """
    ensure_columns(conn)
    project = ctx["project"]
    project_id = project["project_id"]
    trade_date = ctx["trade_date"]
    valuation = ctx["valuation_latest"]
    report_id = report_id_for(project_id, trade_date)

    report = {
        "report_id": report_id,
        "project_id": project_id,
        "trade_date": trade_date,
        "valuation_as_of": valuation.get("as_of") if valuation else None,
        "analysis_text": analysis.get("analysis_text"),
        "self_review": analysis.get("self_review"),
        "llm_provider": analysis.get("llm_provider"),
        "llm_model": analysis.get("llm_model"),
        "prompt_version": PROMPT_VERSION,
    }
    check = validate_report(report, facts, codes=code_set(ctx))
    status = "validated" if check["ok"] else "failed"
    report["status"] = status

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            INSERT INTO fin_report
              (report_id, project_id, trade_date, valuation_as_of, analysis_text,
               self_review, llm_provider, llm_model, prompt_version, status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (report_id) DO UPDATE SET
              valuation_as_of = EXCLUDED.valuation_as_of,
              analysis_text   = EXCLUDED.analysis_text,
              self_review     = EXCLUDED.self_review,
              llm_provider    = EXCLUDED.llm_provider,
              llm_model       = EXCLUDED.llm_model,
              prompt_version  = EXCLUDED.prompt_version,
              status          = EXCLUDED.status
            RETURNING created_at
            """,
            (report_id, project_id, trade_date, report["valuation_as_of"],
             report["analysis_text"], psycopg2.extras.Json(report["self_review"]),
             report["llm_provider"], report["llm_model"], PROMPT_VERSION, status),
        )
        created_at = cur.fetchone()["created_at"]

        for f in facts:
            if not f.get("source_ref") or not f.get("computed_by"):
                raise ValueError(f"事实行 {f['metric_key']} 缺 source_ref / computed_by")
            cur.execute(
                """
                INSERT INTO fin_report_fact
                  (report_id, metric_key, value, unit, source_ref, computed_by)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (report_id, metric_key) DO UPDATE SET
                  value = EXCLUDED.value, unit = EXCLUDED.unit,
                  source_ref = EXCLUDED.source_ref, computed_by = EXCLUDED.computed_by
                """,
                (report_id, f["metric_key"], f.get("value"), f.get("unit"),
                 f["source_ref"], f["computed_by"]),
            )

        artifact_ref = None
        if status == "validated":
            report["created_at"] = created_at
            html = render_html(report, facts, project=project)
            artifact_ref = _store_artifact(cur, report, html, project)
            _store_receipt(cur, report_id, "SUCCESS", artifact_ref)
            cur.execute("UPDATE fin_report SET artifact_ref = %s WHERE report_id = %s",
                        (artifact_ref, report_id))
        else:
            # 校验不通过 → 不发布。**连产物都不生成**（无 HTML、无回执）。
            _store_receipt(cur, report_id, "FAILED", None)
    conn.commit()

    out = {
        "report_id": report_id, "status": status, "artifact_ref": artifact_ref,
        "check": check, "facts": len(facts),
        "used_fallback": bool(analysis.get("used_fallback")),
    }
    return {k: _jsonable(v) for k, v in out.items()}


def _store_artifact(cur, report: dict, html: str, project: dict) -> str:
    """把报告 HTML 存进 `hunter_artifacts.published_artifact`（复用现仓产物表）。

    `source_message_id = fin-report:{report_id}` → 同一份报告只出一件产物（重复生成时覆盖）。
    """
    short_id = "fin-" + report["report_id"].removeprefix("rpt_")[:12]
    source_message_id = f"fin-report:{report['report_id']}"
    cur.execute(
        """
        INSERT INTO hunter_artifacts.published_artifact
          (short_id, owner_user_id, session_id, source_message_id, title,
           artifact_type, content_md, content_html, metadata)
        VALUES (%s,%s,NULL,%s,%s,'html',NULL,%s,%s::jsonb)
        -- 同报告重跑覆盖同一件产物（short_id 由 report_id 推导，稳定）。
        ON CONFLICT (short_id)
        DO UPDATE SET owner_user_id = EXCLUDED.owner_user_id,
                      source_message_id = EXCLUDED.source_message_id,
                      title = EXCLUDED.title, content_html = EXCLUDED.content_html,
                      metadata = EXCLUDED.metadata, published_at = NOW()
        """,
        (short_id, project["user_id"], source_message_id,
         f"每日报告 · {report['trade_date']}", html,
         json.dumps({"kind": "fin_daily_report", "report_id": report["report_id"],
                     "trade_date": report["trade_date"]})),
    )
    return f"artifact:{short_id}"


def _store_receipt(cur, report_id: str, status: str, artifact_ref: Optional[str]) -> None:
    receipt_id = f"rcp_{hashlib.sha256(f'{report_id}:in_app'.encode()).hexdigest()[:24]}"
    cur.execute(
        """
        INSERT INTO fin_publish_receipt (receipt_id, report_id, channel, status, external_id, detail)
        VALUES (%s,%s,'in_app',%s,%s,%s)
        ON CONFLICT (receipt_id) DO UPDATE SET
          status = EXCLUDED.status, external_id = EXCLUDED.external_id,
          detail = EXCLUDED.detail, attempted_at = NOW()
        """,
        (receipt_id, report_id, status, artifact_ref,
         "站内产物" if status == "SUCCESS" else "回读校验未通过，未发布"),
    )


# ── 读（前端 / 校验脚本共用）────────────────────────────────────────────

def load_report(conn, report_id: str) -> Optional[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM fin_report WHERE report_id = %s", (report_id,))
        report = cur.fetchone()
        if not report:
            return None
        cur.execute("SELECT * FROM fin_report_fact WHERE report_id = %s ORDER BY metric_key",
                    (report_id,))
        facts = cur.fetchall()
        cur.execute(
            "SELECT receipt_id, channel, status, external_id, detail, attempted_at "
            "FROM fin_publish_receipt WHERE report_id = %s ORDER BY attempted_at DESC", (report_id,))
        receipts = cur.fetchall()
    return {"report": _d(report), "facts": [_d(f) for f in facts],
            "receipts": [_d(r) for r in receipts]}


def _d(row: dict) -> dict:
    return {k: _jsonable(v) for k, v in dict(row).items()}


def validate_stored(conn, report_id: str) -> dict:
    """M-32 · 可重复跑的校验：重读已落库的报告，逐数字回追事实表。

    **不信任落库时的结论** —— 用同一份事实重新跑一遍回读校验，并把每个数字
    对应到的 `metric_key` 列出来（这就是「可追溯到账本或指标代码」的凭据）。
    """
    loaded = load_report(conn, report_id)
    if not loaded:
        raise LookupError(f"报告不存在：{report_id}")
    facts = loaded["facts"]
    report = loaded["report"]

    trace: list[dict] = []
    violations: list[dict] = []
    inspected = 0
    sr = report.get("self_review") or {}
    fields = [("analysis_text", report.get("analysis_text") or "")]
    fields += [(f"self_review.{k}", sr.get(k) or "") for k in SELF_REVIEW_KEYS]
    for name, text in fields:
        for tok in extract_numbers(text):
            inspected += 1
            hit = _matches_fact(tok, facts)
            row = {"field": name, "token": tok.raw.strip(),
                   "metric_key": hit["metric_key"] if hit else None}
            trace.append(row)
            if not hit:
                violations.append({**row, "reason": "找不到对应的事实行"})
    for p in assert_single_caliber(facts):
        violations.append({"field": "caliber", "token": "-", "metric_key": None, "reason": p})
    return {
        "report_id": report_id, "status": report["status"],
        "numbers_inspected": inspected, "trace": trace,
        "violations": violations, "ok": not violations,
    }


# ════════════════════════════════════════════════════════════════════════
# 七 · 编排：一次报告生成
# ════════════════════════════════════════════════════════════════════════

async def analyze(facts: list[dict], ctx: dict) -> dict:
    """分析层入口。LLM 不可用 / 调用失败 / 输出不可解析 → **降级为规则文案**（如实标注）。"""
    try:
        return await analyze_with_llm(facts, ctx)
    except Exception as exc:  # noqa: BLE001 —— 降级要留痕，不能让一条报告把整轮打挂
        return fallback_analysis(facts, ctx, f"{exc.__class__.__name__}: {str(exc)[:160]}")


async def generate(project_id: str, trade_date: str, *, conn=None,
                   analyzer: Optional[Callable] = None) -> dict:
    """生成一份报告：读账本 → 算事实 → AI 写分析 → 回读校验 → 落库。

    `analyzer` 可注入（测试用假模型驱动「故意改坏一个数字」的场景）。
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        ctx = collect(conn, project_id, trade_date)

        # 无报告日：没有这一天的收盘估值 → **一行都不写**（不是编一份出来）。
        if ctx["valuation_latest"] is None:
            series = ctx.get("valuation_series") or []
            have = _day(series[-1]["as_of"]) if series else None
            return {
                "created": False, "status": "no_report", "report_id": None,
                "reason": (f"{trade_date} 没有收盘估值"
                           + (f"（该项目最近一次估值是 {have}）" if have else "（该项目还没有任何估值）")
                           + "：非交易日、时点未跑或行情缺失时按空态处理，不生成报告。"),
                "valuation_as_of": have,
            }

        facts = build_facts(ctx)
        if analyzer is None:
            analysis = await analyze(facts, ctx)
        else:
            analysis = analyzer(facts, ctx)
        return persist(conn, ctx, facts, analysis)
    finally:
        if own:
            conn.close()
