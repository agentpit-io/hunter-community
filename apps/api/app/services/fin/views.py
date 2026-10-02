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


def _fee_model(cur) -> Optional[dict]:
    cur.execute(
        """
        SELECT version, commission_pct, commission_min, stamp_tax_pct, transfer_fee_pct
          FROM fin_fee_model ORDER BY version DESC LIMIT 1
        """)
    return dash._d(cur.fetchone())


def constraints(cur) -> list[dict]:
    """A 股六条 —— 文案是产品语言，**费率数值取库里那一行**（不写死在文案里）。"""
    fm = _fee_model(cur)
    out = []
    for item in CONSTRAINTS:
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
    }


# ── 总览 ──────────────────────────────────────────────────────────────────

def overview_payload(cur, project_id: str, trade_date: str) -> dict:
    p = dash.project(cur, project_id)
    param = dash.param(cur, project_id)
    vals = dash.valuations(cur, project_id, limit=90)
    account = dash.account_from_valuation(vals)
    pos = dash.positions_with_price(cur, project_id)
    total_mv = sum(Decimal(str(x["market_value"])) for x in pos if x["market_value"] is not None)
    missing_price = [x["code"] for x in pos if x["last_price"] is None]
    recent = [_order_view(o) for o in dash.recent_orders(cur, project_id, limit=8)]
    notes: list[str] = []
    if not vals:
        notes.append("还没有收盘估值记录，总资产与净值显示 —。")
    if missing_price:
        notes.append("这些代码没有行情快照，现价与浮动盈亏显示 —：" + "、".join(missing_price))
    return {
        "project": p,
        "param": param,
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
        "schedule": schedule.as_list(),
        "ops": {
            "recon": dash.recon_latest(cur, project_id),
            "jobs": dash.jobs(cur, project_id, limit=6),
        },
        "notes": notes,
    }


# ── 自动交易 ──────────────────────────────────────────────────────────────

def auto_trade_payload(cur, project_id: str, trade_date: str, *,
                       today: Optional[str] = None) -> dict:
    param = dash.param(cur, project_id)
    p = dash.project(cur, project_id)
    active = active_strategy(param)
    strategies = list((param or {}).get("strategies") or [])

    current_risk = {k: (param or {}).get(k) for k in risk.PRESETS["steady"].keys()}
    tier = (param or {}).get("risk_tier")
    if tier not in risk.PRESETS:
        # 库里没设过档位：不为它编一个名字，交给前端显示「尚未单独设定」。
        tier = None

    day = today or trade_date
    actions = [_order_view(o) for o in dash.orders_on_date(cur, project_id, day)]
    recent = [_order_view(o) for o in dash.recent_orders(cur, project_id, limit=5)]

    return {
        "project": p,
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
        "schedule": schedule.as_list(),
        "constraints": constraints(cur),
        "param_change_log": dash.param_change_log(cur, project_id, limit=10),
    }


# ── 我的账户 ──────────────────────────────────────────────────────────────

def account_payload(cur, project_id: str) -> dict:
    p = dash.project(cur, project_id)
    param = dash.param(cur, project_id)
    vals = dash.valuations(cur, project_id, limit=90)
    account = dash.account_from_valuation(vals)
    pos = dash.positions_with_price(cur, project_id)
    trs = dash.trades(cur, project_id, limit=50)
    used = dash.used_snapshot_ids(cur, project_id)
    for t in trs:
        t["snapshot_used_by_project"] = used.get(t.get("snapshot_id"), 0)
    return {
        "project": p,
        "param": param,
        "account": account,
        "positions": pos[:dash.POSITIONS_LIMIT],
        "positions_truncated": len(pos) > dash.POSITIONS_LIMIT,
        "trades": trs,
        "cash_entries": dash.cash_entries(cur, project_id),
        "param_change_log": dash.param_change_log(cur, project_id, limit=20),
        "constraints": constraints(cur),
        # 开新项目要另选档位，前端拿这个列表渲染三选一；金额依旧由服务端写死。
        "tier_options": [
            {"tier": t, "label": lbl}
            for t, lbl in (("play", "个人玩玩"), ("manage", "个人资产管理"), ("operate", "资产运营"))
        ],
    }
