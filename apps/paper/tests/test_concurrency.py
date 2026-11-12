"""并发同账户 = 单写者闸门（M7 故障注入 FI-3 查出来的缺陷 → 修复 → 回归）。

**缺陷长什么样**（M7 实测，修复前）：两个 Worker 各自读到同一个 `expected_version`，
同时提交 —— 乐观锁**两边都放行**，两笔都成交，账户版本 1 → 3，
而且 `fin_cash_ledger` 里两笔 `freeze` 从同一个可用余额各冻一次，
`available_after` 与流水金额之和从此对不上（后续对账直接判不平）。

**修法**：`ledger.lock_project` —— 进事务先按 project 拿一把**事务级 advisory lock**，
第二个请求排到第一个提交之后，读到的就是新版本，`expected_version` 校验才拦得住它。

这条用例并发跑两个线程，无锁时几乎必然双双成交。
"""

from __future__ import annotations

import os
import threading
import uuid

import pytest

pytestmark = pytest.mark.db

if not os.getenv("PAPER_TEST_DSN"):
    pytest.skip("未设置 PAPER_TEST_DSN，跳过需要账本库的用例", allow_module_level=True)

import psycopg2.extras  # noqa: E402

from app import ledger, matching  # noqa: E402
from helpers import prime_quote  # noqa: E402

CODE = "600011"          # 避开 M6 夹具与别的用例用过的代码


def _conn():
    conn = psycopg2.connect(os.environ["PAPER_TEST_DSN"])
    conn.autocommit = False
    return conn


def test_concurrent_same_account_is_serialized(pg, project):
    """两个并发写者、同一 `expected_version` → 一个成交、一个被版本校验拦下。"""
    # 参考数据 + 该报价日的日历 + 固定报价（`prime_quote` 三件事一起做）
    prime_quote(pg, project, salt=0, code=CODE, price="10.00", prev_close="9.80")
    # 入金：没有本金两边都会因「资金不足」被拒，那样测不到版本校验
    ledger.seed_funding(pg, project)
    pg.connection.commit()

    pg.execute("SELECT version FROM fin_project WHERE project_id = %s", (project,))
    version = int(pg.fetchone()["version"])

    barrier = threading.Barrier(2)
    results: dict[str, dict] = {}

    def worker(tag: str, key: str):
        conn = _conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                barrier.wait(timeout=10)          # 尽可能同时进
                out = matching.engine.execute(cur, {
                    "project_id": project, "code": CODE, "side": "buy", "qty": 100,
                    "price_type": "limit", "limit_price": "10.00",
                    "expected_version": version, "idempotency_key": key,
                })
            conn.commit()
            results[tag] = out
        finally:
            conn.close()

    threads = [
        threading.Thread(target=worker, args=("a", "cnc-" + uuid.uuid4().hex)),
        threading.Thread(target=worker, args=("b", "cnc-" + uuid.uuid4().hex)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert set(results) == {"a", "b"}, results
    statuses = sorted(r["status"] for r in results.values())
    assert statuses == ["filled", "rejected"], statuses
    loser = next(r for r in results.values() if r["status"] == "rejected")
    assert loser["failed_checks"] == ["project_version"]

    # 账本里只有**一笔**成交，版本只 +1
    pg.execute("SELECT count(*) AS n FROM fin_trade WHERE project_id = %s", (project,))
    assert int(pg.fetchone()["n"]) == 1
    pg.execute("SELECT version FROM fin_project WHERE project_id = %s", (project,))
    assert int(pg.fetchone()["version"]) == version + 1

    # 现金自洽：可用 + 冻结 == 流水金额之和（并发没把账本写歪）
    from app import recon
    out = recon.run(pg, project, __import__("datetime").datetime.now(__import__("datetime").timezone.utc))
    failed = [c["name"] for c in out["checks"] if not c["passed"]]
    assert failed == [], failed
