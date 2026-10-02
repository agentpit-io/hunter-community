"""鉴权与实盘字段守卫（用 TestClient 走真实中间件与依赖）。"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

os.environ["HUNTER_INTERNAL_KEY"] = "test-internal-key"
os.environ.setdefault("PAPER_MODE", "PAPER")

from app.main import app  # noqa: E402  须在设置 env 之后导入

client = TestClient(app)
KEY = {"X-Hunter-Internal-Key": "test-internal-key"}


def test_no_key_returns_401():
    r = client.get("/api/v1/projects/prj_x")
    assert r.status_code == 401


def test_wrong_key_returns_401():
    r = client.get("/api/v1/projects/prj_x", headers={"X-Hunter-Internal-Key": "nope"})
    assert r.status_code == 401


def test_correct_key_not_401():
    r = client.get("/api/v1/projects/prj_x", headers=KEY)
    assert r.status_code != 401


def test_post_without_key_401():
    r = client.post("/api/v1/orders", json={})
    assert r.status_code == 401


def test_healthz_public():
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["mode"] == "PAPER"


def test_docs_and_openapi_disabled():
    """自带文档路由不吃 app 级鉴权，实测能绕过 —— 直接关掉，别留口子。"""
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/redoc").status_code == 404


def test_live_field_in_body_rejected():
    body = {"project_id": "p", "broker": "xyz", "code": "600519"}
    r = client.post("/api/v1/orders", json=body, headers=KEY)
    assert r.status_code == 400
    assert "broker" in r.json()["live_fields"]


def test_live_field_nested_rejected():
    body = {"project_id": "p", "order": {"real_account": "123"}}
    r = client.post("/api/v1/orders", json=body, headers=KEY)
    assert r.status_code == 400
    assert "real_account" in r.json()["live_fields"]


def test_live_field_in_query_rejected():
    r = client.get("/api/v1/projects/p", params={"live": "1"}, headers=KEY)
    assert r.status_code == 400


def test_normal_fields_not_flagged():
    """`realized_pnl` 这类正常字段不能被误伤（黑名单是精确匹配，不是子串）。"""
    body = {"project_id": "p", "realized_pnl": "1"}
    r = client.post("/api/v1/orders", json=body, headers=KEY)
    assert r.status_code != 400


def test_reject_message_mentions_paper_mode():
    r = client.post("/api/v1/orders", json={"broker_account": "x"}, headers=KEY)
    assert "PAPER" in r.json()["detail"]


@pytest.mark.parametrize(
    "field", ["live", "real", "real_account", "account_no", "broker", "trade_password"]
)
def test_denylist_terms(field):
    r = client.post("/api/v1/orders", json={field: "x"}, headers=KEY)
    assert r.status_code == 400
