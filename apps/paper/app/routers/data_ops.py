"""数据面运维读接口：缺口与告警投递（M7 · M-20 / M-30）。

两个只读 + 一个「标记已投递」：

| 方法 | 路径 | 用途 |
|---|---|---|
| GET  | `/api/v1/data-gaps` | 最近的缺口（断流 / 无时刻 / 无价） |
| GET  | `/api/v1/data-gaps/summary` | 某时刻以来每种缺口多少条、多少只票 |
| GET  | `/api/v1/alerts/undelivered` | 还没发出去的对账告警 |
| POST | `/api/v1/alerts/mark-sent` | 宿主 dispatcher 发完通知后回执 |

**告警不在容器里发**：`notify-qq` 是宿主脚本，容器拿不到它。这里只把
「不平」变成一条可查询、可投递的记录；宿主 `scripts/fin_recon_alert.sh`
读 `undelivered` 发通知、再打 `mark-sent`。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query

from app import data_gap, db, recon

router = APIRouter(tags=["data-ops"])
CST = timezone(timedelta(hours=8))


@router.get("/api/v1/data-gaps")
def list_gaps(limit: int = 50) -> dict:
    with db.cursor() as cur:
        rows = data_gap.recent(cur, limit)
    return {"items": [data_gap.to_json(r) for r in rows]}


@router.get("/api/v1/data-gaps/summary")
def gaps_summary(hours: int = Query(24, ge=1, le=24 * 30)) -> dict:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    with db.cursor() as cur:
        out = data_gap.summary_since(cur, since)
    out["since"] = out["since"].isoformat()
    for kind in out["kinds"]:
        if kind["last_at"] is not None:
            kind["last_at"] = kind["last_at"].isoformat()
    return out


@router.get("/api/v1/alerts/undelivered")
def undelivered(limit: int = 20) -> dict:
    with db.cursor() as cur:
        rows = recon.undelivered_alerts(cur, limit)
    return {"items": [data_gap.to_json(r) for r in rows]}


@router.post("/api/v1/alerts/mark-sent")
def mark_sent(body: dict) -> dict:
    ids = body.get("ids") or []
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        raise HTTPException(400, "ids 必须是整数数组")
    with db.cursor(commit=True) as cur:
        n = recon.mark_alerts_sent(cur, ids)
    return {"marked": n}
