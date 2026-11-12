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

# 用例里的报价用的是**固定日期**（2026-10-02 10:00 之类），而新鲜度判定要拿真时钟比。
# 默认按 100 年放宽（用例的报价日期从 1990 年起派生），固定日期才不会
# 「明天就跑不过了」；要测「断流 → 挂单」的用例
# 自己用 `helpers.relax_staleness(60)` 临时收紧。
os.environ.setdefault("PAPER_SNAPSHOT_STALE_SECONDS", str(100 * 365 * 24 * 3600))

# L06 · 两把钥匙都要在（paper 的启动自检缺任一就拒绝启动）。用例里让两个测试值
# **取相同的字符串**：既有的用例把 `X-Hunter-Internal-Key: test-internal-key` 写死在
# header 里，而执行门现在校验的是 `HUNTER_EXEC_KEY` —— 同值就不用逐条改 header。
# 「两把钥匙确实不同变量」由 `test_keys.py` 用**不同值**单独证明（不靠这两个默认）。
os.environ.setdefault("HUNTER_INTERNAL_KEY", "test-internal-key")
os.environ.setdefault("HUNTER_EXEC_KEY", "test-internal-key")


def pytest_configure(config):
    config.addinivalue_line("markers", "db: 需要真实账本库（PAPER_TEST_DSN）")


@pytest.fixture(autouse=True, scope="session")
def _register_test_exec_allowance():
    """L06 · 执行允许名单默认拒绝 —— **给老测试补登记**（**不是**把默认改成放行）。

    升到 L06 后空名单 = 谁都不许下单，既有的下单类用例会全被 403 挡下。正确做法是
    **在测试环境里登记**（与真实部署同一个动作），而不是给代码加一个「测试模式放行」
    的口子 —— 那种口子迟早会在生产被打开。

    测试库上补一条**项目通配** `(project, '*')` 的 active 登记，只追加表、`WHERE NOT
    EXISTS` 去重，重复跑不会堆行。**没有 `PAPER_TEST_DSN` 时不写库**（纯逻辑用例照旧）。

    判据本身（空表拒绝 / 维度收窄 / 撤销）由 `tests/test_allowlist.py` 的纯逻辑用例
    与成果文档里的真库端到端（curl）证明，不靠这条 fixture。
    """
    if not _DSN:
        yield
        return
    import psycopg2

    conn = psycopg2.connect(_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO fin_exec_allowance (scope, subject, status, granted_by, note) "
                "SELECT 'project', '*', 'active', 'pytest-conftest', "
                "'L06 测试环境补登记（执行允许名单默认拒绝）' "
                "WHERE NOT EXISTS (SELECT 1 FROM fin_exec_allowance "
                "                  WHERE scope='project' AND subject='*' AND status='active')"
            )
        conn.commit()
    finally:
        conn.close()
    yield


@pytest.fixture(autouse=True)
def _reset_quote_source():
    """每个用例后把行情来源复位，免得一个用例装的假报价漏到下一个用例。"""
    yield
    from app.snapshot.source import set_source

    set_source(None)


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
