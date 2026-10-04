# -*- coding: utf-8 -*-
"""L04 · fin-worker 从**自有策略服务**解析当前生效版本（假客户端，不连网不连库）。

    cd apps/fin-worker && python -m pytest tests/test_strategy_resolve.py -q

盯住两件会静默出错的事：

1. 策略服务返回稳定版本键（`strv_…`）时，决策对象带的就是它 —— 于是能反查到策略登记行；
2. **策略服务不可用时回退到 `fin_param` 声明的原值** —— 决策不因策略服务挂掉而停摆，
   且回退如实记 warning（不静默）。
"""
from __future__ import annotations

import pytest

from app import activities


class _FakeApi:
    """假 `HunterApiClient`：只实现 `strategy_active`。"""

    def __init__(self, *, payload=None, raises=None):
        self._payload = payload
        self._raises = raises
        self.calls = 0

    def strategy_active(self, project_id):
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return self._payload


def _install(monkeypatch, fake):
    monkeypatch.setattr(activities, "HunterApiClient", lambda *a, **kw: fake)


ACTIVE = {"key": "ma_momentum", "version": "v1", "active": True}


def test_resolves_registered_version(monkeypatch):
    _install(monkeypatch, _FakeApi(payload={
        "strategy_key": "ma_momentum", "strategy_version": "strv_abc123",
        "resolved_by": "declared_version", "definition": {"strategy_key": "ma_momentum"}}))
    key, version = activities._resolve_strategy("prj_x", ACTIVE)
    assert (key, version) == ("ma_momentum", "strv_abc123")


def test_falls_back_when_service_unreachable(monkeypatch):
    _install(monkeypatch, _FakeApi(raises=RuntimeError("api 没起")))
    key, version = activities._resolve_strategy("prj_x", ACTIVE)
    assert (key, version) == ("ma_momentum", "v1")     # 回退声明值，逐字同 L04 之前


def test_falls_back_when_unregistered(monkeypatch):
    _install(monkeypatch, _FakeApi(payload={
        "strategy_key": "no_such", "strategy_version": "v9",
        "resolved_by": "unregistered", "definition": None}))
    key, version = activities._resolve_strategy("prj_x", ACTIVE)
    assert (key, version) == ("ma_momentum", "v1")


def test_falls_back_when_no_project(monkeypatch):
    fake = _FakeApi(payload={"strategy_version": "strv_zzz", "resolved_by": "declared_version"})
    _install(monkeypatch, fake)
    key, version = activities._resolve_strategy("", ACTIVE)
    assert (key, version) == ("ma_momentum", "v1")
    assert fake.calls == 0                             # 没项目就不去问


def test_default_key_when_no_active():
    """没有 active 策略条目 → 用示例策略的默认 key / version（同 L04 之前）。"""
    key, version = activities._resolve_strategy("", {})
    assert key and version


# ── L04 · 绑定不可变数据集快照（L03 §八 交接）────────────────────────────

from datetime import datetime, timezone  # noqa: E402


class _FakeSnapApi:
    def __init__(self, row=None, raises=None):
        self._row = row
        self._raises = raises
        self.body = None

    def data_snapshot_create(self, **kw):
        self.body = kw
        if self._raises is not None:
            raise self._raises
        return self._row


def test_bind_data_snapshot_sets_id(monkeypatch):
    fake = _FakeSnapApi(row={"data_snapshot_id": "DSNAP-abc123"})
    monkeypatch.setattr(activities, "HunterApiClient", lambda *a, **kw: fake)
    doc = {"data_snapshot": {"kind": "project_params"}}
    activities._bind_data_snapshot(doc, market="CN_A", code="601398",
                                   now=datetime(2026, 10, 5, tzinfo=timezone.utc),
                                   project={"tier": "play"})
    assert doc["data_snapshot_id"] == "DSNAP-abc123"
    assert doc["data_snapshot"]["data_snapshot_id"] == "DSNAP-abc123"
    # 只传算得出真值的字段：available_at / revision_id 不传（拿不到真值 → NULL，红线 5）。
    assert "available_at" not in fake.body and "revision_id" not in fake.body
    assert fake.body["source"] == "project_params"


def test_bind_data_snapshot_leaves_empty_on_failure(monkeypatch):
    monkeypatch.setattr(activities, "HunterApiClient",
                        lambda *a, **kw: _FakeSnapApi(raises=RuntimeError("api 没起")))
    doc: dict = {"data_snapshot": {}}
    activities._bind_data_snapshot(doc, market="CN_A", code="601398",
                                   now=datetime(2026, 10, 5, tzinfo=timezone.utc),
                                   project={})
    assert "data_snapshot_id" not in doc        # 建不出就留空 → 落库 NULL（不编假键）


def test_bind_data_snapshot_marks_reference_quote(monkeypatch):
    fake = _FakeSnapApi(row={"data_snapshot_id": "DSNAP-hk"})
    monkeypatch.setattr(activities, "HunterApiClient", lambda *a, **kw: fake)
    doc: dict = {}
    activities._bind_data_snapshot(doc, market="HK", code="00700",
                                   now=datetime(2026, 10, 5, tzinfo=timezone.utc),
                                   project={}, reference_price="421.0")
    assert fake.body["source"] == "project_params+reference_quote"
