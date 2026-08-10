"""统一 sub-agent + MCP tool 注册中心。

对外抽象：
  - ToolCall：主 agent function_call decode 后的结构化调用
  - ToolResult：dispatch 后的结果（含 ok/error 两种）
  - ToolRegistry：类级注册表（handler + openai tool_defs + timeout）

注册示例：
    @ToolRegistry.register("get_quote", definition={...}, timeout=5)
    async def _get_quote(tc: ToolCall, bus) -> ToolResult:
        ...
"""
from __future__ import annotations
import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# 中文 display_name 映射（供前端 tool_use 展示）
_DEFAULT_DISPLAY: dict[str, str] = {
    "research": "深度研究",
    "scout": "一手情报",
    "quant_predict": "量化择时",
    "hold_judge": "持仓研判",
    "event_interpret": "事件解读",
    "get_quote": "实时行情",
    "get_kline": "K 线",
    "get_pe_history": "PE 分位",
    # 自选股整合（P0-P2）
    "stock_quickview": "单股速答",
    "stock_news": "关键新闻",
    "watchlist_digest": "自选股日报",
    "portfolio_rebalance": "组合级建议",
    "portfolio_stress": "情景模拟",
    # 持仓建议 Sprint 1
    "update_risk_profile": "风险画像",
}

# 需要 code 参数（若 args 缺失则从 session 上下文自动补）
_NEED_CODE = {"research", "scout", "quant_predict", "hold_judge",
              "event_interpret", "get_quote", "get_kline", "get_pe_history",
              "stock_quickview", "stock_news"}

# 需要 user_id（从 session 上下文自动补 · 自选/组合类 tool）
_NEED_USER_ID = {"stock_quickview", "watchlist_digest",
                 "portfolio_rebalance", "portfolio_stress",
                 "update_risk_profile"}


# ─────────────────────────────── data classes ───────────────────────────────
@dataclass
class ToolCall:
    tool_id: str
    name: str
    args: dict = field(default_factory=dict)
    display_name: str = ""

    @classmethod
    def from_openai(cls, tc, ctx_code: str | None = None,
                    ctx_user_id: str | None = None) -> "ToolCall":
        """把 openai SDK 的 tool_call 对象解析为 ToolCall。"""
        try:
            args = json.loads(tc.function.arguments or "{}")
        except Exception:
            args = {}
        name = tc.function.name
        # 上下文注入：session 有股票码则补 code
        if ctx_code and "code" not in args and name in _NEED_CODE:
            args["code"] = ctx_code
        # 上下文注入：session 有 user_id 则补 user_id（自选/组合类 tool 需要）
        if ctx_user_id and "user_id" not in args and name in _NEED_USER_ID:
            args["user_id"] = ctx_user_id
        return cls(
            tool_id=tc.id or f"t-{uuid.uuid4().hex[:8]}",
            name=name,
            args=args,
            display_name=_DEFAULT_DISPLAY.get(name, name),
        )

    def to_dict(self) -> dict:
        return {"tool_id": self.tool_id, "name": self.name, "args": self.args}

    def to_use_payload(self) -> dict:
        return {
            "tool_id": self.tool_id,
            "name": self.name,
            "display_name": self.display_name or _DEFAULT_DISPLAY.get(self.name, self.name),
            "started_at": _now_iso(),
        }


@dataclass
class ToolResult:
    tool_call: ToolCall
    status: str                     # "ok" | "error"
    duration_ms: int = 0
    summary: dict = field(default_factory=dict)
    detail_ref: Optional[dict] = None
    error: Optional[dict] = None

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "tool_id": self.tool_call.tool_id,
            "name": self.tool_call.name,
            "status": self.status,
            "duration_ms": self.duration_ms,
        }
        if self.status == "ok":
            d["summary"] = self.summary
            if self.detail_ref:
                d["detail_ref"] = self.detail_ref
        else:
            d["error"] = self.error or {"code": "UNKNOWN", "message": ""}
        return d

    @classmethod
    def error_of(cls, tc: ToolCall, code: str, message: str,
                  duration_ms: int = 0) -> "ToolResult":
        return cls(
            tool_call=tc, status="error", duration_ms=duration_ms,
            error={"code": code, "message": message},
        )


# ─────────────────────────────── registry ───────────────────────────────
HandlerFn = Callable[[ToolCall, Any], Awaitable[ToolResult]]


class ToolRegistry:
    """类级单例注册中心。register 是装饰器；dispatch 走超时保护。"""

    _handlers: dict[str, HandlerFn] = {}
    _defs: dict[str, dict] = {}     # 用 dict 存以避免重复注册（模块被 import 多次时幂等）
    _timeouts: dict[str, int] = {}   # 秒

    # ────── 注册 ──────
    @classmethod
    def register(cls, name: str, definition: dict, timeout: int = 30):
        def _wrap(fn: HandlerFn) -> HandlerFn:
            cls._handlers[name] = fn
            cls._defs[name] = definition
            cls._timeouts[name] = timeout
            return fn
        return _wrap

    @classmethod
    def clear(cls) -> None:
        """仅测试用"""
        cls._handlers.clear()
        cls._defs.clear()
        cls._timeouts.clear()

    # ────── 查询 ──────
    @classmethod
    def openai_tool_defs(cls) -> list[dict]:
        """转成 OpenAI function-calling tools 格式"""
        return [{"type": "function", "function": d} for d in cls._defs.values()]

    @classmethod
    def known_tools(cls) -> list[str]:
        return list(cls._defs.keys())

    # ────── 调度 ──────
    @classmethod
    async def dispatch(cls, tc: ToolCall, bus, use_cache: bool = True) -> ToolResult:
        handler = cls._handlers.get(tc.name)
        if handler is None:
            return ToolResult.error_of(tc, "UNKNOWN_TOOL", f"未注册工具: {tc.name}")

        # 命中 Redis 缓存 → 直接返回（duration 记 <1ms 便于监控）
        if use_cache:
            try:
                from . import cache as _cache  # 延迟 import 避免循环
                cached = _cache.get(tc.name, tc.args)
            except Exception:
                cached = None
            if cached and "summary" in cached:
                return ToolResult(
                    tool_call=tc, status="ok", duration_ms=0,
                    summary=cached["summary"],
                    detail_ref=cached.get("detail_ref"),
                )

        timeout = cls._timeouts.get(tc.name, 30)
        t0 = time.time()
        try:
            result = await asyncio.wait_for(handler(tc, bus), timeout=timeout)
        except asyncio.TimeoutError:
            return ToolResult.error_of(
                tc, "UPSTREAM_TIMEOUT",
                f"工具 {tc.name} 超时 (>{timeout}s)",
                duration_ms=int((time.time() - t0) * 1000),
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            return ToolResult.error_of(
                tc, "INTERNAL", f"{type(e).__name__}: {e}",
                duration_ms=int((time.time() - t0) * 1000),
            )

        # 成功结果写缓存
        if use_cache and result.status == "ok":
            try:
                from . import cache as _cache
                _cache.put(tc.name, tc.args, result.summary, result.detail_ref)
            except Exception:
                pass
        return result


# ─────────────────────────────── 便捷工具 ───────────────────────────────
def new_tool_call(name: str, args: dict, ctx_code: str | None = None,
                  ctx_user_id: str | None = None) -> ToolCall:
    """手动构造 ToolCall（用于 fallback 路径 / 单测）"""
    if ctx_code and "code" not in args and name in _NEED_CODE:
        args = {**args, "code": ctx_code}
    if ctx_user_id and "user_id" not in args and name in _NEED_USER_ID:
        args = {**args, "user_id": ctx_user_id}
    return ToolCall(
        tool_id=f"t-{uuid.uuid4().hex[:8]}",
        name=name, args=args,
        display_name=_DEFAULT_DISPLAY.get(name, name),
    )


def load_all_tools() -> None:
    """按需 import 所有 mcp + subagents 模块，触发装饰器注册。

    orchestrator 启动时调一次即可；重复调用无副作用（因 dict 幂等）。
    单个模块 import 失败不影响其余（打 warning）。
    """
    from loguru import logger as _lg
    # MCP tools
    _import_or_warn("app.services.mcp.market_tools", _lg)
    # Sub-agents（P2.S3 起 5 个专家全注册）
    _import_or_warn("app.services.subagents.research_agent", _lg)
    _import_or_warn("app.services.subagents.scout_agent",    _lg)
    _import_or_warn("app.services.subagents.quant_agent",    _lg)
    _import_or_warn("app.services.subagents.hold_agent",     _lg)
    _import_or_warn("app.services.subagents.event_agent",    _lg)
    # 自选股整合（P0-P2）· 5 个新 tool 分两个模块
    _import_or_warn("app.services.subagents.watchlist_agent", _lg)
    _import_or_warn("app.services.subagents.portfolio_agent", _lg)
    # Skill tool（P3.S6 起）
    _import_or_warn("app.services.agent.skill_tool", _lg)
    # 触发 skill 加载（幂等）
    try:
        from . import skill_loader as _sl
        _sl.load_all_skills()
    except Exception as e:
        _lg.warning("[tool_registry] load_all_skills 失败: {}", e)


def _import_or_warn(mod: str, logger) -> None:
    import importlib
    try:
        importlib.import_module(mod)
    except Exception as e:
        logger.warning("[tool_registry] 加载 {} 失败（跳过）: {}", mod, e)
