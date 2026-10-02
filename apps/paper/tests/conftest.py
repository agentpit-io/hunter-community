"""pytest 根 conftest。

两件事：
1. 让 `tests/` 能 `import app.*`（把 `apps/paper` 放进 sys.path）。
2. 提供连库夹具 —— **没有 `PAPER_TEST_DSN` 时自动跳过**，纯风控用例照样跑得起来。

`PAPER_TEST_DSN` 应指向一个已跑过 `0023_fin_core` 的库，且**用 `fin_paper_rw`
角色登录**（自检 / 权限边界就是这么设计的）。本机跑法见成果报告。
"""

from __future__ import annotations

import os
import sys
import uuid

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_DSN = os.getenv("PAPER_TEST_DSN", "").strip()
if _DSN:
    # config.database_url() 优先读 PAPER_DATABASE_URL
    os.environ["PAPER_DATABASE_URL"] = _DSN


def pytest_configure(config):
    config.addinivalue_line("markers", "db: 需要真实账本库（PAPER_TEST_DSN）")


@pytest.fixture
def pg():
    """给一个连上测试库的 cursor，测试结束时回滚。"""
    if not _DSN:
        pytest.skip("未设置 PAPER_TEST_DSN，跳过需要账本库的用例")
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(_DSN)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def project(pg):
    """建一个干净的 active 项目（user_id 用随机 UUID，避免撞部分唯一索引）。"""
    user_id = str(uuid.uuid4())
    project_id = "prj_test_" + uuid.uuid4().hex[:16]
    pg.execute(
        "INSERT INTO fin_project (project_id, user_id, tier, initial_capital) "
        "VALUES (%s, %s, 'play', 10000)",
        (project_id, user_id),
    )
    pg.execute(
        """
        INSERT INTO fin_param (
          project_id, board_flags, sector_prefs, strategies,
          hold_days_max, stop_loss_pct, take_profit_pct, max_positions,
          max_position_pct, min_order_amount, daily_max_new, daily_max_orders,
          daily_loss_halt_pct, account_drawdown_halt_pct
        ) VALUES (%s, '{}', '[]', '[]', 3, -0.04, 0.06, 2, 0.5, 1000, 2, 2, -0.03, -0.10)
        """,
        (project_id,),
    )
    # 路由用自己的连接读这张表 —— 必须提交，否则它看不见。
    pg.connection.commit()
    return project_id
