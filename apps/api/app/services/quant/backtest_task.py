"""D-4 · 回测任务队列(BackgroundTasks + Redis)
(Phase D · 2026-08-17)

设计要点:
- 复用 kpred_cache 的 Redis client · 无新依赖
- Task 状态存 Redis · 1 h TTL · 无 worker 常驻(依赖 FastAPI BackgroundTasks)
- 单 pm2 fork 无问题(BackgroundTasks 在同 worker 内)
- 多 worker 场景需上 Celery(v2)

状态机:
  queued → running → done / error
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import date, timedelta

from app.services import kpred_cache as rc

log = logging.getLogger(__name__)

TASK_PREFIX = "quant:bt_task:"
TTL_SEC = 3600            # 1 小时保留


def _make_task_id() -> str:
    return uuid.uuid4().hex[:16]


def submit(strategy: dict, start: date, end: date, user_id: str | None) -> str:
    """入队 · 返回 task_id · 状态设 queued"""
    task_id = _make_task_id()
    rc.set(f"{TASK_PREFIX}{task_id}", {
        "status": "queued",
        "created_at": time.time(),
        "strategy_id": strategy.get("id"),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "user_id": user_id,
    }, TTL_SEC)
    return task_id


def run_and_store(task_id: str, strategy: dict, start: date, end: date, user_id: str | None):
    """BackgroundTasks 调用 · 跑回测 · 结果存 Redis"""
    from app.services.quant import backtest_engine, strategy_engine
    rc.set(f"{TASK_PREFIX}{task_id}", {
        "status": "running",
        "started_at": time.time(),
    }, TTL_SEC)
    try:
        result = backtest_engine.run_backtest(strategy, start, end, user_id)
        if "error" in result:
            rc.set(f"{TASK_PREFIX}{task_id}", {
                "status": "error",
                "error": result["error"],
                "message": result.get("message", ""),
            }, TTL_SEC)
            return
        # 补 positions 里的 name(前端展示用)
        positions = result.get("positions", [])
        if positions:
            name_map = strategy_engine.fetch_stock_names([p["code"] for p in positions])
            for p in positions:
                p["name"] = name_map.get(p["code"], p["code"])
        rc.set(f"{TASK_PREFIX}{task_id}", {
            "status": "done",
            "finished_at": time.time(),
            "result": result,
        }, TTL_SEC)
        log.info("[bt_task] %s done · %d ms", task_id, result.get("duration_ms", 0))
    except Exception as e:
        log.exception("[bt_task] %s failed", task_id)
        rc.set(f"{TASK_PREFIX}{task_id}", {
            "status": "error",
            "error": type(e).__name__,
            "message": str(e)[:500],
        }, TTL_SEC)


def get_status(task_id: str) -> dict:
    """轮询 · queued/running/done/error/not_found"""
    raw = rc.get(f"{TASK_PREFIX}{task_id}")
    if raw is None:
        return {"status": "not_found"}
    return raw
