"""对账：每天（以及被叫到时）把账本自洽性核一遍，写一行 `fin_recon_log`。

六项检查，`checks` 里逐项 `{name, expected, actual, passed}`。任何一项不过就
`passed=false` 并打 ERROR 日志（`08 §七` 的告警出口）——**不平不能悄悄过去**。

规则来源：`05 §3.2` M-30「任意时点 持仓市值 + 可用 + 冻结 = 总资产；买入数量恒为
100 股整数倍；成交合计与资金变动逐笔对得上」，外加两条本实现引入的不变量
（每笔成交 +1 版本、无挂单时冻结归零）。
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import psycopg2.extras
from loguru import logger

from app import ledger

_LOT = 100


def _check(name: str, expected: Any, actual: Any, passed: bool) -> dict:
    return {"name": name, "expected": str(expected), "actual": str(actual), "passed": bool(passed)}


def run(cur, project_id: str, as_of) -> dict:
    project = ledger.get_project(cur, project_id)
    if not project:
        raise LookupError(f"项目不存在：{project_id}")

    checks: list[dict] = []
    available, frozen = ledger.cash_balance(cur, project_id)

    # ── 1 · 现金总额 = 流水金额之和 ───────────────────────────────────────
    cur.execute(
        "SELECT COALESCE(SUM(amount), 0) AS s FROM fin_cash_ledger WHERE project_id = %s",
        (project_id,),
    )
    ledger_sum = Decimal(str(cur.fetchone()["s"])).quantize(Decimal("0.0001"))
    cash_total = available + frozen
    checks.append(
        _check("cash_sum", cash_total, ledger_sum, cash_total == ledger_sum)
    )

    # ── 2 · 持仓市值 + 可用 + 冻结 = 总资产 ───────────────────────────────
    cur.execute(
        """
        SELECT p.code, p.qty, s.last_price
          FROM fin_position p
          LEFT JOIN LATERAL (
            SELECT last_price FROM fin_snapshot
             WHERE code = p.code ORDER BY snapshot_time DESC LIMIT 1
          ) s ON true
         WHERE p.project_id = %s AND p.qty > 0
        """,
        (project_id,),
    )
    market_value = Decimal("0.0000")
    no_price: list[str] = []
    for row in cur.fetchall():
        if row["last_price"] is None:
            no_price.append(row["code"])
            continue
        market_value += Decimal(str(row["qty"])) * Decimal(str(row["last_price"]))
    total_assets = (ledger_sum + market_value).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    if no_price:
        checks.append(
            _check("balance_equation", "所有持仓有价", f"缺价：{','.join(no_price)}", False)
        )
    else:
        expected = (available + frozen + market_value).quantize(Decimal("0.0001"))
        checks.append(
            _check("balance_equation", expected, total_assets, expected == total_assets)
        )

    # ── 3 · 买入恒为整手 ─────────────────────────────────────────────────
    cur.execute(
        "SELECT trade_id, qty FROM fin_trade WHERE project_id = %s AND side = 'buy' AND qty %% %s <> 0",
        (project_id, _LOT),
    )
    bad_lots = cur.fetchall()
    checks.append(
        _check("buy_lot", "0 笔非整手", f"{len(bad_lots)} 笔非整手", not bad_lots)
    )

    # ── 4 · 成交与流水逐笔对得上 ─────────────────────────────────────────
    cur.execute(
        """
        SELECT t.trade_id, t.side, t.amount, t.total_fee,
               COALESCE(l.s, 0) AS ledger_sum
          FROM fin_trade t
          LEFT JOIN LATERAL (
            SELECT SUM(amount) AS s FROM fin_cash_ledger WHERE trade_id = t.trade_id
          ) l ON true
         WHERE t.project_id = %s ORDER BY t.traded_at
        """,
        (project_id,),
    )
    mismatched = []
    for row in cur.fetchall():
        amount = Decimal(str(row["amount"]))
        fee = Decimal(str(row["total_fee"]))
        expected = (-(amount + fee)) if row["side"] == "buy" else (amount - fee)
        expected = expected.quantize(Decimal("0.0001"))
        actual = Decimal(str(row["ledger_sum"])).quantize(Decimal("0.0001"))
        if expected != actual:
            mismatched.append(f"{row['trade_id']}(应{expected}/实{actual})")
    checks.append(
        _check("cash_ties_trades", "逐笔相等", "; ".join(mismatched) or "全部相等", not mismatched)
    )

    # ── 5 · 持仓 = 成交净额 ──────────────────────────────────────────────
    cur.execute(
        """
        SELECT p.code, p.qty,
               COALESCE(SUM(CASE WHEN t.side = 'buy' THEN t.qty ELSE -t.qty END), 0) AS net
          FROM fin_position p
          LEFT JOIN fin_trade t ON t.project_id = p.project_id AND t.code = p.code
         WHERE p.project_id = %s GROUP BY p.code, p.qty
        """,
        (project_id,),
    )
    pos_bad = [r["code"] for r in cur.fetchall() if int(r["qty"]) != int(r["net"])]
    checks.append(
        _check("position_ties_trades", "持仓 = 成交净额", ",".join(pos_bad) or "全部相等", not pos_bad)
    )

    # ── 6 · 版本号 = 成交笔数 ────────────────────────────────────────────
    cur.execute(
        "SELECT COUNT(*) AS n FROM fin_trade WHERE project_id = %s", (project_id,)
    )
    trades = int(cur.fetchone()["n"])
    checks.append(
        _check("project_version", trades, int(project["version"]), trades == int(project["version"]))
    )

    # ── 7 · 无挂单时冻结必须归零 ─────────────────────────────────────────
    cur.execute(
        """
        SELECT COUNT(*) AS n FROM fin_order
         WHERE project_id = %s AND status IN ('pending','accepted','partially_filled')
        """,
        (project_id,),
    )
    open_orders = int(cur.fetchone()["n"])
    frozen_ok = open_orders > 0 or frozen == 0
    checks.append(
        _check("frozen_zero_no_open", "0.0000", str(frozen), frozen_ok)
    )

    passed = all(c["passed"] for c in checks)
    cur.execute(
        """
        INSERT INTO fin_recon_log (project_id, as_of, passed, checks)
        VALUES (%s, %s, %s, %s) RETURNING id, created_at
        """,
        (project_id, as_of, passed, psycopg2.extras.Json(checks)),
    )
    row = cur.fetchone()

    if not passed:
        failed = [c["name"] for c in checks if not c["passed"]]
        logger.error(
            "[paper.recon] 账本不平 · project={} · 未通过：{}", project_id, failed
        )
    else:
        logger.info("[paper.recon] 对账通过 · project={} · {} 项", project_id, len(checks))

    return {
        "id": row["id"],
        "project_id": project_id,
        "as_of": as_of,
        "passed": passed,
        "checks": checks,
        "created_at": row["created_at"],
    }
