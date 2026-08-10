"""alert 模块 · 无 Redis / 无 webhook 时静默通过"""
import pytest


def test_no_webhook_no_send(monkeypatch):
    from app.services.agent import alert
    # webhook 空时 _send 不会实际请求（但仍会走判定逻辑）
    monkeypatch.setattr(alert, "_WEBHOOK", "")
    # 全流程不抛
    alert.record_message_end(1000, 0.02, False)
    alert.record_tool_result("ok")


def test_no_redis_records_are_noop(monkeypatch):
    from app.services.agent import alert
    monkeypatch.setattr(alert, "_redis", None)
    alert.record_message_end(999999, 999.9, True)  # 应静默通过
    alert.record_tool_result("error")


def test_cooldown_key_stable():
    from app.services.agent.alert import _cooldown_key
    assert _cooldown_key("error_rate") == "agent:alert:cooldown:error_rate"
