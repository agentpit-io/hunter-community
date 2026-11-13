"""券商专属只读 MCP 服务公共底座（`_shared.py`）。

提供：
1. 自动探测并挂载 `apps/api` 与 `tools/opencode-mcp` 到 `sys.path`，使 MCP 进程无论在
   宿主机开发目录还是在 `hca-daemon` 容器内（`/opt/hca/mcp-servers`）都能直接调用
   `BrokerDataRouter` 与 `hca_size_guard`。
2. 统一出口大小闸封装 `dump_guarded_envelope(envelope, tool_name, max_bytes)`：
   将 `BrokerDataEnvelope` 序列化为 JSON 并强制通过 16KB 三档大小闸 `hca_size_guard.fit()`，
   确保超长列表（如全市场公告列表、长周期 K 线）永远返回 ≤ 16KB 的合法 JSON 并带
   `_hca_size_guard` 裁剪说明（TC-MCP-05）。
3. 零依赖、毫秒级启动的 MCP Stdio JSON-RPC 2.0 服务端循环 `run_stdio_mcp_server`，
   同时导出标准 `TOOLS` 与 `call_tool_sync(name, arguments)` 供单元测试与契约探针直接调用。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent

for candidate in [
    REPO_ROOT / "apps" / "api",
    REPO_ROOT / "tools" / "opencode-mcp",
    Path("/opt/hca/mcp"),
    Path("/app"),
]:
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

try:
    from hca_size_guard import fit as _size_guard_fit
except ImportError:
    from tools.opencode_mcp.hca_size_guard import fit as _size_guard_fit  # type: ignore[no-redef]

from app.providers.broker import BrokerDataEnvelope, BrokerDataRouter, get_default_broker_router


def get_router() -> BrokerDataRouter:
    """获取当前 MCP 进程的 BrokerDataRouter 实例（支持环境变量控制主备开关）。"""
    allow_fb = os.getenv("BROKER_ALLOW_FALLBACK", "1").strip() not in ("0", "false", "False")
    sim_timeout = os.getenv("BROKER_SIMULATE_TIMEOUT", "0").strip() in ("1", "true", "True")
    if sim_timeout or not allow_fb:
        from app.providers.broker.galaxy_adapter import GalaxyBrokerAdapter
        from app.providers.broker.public_adapter import PublicFallbackAdapter

        return BrokerDataRouter(
            primary=GalaxyBrokerAdapter(simulate_timeout=sim_timeout),
            fallback=PublicFallbackAdapter(),
            allow_fallback=allow_fb,
        )
    return get_default_broker_router()


def dump_guarded_envelope(
    envelope: BrokerDataEnvelope,
    tool_name: str,
    *,
    max_bytes: int | None = None,
) -> str:
    """将统一数据信封序列化为 JSON 字符串，并强制经过 16KB 三档大小闸处理。"""
    payload = envelope.model_dump(mode="json")
    raw_json = json.dumps(payload, ensure_ascii=False)
    return _size_guard_fit(raw_json, tool=tool_name, max_bytes=max_bytes)


def run_stdio_mcp_server(
    server_name: str,
    server_version: str,
    tools_spec: list[dict[str, Any]],
    dispatcher: Callable[[str, dict[str, Any]], str],
) -> None:
    """运行标准 MCP (Model Context Protocol) JSON-RPC 2.0 Stdio 服务端。

    兼容 AtomCode 5.1.0 daemon、`dump_mcp_tools.py` 与 MCP Inspector。
    """
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = msg.get("method", "")
        msg_id = msg.get("id")
        params = msg.get("params") or {}

        # 通知类消息无需响应
        if msg_id is None:
            continue

        if method == "initialize":
            proto = params.get("protocolVersion") or "2024-11-05"
            resp = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": proto,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": server_name, "version": server_version},
                },
            }
        elif method == "ping":
            resp = {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        elif method == "tools/list":
            resp = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": tools_spec},
            }
        elif method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments") or {}
            try:
                text_out = dispatcher(tool_name, arguments)
                resp = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": text_out}],
                        "isError": False,
                    },
                }
            except Exception as exc:  # noqa: BLE001
                err_payload = json.dumps(
                    {
                        "ok": False,
                        "data": None,
                        "errors": [{"code": "TOOL_EXECUTION_ERROR", "message": str(exc)}],
                    },
                    ensure_ascii=False,
                )
                resp = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": err_payload}],
                        "isError": True,
                    },
                }
        else:
            resp = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }

        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()
