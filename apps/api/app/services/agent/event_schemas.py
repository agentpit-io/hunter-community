"""SSE 事件 payload 定义（对应 03 §3.2.1）。

pydantic v1 兼容写法（项目使用 fastapi + pydantic v1）。
"""
from __future__ import annotations
from typing import Any, Literal, Optional
from pydantic import BaseModel


# ────────────────────────────── 事件 name 枚举 ──────────────────────────────
SSEEventName = Literal[
    "session", "router_decision", "tool_use", "tool_progress",
    "tool_result", "message_delta", "message_end", "error",
]


# ────────────────────────────── payload 模型 ──────────────────────────────
class StockContext(BaseModel):
    code: str
    name: Optional[str] = None


class SessionPayload(BaseModel):
    session_id: str
    message_id: str
    stock_context: Optional[StockContext] = None


class ToolCallDesc(BaseModel):
    tool_id: str
    name: str
    args: dict = {}


class RouterDecisionPayload(BaseModel):
    reason: str
    tool_calls: list[ToolCallDesc]


class ToolUsePayload(BaseModel):
    tool_id: str
    name: str
    display_name: str
    started_at: str  # ISO8601


class ToolProgressPayload(BaseModel):
    tool_id: str
    name: str
    percent: int  # 0-100
    detail: str = ""
    phase: Optional[str] = None


class ToolErrorInfo(BaseModel):
    code: str
    message: str


class ToolResultPayload(BaseModel):
    tool_id: str
    name: str
    status: Literal["ok", "error"]
    duration_ms: int
    summary: Optional[dict] = None
    detail_ref: Optional[dict] = None
    error: Optional[ToolErrorInfo] = None


class MessageDeltaPayload(BaseModel):
    content: str


class UsageInfo(BaseModel):
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_cny: float = 0.0


class MessageEndPayload(BaseModel):
    finish_reason: str = "stop"
    usage: Optional[UsageInfo] = None


class ErrorPayload(BaseModel):
    code: Literal[
        "NO_SESSION", "RATE_LIMIT", "LLM_FAILED",
        "UPSTREAM_TIMEOUT", "INTERNAL",
    ]
    message: str
    recoverable: bool = False
    fallback: Optional[str] = None  # e.g. "keyword_route"


# ────────────────────────────── 反向映射 ──────────────────────────────
EVENT_MODEL: dict[str, type[BaseModel]] = {
    "session": SessionPayload,
    "router_decision": RouterDecisionPayload,
    "tool_use": ToolUsePayload,
    "tool_progress": ToolProgressPayload,
    "tool_result": ToolResultPayload,
    "message_delta": MessageDeltaPayload,
    "message_end": MessageEndPayload,
    "error": ErrorPayload,
}


def validate_event(name: str, data: dict) -> BaseModel:
    """反向校验：orchestrator 发事件前可用于契约测试"""
    model = EVENT_MODEL.get(name)
    if model is None:
        raise ValueError(f"unknown SSE event name: {name}")
    return model(**data)
