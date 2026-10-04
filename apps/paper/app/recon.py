"""对账：把账本自洽性核一遍，写一行 `fin_recon_log`。

规则来源：`05 §3.4` M-30。**三项必查**（任一项不过 → `passed=false`）：

1. 任意时点 **持仓市值 + 可用 + 冻结 = 总资产**（`balance_equation`）；
2. **买入数量恒为 100 股整数倍**（`buy_lot`）；
3. **成交合计与资金变动逐笔对得上**（`cash_ties_trades`）。

外加本实现引入的不变量（同样的 `checks` 结构，逐项 `{name, expected, actual, passed}`）：
现金合计 = 流水金额之和、持仓 = 成交净额、版本号 = 成交笔数、无挂单时冻结归零、
冻结额 = 未成交买单的占用之和。

## 任意历史时点都能重跑（`05 §3.4` 「任意时点」）

`as_of` 不只是写进日志的一个字段，它是**时间轴**：

| 看哪张表 | 用什么时刻过滤 |
|---|---|
| 成交 `fin_trade` | `traded_at <= as_of`（业务时刻，来自数据源） |
| 现金 `fin_cash_ledger` | `entry_id <= cutoff`，cutoff 见 `ledger.cash_state_at` |
| 快照 `fin_snapshot` | `snapshot_time <= as_of`（按数据源时刻，不按入库时间） |
| 委托 `fin_order` | `created_at <= as_of` |

`as_of` 取「现在」时，全部行都落进来 —— 行为与「只对当天」逐位一致。

## 不平就告警

对账不过 → 打 ERROR 日志 + 写一行 `passed=false` 的 `fin_recon_log`
（**告警出口复用 hunter 的通知通道 `notify-qq`，由宿主脚本
`scripts/fin_recon_alert.sh` 读这张表发出** —— `paper` 在容器里，
拿不到宿主的通知脚本。容器负责把不平**变成可查询的事实**，不另起一个告警通道）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

import psycopg2.extras
from loguru import logger
from typing import Optional

from app import ledger

_LOT = 100

# ── DDL 随代码走（迁移文件 0028 是同内容留档）──────────────────────────────
#
# `fin_alert_log` 是**告警投递记录**，不是账本、也不是对账结果：
#   · `fin_recon_log` 保持只追加（`09 §四` 的表都在「只追加」约定里）；
#   · 投递状态（发出去了没有）放在这张单独的日志表里，宿主 dispatcher 读它的
#     `sent_at IS NULL` 决定要不要发 —— 于是「发过没有」不需要 UPDATE 对账表。
#
# 建表 / 授权语句的**单一来源**（L02）：见 `app/aux_ddl.py`（原来这里与
# `selfcheck.py` 各写一遍）。
from app.aux_ddl import FIN_ALERT_LOG_DDL as _DDL, FIN_ALERT_LOG_GRANT as _GRANT
_ddl_done = False


def ensure_schema(cur) -> None:
    """表不在才补 DDL。**先查存在性** —— `fin_paper_rw` 跑 DDL / GRANT 会把事务
    打成 aborted，症状出现在很远的调用点（同 `data_gap.ensure_table` 的注释）。
    """
    global _ddl_done
    if _ddl_done:
        return
    try:
        cur.execute("SELECT to_regclass('public.fin_alert_log') AS t")
        row = cur.fetchone()
        exists = row is not None and (row.get("t") if isinstance(row, dict) else row[0]) is not None
    except Exception:  # noqa: BLE001 —— 查不出来当"在"，别误触发无权限的 DDL
        exists = True
    if exists:
        _ddl_done = True
        return
    try:
        cur.execute("SET lock_timeout = '5s'")
        cur.execute(_DDL)
        try:
            cur.execute(_GRANT)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[paper.recon] 授权 fin_alert_log 失败（继续）：{}", exc)
        _ddl_done = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[paper.recon] 建 fin_alert_log 失败（本轮不记告警）：{}", exc)


def _check(name: str, expected, actual, passed: bool, **extra) -> dict:
    item = {"name": name, "expected": str(expected), "actual": str(actual), "passed": bool(passed)}
    item.update(extra)
    return item


def run(cur, project_id: str, as_of, market: Optional[str] = None) -> dict:
    project = ledger.get_project(cur, project_id)
    if not project:
        raise LookupError(f"项目不存在：{project_id}")

    ensure_schema(cur)
    as_of = ledger.as_dt(as_of)
    # 对账按**市场子账户**核（缺省取项目 market_scope；A 股项目 = CN_A，行为不变）。
    market = ledger.resolve_market(project, market)
    checks: list[dict] = []

    # 账户状态**截至 as_of**（该子账户）
    available, frozen, cutoff = ledger.cash_state_at(cur, project_id, as_of, market)

    # ── 1 · 现金总额 = 流水金额之和（截至 as_of 的行）─────────────────────
    # **和 `cash_state_at` 用同一个 cutoff**：两处各自写一套过滤条件，
    # 只要有一条边界不一致（比如本金行被一边带上、一边漏掉），这项就会假不平。
    cur.execute(
        "SELECT COALESCE(SUM(amount), 0) AS s FROM fin_cash_ledger "
        " WHERE project_id = %s AND entry_id <= %s AND market = %s",
        (project_id, cutoff, market),
    )
    ledger_sum = Decimal(str(cur.fetchone()["s"])).quantize(Decimal("0.0001"))
    cash_total = available + frozen
    checks.append(_check("cash_sum", cash_total, ledger_sum, cash_total == ledger_sum))

    # ── 2 · 持仓市值 + 可用 + 冻结 = 总资产（M-30 第 1 项）───────────────
    # 持仓同样截至 as_of：由成交净额推出来（`fin_position` 只有当前值，没有历史）。
    cur.execute(
        """
        SELECT t.code,
               COALESCE(SUM(CASE WHEN t.side = 'buy' THEN t.qty ELSE -t.qty END), 0) AS qty
          FROM fin_trade t
         WHERE t.project_id = %s AND t.traded_at <= %s AND t.market = %s
         GROUP BY t.code
        """,
        (project_id, as_of, market),
    )
    positions = {r["code"]: int(r["qty"]) for r in cur.fetchall() if int(r["qty"]) > 0}

    market_value = Decimal("0.0000")
    no_price: list[str] = []
    for code, qty in sorted(positions.items()):
        snap = ledger.latest_snapshot_at(cur, code, as_of)
        if not snap or snap["last_price"] is None or snap["missing_flag"]:
            no_price.append(code)
            continue
        market_value += Decimal(qty) * Decimal(str(snap["last_price"]))
    market_value = market_value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

    if no_price:
        checks.append(_check("balance_equation", "所有持仓有价",
                             f"缺价：{','.join(no_price)}", False))
    else:
        # `total_assets` 取**账本里记过的那一个**（估值行）——拿推导值跟自己比是废检查。
        recorded = ledger._fetchone(
            cur,
            "SELECT cash_available, cash_frozen, market_value, total_assets "
            "  FROM fin_valuation WHERE project_id = %s AND market = %s AND as_of <= %s "
            " ORDER BY as_of DESC LIMIT 1",
            (project_id, market, as_of),
        )
        derived = (available + frozen + market_value).quantize(Decimal("0.0001"))
        if recorded is not None:
            rec_sum = (Decimal(str(recorded["cash_available"]))
                       + Decimal(str(recorded["cash_frozen"]))
                       + Decimal(str(recorded["market_value"]))).quantize(Decimal("0.0001"))
            recorded_total = Decimal(str(recorded["total_assets"])).quantize(Decimal("0.0001"))
            # 两条一起成立才算过：估值行自己自洽，且与账本现算的现金 / 市值一致。
            passed = (rec_sum == recorded_total
                      and Decimal(str(recorded["cash_available"])) == available
                      and Decimal(str(recorded["cash_frozen"])) == frozen
                      and Decimal(str(recorded["market_value"])) == market_value)
            checks.append(_check(
                "balance_equation", recorded_total, derived, passed,
                recorded_as_of=str(recorded.get("as_of") if isinstance(recorded, dict) else None),
            ))
        else:
            # 还没有估值行：只能核「现金合计 + 市值」与自己推的总资产相等 ——
            # 这是恒等式，记 `derived=true` 让读的人知道它证不了什么。
            checks.append(_check("balance_equation", derived, derived, True, derived=True))

    # ── 3 · 买入恒为整手（M-30 第 2 项）──────────────────────────────────
    cur.execute(
        "SELECT trade_id, qty FROM fin_trade "
        " WHERE project_id = %s AND side = 'buy' AND traded_at <= %s "
        "   AND qty %% %s <> 0 AND market = %s",
        (project_id, as_of, _LOT, market),
    )
    bad_lots = cur.fetchall()
    checks.append(_check("buy_lot", "0 笔非整手", f"{len(bad_lots)} 笔非整手", not bad_lots))

    # ── 4 · 成交与流水逐笔对得上（M-30 第 3 项）──────────────────────────
    cur.execute(
        """
        SELECT t.trade_id, t.side, t.amount, t.total_fee, COALESCE(l.s, 0) AS ledger_sum
          FROM fin_trade t
          LEFT JOIN LATERAL (
            SELECT SUM(amount) AS s FROM fin_cash_ledger
             WHERE trade_id = t.trade_id
               AND (created_at <= %s OR t.traded_at <= %s)
          ) l ON true
         WHERE t.project_id = %s AND t.traded_at <= %s AND t.market = %s
         ORDER BY t.traded_at
        """,
        (as_of, as_of, project_id, as_of, market),
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
    checks.append(_check("cash_ties_trades", "逐笔相等",
                         "; ".join(mismatched) or "全部相等", not mismatched))

    # ── 5 · 持仓 = 成交净额 ──────────────────────────────────────────────
    # 「是不是现在」要有**容差**：调用方把 `as_of` 写成"此刻"时，落在服务端 `now()`
    # 前面几毫秒是常态，严格比较会把一次"现在的对账"判成历史时点，于是
    # `project_version` / `position_ties_trades` 被静默标成 skipped —— 三项必查里
    # 有两项没真跑，而结果显示"通过"。5 秒容差足够吃掉网络与截断误差。
    is_now = as_of >= datetime.now(timezone.utc) - timedelta(seconds=5)
    if is_now:
        cur.execute(
            """
            SELECT p.code, p.qty,
                   COALESCE(SUM(CASE WHEN t.side = 'buy' THEN t.qty ELSE -t.qty END), 0) AS net
              FROM fin_position p
              LEFT JOIN fin_trade t ON t.project_id = p.project_id AND t.code = p.code
                                    AND t.market = p.market
             WHERE p.project_id = %s AND p.market = %s GROUP BY p.code, p.qty
            """,
            (project_id, market),
        )
        pos_bad = [r["code"] for r in cur.fetchall() if int(r["qty"]) != int(r["net"])]
        checks.append(_check("position_ties_trades", "持仓 = 成交净额",
                             ",".join(pos_bad) or "全部相等", not pos_bad))
    else:
        # `fin_position` 只有**当前**持仓，没有历史快照。拿当前值去比「截至 as_of 的
        # 成交净额」是在比两个时间轴，必然对不上 —— 如实标 skipped，不制造假不平。
        checks.append(_check("position_ties_trades", "—", "历史时点跳过", True, skipped=True,
                             note="持仓表只有当前值，历史时点无法复核本项"))

    # ── 6 · 版本号 = 成交笔数 ────────────────────────────────────────────
    cur.execute(
        "SELECT COUNT(*) AS n FROM fin_trade "
        " WHERE project_id = %s AND traded_at <= %s AND market = %s",
        (project_id, as_of, market),
    )
    trades = int(cur.fetchone()["n"])
    if is_now:
        checks.append(_check("project_version", trades, int(project["version"]),
                             trades == int(project["version"])))
    else:
        # 历史时点：`fin_project.version` 只有当前值，把它和「截至 as_of 的成交数」比
        # 是拿两个时间轴比大小。如实标 skipped，不假装通过（也不假装失败）。
        checks.append(_check("project_version", trades, int(project["version"]),
                             True, skipped=True,
                             note="历史时点：账户版本只有当前值，本项跳过"))

    # ── 7 · 无挂单时冻结必须归零 ─────────────────────────────────────────
    cur.execute(
        """
        SELECT COUNT(*) AS n FROM fin_order
         WHERE project_id = %s AND created_at <= %s AND market = %s
           AND status IN ('pending','accepted','partially_filled')
        """,
        (project_id, as_of, market),
    )
    open_orders = int(cur.fetchone()["n"])
    frozen_ok = open_orders > 0 or frozen == 0
    checks.append(_check("frozen_zero_no_open", "0.0000", str(frozen), frozen_ok,
                         open_orders=open_orders))

    # ── 8 · 冻结额 = 未成交买单的占用之和 ────────────────────────────────
    # 只在「现在」这一档有精确口径：历史时点的挂单表由 `updated_at` 变过状态，
    # 用当前 `frozen_amount` 回算过去会得出错误结论。
    if is_now:
        expected_frozen = ledger.open_buy_frozen(cur, project_id, market)
        checks.append(_check("frozen_matches_open", expected_frozen, frozen,
                             expected_frozen == frozen))
    else:
        checks.append(_check("frozen_matches_open", "—", frozen, True, skipped=True,
                             note="历史时点：挂单状态只有当前值，本项跳过"))

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
        logger.error("[paper.recon] 账本不平 · project={} · as_of={} · 未通过：{}",
                     project_id, as_of, failed)
        _queue_alert(cur, row["id"], project_id, as_of, checks)
    else:
        logger.info("[paper.recon] 对账通过 · project={} · as_of={} · {} 项",
                    project_id, as_of, len(checks))

    return {
        "id": row["id"],
        "project_id": project_id,
        "as_of": as_of,
        "passed": passed,
        "checks": checks,
        "created_at": row["created_at"],
    }


# ── 告警（宿主 dispatcher 读这张表发 notify-qq）────────────────────────────

def _queue_alert(cur, recon_id: int, project_id: str, as_of, checks: list[dict]) -> None:
    """把「不平」排进 `fin_alert_log`（`sent_at` 为空 = 还没发）。

    **不在容器里发**：`notify-qq` 是宿主脚本，`paper` 容器拿不到它。
    容器负责把不平变成一条**可查询、可投递**的记录，宿主 `scripts/fin_recon_alert.sh`
    读它并发通知 —— 通道仍然是 hunter 现有的那一个，没有第二套告警系统。
    """
    failed = [c for c in checks if not c["passed"]]
    detail = "；".join(f"{c['name']} 应 {c['expected']} 实 {c['actual']}" for c in failed)
    subject = f"[智能炒股] 账本不平 · {project_id}"
    items = "".join(
        f"<li>{c['name']}：应 {c['expected']} / 实 {c['actual']}</li>" for c in failed
    )
    body = (f"<p>对账未通过（as_of {as_of}）。</p><ul>{items}</ul>"
            f"<p>项目 {project_id} · 共 {len(checks)} 项，未通过 {len(failed)} 项。</p>")
    try:
        cur.execute(
            """
            INSERT INTO fin_alert_log (kind, ref_id, channel, project_id, subject, body)
            VALUES ('recon_failed', %s, 'notify-qq', %s, %s, %s)
            ON CONFLICT (kind, ref_id) DO NOTHING
            """,
            (recon_id, project_id, subject, body),
        )
    except Exception as exc:  # noqa: BLE001 —— 告警排不上不该让对账事务回滚
        logger.warning("[paper.recon] 排告警失败 recon={} · {} · {}", recon_id, detail, exc)


def undelivered_alerts(cur, limit: int = 20) -> list[dict]:
    """未投递的告警。宿主脚本 `scripts/fin_recon_alert.sh` 调这个口径。"""
    ensure_schema(cur)
    cur.execute(
        "SELECT id, kind, ref_id, channel, project_id, subject, body, created_at "
        "  FROM fin_alert_log WHERE sent_at IS NULL ORDER BY id LIMIT %s",
        (limit,),
    )
    return cur.fetchall()


def mark_alerts_sent(cur, ids: list[int]) -> int:
    if not ids:
        return 0
    cur.execute(
        "UPDATE fin_alert_log SET sent_at = now() WHERE id = ANY(%s) AND sent_at IS NULL",
        (ids,),
    )
    return cur.rowcount
