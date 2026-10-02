"""智能炒股 · 自动交易页的三个写操作（M6）。

三个动作都**只改 `fin_param`**（开户时的那套参数），并把每一次改动追加进
`fin_param_change_log` —— 那张表只追加、不给改，是「谁在什么时候把刹车松了/紧了」
的唯一权威（`05 §3.2` M-16 的验收口径）。

| 动作 | 改什么 | 下一笔生效点 |
|---|---|---|
| 总开关 `auto_enabled` | 布尔 | **下一次 `decide` 时点**：fin-worker 读它，false 时不产生新委托 |
| 策略切换 `strategies` | 列表里哪一个是 active | 下一次 `decide`：`build_decision` 按 active 的策略 key 出意图 |
| 风险档位 `risk_tier` | 单票上限 / 两条熔断线 | 下一次下单前的风控校验 |

**DDL 随代码走**（仓内铁律：`db/migrations/*.sql` 对已有部署不生效）：
新增两列由本模块的 `ensure_schema()` 幂等补齐，`db/migrations/0027_fin_control.sql`
只是给全新安装与留档用，两处保持一致。
"""

from __future__ import annotations

from typing import Any, Optional

import psycopg2
import psycopg2.extras

from app.services.fin import risk

# 幂等 DDL。放在模块里、由 `ensure_schema()` 每进程跑一次 —— 与
# `report.ensure_columns` 同一套路数（部署后、迁移跑完之前也不该 500）。
_DDL = [
    "ALTER TABLE fin_param ADD COLUMN IF NOT EXISTS auto_enabled BOOLEAN NOT NULL DEFAULT true",
    "ALTER TABLE fin_param ADD COLUMN IF NOT EXISTS risk_tier TEXT",
]

_schema_done = False


def ensure_schema(conn) -> None:
    """补两列（幂等）。**每进程只跑一次**，与 `agent_run._conn` 同一个理由：
    每次连库都 ALTER 会在有 idle in transaction 的连接时排队拿 ACCESS EXCLUSIVE。"""
    global _schema_done
    if _schema_done:
        return
    with conn.cursor() as cur:
        for stmt in _DDL:
            cur.execute(stmt)
    conn.commit()
    _schema_done = True


def _param_row(cur, project_id: str) -> Optional[dict[str, Any]]:
    cur.execute(
        """
        SELECT project_id, auto_enabled, risk_tier, strategies,
               max_position_pct, daily_loss_halt_pct, account_drawdown_halt_pct
          FROM fin_param
         WHERE project_id = %s
        """,
        (project_id,),
    )
    return cur.fetchone()


def _log(cur, project_id: str, actor: str, field: str, old: Any, new: Any) -> None:
    """往 `fin_param_change_log` 追加一行。只追加，不回改（这是留痕的全部意义）。"""
    cur.execute(
        """
        INSERT INTO fin_param_change_log (project_id, actor, field, old_value, new_value)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (project_id, actor, field,
         psycopg2.extras.Json(old), psycopg2.extras.Json(new)),
    )


def _jsonable(value: Any) -> Any:
    from decimal import Decimal
    if isinstance(value, Decimal):
        return float(value)
    return value


# ── 总开关 ────────────────────────────────────────────────────────────────

def set_auto_enabled(conn, project_id: str, enabled: bool, actor: str) -> dict[str, Any]:
    """开 / 关自动交易总开关。

    **关掉之后不再产生新委托**：`fin-worker` 的 `decide` 时点在出意图之前读这一列，
    为 false 时不下单（改的是 fin-worker 的 `build_decision` 活动，见那边注释）。
    已有持仓与已成交记录**一个字节都不动** —— 停的是后续操作，不是清仓。
    """
    ensure_schema(conn)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        row = _param_row(cur, project_id)
        if not row:
            raise LookupError(f"项目参数不存在：{project_id}")
        old = bool(row["auto_enabled"])
        changed = old != enabled
        if changed:
            cur.execute("UPDATE fin_param SET auto_enabled = %s, updated_at = now() "
                        "WHERE project_id = %s", (enabled, project_id))
            _log(cur, project_id, actor, "auto_enabled", old, enabled)
    conn.commit()
    return {"project_id": project_id, "auto_enabled": enabled, "changed": changed}


# ── 策略切换 ──────────────────────────────────────────────────────────────

def active_strategy(param: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """当前生效的策略条目。

    `strategies` 是 `[{key, name, params, version, active?}]`。**有一条标了
    `active` 就用它，没有就用第一条** —— 开户写进去的第一条本来就是默认策略，
    老数据因此不必迁移。`fin-worker` 与本模块共用这一份判据（同一个函数）。
    """
    items = list((param or {}).get("strategies") or [])
    if not items:
        return None
    for item in items:
        if isinstance(item, dict) and item.get("active"):
            return item
    return items[0] if isinstance(items[0], dict) else None


def set_strategy(conn, project_id: str, key: str, actor: str) -> dict[str, Any]:
    """切换生效策略。**下一次 `decide` 时点生效**（不是当场补一笔）。"""
    ensure_schema(conn)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        row = _param_row(cur, project_id)
        if not row:
            raise LookupError(f"项目参数不存在：{project_id}")
        items = list(row["strategies"] or [])
        keys = [i.get("key") for i in items if isinstance(i, dict)]
        if key not in keys:
            raise ValueError(f"这个项目没有开放策略 {key!r}（可选 {', '.join(k or '?' for k in keys)}）")
        before = active_strategy({"strategies": items}) or {}
        changed = before.get("key") != key
        new_items = [{**i, "active": (i.get("key") == key)} for i in items if isinstance(i, dict)]
        if changed:
            cur.execute("UPDATE fin_param SET strategies = %s, updated_at = now() WHERE project_id = %s",
                        (psycopg2.extras.Json(new_items), project_id))
            _log(cur, project_id, actor, "active_strategy", before.get("key"), key)
        after = active_strategy({"strategies": new_items})
    conn.commit()
    return {
        "project_id": project_id,
        "active_strategy": after,
        "changed": changed,
        "effective": "下一次决策（下一个 decide 时点）生效",
    }


# ── 风险档位（单向棘轮）──────────────────────────────────────────────────

def apply_risk_tier(conn, project_id: str, tier: str, *, confirm: bool, actor: str) -> dict[str, Any]:
    """套用风险档位。

    **只能收紧**（`03 §七`）：逐字段比，只要有一个字段变松就必须 `confirm=True`。
    没有 confirm 时抛 `ValueError`，由路由翻成 409 并把「哪一条被放宽了」原样带回前端，
    二次确认框里照原话显示。
    """
    ensure_schema(conn)
    try:
        target = risk.preset(tier)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        row = _param_row(cur, project_id)
        if not row:
            raise LookupError(f"项目参数不存在：{project_id}")
        current = {k: row[k] for k in risk.PRESETS[tier].keys()}
        diff = risk.compare(current, target)
        if diff["loosened"] and not confirm:
            raise PermissionError({
                "message": "这次调整会放宽风控上限，需要二次确认",
                "loosened": diff["loosened"],
                "changes": diff["changes"],
            })
        for item in diff["changes"]:
            if item["direction"] == "same":
                continue
            cur.execute(
                f"UPDATE fin_param SET {item['field']} = %s, updated_at = now() WHERE project_id = %s",
                (item["new"], project_id),
            )
            _log(cur, project_id, actor, item["field"], item["old"], item["new"])
        old_tier = row["risk_tier"]
        cur.execute("UPDATE fin_param SET risk_tier = %s, updated_at = now() WHERE project_id = %s",
                    (tier, project_id))
        if old_tier != tier:
            _log(cur, project_id, actor, "risk_tier", old_tier, tier)
        after = _param_row(cur, project_id)
    conn.commit()
    return {
        "project_id": project_id,
        "risk_tier": tier,
        "label": risk.TIER_LABEL[tier],
        "applied": {k: _jsonable(after[k]) for k in target.keys()},
        "changes": diff["changes"],
        "confirmed_loosen": bool(diff["loosened"]) and confirm,
        "tightens": diff["tightens"],
    }
