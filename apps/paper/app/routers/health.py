"""健康检查。**唯一不需要内部口令的端点**（容器 HEALTHCHECK 跑在容器内、拿不到密钥）。

只回「进程活着 + 模式是不是 PAPER + 账本库连通性」，不含任何账本数据。
"""

from __future__ import annotations

import psycopg2
from fastapi import APIRouter

from app import config, db

router = APIRouter(tags=["paper"])


@router.get("/healthz")
def healthz() -> dict:
    db_ok = True
    try:
        with db.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
    except psycopg2.Error:
        db_ok = False
    return {
        "ok": db_ok,
        "mode": config.paper_mode(),
        "mode_fixed": config.paper_mode() == config.PAPER_MODE_REQUIRED,
        "ledger_db": db_ok,
    }
