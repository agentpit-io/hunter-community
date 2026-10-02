"""幂等命令与账户版本（M-13 · `01方案 §11.1`）。

四条验收：

1. 重放同一条命令**不产生第二笔成交**（`fin_trade` 只 +1，第二次返回首次结果）；
2. 同键不同内容 → 返回**冲突**，不覆盖；
3. 并发同账户：后到者被 `version` 拦下并**留痕**；
4. **已完成请求先返回原回执，再处理新的账户版本校验** —— 这条最容易写反，
   写反了会让「已经成功、只是回执丢在路上」的重试被版本校验拒掉。
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("HUNTER_INTERNAL_KEY", "test-internal-key")
os.environ.setdefault("PAPER_MODE", "PAPER")

from helpers import install_quote_source, order_body, prime_quote, seed_reference, uniq_date, uniq_when  # noqa: E402

pytestmark = pytest.mark.db

pytest.importorskip("psycopg2")
if not os.getenv("PAPER_TEST_DSN"):
    pytest.skip("未设置 PAPER_TEST_DSN，跳过幂等用例", allow_module_level=True)

from app import idempotency  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)
H = {"X-Hunter-Internal-Key": "test-internal-key"}


def _get(path):
    r = client.get(path, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def _post(path, body):
    r = client.post(path, json=body, headers=H)
    return r.status_code, (r.json() if r.content else None)


@pytest.fixture
def ready(pg, project):
    seed_reference(pg, code="600519", trade_date=uniq_date(project))
    pg.connection.commit()
    install_quote_source(when=uniq_when(project))
    assert _post(f"/api/v1/projects/{project}/funding", {})[0] == 200
    return project


def _trades(pid):
    return _get(f"/api/v1/projects/{pid}/trades")["items"]


# ── 请求摘要：规范化的意义 ────────────────────────────────────────────────

def test_request_hash_ignores_decimal_spelling_and_key_order():
    """`10.0` 与 `10.00` 是同一个价、字段顺序无关 —— 否则「同内容的重试」会被误判。"""
    a = {"project_id": "p", "code": "600519", "side": "buy", "qty": 100,
         "price_type": "limit", "limit_price": Decimal("10.0")}
    b = {"limit_price": Decimal("10.00"), "qty": 100, "side": "buy",
         "code": "600519", "project_id": "p", "price_type": "limit"}
    assert idempotency.request_hash(a) == idempotency.request_hash(b)


def test_request_hash_changes_when_content_changes():
    a = {"project_id": "p", "code": "600519", "side": "buy", "qty": 100,
         "price_type": "limit", "limit_price": Decimal("10.00")}
    b = {**a, "qty": 200}
    assert idempotency.request_hash(a) != idempotency.request_hash(b)


def test_request_hash_ignores_fields_outside_the_business_set():
    """`idempotency_key` 自己不参与摘要 —— 参与的话同一条命令换个键就算新内容。

    `source` / `actor` **在**摘要里（它们是命令的一部分，只是不参与风控判断）。
    """
    a = {"project_id": "p", "code": "600519", "side": "buy", "qty": 100,
         "price_type": "limit", "limit_price": Decimal("10.00")}
    b = {**a, "idempotency_key": "k1"}
    assert idempotency.request_hash(a) == idempotency.request_hash(b)


# ── 重放 ──────────────────────────────────────────────────────────────────

def test_replay_same_key_same_content_does_not_duplicate(ready):
    key = f"cmd-{uuid.uuid4().hex}"
    body = order_body(ready, idempotency_key=key)

    status, first = _post("/api/v1/orders", body)
    assert status == 200 and first["status"] == "filled"
    assert first.get("idempotent_replay", False) is False

    status, second = _post("/api/v1/orders", body)
    assert status == 200
    assert second["idempotent_replay"] is True
    # 回执逐字相同（除了那一面「这是重放」的旗子）
    assert second["trade_id"] == first["trade_id"]
    assert second["order_id"] == first["order_id"]
    assert second["price"] == first["price"]
    assert second["snapshot_id"] == first["snapshot_id"]

    # 成交只多了一笔，委托也只多了一条
    assert len(_trades(ready)) == 1
    orders = _get(f"/api/v1/projects/{ready}/orders")["items"]
    assert len(orders) == 1
    # 账户版本也只 +1
    assert _get(f"/api/v1/projects/{ready}")["project"]["version"] == 1


def test_replay_returns_original_receipt_even_after_version_moved(ready):
    """**已完成请求先返回原回执，再处理账户版本校验**（`01方案 §11.1` 末两条）。

    这条顺序写反的后果：一次「已经成功、只是回执丢在路上」的重试会被版本校验
    拒掉 —— 用户看到「账户版本冲突」，而他只是重试了一次已经成功的下单。
    """
    key = f"cmd-{uuid.uuid4().hex}"
    body = order_body(ready, idempotency_key=key, expected_version=0)

    status, first = _post("/api/v1/orders", body)
    assert status == 200 and first["status"] == "filled"
    assert _get(f"/api/v1/projects/{ready}")["project"]["version"] == 1

    # 原样重试：`expected_version` 仍是 0，而账户已经到 1 了 —— 不许被拦
    status, second = _post("/api/v1/orders", body)
    assert status == 200
    assert second["status"] == "filled"
    assert second["idempotent_replay"] is True
    assert second["trade_id"] == first["trade_id"]
    assert len(_trades(ready)) == 1


def test_replay_of_a_rejection_returns_the_same_rejection(ready):
    """被拒的命令重放也返回原回执，不会因为「这次换个时机」变成成交。"""
    key = f"cmd-{uuid.uuid4().hex}"
    body = order_body(ready, qty=150, idempotency_key=key)

    _, first = _post("/api/v1/orders", body)
    assert first["status"] == "rejected"

    _, second = _post("/api/v1/orders", body)
    assert second["idempotent_replay"] is True
    assert second["decline_reason"] == first["decline_reason"]
    assert _trades(ready) == []


# ── 冲突 ──────────────────────────────────────────────────────────────────

def test_same_key_different_content_conflicts_without_overwriting(ready):
    key = f"cmd-{uuid.uuid4().hex}"
    _, first = _post("/api/v1/orders", order_body(ready, qty=100, idempotency_key=key))
    assert first["status"] == "filled"

    # 同键、不同内容（数量从 100 改成 200）
    status, conflict = _post("/api/v1/orders", order_body(ready, qty=200, idempotency_key=key))
    assert status == 409, conflict
    assert "拒绝覆盖" in conflict["detail"]
    assert key in conflict["detail"]

    # 原记录**没被覆盖**：还是那笔 100 股，也只有一笔成交
    assert len(_trades(ready)) == 1
    orders = _get(f"/api/v1/projects/{ready}/orders")["items"]
    assert len(orders) == 1 and orders[0]["qty"] == 100


def test_conflict_record_keeps_the_first_hash(ready, pg):
    key = f"cmd-{uuid.uuid4().hex}"
    body = order_body(ready, qty=100, idempotency_key=key)
    _post("/api/v1/orders", body)
    # 服务端摘要算的是 `OrderIn.model_dump()`（pydantic 补默认值 + Decimal），不是原始 JSON
    from app.schemas import OrderIn

    before = idempotency.request_hash(OrderIn(**body).model_dump())

    _post("/api/v1/orders", order_body(ready, qty=200, idempotency_key=key))

    pg.execute("SELECT request_hash FROM fin_idempotency WHERE idempotency_key = %s", (key,))
    row = pg.fetchone()
    pg.connection.rollback()
    assert row["request_hash"] == before


# ── 账户版本 ──────────────────────────────────────────────────────────────

def test_version_conflict_is_refused_and_recorded(ready):
    """并发同账户：后到者被 version 拦下并留痕（一条 `rejected` 委托）。"""
    key_a = f"cmd-{uuid.uuid4().hex}"
    _, first = _post("/api/v1/orders",
                     order_body(ready, idempotency_key=key_a, expected_version=0))
    assert first["status"] == "filled"

    # 第二个 Worker 手里还是 version=0（它读到的是并发之前的状态）
    key_b = f"cmd-{uuid.uuid4().hex}"
    status, second = _post("/api/v1/orders",
                           order_body(ready, idempotency_key=key_b, expected_version=0))
    assert status == 200 and second["status"] == "rejected"
    assert "账户版本冲突" in second["decline_reason"]
    assert second["expected_version"] == 0 and second["actual_version"] == 1

    # 留痕：多了一条 rejected 委托，但没有多出成交
    orders = _get(f"/api/v1/projects/{ready}/orders")["items"]
    assert len(orders) == 2
    assert [o["status"] for o in orders].count("rejected") == 1
    assert len(_trades(ready)) == 1
    # 账户版本仍停在 1
    assert _get(f"/api/v1/projects/{ready}")["project"]["version"] == 1


def test_version_conflict_replay_is_stable(ready):
    """冲突也是「已完成请求」：同一个键重放返回同一个冲突，不再多留一条痕。"""
    _, first = _post("/api/v1/orders",
                     order_body(ready, idempotency_key=f"cmd-{uuid.uuid4().hex}",
                                expected_version=0))
    assert first["status"] == "filled"

    key = f"cmd-{uuid.uuid4().hex}"
    body = order_body(ready, idempotency_key=key, expected_version=0)
    _, conflict = _post("/api/v1/orders", body)
    assert conflict["status"] == "rejected"

    _, again = _post("/api/v1/orders", body)
    assert again["idempotent_replay"] is True
    assert again["order_id"] == conflict["order_id"]
    orders = _get(f"/api/v1/projects/{ready}/orders")["items"]
    assert len(orders) == 2       # 1 filled + 1 rejected，重放没多写


def test_matching_version_is_accepted(ready):
    _, rec = _post("/api/v1/orders", order_body(ready, expected_version=0))
    assert rec["status"] == "filled"


def test_no_expected_version_means_no_version_check(ready):
    """不传 `expected_version` 就跳过乐观锁（向后兼容：M2 的调用方没有这个字段）。"""
    _post("/api/v1/orders", order_body(ready))
    _, rec = _post("/api/v1/orders", order_body(ready))
    assert rec["status"] == "filled"
    assert _get(f"/api/v1/projects/{ready}")["project"]["version"] == 2


def test_order_without_idempotency_key_still_works(ready):
    """不带幂等键照常下单 —— 只是没有去重（库表 `idempotency_key` 可空）。"""
    _, first = _post("/api/v1/orders", order_body(ready))
    _, second = _post("/api/v1/orders", order_body(ready))
    assert first["trade_id"] != second["trade_id"]
    assert len(_trades(ready)) == 2


# ── 隐藏的坑：并发 / 事务 ─────────────────────────────────────────────────

def test_idempotency_row_is_written_in_the_same_transaction(ready, pg):
    """幂等记录与账本变更同一个事务：提交后两者都在（`01方案 §11.1` 最后一条）。"""
    key = f"cmd-{uuid.uuid4().hex}"
    _post("/api/v1/orders", order_body(ready, idempotency_key=key))

    pg.execute("SELECT response_ref, project_id FROM fin_idempotency WHERE idempotency_key = %s",
               (key,))
    row = pg.fetchone()
    pg.connection.rollback()
    assert row is not None and row["project_id"] == ready
    assert row["response_ref"] == _trades(ready)[0]["trade_id"]
