"""智能炒股 · 开户的库访问层（一期只有项目与参数）。

连接方式与 `app/services/database.py` 同口径：psycopg2 直连 `DATABASE_URL`，
没有连接池、不依赖 FastAPI 上下文。这里不 import `database.py`，是为了让本模块
能被单测直接调用（不拉起整个 app）。

幂等口径（M1 定，写进成果文档）：
  「一个账户同时只有一个进行中项目」由库层的部分唯一索引
  `fin_project_one_active` 保证（`db/migrations/0023_fin_core.sql`），**不靠界面拦**。
  `create_project()` 的策略是 **返回现成的那个**（`created=False`），不是报冲突 ——
  向导第六步「提交 → 重复点」不应该给用户一个红色报错；并发撞索引时同样回读现成的。
  已有 active 项目而用户这次选了**不同**档位时，照旧返回现成的（连同它真实的档位），
  由前端提示「你已有进行中的项目」——档位不可改，换档位要走「开新项目」（M-16，二期前）。
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

import psycopg2
import psycopg2.extras

from app.services.fin import tiers

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://hunter:hunter@localhost:5432/hunter")


def get_conn():
    return psycopg2.connect(DATABASE_URL)


# ── JSON 化：Decimal → float，datetime → ISO 字符串 ────────────────────────
# psycopg2 把 NUMERIC 读成 Decimal、TIMESTAMPTZ 读成 datetime，直接进 JSON 会报错。
# 明面上是「格式转换」，背后的红线是**不许 round 成假数**：这里只是搬运，不做任何
# 单位换算或四舍五入（`float(Decimal('1000000.0000'))` == 1000000.0）。
def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {k: _jsonable(v) for k, v in row.items()}


def _project_row(cur, user_id: str) -> Optional[dict[str, Any]]:
    cur.execute(
        """
        SELECT project_id, user_id, tier, status, initial_capital, currency,
               market_scope, version, run_mode, opened_at, closed_at, close_reason
          FROM fin_project
         WHERE user_id = %s AND status = 'active'
         ORDER BY opened_at DESC
         LIMIT 1
        """,
        (user_id,),
    )
    row = cur.fetchone()
    return _row_to_dict(row) if row else None


def _param_row(cur, project_id: str) -> Optional[dict[str, Any]]:
    cur.execute(
        """
        SELECT project_id, board_flags, sector_prefs,
               liquidity_min_amount, liquidity_max_participation, strategies,
               hold_days_max, stop_loss_pct, take_profit_pct,
               max_positions, max_position_pct, min_order_amount,
               daily_max_new, daily_max_orders,
               daily_loss_halt_pct, account_drawdown_halt_pct,
               params_locked_at, updated_at
          FROM fin_param
         WHERE project_id = %s
        """,
        (project_id,),
    )
    row = cur.fetchone()
    return _row_to_dict(row) if row else None


def get_current(user_id: str) -> Optional[dict[str, Any]]:
    """当前进行中的项目 + 它的参数；没有返回 None。"""
    if not user_id:
        return None
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            project = _project_row(cur, user_id)
            if not project:
                conn.rollback()
                return None
            param = _param_row(cur, project["project_id"])
        conn.rollback()  # 只读
        return {"project": project, "param": param, "tier_template": tiers.build_template(project["tier"])}
    finally:
        conn.close()


def _insert_project(cur, user_id: str, tier: str) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    """往库里插一个项目 + 一套参数（**不提交**，由调用方决定事务边界）。"""
    template = tiers.build_template(tier)

    # 账户：一个用户一个账户，项目是它的「一段」
    account_id = "acct_" + uuid.uuid4().hex[:24]
    cur.execute(
        """
        INSERT INTO fin_account (account_id, user_id, locked_at)
        VALUES (%s, %s, now())
        ON CONFLICT (user_id) DO NOTHING
        """,
        (account_id, user_id),
    )

    # 项目：本金由档位写死，写入后不可改
    project_id = "prj_" + uuid.uuid4().hex[:24]
    cur.execute(
        """
        INSERT INTO fin_project (project_id, user_id, tier, initial_capital)
        VALUES (%s, %s, %s, %s)
        RETURNING project_id, user_id, tier, status, initial_capital, currency,
                  market_scope, version, run_mode, opened_at, closed_at, close_reason
        """,
        (project_id, user_id, tier, template["initial_capital"]),
    )
    project = _row_to_dict(cur.fetchone())

    # 参数：整套从档位模板写死。params_locked_at 留空 —— 一期没有成交，
    # 「首次成交时刻」还没发生（`03 §九`）；档位的不可改由「改档位只能开新项目」保证。
    cur.execute(
        """
        INSERT INTO fin_param (
          project_id, board_flags, sector_prefs,
          liquidity_min_amount, liquidity_max_participation, strategies,
          hold_days_max, stop_loss_pct, take_profit_pct,
          max_positions, max_position_pct, min_order_amount,
          daily_max_new, daily_max_orders,
          daily_loss_halt_pct, account_drawdown_halt_pct,
          params_locked_at
        ) VALUES (
          %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL
        )
        """,
        (
            project_id,
            psycopg2.extras.Json(template["board_flags"]),
            psycopg2.extras.Json(template["sector_prefs"]),
            template["liquidity_min_amount"],
            template["liquidity_max_participation"],
            psycopg2.extras.Json(template["strategies"]),
            template["hold_days_max"],
            template["stop_loss_pct"],
            template["take_profit_pct"],
            template["max_positions"],
            template["max_position_pct"],
            template["min_order_amount"],
            template["daily_max_new"],
            template["daily_max_orders"],
            template["daily_loss_halt_pct"],
            template["account_drawdown_halt_pct"],
        ),
    )
    return project, _param_row(cur, project_id)


def create_project(user_id: str, tier: str) -> dict[str, Any]:
    """开户（幂等）：已存在 active 项目时**返回现成的那个**，不新建。

    这是向导第六步「提交 → 重复点」的路径 —— 重复提交不该给用户一个红色报错。
    **要换档位 / 开新账本走 `open_new_project()`**（M-16：先关旧的，再开新的）。
    """
    if tier not in tiers.TIER_ORDER:
        raise ValueError(f"未知档位：{tier!r}（可选 {', '.join(tiers.TIER_ORDER)}）")

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            existing = _project_row(cur, user_id)
            if existing:
                param = _param_row(cur, existing["project_id"])
                conn.rollback()
                return {
                    "project": existing,
                    "param": param,
                    "tier_template": tiers.build_template(existing["tier"]),
                    "created": False,
                }
            project, param = _insert_project(cur, user_id, tier)
            template = tiers.build_template(tier)
        conn.commit()
        return {"project": project, "param": param, "tier_template": template, "created": True}
    except psycopg2.errors.UniqueViolation:
        # 并发：两个请求同时开户，后到者撞 `fin_project_one_active` → 回读现成的那个
        conn.rollback()
        current = get_current(user_id)
        if current:
            current["created"] = False
            return current
        raise
    finally:
        conn.close()


def open_new_project(user_id: str, tier: str, reason: str = "user_opened_new") -> dict[str, Any]:
    """**开新项目**（`05 §3.2` M-16）。

    用户原话口径：**先关闭当前项目，再开新的**。所以这里是「关旧 + 开新」的
    **一个事务**，不是「暂停」——`status='closed'` + `closed_at` + `close_reason` 三样都写，
    并把关停动作记进 `fin_param_change_log`（只追加，不给改）。

    旧账本、旧成交、旧报告**一行都不动**（`fin_*` 的追加表没有 DELETE/UPDATE），
    所以开新项目之后旧的那一段仍然可读可回看。

    `fin_project_one_active` 部分唯一索引照旧生效：同一用户任何时刻只能有一个
    `status='active'`。关停在同事务里先做，所以新插的那行不会撞索引；真撞上
    （并发开新项目）会让整个事务回滚 —— 于是**不会出现两个 active**。
    """
    if tier not in tiers.TIER_ORDER:
        raise ValueError(f"未知档位：{tier!r}（可选 {', '.join(tiers.TIER_ORDER)}）")

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            closed = None
            existing = _project_row(cur, user_id)
            if existing:
                cur.execute(
                    """
                    UPDATE fin_project
                       SET status = 'closed', closed_at = now(), close_reason = %s
                     WHERE project_id = %s
                     RETURNING project_id, user_id, tier, status, initial_capital, currency,
                               market_scope, version, run_mode, opened_at, closed_at, close_reason
                    """,
                    (reason, existing["project_id"]),
                )
                closed = _row_to_dict(cur.fetchone())
                # 关停动作进变更日志（`05 §3.2` M-16 验收项：记入变更日志）
                for field, old, new in (
                    ("status", "active", "closed"),
                    ("close_reason", None, reason),
                ):
                    cur.execute(
                        """
                        INSERT INTO fin_param_change_log (project_id, actor, field, old_value, new_value)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (existing["project_id"], user_id, field,
                         psycopg2.extras.Json(old), psycopg2.extras.Json(new)),
                    )

            project, param = _insert_project(cur, user_id, tier)
            # 新项目的开立也要留痕（同一条日志表）
            cur.execute(
                """
                INSERT INTO fin_param_change_log (project_id, actor, field, old_value, new_value)
                VALUES (%s, %s, 'opened', NULL, %s)
                """,
                (project["project_id"], user_id, psycopg2.extras.Json({"tier": tier})),
            )
            template = tiers.build_template(tier)
        conn.commit()
        return {
            "project": project,
            "param": param,
            "tier_template": template,
            "created": True,
            "closed_project": closed,
        }
    finally:
        conn.close()


def list_projects(user_id: str) -> list[dict[str, Any]]:
    """该用户的**全部**项目（进行中 + 已关闭），按开立时间倒序。

    开新项目之后旧账本仍要可回看 —— 这就是那个「回看」的读入口。
    """
    if not user_id:
        return []
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT project_id, user_id, tier, status, initial_capital, currency,
                       market_scope, version, run_mode, opened_at, closed_at, close_reason
                  FROM fin_project
                 WHERE user_id = %s
                 ORDER BY opened_at DESC
                """,
                (user_id,),
            )
            rows = [_row_to_dict(r) for r in cur.fetchall()]
        conn.rollback()
        return rows
    finally:
        conn.close()


def get_project(user_id: str, project_id: str) -> Optional[dict[str, Any]]:
    """读**任意**一个属于该用户的项目（含已关闭）。不属于该用户 → None。"""
    if not user_id or not project_id:
        return None
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT project_id, user_id, tier, status, initial_capital, currency,
                       market_scope, version, run_mode, opened_at, closed_at, close_reason
                  FROM fin_project
                 WHERE project_id = %s AND user_id = %s
                """,
                (project_id, user_id),
            )
            row = cur.fetchone()
            if not row:
                conn.rollback()
                return None
            project = _row_to_dict(row)
            param = _param_row(cur, project_id)
        conn.rollback()
        return {"project": project, "param": param}
    finally:
        conn.close()
