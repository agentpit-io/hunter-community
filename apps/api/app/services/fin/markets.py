"""三个市场的**状态**与**跨市场合计**（N5 · 前端市场切换器与总览分列的数据源）。

两件事：

1. **市场状态**（`market_status`）：按市场给「当天是不是交易日 + 现在处于哪个时段」。
   判定**只认 `fin_market_calendar`（`(market, trade_date)` 的那些行）**，
   **不搬 `gm/market_calendar.py:16-58` 那套判定**（它不含节假日，会把港股国庆当交易日 ——
   `11-…实施方案.md` §3.6 点名它「不可直接搬」）。它的**中文标签表**可以复用（本模块沿用同一批词）。
   日历缺行 → 状态 `unknown`（「未知 ≠ 交易日」，与 `paper/app/risk/session.py` 同一原则）。

2. **跨市场合计**（`combined_assets`）：逐市场本币总资产 + 折人民币合计。
   汇率**只在请求时现取**（`fin_data.fetch_fx`，新浪，带时刻），并带 `fx_source` / `fx_at`；
   **取不到就 `total_cny = None` + `reason`**，绝不用仓内那两个编出来的常量顶替。

本模块只读账本（`fin_valuation` / `fin_market_calendar` / `fin_market_rule`），不写任何一行。
"""

from __future__ import annotations

from datetime import datetime, time, timezone
from decimal import Decimal
from typing import Any, Optional
from zoneinfo import ZoneInfo

import psycopg2.extras

# 与 `report.py` / `paper ledger` 同一套口径（三处写死同一批常量，改要一起改）。
MARKETS = ("CN_A", "HK", "US")
MARKET_LABEL = {"CN_A": "A 股", "HK": "港股", "US": "美股"}
MARKET_CURRENCY = {"CN_A": "CNY", "HK": "HKD", "US": "USD"}
CURRENCY_SYMBOL = {"CNY": "¥", "HKD": "HK$", "USD": "$"}
MARKET_TZ = {"CN_A": "Asia/Shanghai", "HK": "Asia/Hong_Kong", "US": "America/New_York"}

# 中文标签沿用 `gm/market_calendar.state_label` 那批词（文案可复用，判定不可复用）。
STATE_LABEL = {
    "pre_market": "盘前", "open": "交易中", "after_hours": "盘后",
    "lunch": "午间休市", "closed": "休市", "unknown": "日历缺失",
}


def _minutes(hhmm: str) -> Optional[int]:
    try:
        hh, mm = str(hhmm).strip().split(":")
        return int(hh) * 60 + int(mm)
    except (ValueError, AttributeError):
        return None


def _state_from_sessions(sessions: list, local_now: datetime) -> str:
    """按市场当地时刻落在哪一段，给一个时段状态。时段两端都含（与风控一致）。"""
    windows = []
    for w in sessions or []:
        o, c = _minutes(w.get("open")), _minutes(w.get("close"))
        if o is not None and c is not None:
            windows.append((o, c))
    if not windows:
        return "unknown"
    cur = local_now.hour * 60 + local_now.minute
    windows.sort()
    if cur < windows[0][0]:
        return "pre_market"
    for i, (o, c) in enumerate(windows):
        if o <= cur <= c:
            return "open"
        # 在相邻两段之间 = 午间休市；最后一段之后 = 盘后。
        if cur < o:
            return "lunch" if i > 0 else "pre_market"
    return "after_hours"


def market_status(cur, *, at: Optional[datetime] = None,
                  trade_date: Optional[str] = None) -> list[dict]:
    """三个市场各自的状态（给前端市场切换器）。

    `trade_date` 指定就用那一天查日历（用于回看「A 股休市那天港美股在不在交易」）；
    不给则用「现在」在该市场的当地日期。`at` 指定「现在」的 UTC 时刻（默认真·现在，
    测试可注入固定时刻）。
    """
    at = at or datetime.now(timezone.utc)
    cur.execute("SELECT market, sessions FROM fin_market_rule")
    rules = {r["market"]: r["sessions"] for r in cur.fetchall()}

    out: list[dict] = []
    for m in MARKETS:
        tz = ZoneInfo(MARKET_TZ[m])
        local_now = at.astimezone(tz)
        day = trade_date or local_now.date().isoformat()
        cur.execute(
            """
            SELECT is_trading, calendar_source, sessions
              FROM fin_market_calendar WHERE market = %s AND trade_date = %s
            """, (m, day))
        row = cur.fetchone()
        sessions = (row or {}).get("sessions") or rules.get(m) or []
        if row is None:
            state, is_trading, source, note = "unknown", None, None, (
                f"{MARKET_LABEL[m]} {day} 没有交易日历行 —— 按「未知 ≠ 交易日」处理，"
                "该市场不可交易（先跑该市场的日历同步）")
        elif not row["is_trading"]:
            state, is_trading, source = "closed", False, row.get("calendar_source")
            note = f"{MARKET_LABEL[m]} {day} 非交易日（休市）"
        else:
            state = _state_from_sessions(sessions, local_now)
            is_trading, source = True, row.get("calendar_source")
            note = f"{MARKET_LABEL[m]} {day} 交易日 · 当前 {STATE_LABEL.get(state, state)}"
        out.append({
            "market": m, "label": MARKET_LABEL[m], "currency": MARKET_CURRENCY[m],
            "symbol": CURRENCY_SYMBOL[MARKET_CURRENCY[m]],
            "timezone": MARKET_TZ[m],
            "trade_date": day,
            "local_time": local_now.isoformat(),
            "is_trading_day": is_trading,        # None = 日历缺失（不是交易日，也不是「不是交易日」）
            "state": state, "state_label": STATE_LABEL.get(state, state),
            "sessions": sessions, "calendar_source": source, "note": note,
        })
    return out


# ── 市场规则事实（P3 · 界面按市场出参数）──────────────────────────────────
# `fin_market_rule` 的**展示用子集**。界面（向导「选市场」步、自动交易页的时点）
# 一律从这里读，**不许在 React 里写死**（红线：「不许拿 A 股规则顶替港美股」）。
#
# 只回事实列，不回 `source` 那一长段（它是给留档用的，界面不展示）；
# 中文化（涨跌停模式 → 中文、T+N → 中文）是**展示层**的事，不在这里做。
_RULE_COLS = (
    "points", "sellable_rule", "sellable_days", "lot_rule", "lot_fixed",
    "price_limit_mode", "market_order_supported", "fee_model_version",
)


def market_rules(cur) -> dict[str, dict]:
    """每个市场的规则事实（`fin_market_rule` 一行一条）。

    查不到该市场的行 → 该市场**不出现在结果里**（调用方据此显示「规则未知」），
    **不回落 A 股**、不编默认值。
    """
    cur.execute(
        "SELECT market, " + ", ".join(_RULE_COLS) + " FROM fin_market_rule")
    return {r["market"]: {k: r.get(k) for k in _RULE_COLS} for r in cur.fetchall()}


def project_market_accounts(cur, project_id: str) -> list[dict]:
    """项目的**每个子账户**一行（P3 · 「我的账户」页的「市场与子账户」卡片）。

    真值来自 `fin_project_market`（市场集合 + 各市场本金/币种/开户时刻），
    当前净值取自该市场**最新一行** `fin_valuation`（没有估值 → `None`，**不补 0**）。
    顺序恒为 `CN_A → HK → US`（与落库顺序同一口径）。
    """
    cur.execute(
        """
        SELECT pm.market, pm.currency, pm.initial_capital, pm.opened_at,
               v.total_assets, v.nav, v.as_of
          FROM fin_project_market pm
          LEFT JOIN LATERAL (
                 SELECT total_assets, nav, as_of
                   FROM fin_valuation
                  WHERE project_id = pm.project_id AND market = pm.market
                  ORDER BY as_of DESC LIMIT 1
               ) v ON true
         WHERE pm.project_id = %s
        """, (project_id,))
    out = []
    for r in cur.fetchall():
        out.append({
            "market": r["market"],
            "label": MARKET_LABEL.get(r["market"], r["market"]),
            "currency": r["currency"],
            "initial_capital": float(r["initial_capital"]) if r["initial_capital"] is not None else None,
            "opened_at": r["opened_at"].isoformat() if r.get("opened_at") else None,
            "nav": float(r["nav"]) if r.get("nav") is not None else None,
            "total_assets": float(r["total_assets"]) if r.get("total_assets") is not None else None,
            "as_of": r["as_of"].isoformat() if r.get("as_of") else None,
        })
    out.sort(key=lambda x: MARKETS.index(x["market"]) if x["market"] in MARKETS else 99)
    return out


def combined_assets(cur, project_id: str) -> dict:
    """跨市场合计：逐市场本币总资产（不折算）+ 折人民币合计（带汇率来源与时刻）。

    取不到汇率 → `total_cny=None` 且 `reason` 写明（对齐红线「指标算不出显示 —」）。
    """
    cur.execute(
        """
        SELECT DISTINCT ON (market) market, currency, total_assets, as_of
          FROM fin_valuation WHERE project_id = %s
         ORDER BY market, as_of DESC
        """, (project_id,))
    rows = [dict(r) for r in cur.fetchall()]

    by_market: list[dict] = []
    for r in rows:
        m = r["market"]
        by_market.append({
            "market": m, "label": MARKET_LABEL.get(m, m),
            "currency": r.get("currency") or MARKET_CURRENCY.get(m),
            "total_assets": float(r["total_assets"]) if r["total_assets"] is not None else None,
            "as_of": r["as_of"].isoformat() if r.get("as_of") else None,
        })
    by_market.sort(key=lambda x: MARKETS.index(x["market"]) if x["market"] in MARKETS else 99)

    from app.services import fin_data
    try:
        fx = fin_data.fetch_fx()
    except Exception:  # noqa: BLE001 —— 汇率拉不到不能挡住总览
        fx = {}

    reasons: list[str] = []
    total = Decimal(0)
    terms: list[dict] = []
    for b in by_market:
        if b["total_assets"] is None:
            reasons.append(f"{b['label']} 缺收盘估值")
            continue
        amount = Decimal(str(b["total_assets"]))
        cur_c = b["currency"]
        if cur_c == "CNY":
            total += amount
            terms.append({**b, "converted": float(amount), "fx": None})
        else:
            pair = "HKDCNY" if cur_c == "HKD" else "USDCNY" if cur_c == "USD" else None
            info = (fx or {}).get(pair) if pair else None
            if not info:
                reasons.append(f"缺 {cur_c}→CNY 汇率（合计不含{b['label']}）")
                continue
            conv = amount * Decimal(str(info["rate"]))
            total += conv
            terms.append({**b, "converted": float(conv), "fx": {
                "pair": pair, "rate": info["rate"], "source": info["source"], "at": info["at"]}})

    ok = not reasons
    fx_used = [t["fx"] for t in terms if t.get("fx")]
    return {
        "by_market": by_market,
        "terms": terms,
        "total_cny": float(total) if ok else None,
        "currency": "CNY",
        "fx_source": "; ".join(sorted({f["source"] for f in fx_used})) or None,
        "fx_at": "; ".join(sorted({f["at"] for f in fx_used})) or None,
        "reason": None if ok else "；".join(dict.fromkeys(reasons)),
    }
