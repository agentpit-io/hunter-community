"""智能炒股 · 发布（L07）· `publish.submit` / `publish.get` 两个入口（`01方案 §10.1`）。

一句话：**把「发布」从生成流程里拆出来，做成独立一步** —— 生成 → 校验 → **发布**
（可单独重试 / 核实）。渠道由适配器决定，回执统一落 `fin_publish_receipt`。

- `submit(conn, report_id, channel, …)` —— 发布一份报告到某渠道（**不盲目重发**）。
- `get(conn, receipt_id, resolve=…)` —— 读回执；`resolve=True` 时**查外部状态**落定 UNKNOWN。
- `resolve(conn, receipt_id, status, …)` —— **手动核实入口**（查不出外部状态时人工落定）。
- `list_pending(conn, owner_user_id=…)` —— **待核实队列**（`status='UNKNOWN'`）。

三条别改坏（`plan/L07.md` §1.2 / §五 红线）：

1. **`UNKNOWN` 下再提交同一份报告 → 拒绝**（`PublishBlocked`），除非显式 `resend=True`
   且写明原因；重发**留痕**（谁 / 何时 / 为什么，写进 `resend_*` 列）。
2. **`SUCCESS` 下再提交 → 幂等返回**（不重复发布，`01方案 §2`）。
3. **发布前先过数字回读校验** —— 报告 `status != 'validated'` 一律拒绝（**不放松红线 5**）。
"""

from __future__ import annotations

from typing import Any, Optional

import psycopg2.extras

from .base import (  # noqa: F401 —— 对外 re-export
    CHANNELS, FAILED, STATUSES, SUCCESS, UNKNOWN, Adapter, PublishBlocked, PublishResult,
    parse_target, receipt_id_for, target_str,
)
from .file import FileAdapter
from .in_app import InAppAdapter
from .webhook import WebhookAdapter

# 渠道注册表。**新增渠道 = 加一个适配器 + 在这里登记**，不加基础设施（`§6.1`）。
ADAPTERS: dict[str, Adapter] = {
    a.channel: a for a in (InAppAdapter(), WebhookAdapter(), FileAdapter())
}

_RECEIPT_COLS = ("receipt_id, report_id, channel, status, external_id, detail, target, "
                 "resend_of, resend_by, resend_reason, resend_at, attempted_at, resolved_at")
# 同一组列、带表别名前缀（`list_pending` 的 join 用）。
_RECEIPT_COLS_R = ", ".join(f"r.{c.strip()}" for c in _RECEIPT_COLS.split(","))


# ════════════════════════════════════════════════════════════════════════
# 回执落库（**唯一的写回执 SQL** —— 老 in_app 路径也走这里）
# ════════════════════════════════════════════════════════════════════════

def write_receipt(cur, report_id: str, channel: str, result: PublishResult, *,
                  target: Optional[str] = None, resend_of: Optional[str] = None,
                  resend_by: Optional[str] = None, resend_reason: Optional[str] = None) -> str:
    """把一次发布结果写进回执表（`(report_id, channel)` 一行，重复提交就地更新）。

    `target` 不给就用 `result.target`；`submit` 会传**规格字符串**（含 `status_url`）以便核实。
    """
    receipt_id = receipt_id_for(report_id, channel)
    target = target if target is not None else result.target
    cur.execute(
        """
        INSERT INTO fin_publish_receipt
          (receipt_id, report_id, channel, status, external_id, detail, target,
           resend_of, resend_by, resend_reason, resend_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                CASE WHEN %s IS NULL THEN NULL ELSE NOW() END)
        ON CONFLICT (receipt_id) DO UPDATE SET
          status = EXCLUDED.status, external_id = EXCLUDED.external_id,
          detail = EXCLUDED.detail, target = EXCLUDED.target,
          resend_of = EXCLUDED.resend_of, resend_by = EXCLUDED.resend_by,
          resend_reason = EXCLUDED.resend_reason, resend_at = EXCLUDED.resend_at,
          attempted_at = NOW()
        """,
        (receipt_id, report_id, channel, result.status, result.external_id,
         result.detail, target, resend_of, resend_by, resend_reason, resend_of),
    )
    return receipt_id


def _load_receipt(cur, receipt_id: str) -> Optional[dict]:
    cur.execute(f"SELECT {_RECEIPT_COLS} FROM fin_publish_receipt WHERE receipt_id = %s",
                (receipt_id,))
    return cur.fetchone()


def _jsonable(value: Any) -> Any:
    from datetime import date, datetime
    from decimal import Decimal
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _d(row: dict) -> dict:
    return {k: _jsonable(v) for k, v in dict(row).items()}


# ════════════════════════════════════════════════════════════════════════
# 内容与项目（发布要用到的上下文）
# ════════════════════════════════════════════════════════════════════════

def _project(conn, report: dict) -> dict:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM fin_project WHERE project_id = %s",
                    (report.get("project_id"),))
        row = cur.fetchone()
    return dict(row) if row else {}


def content_html(conn, loaded: dict) -> Optional[str]:
    """发布要送出去的 HTML 产物：优先取已落库的站内产物，没有再按事实现渲染。

    **渲染只改措辞与版面，不改数字**（表达层口径，`report.py` 模块文档）。
    """
    ref = (loaded.get("report") or {}).get("artifact_ref")
    if ref:
        short_id = str(ref).removeprefix("artifact:")
        with conn.cursor() as cur:
            cur.execute("SELECT content_html FROM hunter_artifacts.published_artifact "
                        "WHERE short_id = %s", (short_id,))
            row = cur.fetchone()
        if row and row[0]:
            return row[0]
    from app.services.fin import report as report_svc
    try:
        return report_svc.render_html(loaded["report"], loaded["facts"],
                                      project=_project(conn, loaded["report"]))
    except Exception:  # noqa: BLE001 —— 渲染不出来就让适配器按“无产物”如实报失败
        return None


# ════════════════════════════════════════════════════════════════════════
# 入口一 · submit（发布）
# ════════════════════════════════════════════════════════════════════════

def submit(conn, report_id: str, channel: str, *, target=None, resend: bool = False,
           actor: Optional[str] = None, reason: Optional[str] = None,
           loaded: Optional[dict] = None, html: Optional[str] = None,
           cur=None, project: Optional[dict] = None) -> dict:
    """把报告 `report_id` 发布到 `channel`。

    **发布前的三道闸**（按顺序）：

    1. 渠道必须已登记；
    2. **回执现状闸**：已有 `UNKNOWN` 且没带 `resend` → **拒绝**（不盲目重发）；
       已有 `SUCCESS` 且没带 `resend` → **幂等返回**（不重复发布）；
    3. **校验闸**：报告必须 `status='validated'`（回读校验没过的一律不发）。

    `cur` 给了就在**调用方的事务**里写（生成流程的 in_app 路径就这么用）；
    没给就自开游标并 `commit`。
    """
    adapter = ADAPTERS.get(channel)
    if adapter is None:
        raise PublishBlocked(f"未知渠道 {channel!r}（只支持 {sorted(ADAPTERS)}）", code="bad_channel")

    receipt_id = receipt_id_for(report_id, channel)
    own_cursor = cur is None
    if own_cursor:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        existing = _load_receipt(cur, receipt_id)
        if existing and not resend:
            st = existing["status"]
            if st == UNKNOWN:
                raise PublishBlocked(
                    f"上一份回执状态为 UNKNOWN（结果不明，可能已经发出去了）——"
                    f"要重发必须显式带重发标记并写明原因（report={report_id} channel={channel}）",
                    code="unknown_needs_resend")
            if st == SUCCESS:
                # 幂等：已经发成功了，不重复发布。
                return {**_d(existing), "idempotent": True,
                        "note": "该报告该渠道已成功发布过，未重复发布"}

        if loaded is None:
            from app.services.fin import report as report_svc
            loaded = report_svc.load_report(conn, report_id)
        if not loaded:
            raise PublishBlocked(f"报告不存在：{report_id}", code="not_found")

        report = loaded["report"]
        if report.get("status") != "validated":
            raise PublishBlocked(
                f"报告 {report_id} 未通过数字回读校验（status={report.get('status')}），不许发布",
                code="not_validated")

        if html is None:
            html = content_html(conn, loaded)
        if project is None:
            project = _project(conn, report)

        result = adapter.submit(report, target, html=html,
                                ctx={"cur": cur, "conn": conn, "project": project, "html": html})

        rid = write_receipt(
            cur, report_id, channel, result, target=target_str(target),
            resend_of=(receipt_id if resend else None),
            resend_by=(actor if resend else None),
            resend_reason=(reason if resend else None),
        )

        # 站内发布成功 → 把产物引用回写报告（与改造前 `persist` 行为一致）。
        if channel == "in_app" and result.status == SUCCESS and result.external_id:
            cur.execute("UPDATE fin_report SET artifact_ref = %s WHERE report_id = %s",
                        (result.external_id, report_id))

        if own_cursor:
            conn.commit()

        return {
            "receipt_id": rid, "report_id": report_id, "channel": channel,
            "status": result.status, "external_id": result.external_id,
            "detail": result.detail, "target": target_str(target),
            "resend": bool(resend), "idempotent": False,
        }
    finally:
        if own_cursor:
            cur.close()


# ════════════════════════════════════════════════════════════════════════
# 入口二 · get（读回执 / 核实）
# ════════════════════════════════════════════════════════════════════════

def get(conn, receipt_id: str, *, resolve: bool = False) -> dict:
    """读一条回执。`resolve=True` 且状态是 `UNKNOWN` 时，**查外部状态**尝试落定。

    查得出（适配器 `probe` 返回 `SUCCESS`/`FAILED`）就落定并写 `resolved_at`；
    查不出 → **保留 `UNKNOWN`**（`§11.2`），返回体里带 `probe: "unresolved"`。
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        row = _load_receipt(cur, receipt_id)
    if not row:
        raise PublishBlocked(f"回执不存在：{receipt_id}", code="not_found")

    receipt = _d(row)
    probe_outcome = None
    if resolve and receipt["status"] == UNKNOWN:
        adapter = ADAPTERS.get(receipt["channel"])
        probe_outcome = adapter.probe(receipt) if adapter else None
        if probe_outcome in (SUCCESS, FAILED):
            receipt = _apply_resolve(conn, receipt_id, probe_outcome,
                                     actor="auto:probe", note="查外部状态落定")
        else:
            probe_outcome = "unresolved"
    return {**receipt, "probe": probe_outcome}


def resolve(conn, receipt_id: str, status: str, *, actor: Optional[str] = None,
            note: Optional[str] = None) -> dict:
    """**手动核实入口**：操作者看了外部状态后，把回执落成 `SUCCESS` / `FAILED`。

    只允许 `SUCCESS` / `FAILED`（`UNKNOWN` 是「还没核实」，不是「核实的结果」）。
    """
    if status not in (SUCCESS, FAILED):
        raise PublishBlocked(f"核实只能落 SUCCESS / FAILED，收到 {status!r}", code="bad_status")
    return _apply_resolve(conn, receipt_id, status, actor=actor, note=note)


def _apply_resolve(conn, receipt_id: str, status: str, *, actor: Optional[str],
                   note: Optional[str]) -> dict:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            UPDATE fin_publish_receipt
               SET status = %s,
                   detail = COALESCE(detail, '') || %s,
                   resolved_at = NOW()
             WHERE receipt_id = %s
            RETURNING """ + _RECEIPT_COLS,
            (status, f" · 核实[{actor or '-'}]：{note or '查外部状态'}", receipt_id))
        row = cur.fetchone()
    conn.commit()
    if not row:
        raise PublishBlocked(f"回执不存在：{receipt_id}", code="not_found")
    return _d(row)


# ════════════════════════════════════════════════════════════════════════
# 待核实队列
# ════════════════════════════════════════════════════════════════════════

def list_pending(conn, *, owner_user_id: Optional[str] = None, limit: int = 100) -> list[dict]:
    """`status='UNKNOWN'` 的待核实回执行（按 owner 过滤：只能看自己项目的）。"""
    sql = (f"SELECT {_RECEIPT_COLS_R} "
           "FROM fin_publish_receipt r JOIN fin_report rp ON rp.report_id = r.report_id "
           "JOIN fin_project p ON p.project_id = rp.project_id "
           "WHERE r.status = 'UNKNOWN'")
    args: list = []
    if owner_user_id is not None:
        sql += " AND p.user_id = %s"
        args.append(owner_user_id)
    sql += " ORDER BY r.attempted_at DESC LIMIT %s"
    args.append(int(limit))
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, tuple(args))
        rows = cur.fetchall()
    return [_d(r) for r in rows]


__all__ = [
    "ADAPTERS", "Adapter", "CHANNELS", "FAILED", "PublishBlocked", "PublishResult",
    "STATUSES", "SUCCESS", "UNKNOWN", "content_html", "get", "list_pending",
    "parse_target", "receipt_id_for", "resolve", "submit", "write_receipt",
]
