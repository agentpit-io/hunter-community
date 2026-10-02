"""内部 HTTP 端点。不需要 Temporal —— 只测鉴权与清单。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api import app

client = TestClient(app)
KEY = {"X-Hunter-Internal-Key": "test-internal-key"}


def test_healthz_is_public_and_lists_six_points():
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert len(body["points"]) == 6
    assert {p["key"] for p in body["points"]} == {"0915", "0930", "1130", "1300", "1455", "1530"}


def test_internal_points_requires_key():
    assert client.get("/internal/points").status_code == 401
    r = client.get("/internal/points", headers=KEY)
    assert r.status_code == 200
    assert len(r.json()["points"]) == 6


def test_trigger_requires_key():
    assert client.post("/internal/trigger/0930").status_code == 401


def test_trigger_unknown_point_404s_before_touching_temporal():
    r = client.post("/internal/trigger/9999", headers=KEY)
    assert r.status_code == 404
    assert "未知时点" in r.json()["detail"]
