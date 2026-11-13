"""`broker_funds` —— P1 券商 ETF、公募基金与可转债只读 MCP 服务。"""
from .server import SERVER_NAME, SERVER_VERSION, TOOLS, call_tool_sync

__all__ = ["SERVER_NAME", "SERVER_VERSION", "TOOLS", "call_tool_sync"]
