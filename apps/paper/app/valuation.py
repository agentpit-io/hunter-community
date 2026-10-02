"""估值：`total_assets = 可用 + 冻结 + 持仓市值`，`nav = total_assets / 本金`。

**缺价就不出估值**：任何一个持仓标的翻不到快照价，这里抛 `MissingPrice`，由路由
转成 4xx 并点名是哪几只。绝不拿 `avg_cost` 顶替市值 —— 那是把成本当市价，
在报告里会变成一条看起来正常的假净值（`CLAUDE.md` 铁律）。

快照 `quality='stale'` 仍可估值，但估值行标 `quality='stale'` + `missing_flag=true`，
让下游（报告 / 前端）知道这个数是从旧价算出来的。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from app import ledger

# 快照超过这个时长算「旧」。盘中采集是分钟级，4 小时足够区分「今天早些时候」与「好几天前」。
STALE_AFTER = timedelta(hours=4)


class MissingPrice(Exception):
    def __init__(self, codes: list[str]):
        self.codes = codes
        super().__init__(f"以下持仓标的没有可用快照价，无法估值：{', '.join(codes)}")


def compute(cur, project_id: str, as_of: datetime) -> dict:
    project = ledger.get_project(cur, project_id)
    if not project:
        raise LookupError(f"项目不存在：{project_id}")

    available, frozen = ledger.cash_balance(cur, project_id)
    positions = [p for p in ledger.list_positions(cur, project_id) if int(p["qty"]) > 0]

    market_value = Decimal("0.0000")
    missing: list[str] = []
    stale = False
    for pos in positions:
        # **截至 as_of 的价**，不是「最新那个价」：拿最新价去估过去时点，等于把
        # 现在的信息泄漏给过去（`01方案 §6.2`）。as_of = 现在时行为与从前逐位一致。
        snap = ledger.latest_snapshot_at(cur, pos["code"], as_of)
        if not snap or snap["last_price"] is None or snap["missing_flag"]:
            missing.append(pos["code"])
            continue
        market_value += Decimal(str(pos["qty"])) * Decimal(str(snap["last_price"]))
        if snap["quality"] == "stale" or (as_of - snap["snapshot_time"]) > STALE_AFTER:
            stale = True

    if missing:
        raise MissingPrice(sorted(missing))

    market_value = market_value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    total = (available + frozen + market_value).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    nav = (total / Decimal(str(project["initial_capital"]))).quantize(
        Decimal("0.000001"), rounding=ROUND_HALF_UP
    )
    return {
        "project_id": project_id,
        "as_of": as_of,
        "cash_available": available,
        "cash_frozen": frozen,
        "market_value": market_value,
        "total_assets": total,
        "nav": nav,
        "price_source": "fin_snapshot",
        "quality": "stale" if stale else "ok",
        "missing_flag": stale,
    }


def store(cur, project_id: str, as_of: datetime) -> dict:
    """写一行 `fin_valuation`。PK 是 (project_id, as_of)，重复时返回已有行（只追加）。"""
    existing = ledger._fetchone(
        cur,
        "SELECT * FROM fin_valuation WHERE project_id = %s AND as_of = %s",
        (project_id, as_of),
    )
    if existing:
        return {**existing, "created": False}

    row = compute(cur, project_id, as_of)
    cur.execute(
        """
        INSERT INTO fin_valuation
          (project_id, as_of, cash_available, cash_frozen, market_value, total_assets,
           nav, price_source, quality, missing_flag)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING *
        """,
        (
            row["project_id"], row["as_of"], row["cash_available"], row["cash_frozen"],
            row["market_value"], row["total_assets"], row["nav"], row["price_source"],
            row["quality"], row["missing_flag"],
        ),
    )
    return {**cur.fetchone(), "created": True}


def latest(cur, project_id: str) -> Optional[dict]:
    return ledger._fetchone(
        cur,
        "SELECT * FROM fin_valuation WHERE project_id = %s ORDER BY as_of DESC LIMIT 1",
        (project_id,),
    )
