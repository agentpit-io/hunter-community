"""页面级读模型装配（M6）—— 把 `dashboard.py` 的裸查询拼成前端直接用的一份 JSON。

**这里不做计算**：数字全部来自 `dashboard.py` 从账本表读出来的行。本模块只做
「取哪几块、怎么命名、缺了怎么标」，不做四则运算（唯一的例外是百分比的换算，
那也是展示格式）。一旦在这里开始「估算」，页面上的数就不是账本上的数了。

**共同约定**：
- 任何一块数据缺失 → 对应字段 `None` 或 `[]`，**不补默认值**；并带 `notes` 说明为什么空。
- 每个数字旁尽量带 `source`（哪张表哪一行），页面可以据此显示「凭证」。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from app.services.fin import dashboard as dash
from app.services.fin import markets as markets_svc
from app.services.fin import schedule
from app.services.fin.control import active_strategy
from app.services.fin import risk

# A 股硬约束六条（页面「③ A 股规则」）。文案是产品语言，**数值来自实测**：
# 费率读 `fin_fee_model` 的最新一行（paper 风控用的就是它），涨跌停读 `fin_instrument`。
# 口径来源：`05 §3.2` M-11 逐字（交易时段 / T+1 / 整手 / 涨跌停 / 费用 / 资金持仓校验）。
CONSTRAINTS = (
    {"key": "session", "title": "交易时段",
     "text": "09:30–11:30、13:00–15:00 连续竞价；09:15–09:25 与 14:57–15:00 集合竞价。时段之外只能挂着，不能成交。"},
    {"key": "t1", "title": "T+1",
     "text": "当天买入的股票，次一交易日起才能卖出。当天买、当天卖的「做 T」在本版本里做不到，账本会直接拒绝。"},
    {"key": "lot", "title": "整手买入",
     "text": "买入必须是 100 股整数倍；卖出可以出现不足 100 股的零股（通常来自送转股）。"},
    {"key": "limit", "title": "涨跌停",
     "text": "主板 ±10%、创业板与科创板 ±20%、ST 股 ±5%。封涨停买不进、封跌停卖不出，账本按「无法成交」处理。"},
    {"key": "fee", "title": "费用",
     "text": "佣金 {commission}（每笔最低 {commission_min} 元）、卖出印花税 {stamp}、过户费 {transfer}。费用逐笔计入成本，不进「凭空收益」。"},
    {"key": "price", "title": "价格基准",
     "text": "成交价一律取委托到达时刻的行情快照价，不取当日均价、不取收盘价、不做「事后择优」。"},
)

# 港股 / 美股的硬约束（N5）。**逐条如实写「做没做校验」**（红线 §六.2：某市场某条
# 规则没做校验必须写明「未做」，不许静默跳过）。数值（时段 / 费率）来自
# `fin_market_rule` 与 `fin_fee_model`（按市场那一行），**不写死在这里**。
CONSTRAINTS_HK = (
    {"key": "session", "title": "交易时段",
     "text": "09:30–12:00、13:00–16:00 连续交易，另含 16:00–16:10 收市竞价。时段之外只能挂着，不能成交。"},
    {"key": "t1", "title": "当日回转（T+0）",
     "text": "港股当日买入当日即可卖出（same_day），没有 A 股的 T+1 限制。"},
    {"key": "lot", "title": "按标的每手股数",
     "text": "买入必须是该标的每手股数的整数倍（1 / 100 / 500 / 1000 / 2000 不等，来源：港交所官方 ListOfSecurities 导出）。每手股数缺失的标的直接拒绝，不猜。"},
    {"key": "limit", "title": "价格带校验：未做",
     "text": "港股无涨跌停（另有市调机制 VCM）。本版本对港股**不做价格带校验**，回执与报告里都标了「未做」—— 不是假装校验过。"},
    {"key": "fee", "title": "费用",
     "text": "佣金、印花税（双边）、交易费 / 交易征费 / 结算费。逐笔计入成本，费率取自 fin_fee_model 的港股行（标了来源与生效日）。"},
    {"key": "price", "title": "价格基准",
     "text": "成交价取委托到达时刻的行情快照价（港股无盘口，快照标 quote_quality=no_book）。不取当日均价、不取收盘价。"},
)
CONSTRAINTS_US = (
    {"key": "session", "title": "交易时段",
     "text": "09:30–16:00（美东时间，自动跟随夏令时）。时段之外只能挂着，不能成交。"},
    {"key": "t1", "title": "当日回转（T+0）",
     "text": "美股当日买入当日即可卖出（same_day），没有 A 股的 T+1 限制。"},
    {"key": "lot", "title": "1 股起",
     "text": "美股每手为 1 股（这是事实，不是近似）。"},
    {"key": "limit", "title": "价格带校验：未做",
     "text": "美股无涨跌停（另有 LULD 熔断）。本版本对美股**不做价格带校验**，回执与报告里都标了「未做」。"},
    {"key": "fee", "title": "费用",
     "text": "佣金、SEC 规费 / 交易活动费（卖出收）。费率取自 fin_fee_model 的美股行（标了来源与生效日）。"},
    {"key": "price", "title": "价格基准",
     "text": "成交价取委托到达时刻的行情快照价（美股无盘口，快照标 quote_quality=no_book）。不取当日均价、不取收盘价。"},
)
CONSTRAINTS_BY_MARKET = {"CN_A": CONSTRAINTS, "HK": CONSTRAINTS_HK, "US": CONSTRAINTS_US}


def _rate_text(value) -> str:
    """费率说人话：能写成「千分之」的就写千分之，否则写「万分之」。

    这不是换算口径，是同一句话的两种写法（0.0005 = 千分之 0.5 = 万分之 5）——
    取哪个只为了让用户一眼看懂量级，与 `ref/原型/02-我的账户.html` 的文案一致。
    """
    if value is None:
        return "—"
    d = Decimal(str(value))
    if d >= Decimal("0.0005"):
        return f"千分之 {d * 1000:.4g}"
    return f"万分之 {d * 10000:.4g}"


def _fee_model(cur, market: str = "CN_A") -> Optional[dict]:
    # 按市场取该市场的费率行（二期起 fin_fee_model 带 market 维；老库没这列时回落最新一行）。
    cur.execute(
        """
        SELECT version, market, commission_pct, commission_min, stamp_tax_pct, transfer_fee_pct
          FROM fin_fee_model WHERE market = %s ORDER BY version DESC LIMIT 1
        """, (market,))
    row = cur.fetchone()
    if row is None:
        cur.execute(
            """
            SELECT version, commission_pct, commission_min, stamp_tax_pct, transfer_fee_pct
              FROM fin_fee_model ORDER BY version DESC LIMIT 1
            """)
        row = cur.fetchone()
    return dash._d(row)


def constraints(cur, market: str = "CN_A") -> list[dict]:
    """六条硬约束 —— 文案是产品语言，**费率数值取库里那一行**（不写死在文案里）。

    `market` 决定用哪一组文案（A 股 / 港股 / 美股各一组，港美股如实标注「未做」的条目）。
    """
    spec = CONSTRAINTS_BY_MARKET.get(market, CONSTRAINTS)
    fm = _fee_model(cur, market) if market == "CN_A" else None
    out = []
    for item in spec:
        text = item["text"]
        if item["key"] == "fee" and fm:
            text = text.format(
                commission=_rate_text(fm["commission_pct"]),
                commission_min=f"{Decimal(str(fm['commission_min'])):g}",
                stamp=_rate_text(fm["stamp_tax_pct"]) if fm["stamp_tax_pct"] else "—",
                transfer=_rate_text(fm["transfer_fee_pct"]),
            )
        out.append({"key": item["key"], "title": item["title"], "text": text,
                    "source": f"fin_fee_model:{fm['version']}" if (item["key"] == "fee" and fm) else None})
    return out


def _name_of(row: dict) -> Optional[str]:
    return row.get("name")


def _order_view(o: dict) -> dict:
    """一条动作（委托，可能已成交）的展示形状。**不改数值**，只做字段重命名与配对。"""
    status = o.get("status")
    ok = status in ("filled", "partially_filled")
    return {
        "order_id": o.get("order_id"),
        "code": o.get("code"),
        "name": _name_of(o),
        "side": o.get("side"),
        "qty": o.get("qty"),
        "filled_qty": o.get("filled_qty"),
        "price_type": o.get("price_type"),
        "limit_price": o.get("limit_price"),
        "status": status,
        "status_text": {
            "pending": "待成交", "accepted": "已受理", "partially_filled": "部分成交",
            "filled": "已成交", "rejected": "被拒绝", "cancelled": "已撤销", "expired": "收盘撤单",
        }.get(status, status),
        "outcome": "traded" if ok else ("declined" if status in ("rejected",) else "open"),
        "decline_reason": o.get("decline_reason"),
        "trade_id": o.get("trade_id"),
        "price": o.get("trade_price"),
        "total_fee": o.get("total_fee"),
        "snapshot_id": o.get("snapshot_id"),
        "created_at": o.get("created_at"),
        "decision_ref": o.get("decision_ref"),
        # 市场与币种：金额按本币渲染（前端 `money(v, currency)`）。
        "market": o.get("market"),
        "currency": o.get("currency"),
    }


# ── 总览 ──────────────────────────────────────────────────────────────────

def overview_payload(cur, project_id: str, trade_date: str,
                     market: Optional[str] = None) -> dict:
    p = dash.project(cur, project_id)
    param = dash.param(cur, project_id)
    # `market` 不给 = 一期语义：这个项目的全部行（A 股项目下就是 CN_A 子账户）。
    vals = dash.valuations(cur, project_id, limit=90, market=market)
    account = dash.account_from_valuation(vals)
    pos = dash.positions_with_price(cur, project_id, market=market)
    total_mv = sum(Decimal(str(x["market_value"])) for x in pos if x["market_value"] is not None)
    missing_price = [x["code"] for x in pos if x["last_price"] is None]
    recent = [_order_view(o) for o in dash.recent_orders(cur, project_id, limit=8, market=market)]
    notes: list[str] = []
    if not vals:
        notes.append("还没有收盘估值记录，总资产与净值显示 —。")
    if missing_price:
        notes.append("这些代码没有行情快照，现价与浮动盈亏显示 —：" + "、".join(missing_price))
    return {
        "project": p,
        "param": param,
        "market": market,
        "currency": account.get("currency"),
        "as_of": account["as_of"],
        "account": account,
        "positions": {
            "count": len(pos),
            "market_value_by_snapshot": float(total_mv) if pos else None,
            "missing_price_codes": missing_price,
            "truncated": len(pos) > dash.POSITIONS_LIMIT,
            "items": pos[:dash.POSITIONS_LIMIT],
        },
        "recent_actions": recent,
        "switch": {"auto_enabled": bool((param or {}).get("auto_enabled", True))},
        "schedule": schedule.as_list(market or "CN_A"),
        # 市场切换器 + 跨市场合计（按市场分列；合计带汇率来源与时刻，取不到显示 —）。
        "markets": markets_svc.market_status(cur),
        "combined": markets_svc.combined_assets(cur, project_id),
        "ops": {
            "recon": dash.recon_latest(cur, project_id),
            "jobs": dash.jobs(cur, project_id, limit=6),
        },
        "notes": notes,
    }


# ── 自动交易 ──────────────────────────────────────────────────────────────

def auto_trade_payload(cur, project_id: str, trade_date: str, *,
                       today: Optional[str] = None, market: Optional[str] = None) -> dict:
    param = dash.param(cur, project_id)
    p = dash.project(cur, project_id)
    active = active_strategy(param)
    strategies = list((param or {}).get("strategies") or [])
    mk = market or "CN_A"

    current_risk = {k: (param or {}).get(k) for k in risk.PRESETS["steady"].keys()}
    tier = (param or {}).get("risk_tier")
    if tier not in risk.PRESETS:
        # 库里没设过档位：不为它编一个名字，交给前端显示「尚未单独设定」。
        tier = None

    day = today or trade_date
    actions = [_order_view(o) for o in dash.orders_on_date(cur, project_id, day, market=market)]
    recent = [_order_view(o) for o in dash.recent_orders(cur, project_id, limit=5, market=market)]

    return {
        "project": p,
        "market": market,
        "switch": {
            "auto_enabled": bool((param or {}).get("auto_enabled", True)),
            "updated_at": (param or {}).get("updated_at"),
        },
        "strategies": strategies,
        "active_strategy": active,
        "risk": {
            "tier": tier,
            "label": risk.TIER_LABEL.get(tier) if tier else None,
            "matches_preset": risk.tier_of(current_risk),
            "current": {k: current_risk[k] for k in current_risk},
            "presets": {
                t: {"label": risk.TIER_LABEL[t], "values": risk.PRESETS[t],
                    "fields": risk.FIELD_LABEL}
                for t in risk.TIER_ORDER
            },
        },
        "today": {"date": day, "actions": actions},
        # 「今天没有动作」时给一条落点：最近一次动作是哪天。非交易日 / 尚未到点时点都会用到。
        "recent_actions": recent,
        "schedule": schedule.as_list(mk, schedule.times_of(cur, mk)),
        "schedule_market": mk,
        "markets": markets_svc.market_status(cur),
        "constraints": constraints(cur, mk),
        "param_change_log": dash.param_change_log(cur, project_id, limit=10),
    }


# ── 我的账户 ──────────────────────────────────────────────────────────────

def account_payload(cur, project_id: str, market: Optional[str] = None) -> dict:
    p = dash.project(cur, project_id)
    param = dash.param(cur, project_id)
    vals = dash.valuations(cur, project_id, limit=90, market=market)
    account = dash.account_from_valuation(vals)
    pos = dash.positions_with_price(cur, project_id, market=market)
    trs = dash.trades(cur, project_id, limit=50, market=market)
    used = dash.used_snapshot_ids(cur, project_id)
    for t in trs:
        t["snapshot_used_by_project"] = used.get(t.get("snapshot_id"), 0)
    return {
        "project": p,
        "param": param,
        "market": market,
        "currency": account.get("currency"),
        "account": account,
        "positions": pos[:dash.POSITIONS_LIMIT],
        "positions_truncated": len(pos) > dash.POSITIONS_LIMIT,
        "trades": trs,
        "cash_entries": dash.cash_entries(cur, project_id, market=market),
        "param_change_log": dash.param_change_log(cur, project_id, limit=20),
        "markets": markets_svc.market_status(cur),
        "constraints": constraints(cur, market or "CN_A"),
        # 开新项目要另选档位，前端拿这个列表渲染三选一；金额依旧由服务端写死。
        "tier_options": [
            {"tier": t, "label": lbl}
            for t, lbl in (("play", "个人玩玩"), ("manage", "个人资产管理"), ("operate", "资产运营"))
        ],
    }
