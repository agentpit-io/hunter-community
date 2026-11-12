"""自有策略服务（五期 `L04`）· 身份证 + 版本锁 + 三个正式入口。

技术方案 §1.1 把「**策略代码与版本**」列为四大权威来源之一 —— 但五期开工前仓里根本没有
「策略服务」：唯一的策略是写死的示例策略（`apps/fin-worker/app/strategy/sample.py`），
策略身份是 `fin_param.strategies` 这个 JSONB 列里的 `{key, name, params, version:"v1"}`，
`version` 只是个**自由字符串**（没有版本表、没有内容哈希、没有「改了报错」的约束）。

## 本模块做什么

| 工具 | 语义 |
|---|---|
| `strategy.submit` | 登记一个候选版本（**未生效**）：算内容哈希 → 登记进 `fin_strategy_definition` → 追加 `submitted` 事件 |
| `strategy.get`    | 查状态（候选 / 生效中 / 已取消 / 曾生效已让位 / 被拒绝）：**从事件重建的投影** |
| `strategy.cancel` | **只能取消还没生效的候选**；已经生效的**拒绝** —— 那走 `L01` 那条回滚路 |

## 「不可变」靠机制，不靠自觉

登记表**只追加**：`BEFORE UPDATE OR DELETE` 触发器抛异常（迁移 `0049`）。「改策略」=
**登记一个新版本**（新 `strategy_version_id`），不是改老行。`strategy_version_id` 由
**内容哈希**推导（`version_id_of`），同一份内容重复登记得到同一个键（幂等），内容一变就是新键。

## 与四期 `R8` 的关系：**复用，不另起一套**

`R8` 已经做了「配置版本注册 + CAS 原子切换 + 紧急回滚」（`control.activate_candidate` /
`restore_base_config`）。本模块**不碰** `fin_param`，也**不实现第二套切换机制** ——
它只补上「**策略定义**」这一层（`R8` 只有「参数」这一层）。真正让某个版本生效，仍然走
`control.py` 那个**唯一写入口**；生效时 `control` 会在**同一事务**里追加一条 `activated` 事件
（`record_activated_event`），于是「哪一版曾生效」也有审计。

## 红线

- **不给策略开放松风控的口子**（红线 8）：`submit` 只认 `target='strategy'`；任何
  `target='risk'` **一律拒绝并记拒绝事件**（策略服务只回答「用哪个策略」，不回答「风控能不能松」）。
- **不是第二套调度**：本模块没有任何定时 / 触发，只有被调用的三个入口。
- **不改 `fin_param.strategies`**：唯一写入口是 `control.py`（红线 7）。
- **不碰量化/回测域那张 `strategy` 表**（`apps/api/sql/20260817_quant_strategy.sql`）。

## 内置示例策略

示例策略**降级为一个登记在册、可被替换的内置策略**（不是删掉）：`ensure_builtins()` 把
`tiers._STRATEGIES` 里的每个内置策略登记成一个 `origin='builtin'` 的定义，`source_ref` 指向
**当前唯一的执行体** `apps/fin-worker/app/strategy/sample.py:build_decision`。要「替换」它，
就 `submit` 一个 `source_ref` 不同（新内容 → 新版本键）的候选，再经唯一写入口切换 ——
**登记表就是「可被替换」的落点**。
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime
from typing import Any, Optional

import psycopg2
import psycopg2.extras

# 连接方式与 `app/services/fin/data_snapshot.py` / `store.py` 同口径：
# psycopg2 直连，模块级 `DATABASE_URL`（测试按同一套手法 patch 它）。
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://hunter:hunter@127.0.0.1:5432/hunter")

# 候选状态闭集（`get` 的投影值）。`candidate`/`active`/`cancelled` 是方案点名的三个；
# `superseded`（曾生效、现已让位）与 `rejected`（提交被拒，如放风控）是**如实的额外终局**，
# 不许把它们硬塞成三个之一（「空的比假的好」同一精神）。
STATUS_CANDIDATE = "candidate"
STATUS_ACTIVE = "active"
STATUS_CANCELLED = "cancelled"
STATUS_SUPERSEDED = "superseded"
STATUS_REJECTED = "rejected"
STATUS_REGISTERED = "registered"      # 已登记但从未 submit 过候选（内置版本就是这一档）

STATUS_LABEL = {
    STATUS_CANDIDATE: "候选（未生效）",
    STATUS_ACTIVE: "生效中",
    STATUS_CANCELLED: "已取消",
    STATUS_SUPERSEDED: "曾生效·现已让位",
    STATUS_REJECTED: "已拒绝",
    STATUS_REGISTERED: "已登记（未提交候选）",
}

# 唯一允许的提交目标：策略服务只回答「用哪个策略」。
ALLOWED_TARGETS = ("strategy",)

# 内置执行体：当前**唯一**的策略执行实现（示例策略）。内置登记都用它当 `source_ref`，
# 于是「这个登记行谁在执行」有据可查；要换执行体就 submit 一个 source_ref 不同的候选。
BUILTIN_SOURCE_REF = "apps/fin-worker/app/strategy/sample.py:build_decision"


def get_conn():
    """直连（口径同 `data_snapshot.get_conn`）。调用方负责 `commit` / `close`。"""
    return psycopg2.connect(DATABASE_URL)


class StrategyError(ValueError):
    """策略服务的输入 / 状态不合法（路由层翻 400）。"""


class RiskLooseningRefused(StrategyError):
    """红线 8：策略服务不处理「放松风控」的提交 —— 一律拒绝并记拒绝事件。"""


class CandidateIsActive(StrategyError):
    """`cancel` 一个**已经生效**的候选 —— 拒绝（那走回滚路，红线 9）。"""


# ── 游标辅助 ─────────────────────────────────────────────────────────────
#
# 本模块的函数可以收到**普通游标**（如 `main.py` 启动时的 `conn.cursor()`）或
# `RealDictCursor`（服务层内部）。读行一律走这两个辅助 —— 用同一个连接**另开一个**
# dict 游标，于是调用方传什么游标都不影响「按列名取值」。

def _row(cur, sql: str, params: tuple = ()) -> Optional[dict[str, Any]]:
    with cur.connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
        c.execute(sql, params)
        r = c.fetchone()
        return dict(r) if r is not None else None


def _rows(cur, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with cur.connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
        c.execute(sql, params)
        return [dict(r) for r in c.fetchall()]


# ── 内容哈希 / 稳定版本键 ────────────────────────────────────────────────

def canonical_content(*, strategy_key: str, name: str, source_ref: str,
                      params: Any) -> str:
    """把一份策略定义规范化成**稳定**字符串（同内容 → 同串 → 同哈希）。

    `sort_keys` + 紧凑分隔符 + `ensure_ascii=False`：参数键序不影响哈希，
    中文名不影响哈希。**任何一处参与定义的内容变了，哈希就变** —— 这正是
    「改策略 = 新版本」的判据。
    """
    payload = {
        "strategy_key": str(strategy_key or "").strip(),
        "name": str(name or "").strip(),
        "source_ref": str(source_ref or "").strip(),
        "params": _jsonable(params or {}),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _jsonable(value: Any) -> Any:
    """把值收敛成可 JSON 序列化的形状；不可序列化 → 抛 `StrategyError`（不猜）。"""
    try:
        # 借一次 JSON 往返做校验（Decimal / set / 自定义对象都会在这里失败）。
        return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, default=_no_default))
    except (TypeError, ValueError) as exc:
        raise StrategyError(f"参数不可序列化（{exc.__class__.__name__}: {exc}）") from exc


def _no_default(_obj: Any):  # pragma: no cover —— 只为让 json.dumps 抛 TypeError
    raise TypeError("不可序列化的值")


def content_hash_of(*, strategy_key: str, name: str, source_ref: str, params: Any) -> str:
    """定义内容的 sha256（十六进制）。"""
    canon = canonical_content(strategy_key=strategy_key, name=name,
                              source_ref=source_ref, params=params)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def version_id_of(*, strategy_key: str, name: str, source_ref: str, params: Any) -> str:
    """稳定版本键：`strv_` + 内容哈希前 24 位（十六进制）。

    **由内容决定**：同一份定义在任何进程 / 任何时间算出来都是同一个键；内容一变就是新键。
    这就是「版本不可改」的机械保证 —— 改一个字，键就换了，旧键那一行动不了。
    """
    return "strv_" + content_hash_of(strategy_key=strategy_key, name=name,
                                     source_ref=source_ref, params=params)[:24]


# ── 内置策略登记 ─────────────────────────────────────────────────────────

def builtin_definitions() -> list[dict[str, Any]]:
    """内置策略目录（唯一来源：`tiers._STRATEGIES` 的 key / name / params）。

    `tiers` 与本模块**互相引用**，所以这里**晚导入**（函数体内）—— `tiers` 在模块级
    import 本模块拿 `version_id_of`（纯函数），本模块在调用时才 import `tiers`。见模块文档。
    """
    from app.services.fin import tiers as tiers_svc

    out: list[dict[str, Any]] = []
    for key, meta in tiers_svc._STRATEGIES.items():
        out.append({
            "strategy_key": key,
            "name": meta["name"],
            "source_ref": BUILTIN_SOURCE_REF,
            "params": dict(meta.get("params") or {}),
            "note": ("内置示例策略登记（L04）：声明身份与参数；当前唯一执行体是固定示例策略 "
                     f"`{BUILTIN_SOURCE_REF}`（尚未有独立实现）。要替换它，submit 一个 "
                     "source_ref 不同的候选，再经唯一写入口切换。"),
        })
    return out


def ensure_builtins(cur) -> list[dict[str, Any]]:
    """**幂等**登记全部内置策略定义（`ON CONFLICT DO NOTHING`）。返回登记行列表。

    `origin='builtin'`，`created_by='system'`。重复调用不会多出行（唯一索引
    `(strategy_key, content_hash)` 兜住）—— 所以它可以在启动时、每次 submit 时安全重跑。
    """
    rows: list[dict[str, Any]] = []
    for d in builtin_definitions():
        rows.append(register(
            cur, strategy_key=d["strategy_key"], name=d["name"],
            source_ref=d["source_ref"], params=d["params"],
            origin="builtin", created_by="system", note=d.get("note")))
    return rows


def register(cur, *, strategy_key: str, name: str, source_ref: str, params: Any,
             origin: str = "user", created_by: str = "system",
             note: Optional[str] = None) -> dict[str, Any]:
    """登记一个策略定义（幂等）。返回落库 / 已存在的行。

    幂等键是 `strategy_version_id`（= 内容哈希），`ON CONFLICT DO NOTHING` 后回读 ——
    于是「同一份内容登记两次」不会多出一行、也不会报错（`submit` 同一候选两次得到同一个键）。
    """
    strategy_key = str(strategy_key or "").strip()
    name = str(name or "").strip()
    source_ref = str(source_ref or "").strip()
    if not strategy_key or not name or not source_ref:
        raise StrategyError("strategy_key / name / source_ref 都不能为空")
    if origin not in ("builtin", "user", "evolution"):
        raise StrategyError(f"未知来源 {origin!r}（只认 builtin / user / evolution）")
    params = _jsonable(params or {})
    if not isinstance(params, dict):
        raise StrategyError("params 必须是对象")

    vid = version_id_of(strategy_key=strategy_key, name=name,
                        source_ref=source_ref, params=params)
    chash = content_hash_of(strategy_key=strategy_key, name=name,
                            source_ref=source_ref, params=params)
    cur.execute(
        """
        INSERT INTO fin_strategy_definition
          (strategy_version_id, strategy_key, name, source_ref, params,
           content_hash, origin, note, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (strategy_version_id) DO NOTHING
        """,
        (vid, strategy_key, name, source_ref, psycopg2.extras.Json(params),
         chash, origin, note, created_by),
    )
    return _row(
        cur,
        "SELECT strategy_version_id, strategy_key, name, source_ref, params, "
        "       content_hash, origin, note, created_by, created_at "
        "  FROM fin_strategy_definition WHERE strategy_version_id = %s",
        (vid,),
    )


# ── 候选事件（追加式）────────────────────────────────────────────────────

def record_event(cur, *, candidate_id: str, strategy_key: str, kind: str,
                 project_id: Optional[str] = None, payload: Optional[dict] = None,
                 actor: str = "system") -> dict[str, Any]:
    """追加一条候选状态事件（**只追加**，无 UPDATE / DELETE）。"""
    if kind not in ("submitted", "cancelled", "activated", "rejected"):
        raise StrategyError(f"未知候选事件 {kind!r}")
    event_id = "svc_" + uuid.uuid4().hex[:24]
    cur.execute(
        """
        INSERT INTO fin_strategy_candidate
          (event_id, candidate_id, strategy_key, project_id, kind, payload, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (event_id, candidate_id, strategy_key, project_id, kind,
         psycopg2.extras.Json(_jsonable(payload or {})), actor),
    )
    return {"event_id": event_id, "candidate_id": candidate_id, "kind": kind,
            "strategy_key": strategy_key, "project_id": project_id}


def _events_of(cur, candidate_id: str) -> list[dict[str, Any]]:
    return _rows(
        cur,
        "SELECT event_id, candidate_id, strategy_key, project_id, kind, payload, "
        "       created_by, created_at FROM fin_strategy_candidate "
        " WHERE candidate_id = %s ORDER BY created_at ASC, event_id ASC",
        (candidate_id,),
    )


# ── 当前生效版本 ─────────────────────────────────────────────────────────

def _active_entry(cur, project_id: str) -> Optional[dict[str, Any]]:
    """项目 `fin_param.strategies` 里标了 `active` 的那一条（判据唯一实现在 `control`）。"""
    from app.services.fin import control as control_svc   # 晚导入，避开循环

    row = _row(cur, "SELECT strategies FROM fin_param WHERE project_id = %s", (project_id,))
    if not row:
        return None
    return control_svc.active_strategy({"strategies": list(row.get("strategies") or [])})


def _definition_by_version(cur, version_id: str) -> Optional[dict[str, Any]]:
    return _row(
        cur,
        "SELECT strategy_version_id, strategy_key, name, source_ref, params, content_hash, "
        "       origin, note, created_by, created_at FROM fin_strategy_definition "
        " WHERE strategy_version_id = %s",
        (version_id,),
    )


def _latest_builtin(cur, strategy_key: str) -> Optional[dict[str, Any]]:
    return _row(
        cur,
        "SELECT strategy_version_id, strategy_key, name, source_ref, params, content_hash, "
        "       origin, note, created_by, created_at FROM fin_strategy_definition "
        " WHERE strategy_key = %s AND origin = 'builtin' "
        " ORDER BY created_at DESC, strategy_version_id DESC LIMIT 1",
        (strategy_key,),
    )


def active_version(cur, project_id: str) -> dict[str, Any]:
    """项目当前生效的策略版本（`fin-worker` 的 `strategy_version` 从这里来）。

    解析顺序（**每一步都如实记 `resolved_by`**）：

    1. `fin_param.strategies` 里 active 那条的 `version` 若**已经是一个登记行**（且 key 对得上）
       → 直接用它（`declared_version`）；
    2. 否则按 active 的 `key` 找**最新的内置登记行**（`builtin_key`）—— 老库的 `version`
       还是自由字符串 `"v1"` 时的兼容路径；**不编版本键**，只是把「这个 key 的当前内置版本」
       解析出来；
    3. 都没有 → `unregistered`：**如实返回声明的原值**，`definition=None`
       （不假装它登记过）。

    注册表为空（全新库、`ensure_builtins` 还没跑）时**自愈一次**再解析。
    """
    entry = _active_entry(cur, project_id)
    declared_key = str((entry or {}).get("key") or "").strip()
    declared_version = str((entry or {}).get("version") or "").strip()

    row = _definition_by_version(cur, declared_version) if declared_version else None
    if row and (not declared_key or row["strategy_key"] == declared_key):
        resolved_by = "declared_version"
    else:
        row = _latest_builtin(cur, declared_key) if declared_key else None
        resolved_by = "builtin_key" if row else "unregistered"
        if row is None:
            # 注册表可能是空的（全新库）—— 幂等登记一次再试。
            ensure_builtins(cur)
            row = _latest_builtin(cur, declared_key) if declared_key else None
            if row is not None:
                resolved_by = "builtin_key"

    if row is None:
        return {
            "project_id": project_id,
            "declared_key": declared_key or None,
            "strategy_key": declared_key or None,
            "strategy_version": declared_version or None,
            "definition": None,
            "resolved_by": "unregistered",
            "note": ("这个策略键没有可解析的登记版本（fin_param 里的值不是登记行，"
                     "且该 key 没有内置登记）—— 原样返回声明值，不编版本键。"),
        }
    return {
        "project_id": project_id,
        "declared_key": declared_key or None,
        "strategy_key": row["strategy_key"],
        "strategy_version": row["strategy_version_id"],
        "definition": row,
        "resolved_by": resolved_by,
        "note": ("生效版本取自登记行的稳定版本键（L04 策略服务）。"
                 + ("（按 key 解析到内置版本 —— 老库的 version 还是自由字符串。）"
                    if resolved_by == "builtin_key" else "")),
    }


# ── 三个正式入口 ─────────────────────────────────────────────────────────

def submit(conn, *, project_id: str, strategy_key: str, name: str, source_ref: str,
           params: Any, actor: str = "system", target: str = "strategy",
           note: Optional[str] = None, origin: str = "user") -> dict[str, Any]:
    """`strategy.submit`：登记一个候选版本（**未生效**）+ 追加 `submitted` 事件。

    **未生效是真的**：本函数**不碰 `fin_param`**、不切换 active —— 生效走 `control.py`
    那个唯一写入口（红线 7）。**提交候选 ≠ 自动生效**：自动生效是 `L13` 的另一条路
    （`system:auto-apply`，只对「收紧」方向；见 `evolution.auto_apply_proposal`）。

    放风控的提交（`target != 'strategy'`）**一律拒绝并记拒绝事件**（红线 8）。
    """
    strategy_key = str(strategy_key or "").strip()
    if not strategy_key:
        raise StrategyError("strategy_key 不能为空")
    if target not in ALLOWED_TARGETS:
        # 记拒绝事件要用到版本键，所以先算（内容非法时抛 StrategyError，不记）。
        vid = version_id_of(strategy_key=strategy_key, name=name or strategy_key,
                            source_ref=source_ref or "(未提供)", params=params or {})
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                record_event(cur, candidate_id=vid, strategy_key=strategy_key,
                             kind="rejected", project_id=project_id or None,
                             payload={"reason": "策略服务不处理风控目标（红线 8）",
                                      "target": target},
                             actor=actor)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        raise RiskLooseningRefused(
            f"策略服务只回答「用哪个策略」，不回答「风控能不能松」—— 拒绝 target={target!r}"
            "（红线 8）。放风控的提交一律拒绝并记拒绝事件。")

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            row = register(cur, strategy_key=strategy_key, name=name or strategy_key,
                           source_ref=source_ref, params=params, origin=origin,
                           created_by=actor, note=note)
            record_event(cur, candidate_id=row["strategy_version_id"], strategy_key=strategy_key,
                         kind="submitted", project_id=project_id or None,
                         payload={"source_ref": row["source_ref"], "origin": row["origin"]},
                         actor=actor)
            status = _project_status(_events_of(cur, row["strategy_version_id"]),
                                     active_version_id(cur, project_id),
                                     row["strategy_version_id"])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return _candidate_view(row, status, project_id)


def get(conn, *, candidate_id: Optional[str] = None,
        project_id: Optional[str] = None) -> dict[str, Any]:
    """`strategy.get`：查候选状态；给了 `project_id` 则另附「当前生效版本」。

    **状态是从事件重建的投影**（没有 status 列）。`candidate_id` 与 `project_id`
    至少给一个。
    """
    candidate_id = str(candidate_id or "").strip()
    project_id = str(project_id or "").strip()
    if not candidate_id and not project_id:
        raise StrategyError("candidate_id 与 project_id 至少给一个")
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        active_id = active_version_id(cur, project_id) if project_id else None
        result: dict[str, Any] = {}
        if project_id:
            result["active"] = active_version(cur, project_id)
        if candidate_id:
            row = _definition_by_version(cur, candidate_id)
            events = _events_of(cur, candidate_id)
            if row is None and not events:
                raise LookupError(f"没有这个候选 / 策略版本：{candidate_id}")
            if row is None:
                # 只有拒绝事件、没有登记行（放风控被拒的提交就长这样）。
                st = _project_status(events, active_id, candidate_id)
                result["candidate"] = {
                    "candidate_id": candidate_id,
                    "strategy_key": events[0]["strategy_key"] if events else None,
                    "definition": None,
                    "status": st,
                    "status_label": STATUS_LABEL[st],
                    "events": [_event_view(e) for e in events],
                }
            else:
                status = _project_status(events, active_id, candidate_id)
                result["candidate"] = _candidate_view(row, status, project_id, events)
        elif project_id:
            # 没给候选 id：列出该项目提交过的候选（按事件里的 project_id）。
            cur.execute(
                "SELECT DISTINCT candidate_id FROM fin_strategy_candidate "
                " WHERE project_id = %s ORDER BY candidate_id LIMIT 200", (project_id,))
            cands = []
            for r in cur.fetchall():
                cid = r["candidate_id"]
                row = _definition_by_version(cur, cid)
                evs = _events_of(cur, cid)
                st = _project_status(evs, active_id, cid)
                key = (row or {}).get("strategy_key") or (evs[0]["strategy_key"] if evs else None)
                cands.append({
                    "candidate_id": cid,
                    "strategy_key": key,
                    "status": st,
                    "status_label": STATUS_LABEL[st],
                })
            result["candidates"] = cands
    return result


def cancel(conn, *, candidate_id: str, actor: str = "system",
           project_id: Optional[str] = None) -> dict[str, Any]:
    """`strategy.cancel`：取消一个**还没生效**的候选。

    **已经生效的不能 cancel**（红线 9）：那要走 `control` 那条回滚路
    （`rollback_proposal` / `restore_base_config`，回**上一个已验证版本**）。
    已是 `cancelled` 的重复 cancel 幂等（再追加一条 `cancelled`，状态不变）。
    """
    candidate_id = str(candidate_id or "").strip()
    if not candidate_id:
        raise StrategyError("candidate_id 不能为空")
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        row = _definition_by_version(cur, candidate_id)
        events = _events_of(cur, candidate_id)
        if row is None and not events:
            raise LookupError(f"没有这个候选 / 策略版本：{candidate_id}")
        kinds = [e["kind"] for e in events]
        strategy_key = (row or {}).get("strategy_key") or (events[0]["strategy_key"] if events else "")
        # 候选的生效判据：它的版本键 == **它所属项目**当前生效的版本键（与事件无关 ——
        # 内置版本可能从没 submit 过，却正是当前生效的那一版）。
        # 项目从参数取；没给就用事件里记的项目。
        proj = project_id or next((e.get("project_id") for e in reversed(events) if e.get("project_id")), None)
        if not proj:
            raise StrategyError(
                "cancel 缺 project_id，无法确认它还没生效 —— 不冒险删一个可能已生效的版本（红线 9）。")
        if active_version_id(cur, proj) == candidate_id:
            raise CandidateIsActive(
                f"这个候选（{candidate_id}）**已经生效**，不能 cancel —— "
                "要停它得走回滚路（回上一个已验证版本，红线 9），不是取消一个未生效的候选。")
        # 只有 submit 过的才是「候选」；从没提交的登记行不是候选。
        if "submitted" not in kinds:
            raise StrategyError(
                f"{candidate_id} 没有提交记录（不是候选）—— 只有 submit 过的候选才能 cancel。")
        status = _project_status(events, active_version_id(cur, proj), candidate_id)
        record_event(cur, candidate_id=candidate_id, strategy_key=strategy_key,
                     kind="cancelled", project_id=proj,
                     payload={"prior_status": status}, actor=actor)
        new_status = _project_status(_events_of(cur, candidate_id),
                                     active_version_id(cur, proj), candidate_id)
    conn.commit()
    return {
        "candidate_id": candidate_id,
        "strategy_key": strategy_key,
        "status": new_status,
        "status_label": STATUS_LABEL[new_status],
        "cancelled": True,
    }


# ── 投影 / 视图 ──────────────────────────────────────────────────────────

def active_version_id(cur, project_id: Optional[str]) -> Optional[str]:
    """项目当前生效的策略**版本键**（已登记的 `strv_…`），解析不出登记行 → `None`。

    **只返回登记行里的键**（`active_version_id` 与 `active_version` 的唯一区别就在这里：
    后者解析不出时会如实带上声明的原值）。`control.py` 在生效事务里用它追加 `activated`
    事件 —— 登记的键才记，老库那个自由字符串 `"v1"` 不假装成版本键。
    """
    if not project_id:
        return None
    entry = _active_entry(cur, project_id)
    if not entry:
        return None
    key = str(entry.get("key") or "").strip()
    version = str(entry.get("version") or "").strip()
    if version:
        row = _definition_by_version(cur, version)
        if row is not None and (not key or row["strategy_key"] == key):
            return version
    row = _latest_builtin(cur, key) if key else None
    return row["strategy_version_id"] if row else None


def _project_status(events: list[dict[str, Any]], active_version_id: Optional[str],
                    candidate_id: Optional[str] = None) -> str:
    """从事件重建候选状态（审计以事件为准，状态只是投影）。

    `candidate_id` 显式传入：一个**从没 submit 过**的登记行（内置版本）可能正是当前生效的
    那一版 —— 于是「生效中」的判据（版本键 == 项目当前生效版本键）**必须**独立于事件，
    否则 `cancel` 会漏判（这是真出过的 bug：生效版本没有 submitted 事件 → 被当成候选放行）。
    """
    cid = candidate_id or (events[0]["candidate_id"] if events else None)
    kinds = [e["kind"] for e in events]
    if active_version_id and cid and cid == active_version_id and "cancelled" not in kinds:
        return STATUS_ACTIVE
    if "cancelled" in kinds:
        return STATUS_CANCELLED
    if not events:
        return STATUS_REGISTERED           # 只登记过、从没提交候选
    if "activated" in kinds:
        return STATUS_SUPERSEDED
    if "submitted" not in kinds:
        return STATUS_REJECTED
    return STATUS_CANDIDATE


def _event_view(e: dict[str, Any]) -> dict[str, Any]:
    payload = e.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            payload = None
    created = e.get("created_at")
    return {
        "event_id": e["event_id"],
        "kind": e["kind"],
        "project_id": e.get("project_id"),
        "created_by": e.get("created_by"),
        "created_at": created.isoformat() if isinstance(created, datetime) else created,
        "payload": payload,
    }


def _candidate_view(row: dict[str, Any], status: str,
                    project_id: Optional[str] = None,
                    events: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    created = row.get("created_at")
    return {
        "candidate_id": row["strategy_version_id"],
        "strategy_version_id": row["strategy_version_id"],
        "strategy_key": row["strategy_key"],
        "name": row["name"],
        "source_ref": row["source_ref"],
        "params": row.get("params") or {},
        "content_hash": row["content_hash"],
        "origin": row["origin"],
        "note": row.get("note"),
        "created_by": row.get("created_by"),
        "created_at": created.isoformat() if isinstance(created, datetime) else created,
        "project_id": project_id or None,
        "status": status,
        "status_label": STATUS_LABEL[status],
        "events": [_event_view(e) for e in (events or [])],
    }


# ── 供 `control.py` 在生效事务里追加 `activated` 事件 ─────────────────────

def record_activated_event(cur, *, candidate_id: str, strategy_key: str,
                           project_id: Optional[str] = None,
                           payload: Optional[dict] = None, actor: str = "system") -> None:
    """在**生效事务内**追加一条 `activated` 事件（`control` 调）。

    ⚠️ **容错**：这是一条**审计侧记**，不是生效本身。若策略登记表还没建（老库、迁移未跑），
    不能让唯一写入口的切换失败 —— 用 SAVEPOINT 兜住，失败只记日志（`logger` 标准库，
    本模块同 `screen_learned` 的口径：写错名字会在 except 里再抛）。生效本身照常提交。
    """
    import logging
    log = logging.getLogger("app.services.fin.strategy")
    try:
        cur.execute("SAVEPOINT strategy_activated_event")
        record_event(cur, candidate_id=candidate_id, strategy_key=strategy_key,
                     kind="activated", project_id=project_id,
                     payload=payload or {}, actor=actor)
        cur.execute("RELEASE SAVEPOINT strategy_activated_event")
    except Exception as exc:  # noqa: BLE001 —— 审计侧记失败不拖垮生效
        try:
            cur.execute("ROLLBACK TO SAVEPOINT strategy_activated_event")
        except Exception:  # noqa: BLE001
            pass
        log.warning("[strategy] 追加 activated 事件失败（不拖垮生效）：{}", exc)
