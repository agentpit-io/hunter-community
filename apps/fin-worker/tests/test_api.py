"""内部 HTTP 端点。不需要 Temporal —— 只测鉴权与清单。"""

from __future__ import annotations

from fastapi.testclient import TestClient

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


def test_healthz_temporal_connected_reflects_worker_connection(monkeypatch):
    # 回归：temporal_connected 原先量的是 HTTP 惰性 client 是否已建，恒为 false。
    # 现在必须反映 worker 侧是否已连上 Temporal：worker 监听期间为 true，退出后为 false。
    import asyncio

    import temporalio.worker as temporal_worker

    from app import worker as worker_mod

    class FakeWorker:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self):
            # worker 已连上并在监听时，/healthz 必须报 true
            assert client.get("/healthz").json()["temporal_connected"] is True

    async def fake_connect(*args, **kwargs):
        return object()

    async def fake_ensure_schedules(_client):
        return []

    monkeypatch.setattr(worker_mod, "connect_with_retry", fake_connect)
    monkeypatch.setattr(worker_mod, "ensure_schedules", fake_ensure_schedules)
    monkeypatch.setattr(temporal_worker, "Worker", FakeWorker)

    assert client.get("/healthz").json()["temporal_connected"] is False
    asyncio.run(worker_mod.run_worker_forever())
    assert client.get("/healthz").json()["temporal_connected"] is False


def test_healthz_temporal_connected_clears_when_setup_fails_after_connect(monkeypatch):
    # 回归：连接成功后 ensure_schedules / Worker(...) 抛错，标志不能残留为 true。
    import asyncio

    from app import worker as worker_mod

    async def fake_connect(*args, **kwargs):
        return object()

    async def failing_ensure_schedules(_client):
        raise RuntimeError("boom")

    monkeypatch.setattr(worker_mod, "connect_with_retry", fake_connect)
    monkeypatch.setattr(worker_mod, "ensure_schedules", failing_ensure_schedules)

    try:
        asyncio.run(worker_mod.run_worker_forever())
    except RuntimeError:
        pass
    assert client.get("/healthz").json()["temporal_connected"] is False
