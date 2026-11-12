"""运行时控制通道（L08）—— 白名单 / 类型化参数 / 幂等 / 四入口。

**不连 Temporal、不连网**：Temporal 客户端与 paper 客户端都是假对象。
真机上的 `curl` 读数见成果文档 `docs/开发文档/L08-控制通道与MCP.md`。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import api as api_mod
from app import runtime_control as rc
from app.api import app

client = TestClient(app)
KEY = {"X-Hunter-Internal-Key": "test-internal-key"}


# ── 假 Temporal 对象 ───────────────────────────────────────────────────────
class _Desc:
    def __init__(self, status: int, run_id: str = "run-1"):
        self.status = status
        self.run_id = run_id
        self.task_queue = "fin-trading"
        self.start_time = datetime(2026, 10, 5, tzinfo=timezone.utc)
        self.close_time = None
        self.history_length = 42


class _Handle:
    def __init__(self, status: int = 1, job_id: str | None = None):
        self._status = status
        self._job = {"job_id": job_id} if job_id else None
        self.cancelled = 0
        self.signals: list[str] = []

    async def describe(self):
        return _Desc(self._status)

    async def query(self, name: str):
        if name == "current_job":
            return self._job
        if name == "paused":
            return False
        raise RuntimeError(f"no query {name}")

    async def signal(self, name: str):
        self.signals.append(name)

    async def cancel(self):
        self.cancelled += 1
        self._status = 4


class _SchedState:
    def __init__(self):
        self.paused = False
        self.note = None


class _SchedInfo:
    num_actions = 7
    next_action_times: list = []


class _SchedAction:
    pass


class _SchedDesc:
    def __init__(self, paused: bool):
        self.schedule = type("S", (), {"state": _SchedState(), "action": _SchedAction()})()
        self.schedule.state.paused = paused
        self.info = _SchedInfo()


class _SchedHandle:
    def __init__(self):
        self.paused = False

    async def pause(self, note: str = ""):
        self.paused = True

    async def unpause(self, note: str = ""):
        self.paused = False

    async def describe(self):
        return _SchedDesc(self.paused)


class _AlreadyStarted(Exception):
    pass


class _FakeClient:
    def __init__(self, handle=None, sched=None):
        self._handle = handle or _Handle()
        self._sched = sched or _SchedHandle()
        self.started: list[tuple] = []
        self.raise_started = False

    async def start_workflow(self, template, params, **kw):
        if self.raise_started:
            # 与 temporalio 的 WorkflowAlreadyStartedError 行为一致（本模块只 catch 这个类）
            from temporalio.exceptions import WorkflowAlreadyStartedError
            raise WorkflowAlreadyStartedError(kw.get("id", "x"), template)
        self.started.append((template, params, kw))
        return type("H", (), {"result_run_id": "run-x"})()

    def get_workflow_handle(self, wf_id, run_id=None):
        return self._handle

    def get_schedule_handle(self, sid):
        return self._sched


# ── A · 白名单 + 类型化参数（纯函数，不碰 Temporal）────────────────────────
def test_whitelist_lists_only_registered_workflows():
    # 白名单就是允许启动的全部；六个时点 + ETL + 同步 + 四条回路 + 两条采集 = 14
    assert set(rc.TEMPLATES) == {
        "fin.point_preopen", "fin.point_decide", "fin.point_match_a", "fin.point_match_b",
        "fin.point_match_c", "fin.point_close", "fin.market_etl", "fin.instrument_sync",
        "fin.review", "fin.shadow", "fin.observe", "fin.propose",
        # L09：采集补齐（新闻 / 基本面）
        "fin.news_collect", "fin.fundamental_collect",
    }
    assert len(rc.TEMPLATES) == 14


def test_unknown_template_is_rejected():
    with pytest.raises(rc.ControlError) as e:
        rc.validate_start("fin.not_a_real_workflow", {})
    assert e.value.status == 404
    assert "未知工作流模板" in e.value.detail


def test_shell_and_arbitrary_names_are_not_templates():
    for bad in ("bash", "os.system", "fin.point_decide; rm -rf /", "eval"):
        with pytest.raises(rc.ControlError):
            rc.validate_start(bad, {})


def test_missing_required_market_rejected():
    with pytest.raises(rc.ControlError) as e:
        rc.validate_start("fin.point_decide", {})
    assert e.value.status == 400 and "market" in e.value.detail


def test_bad_market_value_rejected():
    with pytest.raises(rc.ControlError) as e:
        rc.validate_start("fin.point_decide", {"market": "MARS"})
    assert e.value.status == 400


def test_etl_market_uses_lowercase_channel_names():
    assert rc.validate_start("fin.market_etl", {"market": "cn"})["market"] == "cn"
    with pytest.raises(rc.ControlError):
        rc.validate_start("fin.market_etl", {"market": "CN_A"})   # 时点用 CN_A，ETL 用 cn


def test_unknown_param_rejected_not_silently_dropped():
    with pytest.raises(rc.ControlError) as e:
        rc.validate_start("fin.point_decide", {"market": "CN_A", "evil": "x"})
    assert e.value.status == 400 and "evil" in e.value.detail


def test_date_and_hhmm_typing():
    ok = rc.validate_start("fin.point_decide", {"market": "CN_A", "trade_date": "2026-10-05",
                                                "at": "09:30"})
    assert ok["trade_date"] == "2026-10-05" and ok["at"] == "09:30"
    for bad in ({"trade_date": "2026/10/05"}, {"trade_date": "today"}, {"at": "9:30"}, {"at": "930"}):
        with pytest.raises(rc.ControlError):
            rc.validate_start("fin.point_decide", {"market": "CN_A", **bad})


def test_bool_is_not_accepted_as_int():
    # Python 里 True 是 int 的子类 —— 显式挡掉，否则 hold_seconds=True 会变成 1
    with pytest.raises(rc.ControlError):
        rc.validate_start("fin.point_decide", {"market": "CN_A", "hold_seconds": True})


def test_int_range_and_type():
    assert rc.validate_start("fin.point_decide", {"market": "CN_A", "hold_seconds": 30})["hold_seconds"] == 30
    with pytest.raises(rc.ControlError):
        rc.validate_start("fin.point_decide", {"market": "CN_A", "hold_seconds": -1})
    with pytest.raises(rc.ControlError):
        rc.validate_start("fin.point_decide", {"market": "CN_A", "hold_seconds": "30"})


def test_str_list_typing():
    ok = rc.validate_start("fin.instrument_sync", {"market": "us", "codes": ["AAPL", "MSFT"]})
    assert ok["codes"] == ["AAPL", "MSFT"]
    with pytest.raises(rc.ControlError):
        rc.validate_start("fin.instrument_sync", {"codes": [1, 2]})


def test_derive_workflow_id_is_stable_and_prefixed():
    wid = rc.derive_workflow_id("fin.point_decide", {"market": "CN_A", "trade_date": "2026-10-05"}, None)
    assert wid == "fin-ctl-fin.point_decide-CN_A-2026-10-05"
    # 显式给 id 就用它
    assert rc.derive_workflow_id("fin.point_decide", {}, "my-id") == "my-id"


# ── B · start / get / pause / cancel 的实现（假客户端）────────────────────
def test_start_impl_returns_run_id():
    c = _FakeClient()
    out = asyncio.run(rc.start_impl(c, "fin.instrument_sync", {"market": "us"}, "wf-1"))
    assert out["started"] is True and out["run_id"] == "run-x"
    assert c.started[0][0] == "fin.instrument_sync"


def test_start_impl_conflict_is_409_not_500():
    c = _FakeClient()
    c.raise_started = True
    with pytest.raises(rc.ControlError) as e:
        asyncio.run(rc.start_impl(c, "fin.instrument_sync", {}, "wf-dup"))
    assert e.value.status == 409


def test_get_impl_reports_status_and_paused():
    c = _FakeClient(handle=_Handle(status=1))
    out = asyncio.run(rc.get_impl(c, workflow_id="wf-1"))
    assert out["status"] == "RUNNING" and out["paused"] is False


def test_get_impl_unknown_workflow_404():
    class _Boom(_Handle):
        async def describe(self):
            from temporalio.service import RPCError
            raise RPCError("workflow not found", None, None)
    c = _FakeClient(handle=_Boom())
    with pytest.raises(rc.ControlError) as e:
        asyncio.run(rc.get_impl(c, workflow_id="nope"))
    assert e.value.status == 404


def test_get_impl_schedule_shows_paused():
    sh = _SchedHandle()
    sh.paused = True
    c = _FakeClient(sched=sh)
    out = asyncio.run(rc.get_impl(c, schedule_id="fin-point-CN_A-0930"))
    assert out["kind"] == "schedule" and out["paused"] is True


def test_pause_schedule_is_idempotent():
    c = _FakeClient()
    a = asyncio.run(rc.pause_impl(c, schedule_id="fin-point-CN_A-0930"))
    b = asyncio.run(rc.pause_impl(c, schedule_id="fin-point-CN_A-0930"))
    assert a["paused"] is True and b["paused"] is True          # 第二次不报错
    r = asyncio.run(rc.pause_impl(c, schedule_id="fin-point-CN_A-0930", resume=True))
    assert r["paused"] is False


def test_pause_workflow_sends_signal():
    h = _Handle(status=1)
    c = _FakeClient(handle=h)
    out = asyncio.run(rc.pause_impl(c, workflow_id="wf-1"))
    assert out["paused"] is True and h.signals == ["pause"]
    asyncio.run(rc.pause_impl(c, workflow_id="wf-1", resume=True))
    assert h.signals == ["pause", "resume"]


def test_pause_closed_workflow_is_not_an_error():
    c = _FakeClient(handle=_Handle(status=2))                    # COMPLETED
    out = asyncio.run(rc.pause_impl(c, workflow_id="wf-1"))
    assert out["changed"] is False and out["status"] == "COMPLETED"


def test_cancel_impl_cancels_workflow_and_job():
    h = _Handle(status=1, job_id="job-42")
    c = _FakeClient(handle=h)

    class _Paper:
        def cancel_job(self, job_id):
            return {"job_id": job_id, "status": "CANCEL_REQUESTED", "changed": True}

    out = asyncio.run(rc.cancel_impl(c, workflow_id="wf-1", paper=_Paper()))
    assert out["cancelled"] is True
    assert out["job_id"] == "job-42" and out["job_status"] == "CANCEL_REQUESTED"
    assert h.cancelled == 1


def test_cancel_impl_is_idempotent_on_closed_workflow():
    c = _FakeClient(handle=_Handle(status=4))                    # ALREADY CANCELED
    out = asyncio.run(rc.cancel_impl(c, workflow_id="wf-1", paper=object()))
    assert out["cancelled"] is False and out["already_ended"] is True
    assert out["status"] == "CANCELED"


def test_cancel_impl_second_call_after_first():
    """第一次取消后工作流变 CANCELED；第二次来 → 幂等返回已结束，不抛。"""
    h = _Handle(status=1, job_id="job-9")
    c = _FakeClient(handle=h)
    first = asyncio.run(rc.cancel_impl(c, workflow_id="wf-1", paper=type("P", (), {"cancel_job": lambda s, j: {"status": "CANCELLED"}})()))
    assert first["cancelled"] is True
    second = asyncio.run(rc.cancel_impl(c, workflow_id="wf-1", paper=object()))
    assert second["already_ended"] is True and second["cancelled"] is False


# ── C · HTTP 四入口（鉴权 + 拒绝 + 幂等）──────────────────────────────────
def _patch_client(monkeypatch, fake):
    async def _gc():
        return fake
    monkeypatch.setattr(api_mod, "_get_client", _gc)


def test_all_four_require_key():
    assert client.post("/internal/runtime/workflow_start", json={"template": "fin.instrument_sync"}).status_code == 401
    assert client.get("/internal/runtime/workflow_get", params={"workflow_id": "x"}).status_code == 401
    assert client.post("/internal/runtime/workflow_pause", json={"workflow_id": "x"}).status_code == 401
    assert client.post("/internal/runtime/workflow_cancel", json={"workflow_id": "x"}).status_code == 401


def test_http_start_rejects_unknown_template(monkeypatch):
    _patch_client(monkeypatch, _FakeClient())
    r = client.post("/internal/runtime/workflow_start", headers=KEY,
                    json={"template": "fin.whatever"})
    assert r.status_code == 404
    assert "未知工作流模板" in r.json()["detail"]


def test_http_start_rejects_unknown_param(monkeypatch):
    _patch_client(monkeypatch, _FakeClient())
    r = client.post("/internal/runtime/workflow_start", headers=KEY,
                    json={"template": "fin.instrument_sync", "params": {"market": "us", "x": 1}})
    assert r.status_code == 400


def test_http_start_ok(monkeypatch):
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)
    r = client.post("/internal/runtime/workflow_start", headers=KEY,
                    json={"template": "fin.instrument_sync", "params": {"market": "us"},
                          "workflow_id": "wf-l08"})
    assert r.status_code == 200 and r.json()["workflow_id"] == "wf-l08"


def test_http_get_and_pause_and_cancel(monkeypatch):
    fake = _FakeClient(handle=_Handle(status=1, job_id="job-7"))

    class _Paper:
        def cancel_job(self, job_id):
            return {"status": "CANCEL_REQUESTED"}

    _patch_client(monkeypatch, fake)
    # get
    r = client.get("/internal/runtime/workflow_get", headers=KEY, params={"workflow_id": "wf-1"})
    assert r.status_code == 200 and r.json()["status"] == "RUNNING"
    # pause（工作流信号）
    r = client.post("/internal/runtime/workflow_pause", headers=KEY, json={"workflow_id": "wf-1"})
    assert r.status_code == 200 and r.json()["paused"] is True
    # cancel（走 paper 时用注入的假 paper，避免真连网）
    import app.runtime_control as _rc
    orig = _rc.cancel_impl

    async def _cancel(client_, **kw):
        kw["paper"] = _Paper()
        return await orig(client_, **kw)
    monkeypatch.setattr(_rc, "cancel_impl", _cancel)
    r = client.post("/internal/runtime/workflow_cancel", headers=KEY, json={"workflow_id": "wf-1"})
    assert r.status_code == 200
    body = r.json()
    assert body["cancelled"] is True and body["job_status"] == "CANCEL_REQUESTED"


def test_http_cancel_requires_workflow_id(monkeypatch):
    _patch_client(monkeypatch, _FakeClient())
    r = client.post("/internal/runtime/workflow_cancel", headers=KEY, json={})
    assert r.status_code == 400
