"""event_schemas 契约测试"""
import pytest
from app.services.agent.event_schemas import validate_event


def test_session_valid():
    validate_event("session", {
        "session_id": "sess_abc", "message_id": "msg-1",
        "stock_context": {"code": "600519", "name": "贵州茅台"},
    })


def test_router_decision_valid():
    validate_event("router_decision", {
        "reason": "综合问需要 3 专家",
        "tool_calls": [
            {"tool_id": "t1", "name": "research", "args": {"code": "600519"}},
            {"tool_id": "t2", "name": "quant_predict", "args": {"code": "600519"}},
        ],
    })


def test_tool_result_ok():
    validate_event("tool_result", {
        "tool_id": "t1", "name": "research", "status": "ok",
        "duration_ms": 8500,
        "summary": {"conclusion": "谨慎持有"},
    })


def test_tool_result_error():
    validate_event("tool_result", {
        "tool_id": "t2", "name": "quant_predict", "status": "error",
        "duration_ms": 21000,
        "error": {"code": "UPSTREAM_TIMEOUT", "message": "Kronos 超时"},
    })


def test_error_payload():
    validate_event("error", {
        "code": "LLM_FAILED", "message": "fc failed",
        "recoverable": True, "fallback": "keyword_route",
    })


def test_unknown_event_raises():
    with pytest.raises(ValueError):
        validate_event("nonexistent_event_name", {})


def test_error_payload_rejects_invalid_code():
    with pytest.raises(Exception):
        validate_event("error", {"code": "MADE_UP", "message": "x"})
