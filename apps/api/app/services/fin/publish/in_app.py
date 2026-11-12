"""站内渠道 `in_app` —— **把改造前的行为原样搬进适配器**（行为不变，老用例逐条不变）。

改造前发布是 `report.py:1124` 的 `_store_receipt`：写一行 `channel='in_app'` 的回执，
`external_id` 是产物引用 `artifact:fin-xxxx`。现在「存产物」这一步由本适配器负责，
回执行由 `publish.write_receipt` 统一写 —— **落库结果与改造前逐字节一致**。

`probe` 永远返回 `None`：站内产物是本地的、写入即确认，不存在「结果不明」；
`in_app` 的回执只会是 `SUCCESS`（产物已落库）或 `FAILED`（回读校验没过 / 缺产物）。
"""

from __future__ import annotations

from typing import Optional

from .base import FAILED, SUCCESS, PublishResult


class InAppAdapter:
    channel = "in_app"

    def submit(self, report: dict, target: Optional[str], *,
               html: Optional[str] = None, ctx: Optional[dict] = None) -> PublishResult:
        ctx = ctx or {}
        # 校验没过 → 站内也不发（`01方案 §9.2`：「校验失败不发布」）。**连产物都不生成**。
        if report.get("status") != "validated":
            return PublishResult(FAILED, None, "回读校验未通过，未发布")

        cur = ctx.get("cur")
        project = ctx.get("project")
        if cur is None or html is None or not project:
            # 站内发布必须有产物与游标；缺了就**如实报失败**，不静默当成功。
            return PublishResult(FAILED, None, "缺少产物或数据库游标，未落站内产物")

        # 复用报告模块既有的产物落库（`hunter_artifacts.published_artifact`）。
        # 延迟 import：`report.py` 顶部 import 本包，避免循环。
        from app.services.fin import report as report_svc
        ref = report_svc._store_artifact(cur, report, html, project)
        return PublishResult(SUCCESS, ref, "站内产物", target=None)

    def probe(self, receipt: dict) -> Optional[str]:
        """站内不存在「结果不明」—— 没有可核实的，返回 `None`。"""
        return None
