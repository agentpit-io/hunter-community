"""执行允许名单（L06）· **默认拒绝**。

方案 §7.4 要求「模拟执行接口采用服务端鉴权、**最小权限与允许列表**」。
`paper` 原有的两道防线都是**拒绝式**的（`security.py`：口令对不对 + 实盘字段黑名单），
没有「**哪些项目 / 标的 / 工具被允许执行**」的正面清单。本模块就是那份清单的判据。

**判据（`plan/L06.md` §1.2，只此一处实现）**

执行一个动作 `(project_id, code?, tool)` 时，逐维查表（表 `fin_exec_allowance`）：

- **项目维度是基础闸门**：必须有 `scope='project'` 且 `subject` 命中 `project_id`
  （或显式通配 `'*'`）的**现行 active** 行 —— 没有就拒。
- **标的 / 工具维度是附加约束**：**只有当该维度存在至少一条现行 active 行时才生效**
  （有人登记过才管）；命中规则同上（`code` / `tool` 或 `'*'`）。
- **空表 = 谁都不许**（默认拒绝）。这是**加固**：升级后要**显式登记**才放行。

**现行状态**：表是**只追加**的（触发器挡 UPDATE / DELETE），一次登记 = 追加一行。
某个 `(scope, subject)` 的现行状态 = `allowance_id` **最大**的那行的 `status`；
撤销 = 再追加一条 `status='revoked'`（留痕，不删）。见 `db/migrations/0051_exec_allowance.sql`。

**不给默认放行**：本模块没有 `FIN_EXEC_ALLOW_ALL` 之类的口子 —— 要放行就在表里登记
（包括登记 `'*'`），不要在图省事的地方放开默认值。
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

# ── 三个维度 + 执行工具名（唯一的字面量来源；路由与用例都从这里取）──────────
SCOPE_PROJECT = "project"
SCOPE_INSTRUMENT = "instrument"
SCOPE_TOOL = "tool"
SCOPES = (SCOPE_PROJECT, SCOPE_INSTRUMENT, SCOPE_TOOL)

# 执行工具名：`paper` 里**会改账本**的那几个动作。挂号 = 这些名字。
TOOL_PLACE_ORDER = "place_order"
TOOL_CANCEL_ORDER = "cancel_order"
TOOL_EXPIRE_ORDERS = "expire_orders"
TOOL_MATCH_OPEN = "match_open"

# 显式通配：登记 subject='*' = 「这一维全部放行」。**是登记出来的，不是默认。**
WILDCARD = "*"


class AllowanceDenied(Exception):
    """不在允许名单上 —— 路由翻成 **403**（不是 401：口令对了，是没被授权执行）。"""

    def __init__(self, message: str, *, scope: Optional[str] = None,
                 subject: Optional[str] = None, registered: Optional[list[str]] = None):
        super().__init__(message)
        self.scope = scope
        self.subject = subject
        self.registered = registered or []


def _rows(cur) -> list[dict]:
    cur.execute(
        "SELECT allowance_id, scope, subject, status, granted_by, granted_at, note "
        "FROM fin_exec_allowance ORDER BY allowance_id"
    )
    return [dict(r) for r in cur.fetchall()]


def current_status(rows: list[dict]) -> dict[tuple[str, str], str]:
    """把整表折成 `{(scope, subject): status}` —— 取每个键 `allowance_id` 最大的那行。"""
    latest: dict[tuple[str, str], str] = {}
    for r in rows:  # 已按 allowance_id 升序 → 后写覆盖前写
        latest[(r["scope"], r["subject"])] = r["status"]
    return latest


def active_subjects(cur, scope: str) -> set[str]:
    """某一维度里**现行 active** 的全部 subject（含可能的 `'*'`）。"""
    return {s for (sc, s), st in current_status(_rows(cur)).items()
            if sc == scope and st == "active"}


def count_active(cur) -> int:
    """现行 active 的**条目数**（按 (scope, subject) 去重）—— 给 `/healthz` 看。"""
    return sum(1 for st in current_status(_rows(cur)).values() if st == "active")


def _hit(registered: set[str], subject: Optional[str]) -> bool:
    if WILDCARD in registered:
        return True
    return subject is not None and subject in registered


def check(cur, *, project_id: str, code: Optional[str] = None,
          tool: Optional[str] = None) -> None:
    """不在名单上就抛 `AllowanceDenied`（**默认拒绝**）。通过则返回 `None`。

    查的是**整个表**一次（`_rows`），三维在同一份快照上判 —— 不会出现「项目这行读到了、
    标的那行读的是另一个时刻」的错位。

    **被拒时一定出声**（L11）：抛出前打一条 `WARNING`，写清「哪个项目 / 哪个工具 / 哪个
    维度被拒 / 现行都有谁」，并给出放行办法 —— 不让一次执行失败只留一个 403 数字。
    """
    status = current_status(_rows(cur))

    def registered(scope: str) -> set[str]:
        return {s for (sc, s), st in status.items() if sc == scope and st == "active"}

    def deny(scope: str, subject: Optional[str], registered_: set[str], how: str):
        # 出声：项目 + 工具 + 维度 + 现行清单 + 放行办法，一条日志把排查需要的都写全。
        logger.warning(
            "[paper.allow] 执行被拒 · 项目={} · 工具={} · 维度={} · 被拒目标={} · "
            "该维度现行 active={} · 放行：{}",
            project_id, tool, scope, subject, sorted(registered_), how,
        )
        raise AllowanceDenied(
            f"执行被拒：项目 {project_id} 的 {tool or scope} 不在执行允许名单"
            f"（scope={scope}，被拒目标 {subject}）—— 默认拒绝。放行：{how}",
            scope=scope, subject=subject, registered=sorted(registered_),
        )

    projects = registered(SCOPE_PROJECT)
    if not _hit(projects, project_id):
        deny(
            SCOPE_PROJECT, project_id, projects,
            "POST /api/v1/exec-allowances "
            f'{{"scope":"project","subject":"{project_id}","granted_by":"<你>"}}',
        )

    instruments = registered(SCOPE_INSTRUMENT)
    if instruments and not _hit(instruments, code):
        deny(
            SCOPE_INSTRUMENT, code, instruments,
            f"该维度已登记 {sorted(instruments)}，不含它。POST /api/v1/exec-allowances "
            f'{{"scope":"instrument","subject":"{code}","granted_by":"<你>"}}',
        )

    tools = registered(SCOPE_TOOL)
    if tools and not _hit(tools, tool):
        deny(
            SCOPE_TOOL, tool, tools,
            f"该维度已登记 {sorted(tools)}，不含它。POST /api/v1/exec-allowances "
            f'{{"scope":"tool","subject":"{tool}","granted_by":"<你>"}}',
        )


def register(cur, *, scope: str, subject: str, granted_by: str,
             note: Optional[str] = None) -> dict:
    """登记一条**放行**（追加一行 `status='active'`）。返回新行。"""
    return _append(cur, scope=scope, subject=subject, status="active",
                   granted_by=granted_by, note=note)


def revoke(cur, *, scope: str, subject: str, granted_by: str,
           note: Optional[str] = None) -> dict:
    """撤销一条（追加一行 `status='revoked'`）。**不删**——留痕。"""
    return _append(cur, scope=scope, subject=subject, status="revoked",
                   granted_by=granted_by, note=note)


def _append(cur, *, scope: str, subject: str, status: str,
            granted_by: str, note: Optional[str]) -> dict:
    if scope not in SCOPES:
        raise ValueError(f"未知维度 {scope!r}（只认 {', '.join(SCOPES)}）")
    if not (subject or "").strip():
        raise ValueError("subject 不能为空")
    if not (granted_by or "").strip():
        raise ValueError("granted_by 不能为空（留痕）")
    cur.execute(
        "INSERT INTO fin_exec_allowance (scope, subject, status, granted_by, note) "
        "VALUES (%s, %s, %s, %s, %s) "
        "RETURNING allowance_id, scope, subject, status, granted_by, granted_at, note",
        (scope, subject.strip(), status, granted_by.strip(), note),
    )
    return dict(cur.fetchone())


def history(cur, *, limit: int = 200) -> list[dict]:
    """登记流水（最近的在前）—— 给登记入口的查询用。"""
    cur.execute(
        "SELECT allowance_id, scope, subject, status, granted_by, granted_at, note "
        "FROM fin_exec_allowance ORDER BY allowance_id DESC LIMIT %s",
        (max(1, int(limit)),),
    )
    return [dict(r) for r in cur.fetchall()]


def effective(cur) -> list[dict[str, Any]]:
    """现行生效的清单（每 (scope, subject) 一条），给看板 / 排查用。"""
    status = current_status(_rows(cur))
    return [{"scope": sc, "subject": s, "status": st}
            for (sc, s), st in sorted(status.items())]
