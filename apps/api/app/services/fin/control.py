"""智能炒股 · `fin_param` 的**唯一写入口**（M6 + 第四段 R8）。

本模块是**全仓唯一**改 `fin_param` 参数的地方。三类动作都**只改 `fin_param`**，并把每一次
改动追加进 `fin_param_change_log` —— 那张表只追加、不给改，是「谁在什么时候把刹车松了/紧了」
的唯一权威（`05 §3.2` M-16 的验收口径）。

| 动作 | 改什么 | 下一笔生效点 |
|---|---|---|
| 总开关 `auto_enabled` | 布尔 | **下一次 `decide` 时点**：fin-worker 读它，false 时不产生新委托 |
| 策略切换 `set_strategy` | 列表里哪一个是 active（**只切 key，不改参数**） | 下一次 `decide`：`build_decision` 按 active 的策略 key 出意图 |
| 风险档位 `apply_risk_tier` | 单票上限 / 两条熔断线 | 下一次下单前的风控校验 |
| **`activate_candidate`**（R8 新增） | **注册候选版本 + 切换**（CAS + 写日志 + 切 active，**四步同事务**） | 下一次 `decide` |
| **`restore_base_config`**（R8 新增） | 回滚到**上一已验证版本** + 停模拟下单 | 下一次 `decide` |

## R8 的两条铁律（`记忆系统追加规则.md` §五 第 7 条）

1. **参数只能经唯一写入口。** 改 `fin_param` 一律走本模块 —— **任何模块都不许直改
   `fin_param.strategies`**（`grep` 守护见 `tests/test_fin_evolution_apply.py`）。
   `activate_candidate` 在**一个事务里**做四件事：CAS（`SELECT … FOR UPDATE`）→ 注册候选版本
   （新 key 追加进 `strategies[]`）→ 写 `fin_param_change_log`（逐字段 `old → new`）→ 切 active key。
   任何一步失败 → **整笔回滚**；**绝不出现「配置已改而日志缺失」**（`03 §4-D` 原话）。
2. **回滚只能回到「上一个已验证版本」**，方向永远是**回到已验证版本**（风险只降不升）；
   目标 key 由调用方（`evolution`）从**版本链**核验后传入，本模块只负责「它确实在
   `strategies[]` 里」这最后一道。回滚**一并停掉该项目的模拟下单**（`auto_enabled=false`，
   复用既有的 `halted` 形态）。

**配置哈希的口径只有一份**：`evolution.config_from_row` / `evolution.hash_config`。
本模块**晚导入**它们（函数体内 `from … import`）—— 既不复制一份口径，也避开
`evolution → control`（生效调用）与 `control → evolution`（哈希）的模块级循环。

**DDL 单一来源**（L02）：`fin_param` 的 `auto_enabled` / `risk_tier` 两列
**只由迁移 `db/migrations/0027_fin_control.sql` 定义** —— api 启动时 `app.migrate`
按 `schema_migrations` 账本增量执行（`boot.sh` 里 `set -e`，迁移失败 api 起不来）。
本模块原来还抄了一份 `_DDL` + `ensure_schema()`，与 `0027` 两处定义同一事实，
L02 已删除（守护用例 `tests/test_l02_single_source.py` 盯着它不许回来）。
"""

from __future__ import annotations

from typing import Any, Optional

import psycopg2
import psycopg2.extras

from app.services.fin import risk


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


def _record_activated(cur, project_id: str, actor: str, *, payload: dict[str, Any]) -> None:
    """生效事务内追加一条 `activated` 事件（L04 · 策略服务的审计侧记）。

    **晚导入** `strategy`（避开循环，与 `_evo()` 同一手法）。它内部用 SAVEPOINT 兜错 ——
    这条侧记失败**不拖垮**生效本身（登记表还没建的老库也能照常切策略）。
    """
    from app.services.fin import strategy as strategy_svc
    vid = strategy_svc.active_version_id(cur, project_id)
    if not vid:
        return                      # 老库那个自由字符串 "v1" 不是版本键，不假装
    strategy_svc.record_activated_event(
        cur, candidate_id=vid, strategy_key=str(payload.get("strategy_key") or ""),
        project_id=project_id, payload=payload, actor=actor)


def set_strategy(conn, project_id: str, key: str, actor: str) -> dict[str, Any]:
    """切换生效策略。**下一次 `decide` 时点生效**（不是当场补一笔）。"""
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
            # L04：同一事务里追加 activated 事件（审计「哪一版曾生效」）。
            _record_activated(cur, project_id, actor,
                              payload={"strategy_key": key, "from_key": before.get("key"),
                                       "to_key": key, "via": "set_strategy"})
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


# ════════════════════════════════════════════════════════════════════════
# R8 · 唯一参数写入口：注册候选版本 + 切换 / 回滚到上一已验证版本
# ════════════════════════════════════════════════════════════════════════


class CasMismatchError(RuntimeError):
    """CAS 校验失败：基线配置在提案之后被改过（并发人工改策略 / 改档位）。

    这是 `03 §6` 点名的必测场景：旧提案**不得**在动过的基线上生效。
    """


# `fin_param` 里对应「顶层白名单参数」的列（其余白名单参数落在 `strategies[].params`）。
# 与 `evolution._RISK_FIELDS` / `evolution._STRATEGY_FIELDS` 里的**顶层那六个**一一对应；
# 本模块的守护用例（`test_fin_evolution_apply.py::test_top_fields_cover_whitelist`）盯着它，
# 加了新的顶层白名单字段而忘了这里会当场红。
_TOP_FIELDS = (
    "max_position_pct", "daily_loss_halt_pct", "account_drawdown_halt_pct",
    "stop_loss_pct", "take_profit_pct", "hold_days_max",
)


def _evo():
    """晚导入 `evolution`（哈希 / 配置口径的唯一实现）。见模块文档「配置哈希的口径只有一份」。"""
    from app.services.fin import evolution as evolution_svc
    return evolution_svc


def _locked_config_row(cur, project_id: str) -> dict[str, Any]:
    """`SELECT … FOR UPDATE` 取 `fin_param` 当前行（含构成本配置的全部列）。

    **锁住它**是 CAS 的关键：并发的人工改策略若已提交，这里读到的就是**改过之后**的行，
    哈希对不上 → 拒绝旧提案。不用 `FOR UPDATE` 的读写之间那道缝，正是 CAS 要堵的东西。
    """
    evo = _evo()
    cols = ", ".join(evo.CONFIG_COLUMNS)
    cur.execute(
        f"SELECT project_id, auto_enabled, risk_tier, {cols} "
        "FROM fin_param WHERE project_id = %s FOR UPDATE",
        (project_id,),
    )
    row = cur.fetchone()
    if not row:
        raise LookupError(f"项目参数不存在：{project_id}")
    return dict(row)


def _hash_of_row(row: dict[str, Any]) -> str:
    evo = _evo()
    return evo.hash_config(evo.config_from_row(row))


def _readback_hash(cur, project_id: str) -> str:
    """事务内**读回**当前行的配置哈希（改完之后的真值，不靠估算）。"""
    evo = _evo()
    cols = ", ".join(evo.CONFIG_COLUMNS)
    cur.execute(f"SELECT {cols} FROM fin_param WHERE project_id = %s", (project_id,))
    return evo.hash_config(evo.config_from_row(dict(cur.fetchone())))


def _same_num(a: Any, b: Any) -> bool:
    """两个数按规范形比（`0.10` / `Decimal('0.1')` / `0.1` 同值）—— 口径同 `evolution._canon_num`。"""
    return _evo()._canon_num(a) == _evo()._canon_num(b)


def activate_candidate(conn, project_id: str, *, candidate_config: dict[str, Any],
                       expected_base_config_hash: str, new_key: str, actor: str,
                       name: Optional[str] = None, version: Optional[str] = None,
                       within_txn=None) -> dict[str, Any]:
    """**R8 唯一参数写入口**：注册候选版本 + 切换（四步同事务）。

    1. **CAS**：`SELECT … FOR UPDATE` 取行 → 算它当前的配置哈希 → 与
       `expected_base_config_hash` 比；不一致抛 `CasMismatchError`（并发人工改过基线，
       旧提案**不得**生效）。
    2. **注册候选版本**：把候选参数作为**新条目**追加进 `strategies[]`
       （`{key, name, params, version}`，**新 key**，旧条目一个字节不动）。
    3. **写 `fin_param_change_log`**：逐字段记 `old → new`（顶层字段 + 新版本登记 + active 切换）。
    4. **切换 active key**：新条目标 `active`，其余取消。

    `within_txn`：可选的 `(cur, receipt) -> None` 回调，**在本事务内**执行（`evolution` 用它
    把 `applied` 事件与配置改动写进**同一笔事务** —— 否则「配置改了而事件没写」会切断回滚链）。

    返回 receipt（含 `from_key` / `to_key` / 两个哈希 / `active_config_hash`）。
    """
    evo = _evo()
    project_id = str(project_id or "").strip()
    new_key = str(new_key or "").strip()
    if not project_id or not new_key:
        raise ValueError("project_id 与 new_key 都不能为空")
    candidate_config = dict(candidate_config or {})
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            row = _locked_config_row(cur, project_id)

            # ① CAS —— 基线必须仍是提案针对的那一份
            current_hash = _hash_of_row(row)
            if current_hash != str(expected_base_config_hash or ""):
                raise CasMismatchError(
                    "基线配置已变（CAS 拒绝）：当前 "
                    f"{current_hash}，提案记的是 {expected_base_config_hash}。"
                    "有人在提案之后改过策略 / 档位 —— 旧提案不得生效。")

            items = [i for i in (row["strategies"] or []) if isinstance(i, dict)]
            before = active_strategy({"strategies": items}) or {}
            base_key = str(before.get("key") or "").strip()
            if new_key in [str(i.get("key") or "").strip() for i in items]:
                raise ValueError(f"策略 key 已存在：{new_key}（新版本必须用新 key，不动旧条目）")

            # ② 新条目 = 基线 active 策略的 params，覆盖上候选值（键形如 strategies.<base>.params.<name>）
            base_entry = next(
                (i for i in items if str(i.get("key") or "").strip() == base_key),
                items[0] if items else {},
            )
            new_entry = {
                "key": new_key,
                "name": name or base_entry.get("name") or new_key,
                "params": dict(base_entry.get("params") or {}),
                "version": version or base_entry.get("version") or "v1",
            }
            params = new_entry["params"]
            prefix = f"strategies.{base_key}.params."
            for field, value in candidate_config.items():
                if str(field).startswith(prefix):
                    params[str(field)[len(prefix):]] = _jsonable(value)
            new_items = [{**i, "active": False} for i in items] + [{**new_entry, "active": True}]

            # ③ 顶层字段逐列落库 + 逐字段留痕
            changed: list[dict[str, Any]] = []
            for field in _TOP_FIELDS:
                if field not in candidate_config:
                    continue
                new_value = _jsonable(candidate_config[field])
                old_value = row.get(field)
                if _same_num(old_value, new_value):
                    continue
                cur.execute(
                    f"UPDATE fin_param SET {field} = %s, updated_at = now() WHERE project_id = %s",
                    (new_value, project_id))
                _log(cur, project_id, actor, field, _jsonable(old_value), new_value)
                changed.append({"field": field, "old": _jsonable(old_value), "new": new_value})

            # ④ 注册新版本 + 切 active（两行日志：版本登记、active 切换）
            cur.execute("UPDATE fin_param SET strategies = %s, updated_at = now() "
                        "WHERE project_id = %s",
                        (psycopg2.extras.Json(new_items), project_id))
            _log(cur, project_id, actor, f"strategies.{new_key}", None, new_entry)
            _log(cur, project_id, actor, "active_strategy", base_key, new_key)
            # L04：同一事务里追加 activated 事件（审计「哪一版曾生效」）。
            _record_activated(cur, project_id, actor,
                              payload={"strategy_key": new_key, "from_key": base_key,
                                       "to_key": new_key, "via": "activate_candidate"})

            receipt = {
                "project_id": project_id,
                "from_key": base_key,
                "to_key": new_key,
                "base_config_hash": current_hash,
                "candidate_config_hash": evo.hash_config(candidate_config),
                "active_config_hash": _readback_hash(cur, project_id),
                "new_entry": new_entry,
                "changed_fields": changed,
                "active": active_strategy({"strategies": new_items}),
            }
            if within_txn is not None:
                within_txn(cur, receipt)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return receipt


def restore_base_config(conn, project_id: str, *, target_key: str, base_config: dict[str, Any],
                        expected_current_config_hash: str, actor: str, reason: str = "",
                        halt: bool = True, within_txn=None) -> dict[str, Any]:
    """**R8 回滚入口**：把配置恢复成 `base_config` 并切回 `target_key`（同一事务），
    同时**停掉该项目的模拟下单**（`halt=True` → `auto_enabled=false`）。

    - **CAS**：先在锁里算当前配置哈希，与 `expected_current_config_hash` 比 —— 不一致
      说明期间被人动过，回滚会踩掉别人的改动，拒绝。
    - **目标核验**：`target_key` 必须真的在 `strategies[]` 里（版本链的核验在
      `evolution.rollback_proposal` 里做，这里做最后一道「它确实存在」）。
    - **方向**：本函数**只恢复** `base_config` 给的顶层字段值，**不做任何放宽** —— 风控上限
      对新旧策略一律适用（`plan/R8.md` §一.4「策略切换 ≠ 可放宽风控」）。

    `within_txn` 同 `activate_candidate`（回滚时写 `rolled_back` 事件）。
    """
    project_id = str(project_id or "").strip()
    target_key = str(target_key or "").strip()
    if not project_id or not target_key:
        raise ValueError("project_id 与 target_key 都不能为空")
    base_config = dict(base_config or {})
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            row = _locked_config_row(cur, project_id)

            current_hash = _hash_of_row(row)
            if current_hash != str(expected_current_config_hash or ""):
                raise CasMismatchError(
                    "回滚前配置已变（CAS 拒绝）：当前 "
                    f"{current_hash}，期望 {expected_current_config_hash}。")

            items = [i for i in (row["strategies"] or []) if isinstance(i, dict)]
            keys = [str(i.get("key") or "").strip() for i in items]
            if target_key not in keys:
                raise ValueError(
                    f"回滚目标 {target_key!r} 不在 fin_param.strategies 里"
                    f"（现有 {', '.join(k or '?' for k in keys)}）—— 拒绝回滚。")
            before = active_strategy({"strategies": items}) or {}
            from_key = str(before.get("key") or "").strip()

            # 恢复顶层字段（只恢复、只可能更紧或原样；base_config 是回滚前记录的基线）
            restored: list[dict[str, Any]] = []
            for field in _TOP_FIELDS:
                if field not in base_config:
                    continue
                new_value = _jsonable(base_config[field])
                old_value = row.get(field)
                if _same_num(old_value, new_value):
                    continue
                cur.execute(
                    f"UPDATE fin_param SET {field} = %s, updated_at = now() WHERE project_id = %s",
                    (new_value, project_id))
                _log(cur, project_id, actor, field, _jsonable(old_value), new_value)
                restored.append({"field": field, "old": _jsonable(old_value), "new": new_value})

            new_items = [{**i, "active": (str(i.get("key") or "").strip() == target_key)}
                         for i in items]
            cur.execute("UPDATE fin_param SET strategies = %s, updated_at = now() "
                        "WHERE project_id = %s",
                        (psycopg2.extras.Json(new_items), project_id))
            if from_key != target_key:
                _log(cur, project_id, actor, "active_strategy", from_key, target_key)

            # 立即停止模拟下单（复用既有 halted 形态：build_decision 读 auto_enabled）
            halted = False
            if halt and bool(row.get("auto_enabled")):
                cur.execute("UPDATE fin_param SET auto_enabled = false, updated_at = now() "
                            "WHERE project_id = %s", (project_id,))
                _log(cur, project_id, actor, "auto_enabled", True, False)
                halted = True

            receipt = {
                "project_id": project_id,
                "from_key": from_key,
                "to_key": target_key,
                "reason": str(reason or ""),
                "restored_fields": restored,
                "halted": halted,
                "active_config_hash": _readback_hash(cur, project_id),
                "active": active_strategy({"strategies": new_items}),
            }
            if within_txn is not None:
                within_txn(cur, receipt)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return receipt
