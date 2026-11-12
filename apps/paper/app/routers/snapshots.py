"""快照：读，以及一次显式的采集。

`POST /api/v1/snapshots` 让「取快照」这一步可以被单独观察 —— 排查「为什么这笔
没成交」时，先看那一步拿到的快照长什么样（有没有 `missing_flag`、时刻是不是旧了），
比翻日志快。它**只追加**，没有任何改历史行的端点。

`snapshot_time` 来自数据源；数据源没给时刻 → 这一步返回 503 并说明原因，
**不会落一行带本机时间的快照**（`09 §六-6`）。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app import db, snapshot

router = APIRouter(tags=["snapshots"])


@router.post("/api/v1/snapshots")
def capture_snapshot(code: str) -> dict:
    with db.cursor(commit=True) as cur:
        snap = snapshot.capture(cur, code)
    if snap is None:
        raise HTTPException(
            503,
            f"{code} 没有可用快照：行情未接通、超时，或数据源未给出时间戳。"
            "按「数据源没给时间戳就别落」的口径，不写这一行，也不拿本机时间顶上",
        )
    return snap


@router.get("/api/v1/snapshots/{snapshot_id}")
def get_snapshot(snapshot_id: str) -> dict:
    with db.cursor() as cur:
        row = snapshot.get(cur, snapshot_id)
    if not row:
        raise HTTPException(404, f"没有这张快照：{snapshot_id}")
    return {**row, "tradable": snapshot.tradable(row)}


@router.get("/api/v1/snapshots")
def latest_snapshot(code: str) -> dict:
    with db.cursor() as cur:
        row = snapshot.latest_for_code(cur, code)
    if not row:
        raise HTTPException(404, f"{code} 还没有任何快照")
    return {**row, "tradable": snapshot.tradable(row)}
