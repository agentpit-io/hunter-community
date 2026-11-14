"""内部 HTTP 端点。不需要 Temporal —— 只测鉴权与清单。"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from app import temporal_status
from app.api import app

client = TestClient(app)
KEY = {"X-Hunter-Internal-Key": "test-internal-key"}


def test_healthz_is_public_and_lists_all_markets_points():
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    # N4：三个市场 × 六个时点
    assert len(body["points"]) == 18
    assert {p["market"] for p in body["points"]} == {"CN_A", "HK", "US"}
    keys = {p["key"] for p in body["points"]}
    assert "CN_A-0915" in keys and "US-1615" in keys


def test_healthz_temporal_connected_follows_worker_flag():
    # #52：healthz 以前看的是 API 的惰性 `_client`，没触发过就恒报 false。
    try:
        temporal_status.mark_disconnected()
        assert client.get("/healthz").json()["temporal_connected"] is False
        temporal_status.mark_connected()
        assert client.get("/healthz").json()["temporal_connected"] is True
    finally:
        temporal_status.mark_disconnected()


def test_run_worker_forever_sets_flag_while_running_and_clears_on_exit(monkeypatch):
    import temporalio.worker

    from app import worker as worker_mod

    seen: dict = {}

    async def fake_connect(*_a, **_kw):
        return object()

    async def fake_ensure(_client):
        return []

    class FakeWorker:
        def __init__(self, *_a, **_kw):
            pass

        async def run(self):
            seen["during_run"] = temporal_status.is_connected()

    monkeypatch.setattr(worker_mod, "connect_with_retry", fake_connect)
    monkeypatch.setattr(worker_mod, "ensure_schedules", fake_ensure)
    monkeypatch.setattr(temporalio.worker, "Worker", FakeWorker)

    temporal_status.mark_disconnected()
    asyncio.run(worker_mod.run_worker_forever())
    assert seen["during_run"] is True
    assert temporal_status.is_connected() is False


def test_internal_points_requires_key():
    assert client.get("/internal/points").status_code == 401
    r = client.get("/internal/points", headers=KEY)
    assert r.status_code == 200
    assert len(r.json()["points"]) == 18


def test_trigger_requires_key():
    assert client.post("/internal/trigger/0930").status_code == 401


def test_trigger_unknown_point_404s_before_touching_temporal():
    r = client.post("/internal/trigger/9999", headers=KEY)
    assert r.status_code == 404
    assert "未知时点" in r.json()["detail"]
