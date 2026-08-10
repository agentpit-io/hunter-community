"""Agent Chat V2 观测埋点

采用"先落日志，量大再入表"策略（详见 04 §T-P0-07 决策 5）。
所有埋点走 loguru 结构化字段，字段名与 03 §4.6 SQL schema 对齐，
未来入表时可直接 grok/regex 抽取。

事件类型：
  - router_decision  · 每次 orchestrator 决策
  - tool_use         · 每次 tool 启动
  - tool_result      · 每次 tool 完成（含 error）
  - message_end      · 每次会话结束（含 usage）
  - error            · 全局错误
"""
from __future__ import annotations
import time
from typing import Any, Optional
from loguru import logger


_TAG = "[agent_v2]"


def _log(event_type: str, **fields):
    """结构化日志。字段包含常见维度：user_id, session_id, message_id, ..."""
    parts = [f"{k}={_fmt(v)}" for k, v in fields.items() if v is not None]
    logger.info("{} event={} {}", _TAG, event_type, " ".join(parts))


def _fmt(v: Any) -> str:
    if isinstance(v, str):
        # 去空格 + 截断
        return v.replace(" ", "_").replace("\n", " ")[:120]
    return str(v)


def router_decision(user_id: str, session_id: str, message_id: str,
                     tool_calls: list, reason: str, latency_ms: int):
    _log("router_decision",
         user_id=user_id, session_id=session_id, message_id=message_id,
         tool_count=len(tool_calls),
         tools=",".join(tc.name for tc in tool_calls) or "none",
         reason=reason[:80], latency_ms=latency_ms)


def tool_result(user_id: str, session_id: str, message_id: str,
                 tool_id: str, tool_name: str, status: str,
                 duration_ms: int, error_code: Optional[str] = None):
    _log("tool_result",
         user_id=user_id, session_id=session_id, message_id=message_id,
         tool_id=tool_id, tool_name=tool_name, status=status,
         duration_ms=duration_ms, error_code=error_code)
    # 触发告警指标记录
    try:
        from . import alert as _alert
        _alert.record_tool_result(status)
    except Exception:
        pass


def message_end(user_id: str, session_id: str, message_id: str,
                 model: str, tokens_in: int, tokens_out: int,
                 cost_cny: float, total_ms: int, tool_count: int,
                 fallback: bool = False):
    _log("message_end",
         user_id=user_id, session_id=session_id, message_id=message_id,
         model=model, tokens_in=tokens_in, tokens_out=tokens_out,
         cost_cny=round(cost_cny, 4), total_ms=total_ms,
         tool_count=tool_count, fallback=fallback)
    try:
        from . import alert as _alert
        _alert.record_message_end(total_ms, cost_cny, fallback)
    except Exception:
        pass


def error(user_id: str, session_id: str, code: str, message: str,
          recoverable: bool = False):
    _log("error",
         user_id=user_id, session_id=session_id, code=code,
         message=message[:120], recoverable=recoverable)
