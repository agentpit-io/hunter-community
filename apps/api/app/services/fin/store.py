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


# ── 市场集合（P1）────────────────────────────────────────────────────────
# 一个项目选了哪几个市场，真值在 `fin_project_market`（一行一个市场）。
# 这三个函数是**服务端硬校验**的落点 —— 界面传什么都不作数（红线：不信界面）。

def normalize_markets(markets: Any) -> list[str]:
    """校验并把市场集合归一成**去重 + 固定顺序**的列表。

    · 空（`[]` 或全空）→ `ValueError`「至少选择一个市场」；
    · 有非法值 → `ValueError` 点名（可选值写在文案里）；
    · 去重后按 `CN_A → HK → US` 固定顺序排 —— 同一集合只有一种存法。
    """
    if markets is None:
        raise ValueError("至少选择一个市场")
    if isinstance(markets, str):          # 防「传了个字符串」被逐字符拆开
        raise ValueError("markets 应是市场名数组")
    values = [m for m in markets if isinstance(m, str) and m.strip()]
    if not values:
        raise ValueError("至少选择一个市场")
    unknown = sorted({m for m in values if m not in tiers.MARKET_CURRENCY})
    if unknown:
        raise ValueError(
            f"未知市场：{', '.join(unknown)}（可选 {', '.join(tiers.MARKET_ORDER)}）"
        )
    seen = set(values)
    return [m for m in tiers.MARKET_ORDER if m in seen]


def scope_of(markets: list[str]) -> str:
    """市场集合 → `fin_project.market_scope` 摘要值（派生，不是真值）。

    单市场 → 该市场值；≥2 个市场 → `'MULTI'`。单市场时与原口径逐字节相同。
    """
    return markets[0] if len(markets) == 1 else "MULTI"


def currency_of(markets: list[str]) -> Optional[str]:
    """市场集合 → `fin_project.currency`：单市场取本币，≥2 个市场为 `NULL`。"""
    return tiers.MARKET_CURRENCY[markets[0]] if len(markets) == 1 else None


def _markets_rows(cur, project_id: str) -> list[dict[str, Any]]:
    """该项目的市场数组（固定顺序）。前端**只认这一个数组**（方案 §3.4）。"""
    cur.execute(
        """
        SELECT market, currency, initial_capital, opened_at
          FROM fin_project_market
         WHERE project_id = %s
         ORDER BY array_position(ARRAY['CN_A','HK','US'], market)
        """,
        (project_id,),
    )
    return [_row_to_dict(r) for r in cur.fetchall()]


def _insert_project_markets(cur, project_id: str, markets: list[str], capital: Any) -> None:
    """按市场逐行插 `fin_project_market`（每市场各一份档位本金、各用本币）。"""
    for market in markets:
        cur.execute(
            """
            INSERT INTO fin_project_market (project_id, market, initial_capital, currency)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (project_id, market) DO NOTHING
            """,
            (project_id, market, capital, tiers.MARKET_CURRENCY[market]),
        )


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


def _project_by_id(cur, user_id: str, project_id: str) -> Optional[dict[str, Any]]:
    """按 `(user_id, project_id)` 取一个项目（**含已关闭**）；不属于该用户 → None。"""
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
    """当前进行中的项目 + 它的参数 + 市场集合；没有返回 None。"""
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
            markets = _markets_rows(cur, project["project_id"])
        conn.rollback()  # 只读
        return {
            "project": project,
            "param": param,
            "markets": markets,
            "tier_template": tiers.build_template(project["tier"]),
        }
    finally:
        conn.close()


def _insert_project(
    cur, user_id: str, tier: str, markets: list[str]
) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    """往库里插一个项目 + 一套参数 + 市场集合（**不提交**，由调用方决定事务边界）。

    `markets` 必须是 `normalize_markets()` 过完的（去重 + 固定顺序）。
    """
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

    # 项目：本金由档位写死，写入后不可改；市场范围写**摘要值**
    # （单市场 = 该市场；≥2 = 'MULTI'），币种 ≥2 市场时为 NULL —— 真值在
    # 下面的 `fin_project_market`。这是「用户根本选不了港美股」那处缺口的修正
    # （原来 INSERT 里没有 market_scope，由 DB 默认成 CN_A）。
    project_id = "prj_" + uuid.uuid4().hex[:24]
    cur.execute(
        """
        INSERT INTO fin_project
          (project_id, user_id, tier, initial_capital, currency, market_scope)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING project_id, user_id, tier, status, initial_capital, currency,
                  market_scope, version, run_mode, opened_at, closed_at, close_reason
        """,
        (project_id, user_id, tier, template["initial_capital"],
         currency_of(markets), scope_of(markets)),
    )
    project = _row_to_dict(cur.fetchone())

    # 市场集合：一行一个市场，每市场各一份档位本金、各用本币
    _insert_project_markets(cur, project_id, markets, template["initial_capital"])

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


def create_project(user_id: str, tier: str, markets: Any = None) -> dict[str, Any]:
    """开户（幂等）：已存在 active 项目时**返回现成的那个**，不新建。

    这是向导第六步「提交 → 重复点」的路径 —— 重复提交不该给用户一个红色报错。
    **要换档位 / 开新账本走 `open_new_project()`**（M-16：先关旧的，再开新的）。

    `markets` 缺省 `["CN_A"]`（**老客户端行为逐字节不变**）；传别的集合时按服务端
    硬校验（非空 + 合法 + 去重后固定顺序）。**幂等口径不变**：已有 active 项目时
    返回现成的那个，不因这次传的 markets 不同而新建。
    """
    if tier not in tiers.TIER_ORDER:
        raise ValueError(f"未知档位：{tier!r}（可选 {', '.join(tiers.TIER_ORDER)}）")
    wanted = normalize_markets(["CN_A"] if markets is None else markets)

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            existing = _project_row(cur, user_id)
            if existing:
                param = _param_row(cur, existing["project_id"])
                markets_rows = _markets_rows(cur, existing["project_id"])
                conn.rollback()
                return {
                    "project": existing,
                    "param": param,
                    "markets": markets_rows,
                    "tier_template": tiers.build_template(existing["tier"]),
                    "created": False,
                }
            project, param = _insert_project(cur, user_id, tier, wanted)
            markets_rows = _markets_rows(cur, project["project_id"])
            template = tiers.build_template(tier)
        conn.commit()
        return {"project": project, "param": param, "markets": markets_rows,
                "tier_template": template, "created": True}
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


def open_new_project(
    user_id: str, tier: str, reason: str = "user_opened_new", markets: Any = None
) -> dict[str, Any]:
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
    wanted = normalize_markets(["CN_A"] if markets is None else markets)

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

            project, param = _insert_project(cur, user_id, tier, wanted)
            # 新项目的开立也要留痕（同一条日志表）
            cur.execute(
                """
                INSERT INTO fin_param_change_log (project_id, actor, field, old_value, new_value)
                VALUES (%s, %s, 'opened', NULL, %s)
                """,
                (project["project_id"], user_id, psycopg2.extras.Json({"tier": tier})),
            )
            # P1：新项目选的市场集合单独留一行（方案 §3.2）
            cur.execute(
                """
                INSERT INTO fin_param_change_log (project_id, actor, field, old_value, new_value)
                VALUES (%s, %s, 'markets', NULL, %s)
                """,
                (project["project_id"], user_id, psycopg2.extras.Json(wanted)),
            )
            markets_rows = _markets_rows(cur, project["project_id"])
            template = tiers.build_template(tier)
        conn.commit()
        return {
            "project": project,
            "param": param,
            "markets": markets_rows,
            "tier_template": template,
            "created": True,
            "closed_project": closed,
        }
    finally:
        conn.close()


def add_markets(user_id: str, project_id: str, markets: Any) -> dict[str, Any]:
    """**追加市场 · 只增不减**（方案 §3.3）。

    入参 `markets` 是**目标集合**（不是「新增那几个」）—— 必须包含项目现有的每一个
    市场；少一个就说明请求在隐含「移除某个市场」，服务端一律 **400**。这一条是
    「只增不减」的**落点**，界面不给移除控件只是双保险（`迭代完善追加规则.md` §三）。

    幂等：已存在的市场跳过（集合里传了也算数）。
    ⚠️ 新追加的市场**从追加时刻起开账**（`opened_at = now()`），**不回填历史** ——
    这段账本没有过去，不补记（与 `paper.seed_funding` 的「账本起点是显式的」同口径）。
    """
    if not project_id:
        raise ValueError("项目不存在")
    requested = normalize_markets(markets)

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            project = _project_by_id(cur, user_id, project_id)
            if not project:
                conn.rollback()
                return {}
            if project["status"] != "active":
                conn.rollback()
                raise ValueError("只能给进行中的项目追加市场；要换市场请开新项目")

            rows = _markets_rows(cur, project_id)
            current = [r["market"] for r in rows]
            # 「只增不减」的守门：传入集合必须是现有集合的超集
            if not set(current).issubset(set(requested)):
                conn.rollback()
                raise ValueError("只能增加市场，不能减少；要减少请关停旧项目、开新项目")

            added = [m for m in requested if m not in set(current)]
            if added:
                _insert_project_markets(
                    cur, project_id, added, tiers.build_template(project["tier"])["initial_capital"]
                )
                new_scope = scope_of(requested)
                cur.execute(
                    """
                    UPDATE fin_project
                       SET market_scope = %s, currency = %s
                     WHERE project_id = %s
                    """,
                    (new_scope, currency_of(requested), project_id),
                )
                cur.execute(
                    """
                    INSERT INTO fin_param_change_log (project_id, actor, field, old_value, new_value)
                    VALUES (%s, %s, 'markets', %s, %s)
                    """,
                    (project_id, user_id,
                     psycopg2.extras.Json(current), psycopg2.extras.Json(requested)),
                )
                project = _project_by_id(cur, user_id, project_id)
            markets_rows = _markets_rows(cur, project_id)
        conn.commit()
        return {
            "project": project,
            "markets": markets_rows,
            "added": added,
            "created": bool(added),
            "note": "追加的市场从此刻开始记账，不回填历史",
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
            rows = []
            for r in cur.fetchall():
                item = _row_to_dict(r)
                item["markets"] = _markets_rows(cur, item["project_id"])
                rows.append(item)
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
            project = _project_by_id(cur, user_id, project_id)
            if not project:
                conn.rollback()
                return None
            param = _param_row(cur, project_id)
            markets = _markets_rows(cur, project_id)
        conn.rollback()
        return {"project": project, "param": param, "markets": markets}
    finally:
        conn.close()
