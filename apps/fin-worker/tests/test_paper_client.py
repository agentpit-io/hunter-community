"""Paper Service 客户端 —— 用假 HTTP 传输覆盖每条路径，不连网。"""

from __future__ import annotations

import httpx
import pytest

from app.bridge.paper import IdempotencyConflict, PaperClient, PaperError


def make(handler) -> tuple[PaperClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(wrapped))
    return PaperClient(base_url="http://paper.test", key="k", client=client), seen


def test_place_order_sends_internal_key_and_body():
    def h(req):
        assert req.headers["X-Hunter-Internal-Key"] == "k"
        assert req.url.path == "/api/v1/orders"
        return httpx.Response(200, json={"status": "filled", "trade_id": "t1"})

    paper, seen = make(h)
    out = paper.place_order({"code": "601398", "idempotency_key": "k1"})
    assert out["status"] == "filled"
    assert len(seen) == 1


def test_get_calendar_404_means_unknown_not_error():
    paper, _ = make(lambda req: httpx.Response(404, json={"detail": "没有"}))
    assert paper.get_calendar("2026-10-09") is None


def test_get_calendar_200_returns_row():
    paper, _ = make(lambda req: httpx.Response(200, json={"trade_date": "2026-10-09",
                                                          "is_trading": True, "sessions": []}))
    row = paper.get_calendar("2026-10-09")
    assert row["is_trading"] is True


def test_409_raises_idempotency_conflict():
    paper, _ = make(lambda req: httpx.Response(409, text="冲突"))
    with pytest.raises(IdempotencyConflict):
        paper.place_order({"code": "601398"})


def test_5xx_raises_paper_error():
    paper, _ = make(lambda req: httpx.Response(500, text="boom"))
    with pytest.raises(PaperError) as ei:
        paper.place_order({"code": "601398"})
    assert ei.value.status == 500


def test_connection_error_becomes_paper_error_status_zero():
    def boom(req):
        raise httpx.ConnectError("nope", request=req)

    paper, _ = make(boom)
    with pytest.raises(PaperError) as ei:
        paper.healthz()
    assert ei.value.status == 0


def test_mark_job_running_tolerates_409():
    """Worker 重启后重放：任务已 RUNNING，409 是「这一步做过了」，不是错误。"""
    paper, _ = make(lambda req: httpx.Response(409, text="不能从 RUNNING 变成 RUNNING"))
    assert paper.mark_job_running("job_1") is None


def test_set_checkpoint_hits_checkpoint_endpoint():
    def h(req):
        assert req.url.path == "/api/v1/jobs/job_1/checkpoint"
        return httpx.Response(200, json={"job_id": "job_1", "checkpoint": {"phase": "ordered"}})

    paper, _ = make(h)
    out = paper.set_checkpoint("job_1", {"phase": "ordered"})
    assert out["checkpoint"]["phase"] == "ordered"


def test_confirm_t1_endpoint_path():
    def h(req):
        assert req.url.path == "/api/v1/projects/prj_1/confirm-t1"
        assert req.method == "POST"
        return httpx.Response(200, json={"positions_made_sellable": 2})

    paper, _ = make(h)
    assert paper.confirm_t1("prj_1")["positions_made_sellable"] == 2


def test_list_projects_passes_status():
    def h(req):
        assert req.url.params["status"] == "active"
        return httpx.Response(200, json={"items": [{"project_id": "prj_1"}], "status": "active"})

    paper, _ = make(h)
    assert paper.list_projects("active")[0]["project_id"] == "prj_1"


def test_expire_open_sends_reason():
    def h(req):
        import json
        body = json.loads(req.content)
        assert body["reason"] == "close"
        assert "at" in body
        return httpx.Response(200, json={"expired": 1})

    paper, _ = make(h)
    assert paper.expire_open("prj_1", "2026-10-09T15:30:00+08:00", reason="close")["expired"] == 1
