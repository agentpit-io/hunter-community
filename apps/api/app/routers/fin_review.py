"""智能炒股 · 复核（复盘）回路 · 内网端点（第四段 R3）。

只有**一条通道**：内网口令（`X-Hunter-Internal-Key`），调用方是 Temporal 里那个
`fin.review` 工作流的三个活动。两个端点，一条只读、一条算候选：

| 端点 | 干什么 | 写库吗 |
|---|---|---|
| `POST /api/internal/fin/review/collect` | 取当日报告 + 事实行 + 成交（只读） | **不写** |
| `POST /api/internal/fin/review/propose` | 让模型只写文字 + 回读校验，产出候选经验 | **不写**（经验经 Memory Service 唯一入口写） |

**复核这条链路不直连经验表**：候选由 `fin-worker` 的 `review_append` 活动
提交到 `POST /api/internal/fin/memory/evidence`（唯一写入口）。所以经验三表
仍然只有 `services/fin/memory.py` 一个模块碰得到 —— 复核服务连 import 都没有。

口径与三条「不许编数字」的约束见 `app/services/fin/review.py` 模块文档。
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

from app.services.fin import review as review_svc
from app.services.fin import switches as switches_svc

router = APIRouter(tags=["fin-review"])

_INTERNAL_KEY = os.getenv("HUNTER_INTERNAL_KEY", "")


def _auth_internal(request: Request) -> None:
    """内网口令（与 `fin_report.py:34` / `fin_memory.py:40` 同一条），**不新增密钥**。"""
    key = request.headers.get("X-Hunter-Internal-Key", "")
    if not _INTERNAL_KEY or key != _INTERNAL_KEY:
        raise HTTPException(401, "internal auth failed")


class ReviewIn(BaseModel):
    project_id: str
    trade_date: str
    market: Optional[str] = None


@router.post("/internal/fin/review/collect")
async def review_collect(body: ReviewIn, request: Request):
    """**只读**：当日报告（含 `self_review` 三问）+ 事实行 + 成交。

    报告不存在也**照常返回**（`report=null`）—— 复核可以在「只有成交、还没报告」
    的时点跑，不是错误。
    """
    _auth_internal(request)
    conn = review_svc.get_conn()
    try:
        collected = review_svc.collect(conn, body.project_id, body.trade_date, body.market)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    finally:
        conn.close()
    logger.info("[fin.review] collect project={} date={} market={} → report={} facts={} trades={}",
                body.project_id, body.trade_date, body.market,
                collected.get("report") is not None, len(collected["facts"]),
                len(collected["trades"]))
    return collected


@router.post("/internal/fin/review/propose")
async def review_propose(body: ReviewIn, request: Request):
    """产出**候选**经验：模型只写文字，数字走 `fact` 引用，回读校验不过即作废。

    模型不可用 / 候选全废 → 降级为规则文案（`used_fallback=true`，如实标注）。
    **本端点一行经验都不写** —— 写走 `POST /api/internal/fin/memory/evidence`。

    ⚠️ **`R21` · 开关关掉的项目，这一步整个跳过（模型一次都不调）。**
    在此之前，开关关着时工作流照样跑完整条链，只在最后一步「写经验」被 503 拒 ——
    也就是**为一个写不进去的结论付模型钱**（还因为 Temporal 重试可能被重放多次）。
    判据按**项目**取：关掉一个项目不该停掉别的项目的复盘。
    """
    _auth_internal(request)
    if not switches_svc.memory_enabled(body.project_id):
        reason = "经验库未启用（本项目开关为关），跳过复盘提案"
        logger.info("[fin.review] propose project={} date={} 跳过：{}",
                    body.project_id, body.trade_date, reason)
        return {"candidates": [], "used_fallback": False, "reason": reason,
                "rejected": [], "skipped": "memory_disabled"}
    try:
        out = await review_svc.run_review(body.project_id, body.trade_date, market=body.market)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    logger.info("[fin.review] propose project={} date={} → {} 条候选 · fallback={} · 作废 {}",
                body.project_id, body.trade_date, len(out.get("candidates") or []),
                out.get("used_fallback"), len(out.get("rejected") or []))
    return out
