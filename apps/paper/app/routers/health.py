"""健康检查。**唯一不需要内部口令的端点**（容器 HEALTHCHECK 跑在容器内、拿不到密钥）。

只回「进程活着 + 模式是不是 PAPER + 账本库连通性」，不含任何账本数据。

L06 起**多做一件可执行自证**：把**执行允许名单的现行条目数**报出来
（`exec_allowances`）—— 「当前是 PAPER」+「有 N 条允许清单」同屏可见
（`plan/L06.md` §1.4）。只是**条数**，不含 subject，公开也无妨。
"""

from __future__ import annotations

import psycopg2
from fastapi import APIRouter

from app import allowlist, config, db

router = APIRouter(tags=["paper"])


@router.get("/healthz")
def healthz() -> dict:
    db_ok = True
    # 允许名单条数（现行 active，按 (scope, subject) 去重）。库不可用 / 表还没迁移
    # → `None`（**不编一个 0** —— 0 会被读成「名单是空的、谁都下不了单」，
    # 那是结论；`None` 才是「没算出来」，与仓内「空的比假的好」同一条线）。
    exec_allowances: int | None = None
    try:
        with db.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
            exec_allowances = allowlist.count_active(cur)
    except psycopg2.Error:
        db_ok = False
    return {
        "ok": db_ok,
        "mode": config.paper_mode(),
        "mode_fixed": config.paper_mode() == config.PAPER_MODE_REQUIRED,
        "ledger_db": db_ok,
        "exec_allowances": exec_allowances,
    }
