"""内部最小 HTTP 端点 —— 给 `api` / 运维用。

它不是账本入口（账本入口只有 `paper`），只做两件事：
- `GET /healthz` —— 存活 + 与 Temporal 的连通性 + 六个时点清单（免密钥，容器健康检查用）。
- `POST /internal/trigger/{point}` —— **手工**触发某个时点的工作流（排障 / 补跑）。

鉴权：`X-Hunter-Internal-Key` == `HUNTER_INTERNAL_KEY`，与 `paper` / api 的内网接口同一把口令。
"""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from loguru import logger
# 这两个符号**放在模块顶层**（而不是函数里）：`temporalio.common.WorkflowIDReusePolicy`
# 曾被我写成 `temporalio.client.WorkflowIDReusePolicy`（ImportError，只在真调端点时才炸）。
# 顶层 import 让 `tests/test_api.py` 只要 import 本模块就能挡住这类导错路径。
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError

from app import config
from app.points import ALL_POINTS, BY_KEY as BY_KEYS, resolve_point_key

app = FastAPI(
    title="Hunter · fin-worker（Runtime Bridge + Temporal Workers）",
    docs_url=None, redoc_url=None, openapi_url=None,
)

_client = None
_client_lock = asyncio.Lock()


async def _get_client():
    global _client
    if _client is not None:
        return _client
    async with _client_lock:
        if _client is None:
            from app.worker import connect_with_retry
            _client = await connect_with_retry(attempts=10, delay=1.0)
    return _client


def _require_key(request: Request) -> None:
    expected = config.internal_key()
    got = request.headers.get("X-Hunter-Internal-Key", "")
    if not expected or got != expected:
        raise HTTPException(status_code=401, detail="internal auth failed")


@app.get("/healthz")
async def healthz() -> dict:
    """免密钥：只回「进程活着 + 各市场时点定义」，不含任何账本数据。"""
    connected = _client is not None
    return {
        "ok": True,
        "service": "fin-worker",
        "temporal_address": config.temporal_address(),
        "temporal_connected": connected,
        "task_queue": config.temporal_task_queue(),
        "points": [{"key": p.key, "market": p.market, "at": p.at,
                    "workflow": p.workflow, "cron": p.cron} for p in ALL_POINTS],
    }


@app.get("/internal/points")
async def list_points(request: Request) -> dict:
    _require_key(request)
    return {"points": [{"key": p.key, "market": p.market, "at": p.at, "kind": p.kind,
                        "workflow": p.workflow, "cron": p.cron, "title": p.title}
                       for p in ALL_POINTS]}


@app.post("/internal/trigger/{point_key}")
async def trigger(point_key: str, request: Request, body: Optional[dict] = None) -> dict:
    """手工触发一个时点的工作流（补跑 / 验收用）。

    `workflow_id = fin-{point}-{trade_date}`，复用策略 `ALLOW_DUPLICATE_FAILED_ONLY`：
    同一天同一时点正在跑或已跑成功 → 拒绝（409），跑挂了可以再触发。
    这是「同一件事不重复开跑」的又一层（业务幂等在 Activity 层，这一层在编排层）。
    """
    _require_key(request)
    point = resolve_point_key(point_key)
    if point is None:
        raise HTTPException(404, f"未知时点：{point_key}（可用 {sorted(set(BY_KEYS))}）")
    req = dict(body or {})
    trade_date = req.get("trade_date") or _today_shanghai()
    req.setdefault("trade_date", trade_date)
    # 市场 / 时点写进 args —— 工作流据此按该市场时区切日、按 (market, date) 读日历。
    req.setdefault("market", point.market)
    req.setdefault("at", point.at)
    req.setdefault("point", point.key)

    client = await _get_client()
    wf_id = f"fin-{point.key}-{trade_date}"
    try:
        handle = await client.start_workflow(
            point.workflow,
            req,
            id=wf_id,
            task_queue=config.temporal_task_queue(),
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY,
        )
    except WorkflowAlreadyStartedError as exc:
        # 「同一天同一时点已经跑过」是**预期的拒绝**，不是服务故障。
        # 原来只 catch 了 RPCError，这个异常类型不同，直接漏成了 500
        # （M7 故障注入验收时撞到：`fin-0930-2026-09-30` 是 M4 跑过的 id）。
        logger.warning("[api] 触发 {} 被拒（已存在）：{}", wf_id, exc)
        raise HTTPException(409, f"工作流 {wf_id} 已在跑或已跑过") from exc
    except RPCError as exc:
        logger.warning("[api] 触发 {} 被拒：{}", wf_id, exc)
        raise HTTPException(409, f"工作流 {wf_id} 已在跑或已跑过：{exc}") from exc
    logger.info("[api] 手工触发 {}（run_id={}）", wf_id, handle.result_run_id)
    # 不等待完成：把 run_id 还给调用方，去 Temporal UI 看执行。
    return {"started": True, "workflow_id": wf_id, "run_id": handle.result_run_id,
            "point": point.key, "trade_date": trade_date}


def _today_shanghai() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
