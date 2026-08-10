"""MCP Tool 目录（内嵌版，Phase 1/2 不外拆进程）

统一签名：async def _tool_impl(tc: ToolCall, bus: StreamBus) -> ToolResult
通过 @ToolRegistry.register 装饰器注册。
"""
