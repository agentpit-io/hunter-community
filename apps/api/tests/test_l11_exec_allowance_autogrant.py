"""L11 · 建项目即自动放行执行允许名单（真库，`TEST_DATABASE_URL` 未设时整体 skip）。

口径（补丁 L11）：产品定位是「AI 自己选股、自己交易」，所以**建项目就要让 AI 能下单**，
不必再让用户去登记。做法是 `store._insert_project()` 在**同一事务**里往
`fin_exec_allowance` 追加一条 `(scope='project', subject=<新 project_id>, status='active',
granted_by='auto:project-create')`。

**不动判据**：`apps/paper/app/allowlist.py` 一字不改，仍是「空表 = 谁都不许」——
放行靠的是表里真的多出这条 active 行。

跑法：

    TEST_DATABASE_URL=postgresql://hunter:hunter@127.0.0.1:5599/hunter \
      cd apps/api && PYTHONPATH=. python -m pytest tests/test_l11_exec_allowance_autogrant.py -q
"""

from __future__ import annotations

import os
import uuid

import psycopg2
import pytest

from app.services.fin import store
from app.services.fin import tiers

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="需要 TEST_DATABASE_URL 指向一个已跑过迁移的 postgres",
)

if TEST_DATABASE_URL:
    # 库里没有 fin_exec_allowance（迁移没跑到 0051）就跳过，别误报。
    try:
        _conn = psycopg2.connect(TEST_DATABASE_URL)
        with _conn.cursor() as _cur:
            _cur.execute("SELECT to_regclass('fin_exec_allowance')")
            _has_table = _cur.fetchone()[0] is not None
        _conn.close()
    except psycopg2.Error:
        _has_table = False
    if not _has_table:
        pytestmark = pytest.mark.skip(reason="库里没有 fin_exec_allowance —— 先跑 0051 迁移")
    store.DATABASE_URL = TEST_DATABASE_URL


def _active_rows_for(project_id: str) -> list[tuple[str, str]]:
    """该项目在 scope=project 上的**现行**登记（只取 (status, granted_by)）。

    现行 = `allowance_id` 最大的那一行；这里顺带把全部行也返回以便断言「只追加」。
    """
    conn = psycopg2.connect(TEST_DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, granted_by FROM fin_exec_allowance "
                "WHERE scope = 'project' AND subject = %s ORDER BY allowance_id",
                (project_id,),
            )
            return [(r[0], r[1]) for r in cur.fetchall()]
    finally:
        conn.close()


def _count_current_active(project_id: str) -> int:
    """现行状态为 active 的条数（按 (scope, subject) 去重取最新一行）——只可能是 0 或 1。"""
    conn = psycopg2.connect(TEST_DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) FROM (
                  SELECT DISTINCT ON (subject) subject, status
                    FROM fin_exec_allowance
                   WHERE scope = 'project' AND subject = %s
                   ORDER BY subject, allowance_id DESC
                ) t WHERE status = 'active'
                """,
                (project_id,),
            )
            return cur.fetchone()[0]
    finally:
        conn.close()


@pytest.fixture()
def uid():
    u = "u-l11-" + uuid.uuid4().hex
    yield u
    # 清掉这个测试用户写进去的项目行（子表先删）。
    # ⚠️ fin_exec_allowance 只追加（触发器挡 DELETE），**清不掉也不该清** ——
    #    留一条绝对项目不存在的孤儿登记是无害的（allowlist 查不到这个项目自然不生效）。
    conn = psycopg2.connect(TEST_DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM fin_project_market WHERE project_id IN "
                        "(SELECT project_id FROM fin_project WHERE user_id = %s)", (u,))
            cur.execute("DELETE FROM fin_param_change_log WHERE project_id IN "
                        "(SELECT project_id FROM fin_project WHERE user_id = %s)", (u,))
            cur.execute("DELETE FROM fin_param WHERE project_id IN "
                        "(SELECT project_id FROM fin_project WHERE user_id = %s)", (u,))
            cur.execute("DELETE FROM fin_project WHERE user_id = %s", (u,))
            cur.execute("DELETE FROM fin_account WHERE user_id = %s", (u,))
        conn.commit()
    finally:
        conn.close()


def test_create_project_auto_grants_exactly_one_active(uid):
    """建项目 → 名单里有**有且只有一条**该项目现行 active 登记（auto:project-create）。"""
    res = store.create_project(uid, tiers.TIER_ORDER[0])
    assert res["created"] is True
    pid = res["project"]["project_id"]

    rows = _active_rows_for(pid)
    assert len(rows) == 1, rows
    assert rows[0] == ("active", store.AUTO_GRANT_PROJECT_CREATE)
    assert _count_current_active(pid) == 1


def test_open_new_project_also_auto_grants(uid):
    """开新项目（M-16：关旧 + 开新）走同一插入点 → 新项目同样自动放行。"""
    first = store.create_project(uid, tiers.TIER_ORDER[0])
    second = store.open_new_project(uid, tiers.TIER_ORDER[1], reason="test")
    assert second["created"] is True
    old_pid = first["project"]["project_id"]
    new_pid = second["project"]["project_id"]
    assert old_pid != new_pid
    assert _count_current_active(new_pid) == 1
    # 关停的旧项目那条登记**不动**（关项目不等于撤销放行；要停得显式 revoke）。
    assert _count_current_active(old_pid) == 1


def test_idempotent_create_does_not_add_a_second_grant(uid):
    """重复建项目返回现成的那个 → 不重复追加登记（否则 accumulate 一堆 active 行）。"""
    r1 = store.create_project(uid, tiers.TIER_ORDER[0])
    r2 = store.create_project(uid, tiers.TIER_ORDER[0])
    assert r2["created"] is False
    pid = r1["project"]["project_id"]
    assert r2["project"]["project_id"] == pid
    assert len(_active_rows_for(pid)) == 1
    assert _count_current_active(pid) == 1
