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

from app.services.market_time import market_tz, tz_name
# 发布（L07）：**发布是独立一步**，渠道走适配器。`publish` 不 import 本模块（只在函数内
# 延迟 import），所以这里顶部 import 不会成环。
from app.services.fin import publish as publish_svc
SHANGHAI = market_tz("CN_A")
# SQL 里「按上海日期切」用的时区名 —— 同一来源（L02）。
_SH_TZ = tz_name("CN_A")

# 代码位置 + 算法版本。`computed_by` 写的就是它 —— 「这个数哪段代码算的」。
CODE_VERSION = "fin.report.build_facts@n5.1"
PROMPT_VERSION = "fin-report-zh@1.1"

# 三个自我总结栏目的稳定 key（`09 §4.8` fin_report.self_review）。
SELF_REVIEW_KEYS = ("did_well", "did_bad", "change_tomorrow")

# 一期只有实盘模拟一种口径（`01方案 §2.2`）。回测 / 前向模拟 / 影子**不在**这条曲线里。
RETURN_CALIBER = "live_paper"
# 任何事实行的 source_ref 命中这些词 = 把别的口径掺进来了 → 校验失败。
_FORBIDDEN_SOURCES = ("backtest", "pred_backtest", "shadow", "replay_sim", "前向模拟", "影子", "回测")

# ── 指标规格：key → (中文名, 单位) ─────────────────────────────────────────
# 渲染与提示词都从这里取标签，**不另抄一份**（`CLAUDE.md`「同一件事写在多处」）。
#
# 二期：金额类指标的 `unit` 不再是写死的 `"CNY"`（那把一个 A 股口径钉进了所有市场），
# 改成 `MONEY_UNIT` + **指标级 `currency`**（`11-…实施方案.md` §3.6 逐字要求）：
# 币种随事实行一起落库，格式化时按币种出符号（A 股 `¥`、港股 `HK$`、美股 `$`）。
MONEY_UNIT = "money"

# 币种 → 符号（报告 HTML 与事实表共用一份；前端 `money()` 走同一张表的 JS 副本）。
CURRENCY_SYMBOL: dict[str, str] = {"CNY": "¥", "HKD": "HK$", "USD": "$"}

METRIC_SPEC: dict[str, tuple[str, str]] = {
    "initial_capital":    ("初始本金", MONEY_UNIT),
    "total_assets":       ("总资产", MONEY_UNIT),
    "cash_available":     ("可用资金", MONEY_UNIT),
    "cash_frozen":        ("冻结资金", MONEY_UNIT),
    "market_value":       ("持仓市值", MONEY_UNIT),
    "nav":                ("净值", "倍"),
    "return_pct":         ("累计收益率", "%"),
    "daily_return_pct":   ("当日收益率", "%"),
    "max_drawdown":       ("最大回撤", "%"),
    "fee_total":          ("累计费用", MONEY_UNIT),
    "fee_today":          ("当日费用", MONEY_UNIT),
    "trade_count_today":  ("当日成交笔数", "笔"),
    "trade_count_total":  ("累计成交笔数", "笔"),
    "position_count":     ("持仓数", "只"),
    "open_order_count":   ("未成交挂单", "笔"),
    "recon_passed":       ("对账通过", ""),
    "data_quality_ok":    ("估值数据完整", ""),
    "days_traded":        ("已运行交易日", "日"),
    "return_caliber_count": ("收益口径数", "种"),
}

# 跨市场汇总的指标规格（**另起一张表**，不塞进 METRIC_SPEC —— 上面的键会被
# 逐市场报告逐条产出，汇总的键只在 `MULTI` 那份报告里出现）。
SUMMARY_METRIC_SPEC: dict[str, tuple[str, str]] = {
    "total_assets_cny":   ("跨市场合计（折人民币）", MONEY_UNIT),
    "fx_HKDCNY":          ("汇率 HKD→CNY", "汇率"),
    "fx_USDCNY":          ("汇率 USD→CNY", "汇率"),
}
# 逐市场总资产在汇总里的键前缀（`total_assets__CN_A` …）：值是**该市场本币**，不折算。
SUMMARY_MARKET_PREFIX = "total_assets__"

# 汇总报告的市场标识（不是三个市场之一，是它们之上的一份）。
SUMMARY_MARKET = "MULTI"
MARKET_LABEL = {"CN_A": "A 股", "HK": "港股", "US": "美股", SUMMARY_MARKET: "跨市场汇总"}


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


# 市场 → 本币（与 `paper` 的 `ledger.MARKET_CURRENCY` 同口径；报告是只读侧，
# 不 import paper 的模块，这里单独一份常量并在 `test_fin_report` 里对齐）。
MARKET_CURRENCY: dict[str, str] = {"CN_A": "CNY", "HK": "HKD", "US": "USD"}


def _currency_of(currency: Optional[str], market: Optional[str]) -> Optional[str]:
    """报告用的本币：显式给了就用，否则按市场推，再没有就 None（**不猜 CNY**）。"""
    if currency:
        return str(currency).upper()
    if market and market in MARKET_CURRENCY:
        return MARKET_CURRENCY[market]
    return None


def build_summary_facts(market_ctxs: list[dict], fx: Optional[dict]) -> list[dict]:
    """跨市场汇总的事实行（纯函数）。

    `market_ctxs` = 每个市场那份报告的 `ctx`（各自带 `market` / `currency` / 估值）；
    `fx` = `{pair: {"rate": float, "at": iso, "source": str}}` 或 `None`。

    **折算口径**：合计只在「跨市场合计」这一处出现，用请求时现取的汇率，
    并把 `fx_source` / `fx_at` 写进 `source_ref`（`11-…实施方案.md` §3.1 A 方案）。
    任一市场缺汇率或缺估值 → **合计为 `None`**（渲染成 `—`），并在 `source_ref` 写明原因 ——
    **绝不拿一个缺项当 0 去凑一个看起来完整的合计**。
    """
    facts: list[dict] = []

    def _label(key: str) -> str:
        if key in SUMMARY_METRIC_SPEC:
            return SUMMARY_METRIC_SPEC[key][0]
        if key.startswith(SUMMARY_MARKET_PREFIX):
            m = key[len(SUMMARY_MARKET_PREFIX):]
            return f"{MARKET_LABEL.get(m, m)}总资产（本币）"
        return key

    def _row(key: str, value, unit: str, currency: Optional[str], source_ref: str) -> dict:
        return {
            "metric_key": key, "label_cn": _label(key), "value": value,
            "unit": unit, "market": SUMMARY_MARKET, "currency": currency,
            "source_ref": source_ref, "computed_by": CODE_VERSION,
        }

    # ① 逐市场总资产：**本币原值、不折算**（账本里是什么就写什么）。
    totals: dict[str, tuple[Optional[Decimal], Optional[str]]] = {}
    for c in market_ctxs:
        m = c.get("market")
        cur = _currency_of(c.get("currency"), m)
        v = c.get("valuation_latest") or {}
        total = _money(v.get("total_assets"))
        totals[m] = (total, cur)
        facts.append(_row(
            f"{SUMMARY_MARKET_PREFIX}{m}", total, MONEY_UNIT, cur,
            f"fin_valuation:{m}:{_day(v.get('as_of')) or 'unknown'}"))

    # ② 汇率：可得出时给值（带来源与时刻），不可得时 value=None 并在 source_ref 写明原因。
    fx = fx or {}

    def _fx_row(pair: str) -> Optional[Decimal]:
        info = fx.get(pair)
        if not info or info.get("rate") is None:
            facts.append(_row(f"fx_{pair}", None, "汇率", None,
                              f"fx:unavailable·{pair}·拿不到带时刻的汇率，合计置 —"))
            return None
        rate = _ratio(info["rate"])
        facts.append(_row(f"fx_{pair}", rate, "汇率", None,
                          f"fx:{info.get('source') or 'unknown'}@{info.get('at') or 'unknown'}"))
        return rate

    hkd = _fx_row("HKDCNY")
    usd = _fx_row("USDCNY")

    # ③ 跨市场合计（折人民币）：**每个市场都要有本币总资产 + 该市场的汇率**才给数。
    reasons: list[str] = []
    combined: Optional[Decimal] = None
    terms: dict[str, Decimal] = {}
    for m, (total, cur) in totals.items():
        if total is None:
            reasons.append(f"{m} 缺估值")
            continue
        if cur == "CNY":
            terms[m] = total
        elif cur == "HKD":
            if hkd is None:
                reasons.append("缺 HKD→CNY 汇率")
            else:
                terms[m] = total * hkd
        elif cur == "USD":
            if usd is None:
                reasons.append("缺 USD→CNY 汇率")
            else:
                terms[m] = total * usd
        else:
            reasons.append(f"{m} 币种未知（{cur or '—'}）")
    if reasons:
        source_ref = "fx:unavailable·合计置 —·原因：" + "、".join(dict.fromkeys(reasons))
    else:
        combined = _money(sum(terms.values(), Decimal(0)))
        srcs = [f"fx:{fx.get(p, {}).get('source', '?')}@{fx.get(p, {}).get('at', '?')}"
                for p in ("HKDCNY", "USDCNY") if p in fx]
        source_ref = "合计=Σ(本币总资产×汇率) · " + " · ".join(srcs)
    facts.append(_row("total_assets_cny", combined, MONEY_UNIT, "CNY", source_ref))
    # 口径声明：汇总也只来自同一条实盘模拟账本（三个子账户），不掺回测 / 前向 / 影子。
    facts.append({
        "metric_key": "return_caliber_count", "label_cn": "收益口径数", "value": Decimal(1),
        "unit": "种", "market": SUMMARY_MARKET, "currency": None,
        "source_ref": f"const:caliber={RETURN_CALIBER}·01方案§2.2", "computed_by": CODE_VERSION,
    })
    return facts


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
    # 事实层的市场维度（`11-…实施方案.md` §3.6「事实层加 market 维度」）。
    # 本币由市场决定；锚本币的金额行把 `currency` 一起写进行里 —— 这样「报告里的钱是哪个币种」
    # 是**行数据**而不是渲染时靠猜（同一份报告可能被不同项目复用）。
    market = ctx.get("market")
    currency = _currency_of(ctx.get("currency"), market)

    def _row(key: str, value: Optional[Decimal], source_ref: str) -> dict:
        label, unit = METRIC_SPEC[key]
        return {
            "metric_key": key, "label_cn": label, "value": value, "unit": unit,
            "market": market, "currency": (currency if unit == MONEY_UNIT else None),
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

def _symbol(currency: Optional[str]) -> str:
    """币种 → 符号。**认不出的币种返回空串**，由调用方在别处写明币种 ——
    绝不默认成 `¥`（那会把港币金额说成人民币）。"""
    return CURRENCY_SYMBOL.get((currency or "").upper(), "")


def format_value(value: Optional[Decimal], unit: str, currency: Optional[str] = None) -> str:
    """渲染一个事实值。`None` → `—`（**算不出就写 —，绝不编数**）。

    金额类（`unit == "money"`）按 `currency` 出符号：A 股 `¥`、港股 `HK$`、美股 `$`；
    币种缺失时不加符号（**不猜**），并保留原始数字。
    """
    if value is None:
        return "—"
    if unit == MONEY_UNIT:
        return f"{_symbol(currency)}{value:,.2f}"
    if unit in CURRENCY_SYMBOL:          # 老口径兼容：unit 直接是币种
        return f"{CURRENCY_SYMBOL[unit]}{value:,.2f}"
    if unit == "%":
        return f"{value:+.2f}%"
    if unit == "倍":
        return f"{value:.4f}"
    if value == value.to_integral_value():
        return f"{int(value)}"
    return f"{value}"


def fact_currency(f: dict) -> Optional[str]:
    """一行事实的币种（金额行才有）。"""
    return f.get("currency") if (f.get("unit") == MONEY_UNIT) else None


def caliber_note_of(market: Optional[str]) -> str:
    """报告口径说明（**不能只写「实盘模拟」**）。

    本期每个市场各一份报告、外加一份跨市场汇总，规则各不相同，所以口径必须写清
    「这是模拟盘」+「本报告覆盖哪个市场/是不是汇总」。
    """
    base = ("收益口径：实盘模拟（1 种）。本报告的收益只来自这一条模拟账本；"
            "回测 / 前向模拟 / 影子运行不与之拼成同一条曲线（01方案 §2.2）。")
    common = ("本报告是模拟盘 · 不接实盘（PAPER_MODE 恒为 PAPER，不配置任何交易凭证）；"
              "三个市场的交易日历、交易时段、费用、价格带与 T+1 规则各不相同，"
              "金额一律以该市场本币记账，账本内不做任何折算。")
    if market == SUMMARY_MARKET:
        return (base + "本报告是跨市场汇总：逐市场金额是各自本币原值、未折算；"
                "「跨市场合计（折人民币）」按请求时现取的汇率折算，"
                "汇率来源与取值时刻见事实表 fx_HKDCNY / fx_USDCNY 两行的 source_ref，"
                "任一市场缺汇率或缺估值时该合计显示 — 并在 source_ref 写明原因。" + common)
    label = MARKET_LABEL.get(market or "", market or "—")
    return (base + f"本报告只覆盖 {label} 一个市场（{MARKET_CURRENCY.get(market or '', '—')} 本币）。"
            + common)


def render_html(report: dict, facts: list[dict], *, project: dict) -> str:
    """表达层：把事实与文字渲染成 HTML 产物。**只改措辞与版面，不改数字。**"""
    fm = facts_map(facts)
    trade_date = report["trade_date"]
    market = report.get("market")
    market_label = MARKET_LABEL.get(market or "", market or "—")
    caliber_note = caliber_note_of(market)

    def g(key: str) -> str:
        f = fm.get(key)
        if not f:
            return "—"
        return format_value(f.get("value"), f.get("unit") or "", fact_currency(f))

    if market == SUMMARY_MARKET:
        # 汇总没有净值 / 回撤这类「单账户」指标 —— 列出来全是 `—` 只会让人以为坏了。
        # 换成**按市场分列的本币总资产 + 折人民币合计**（合计带汇率来源与时刻）。
        kpi = []
        for key in sorted(fm):
            if key.startswith(SUMMARY_MARKET_PREFIX):
                f = fm[key]
                kpi.append((f.get("label_cn") or key,
                            format_value(f.get("value"), f.get("unit") or "", fact_currency(f))))
        kpi.append(("跨市场合计（折人民币）", g("total_assets_cny")))
        kpi.append(("HKD→CNY", g("fx_HKDCNY")))
        kpi.append(("USD→CNY", g("fx_USDCNY")))
    else:
        kpi = [
            ("净值", g("nav")), ("累计收益率", g("return_pct")), ("当日收益率", g("daily_return_pct")),
            ("最大回撤", g("max_drawdown")), ("总资产", g("total_assets")), ("持仓市值", g("market_value")),
            ("可用资金", g("cash_available")), ("累计费用", g("fee_total")),
        ]
    kpi_html = "".join(
        f"<div class='kpi'><div class='k'>{k}</div><div class='v'>{v}</div></div>" for k, v in kpi)

    if market == SUMMARY_MARKET:
        section2_title = "跨市场说明"
        section2_body = (
            "<p>本报告不产出单账户指标（净值 / 回撤 / 成交笔数在各市场报告里）。"
            "跨市场合计按上表 fx_* 两行的汇率与时刻折算；<b>取不到汇率时合计显示 —</b>，"
            "原因写在那一行的 source_ref 里。</p>")
    else:
        section2_title = "今日操作与费用"
        section2_body = (
            f"<p>当日成交 <b>{g('trade_count_today')}</b> 笔，累计成交 <b>{g('trade_count_total')}</b> 笔；"
            f"当日费用 <b>{g('fee_today')}</b>，累计费用 <b>{g('fee_total')}</b>。"
            f"持仓 <b>{g('position_count')}</b> 只，未成交挂单 <b>{g('open_order_count')}</b> 笔。</p>"
            f"<h3>账本与数据质量</h3>"
            f"<p>对账通过：<b>{g('recon_passed')}</b>（1 = 通过）；"
            f"估值数据完整：<b>{g('data_quality_ok')}</b>（1 = 完整）。"
            f"已运行交易日 <b>{g('days_traded')}</b> 天。</p>")

    sr = report.get("self_review") or {}
    review_html = "".join(
        f"<div class='rev'><h4>{title}</h4><p>{_esc(sr.get(key) or '—')}</p></div>"
        for key, title in (("did_well", "做对了什么"), ("did_bad", "没做好什么"),
                           ("change_tomorrow", "明天怎么改")))

    facts_rows = "".join(
        "<tr>"
        f"<td>{_esc(f['metric_key'])}</td>"
        f"<td class='n'>{format_value(f.get('value'), f.get('unit') or '', fact_currency(f))}</td>"
        f"<td>{_esc(f.get('unit') or '—')}</td>"
        f"<td>{_esc(f.get('market') or '—')}</td>"
        f"<td>{_esc(f.get('currency') or '—')}</td>"
        f"<td class='ref'>{_esc(f['source_ref'])}</td>"
        f"<td class='ref'>{_esc(f['computed_by'])}</td>"
        "</tr>"
        for f in sorted(facts, key=lambda x: x["metric_key"]))

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>每日报告 · {_esc(trade_date)} · {_esc(market_label)}</title>
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
<h1>每日报告 · {_esc(trade_date)} · {_esc(market_label)}</h1>
<p>项目 <code>{_esc(project['project_id'])}</code> · 档位 {_esc(str(project.get('tier')))} ·
市场 <b>{_esc(market_label)}</b>（{_esc(report.get('currency') or '—')}） ·
估值时点 {_esc(str(report.get('valuation_as_of')))} · 状态 <b>{_esc(report.get('status'))}</b></p>

<h3>收益与净值</h3>
<div>{kpi_html}</div>
<p class="caliber">{_esc(caliber_note)}</p>

<h3>{section2_title}</h3>
{section2_body}

<h3>今天的自我总结</h3>
{review_html}

<h3>分析</h3>
<p>{_esc(report.get('analysis_text') or '—').replace(chr(10), '<br>')}</p>

<h3>事实表（报告里每个数字的来源）</h3>
<table><thead><tr><th>metric_key</th><th>值</th><th>单位</th><th>市场</th><th>币种</th><th>source_ref</th><th>computed_by</th></tr></thead>
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
        if not f:
            return "—"
        return format_value(f.get("value"), f.get("unit") or "", fact_currency(f))

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


def collect(conn, project_id: str, trade_date: str, market: Optional[str] = None) -> dict:
    """读一个项目在 `trade_date` 这天、**某个市场子账户**的全部账本读数（**只读**）。

    报告的事实层**只认账本**（`fin_*`），不读回测 / 前向模拟 / 影子的任何表。

    `market` 给定就只读该市场子账户的行（二期「分市场独立账本」），
    不给则读这个项目的全部行（一期单市场语义，行为不变）。
    """
    mfilter = " AND market = %s" if market else ""
    margs = (market,) if market else ()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM fin_project WHERE project_id = %s", (project_id,))
        project = cur.fetchone()
        if not project:
            raise LookupError(f"项目不存在：{project_id}")

        # ⚠️ 这里必须**把 build_facts 用到的每一列都选出来**。M6 发现：原来只选了
        # `as_of/nav/total_assets/quality/missing_flag`，而 `build_facts` 还要读
        # `cash_available` / `cash_frozen` / `market_value` —— 于是报告里的「可用资金」
        # 「冻结资金」「持仓市值」三项**恒为 `—`**（`valuation.get()` 拿到 None，
        # 按「算不出就空」写成 None，不报错、不告警）。报告页与总览页同屏对照时一眼可见。
        cur.execute(
            "SELECT as_of, nav, total_assets, cash_available, cash_frozen, market_value, "
            "quality, missing_flag, market, currency FROM fin_valuation "
            f"WHERE project_id = %s{mfilter} ORDER BY as_of ASC", (project_id, *margs))
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
            "SELECT trade_id, code, side, qty, price, amount, total_fee, traded_at, market, currency "
            f"FROM fin_trade WHERE project_id = %s{mfilter} ORDER BY traded_at ASC",
            (project_id, *margs))
        trades = [dict(t) for t in cur.fetchall()]
        if cutoff is not None:
            trades = [t for t in trades if t["traded_at"] <= cutoff]

        cur.execute(
            f"SELECT code, qty FROM fin_position WHERE project_id = %s{mfilter}",
            (project_id, *margs))
        positions = cur.fetchall()

        cur.execute(
            f"SELECT count(*) AS n FROM fin_order WHERE project_id = %s{mfilter} "
            "AND status IN ('pending','accepted','partially_filled')", (project_id, *margs))
        open_orders = int(cur.fetchone()["n"])

        cur.execute(
            "SELECT passed FROM fin_recon_log WHERE project_id = %s ORDER BY id DESC LIMIT 1",
            (project_id,))
        recon = cur.fetchone()

    return {
        "project": dict(project),
        "trade_date": trade_date,
        # 市场与币种：显式取估值行上的值（权威在账本），没有估值就按市场常量推（仍不猜 CNY）。
        "market": market,
        "currency": _currency_of((valuation or {}).get("currency"), market),
        "valuation_latest": dict(valuation) if valuation else None,
        "valuation_series": [dict(r) for r in series],
        "trades": [dict(t) for t in trades],
        "positions": [dict(p) for p in positions],
        "open_order_count": open_orders,
        "recon_latest": dict(recon) if recon else None,
    }


def markets_with_valuation(conn, project_id: str, trade_date: str) -> list[str]:
    """这一天有哪些市场子账户有收盘估值 —— 决定「要出哪几份报告」。

    **按估值行判定，不按 `fin_project.market_scope` 猜**：账本里真有那一行才算数。
    返回值按固定顺序（CN_A → HK → US）排，让报告生成顺序稳定可复现。
    """
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT DISTINCT market FROM fin_valuation "
            f"WHERE project_id = %s AND (as_of AT TIME ZONE '{_SH_TZ}')::date = %s",
            (project_id, trade_date))
        got = {str(r[0]) for r in cur.fetchall() if r[0]}
    return [m for m in ("CN_A", "HK", "US") if m in got]


def report_id_for(project_id: str, trade_date: str, market: Optional[str] = None) -> str:
    """报告 id 从业务身份推导（**不含随机数**）：同项目同日同市场重跑拿到同一行。

    `market=None` 保持一期公式（旧 A 股报告 id 稳定可读回）；给市场时把市场并进键，
    这样「每个市场一份 + 一份 MULTI 汇总」互不覆盖（`fin_report` 的唯一键已放宽为
    `(project_id, market, trade_date)`，见迁移 `0035`）。
    """
    key = f"{project_id}:{trade_date}" if not market else f"{project_id}:{trade_date}:{market}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
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
#   · fin_report.market / fin_report_fact.market / .currency —— N5 新增：市场维度
#     （「每个市场一份 + 一份 MULTI 汇总」需要唯一键带上市场）。
#   · hunter_artifacts.published_artifact 的 html 两列 —— 复用现仓产物表存 HTML
#     报告；老库这个表是「只有 markdown」的版本（本机实测确认），不补列 insert 会报错。
_DDL = """
ALTER TABLE fin_report     ADD COLUMN IF NOT EXISTS market   TEXT NOT NULL DEFAULT 'CN_A';
ALTER TABLE fin_report_fact ADD COLUMN IF NOT EXISTS market   TEXT;
ALTER TABLE fin_report_fact ADD COLUMN IF NOT EXISTS currency TEXT;
"""

# 唯一键从 `(project_id, trade_date)` 放宽成 `(project_id, market, trade_date)`：
# 三个市场 + 一份汇总要能在同一天共存。用 DO 块按当前形状判，**幂等**。
_DDL_UNIQUE = """
DO $$
DECLARE
  con TEXT;
BEGIN
  -- 先把一期那条两列唯一键找出来（名字在不同环境里可能是自动生成的）。
  SELECT c.conname INTO con
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
   WHERE t.relname = 'fin_report' AND c.contype = 'u'
     AND (SELECT array_agg(a.attname::text ORDER BY a.attname)
            FROM unnest(c.conkey) k JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k)
         = ARRAY['project_id','trade_date'];
  IF con IS NOT NULL THEN
    EXECUTE format('ALTER TABLE fin_report DROP CONSTRAINT %I', con);
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid
     WHERE t.relname = 'fin_report' AND c.contype = 'u'
       AND (SELECT array_agg(a.attname::text ORDER BY a.attname)
              FROM unnest(c.conkey) k JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k)
           = ARRAY['market','project_id','trade_date']
  ) THEN
    ALTER TABLE fin_report ADD CONSTRAINT fin_report_project_market_date_key
      UNIQUE (project_id, market, trade_date);
  END IF;
END $$;
"""

_ARTIFACT_DDL = """
ALTER TABLE hunter_artifacts.published_artifact
  ADD COLUMN IF NOT EXISTS artifact_type TEXT NOT NULL DEFAULT 'markdown',
  ADD COLUMN IF NOT EXISTS content_html TEXT;
ALTER TABLE hunter_artifacts.published_artifact
  ALTER COLUMN content_md DROP NOT NULL;
"""


def ensure_columns(conn) -> None:
    """幂等补列 + 放宽唯一键（不 DROP 数据、不改类型、不重命名）。产物表不存在时跳过它的补列。"""
    with conn.cursor() as cur:
        cur.execute(_DDL)
        cur.execute(_DDL_UNIQUE)
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
    # 市场缺省落到 CN_A（一期单市场语义）：报告的市场维度必须有值，不能为 NULL。
    market = ctx.get("market") or "CN_A"
    report_id = report_id_for(project_id, trade_date, market)

    report = {
        "report_id": report_id,
        "project_id": project_id,
        "trade_date": trade_date,
        "market": market,
        "currency": _currency_of(ctx.get("currency"), market),
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
        # 兼容一期遗留行：老库里的报告 id 不含市场段（`(project_id, trade_date)` 唯一），
        # 但迁移 `0035` 把它们的 `market` 回填成了 `CN_A` —— 于是「业务键」相同、`report_id`
        # 不同，直接 insert 会撞 `fin_report_project_market_date_key`。
        # 先按业务键找回那一行，**沿用它的 report_id** 就地更新（不制造第二份）。
        cur.execute(
            "SELECT report_id FROM fin_report "
            "WHERE project_id = %s AND market = %s AND trade_date = %s",
            (project_id, market, trade_date))
        existing = cur.fetchone()
        if existing:
            report_id = existing["report_id"]
            report["report_id"] = report_id

        cur.execute(
            """
            INSERT INTO fin_report
              (report_id, project_id, trade_date, market, valuation_as_of, analysis_text,
               self_review, llm_provider, llm_model, prompt_version, status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (report_id) DO UPDATE SET
              market          = EXCLUDED.market,
              valuation_as_of = EXCLUDED.valuation_as_of,
              analysis_text   = EXCLUDED.analysis_text,
              self_review     = EXCLUDED.self_review,
              llm_provider    = EXCLUDED.llm_provider,
              llm_model       = EXCLUDED.llm_model,
              prompt_version  = EXCLUDED.prompt_version,
              status          = EXCLUDED.status
            RETURNING created_at
            """,
            (report_id, project_id, trade_date, market, report["valuation_as_of"],
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
                  (report_id, metric_key, value, unit, market, currency, source_ref, computed_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (report_id, metric_key) DO UPDATE SET
                  value = EXCLUDED.value, unit = EXCLUDED.unit,
                  market = EXCLUDED.market, currency = EXCLUDED.currency,
                  source_ref = EXCLUDED.source_ref, computed_by = EXCLUDED.computed_by
                """,
                (report_id, f["metric_key"], f.get("value"), f.get("unit"),
                 f.get("market"), f.get("currency"), f["source_ref"], f["computed_by"]),
            )

        artifact_ref = None
        if status == "validated":
            report["created_at"] = created_at
            html = render_html(report, facts, project=project)
            # 发布走**适配器**（L07）：站内渠道的行为与改造前逐字节一致 ——
            # 适配器落产物，`publish.write_receipt` 写 `channel='in_app'` 的回执。
            res = publish_svc.ADAPTERS["in_app"].submit(
                report, None, html=html, ctx={"cur": cur, "project": project})
            publish_svc.write_receipt(cur, report_id, "in_app", res)
            if res.status == publish_svc.SUCCESS:
                artifact_ref = res.external_id
                cur.execute("UPDATE fin_report SET artifact_ref = %s WHERE report_id = %s",
                            (artifact_ref, report_id))
        else:
            # 校验不通过 → 不发布。**连产物都不生成**（无 HTML、无回执的 SUCCESS）。
            publish_svc.write_receipt(
                cur, report_id, "in_app",
                publish_svc.PublishResult(publish_svc.FAILED, None, "回读校验未通过，未发布"))
    conn.commit()

    out = {
        "report_id": report_id, "status": status, "artifact_ref": artifact_ref,
        "market": market, "check": check, "facts": len(facts),
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
         f"每日报告 · {report['trade_date']} · {MARKET_LABEL.get(report.get('market') or '', report.get('market') or '—')}",
         html,
         json.dumps({"kind": "fin_daily_report", "report_id": report["report_id"],
                     "trade_date": report["trade_date"],
                     "market": report.get("market")})),
    )
    return f"artifact:{short_id}"


def _store_receipt(cur, report_id: str, status: str, artifact_ref: Optional[str]) -> None:
    """写一行 `in_app` 回执。

    L07 起**发布走适配器**，回执的落库 SQL 统一在 `publish.write_receipt`（只有一份）；
    本函数保留为兼容入口，行为与改造前一致（`in_app` / 状态 / 产物引用 / 说明文案都不变）。
    """
    detail = "站内产物" if status == "SUCCESS" else "回读校验未通过，未发布"
    publish_svc.write_receipt(cur, report_id, "in_app",
                              publish_svc.PublishResult(status, artifact_ref, detail))


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


async def generate(project_id: str, trade_date: str, *, market: Optional[str] = None,
                   conn=None, analyzer: Optional[Callable] = None) -> dict:
    """生成报告：读账本 → 算事实 → AI 写分析 → 回读校验 → 落库。

    二期口径（`11-…实施方案.md` §3.6）：
    - `market` 给定 → **只出该市场一份**（用于单独重跑某个市场）；
    - `market` 不给 → 出**每个有收盘估值的市场各一份 + 一份跨市场汇总（MULTI）**。
      顶层返回体与一期形状兼容（`status` / `report_id` / `artifact_ref` / `check` …），
      取**第一个市场**那份作主，另附 `reports`（全部）与 `summary`（汇总那份）。

    `analyzer` 可注入（测试用假模型驱动「故意改坏一个数字」的场景）。
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        if market is not None:
            return await _generate_one(conn, project_id, trade_date, market, analyzer)

        markets = markets_with_valuation(conn, project_id, trade_date)
        if not markets:
            # 无报告日：没有这一天的收盘估值 → **一行都不写**（不是编一份出来）。
            ctx0 = collect(conn, project_id, trade_date)
            series = ctx0.get("valuation_series") or []
            have = _day(series[-1]["as_of"]) if series else None
            return {
                "created": False, "status": "no_report", "report_id": None,
                "reason": (f"{trade_date} 没有收盘估值"
                           + (f"（该项目最近一次估值是 {have}）" if have else "（该项目还没有任何估值）")
                           + "：非交易日、时点未跑或行情缺失时按空态处理，不生成报告。"),
                "valuation_as_of": have,
            }

        reports = [await _generate_one(conn, project_id, trade_date, m, analyzer)
                   for m in markets]
        summary = await _generate_summary(conn, project_id, trade_date, markets, analyzer)
        primary = reports[0]
        return {**primary, "reports": reports, "summary": summary}
    finally:
        if own:
            conn.close()


async def _generate_one(conn, project_id: str, trade_date: str, market: str,
                        analyzer: Optional[Callable]) -> dict:
    """一个市场的报告。缺该市场当日估值 → 返回该市场的空态（不写别人的数）。"""
    ctx = collect(conn, project_id, trade_date, market=market)
    if ctx["valuation_latest"] is None:
        series = ctx.get("valuation_series") or []
        have = _day(series[-1]["as_of"]) if series else None
        return {
            "created": False, "status": "no_report", "report_id": None, "market": market,
            "reason": (f"{market} 在 {trade_date} 没有收盘估值"
                       + (f"（该项目该市场最近一次估值是 {have}）" if have else "（该市场还没有任何估值）")
                       + "：非交易日、时点未跑或行情缺失时按空态处理，不生成报告。"),
            "valuation_as_of": have,
        }
    facts = build_facts(ctx)
    analysis = analyzer(facts, ctx) if analyzer is not None else await analyze(facts, ctx)
    return persist(conn, ctx, facts, analysis)


async def _generate_summary(conn, project_id: str, trade_date: str, markets: list[str],
                            analyzer: Optional[Callable]) -> dict:
    """跨市场汇总：逐市场本币总资产（不折算）+ 折人民币合计（带汇率来源与时刻）。"""
    ctxs = [collect(conn, project_id, trade_date, market=m) for m in markets]
    ctxs = [c for c in ctxs if c["valuation_latest"] is not None]
    if not ctxs:
        return {"created": False, "status": "no_report", "report_id": None, "market": SUMMARY_MARKET,
                "reason": "没有任何市场在当天有收盘估值，汇总不出。"}
    fx = _load_fx()
    facts = build_summary_facts(ctxs, fx)
    ctx = {
        "project": ctxs[0]["project"],
        "trade_date": trade_date,
        "market": SUMMARY_MARKET,
        "currency": None,
        "valuation_latest": ctxs[0]["valuation_latest"],
        "valuation_series": [],
        "trades": [t for c in ctxs for t in c.get("trades") or []],
        "positions": [p for c in ctxs for p in c.get("positions") or []],
        "open_order_count": sum(int(c.get("open_order_count") or 0) for c in ctxs),
        "recon_latest": ctxs[0].get("recon_latest"),
    }
    analysis = analyzer(facts, ctx) if analyzer is not None else await analyze(facts, ctx)
    return persist(conn, ctx, facts, analysis)


def _load_fx() -> Optional[dict]:
    """现取汇率。**取不到返回 `None`**（`build_summary_facts` 会把合计写成 `—`）。"""
    try:
        from app.services import fin_data
        fx = fin_data.fetch_fx()
    except Exception as exc:  # noqa: BLE001 —— 汇率拿不到不能挡住报告生成
        logger.warning("[fin.report] 汇率拉取异常：{}", exc)
        return None
    return fx or None
