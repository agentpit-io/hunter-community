"""M2 · 新增 MCP 工具（runtime_mcp / fin_memory_mcp / watchlist 文档检索翻阅）与 16KB 大小闸单测。"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MCP_DIR = REPO / "tools" / "opencode-mcp"
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from hca_size_guard import MAX_BYTES, guard_mcp_text, nbytes  # noqa: E402


def _load_mcp_script(script_name: str):
    captured: dict = {}

    class _Server:
        def __init__(self, name):
            captured["name"] = name

        def list_tools(self):
            def deco(fn):
                captured["list_tools"] = fn
                return fn
            return deco

        def call_tool(self):
            def deco(fn):
                captured["call_tool"] = fn
                return fn
            return deco

        async def run(self, *a, **k):
            return None

        @staticmethod
        def create_initialization_options():
            return {}

    class _Tool:
        def __init__(self, name, description, inputSchema):
            self.name = name
            self.description = description
            self.inputSchema = inputSchema

    class _TextContent:
        def __init__(self, type, text):
            self.type = type
            self.text = text

    mods = {
        "mcp": types.ModuleType("mcp"),
        "mcp.server": types.ModuleType("mcp.server"),
        "mcp.server.stdio": types.ModuleType("mcp.server.stdio"),
        "mcp.types": types.ModuleType("mcp.types"),
    }
    mods["mcp.server"].Server = _Server
    mods["mcp.server.stdio"].stdio_server = lambda *a, **k: None
    mods["mcp.types"].Tool = _Tool
    mods["mcp.types"].TextContent = _TextContent
    for k, m in mods.items():
        sys.modules[k] = m

    spec = importlib.util.spec_from_file_location(
        f"test_{script_name.replace('.py', '')}", MCP_DIR / script_name
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, captured


def test_guard_mcp_text_alias_and_size_limit():
    huge = json.dumps({"items": [{"idx": i, "text": "数" * 100} for i in range(500)]}, ensure_ascii=False)
    assert nbytes(huge) > MAX_BYTES
    out = guard_mcp_text(huge, tool_name="fin_memory_query")
    assert nbytes(out) <= MAX_BYTES
    parsed = json.loads(out)
    assert parsed["_hca_size_guard"]["tool"] == "fin_memory_query"


def test_fin_memory_mcp_tools_and_size_guard(monkeypatch):
    mod, captured = _load_mcp_script("fin_memory_mcp.py")
    tools = asyncio.run(captured["list_tools"]())
    assert [t.name for t in tools] == ["fin_memory_query", "fin_memory_append_evidence"]

    calls: list[tuple[str, dict, str]] = []

    async def _fake_post(path: str, body: dict, uid: str) -> str:
        calls.append((path, body, uid))
        # 构造超过 16KB 的响应以验证 guard_mcp_text 生效
        return json.dumps(
            {"ok": True, "experiences": [{"id": f"exp_{i}", "statement": "稳健复盘结论" * 30} for i in range(300)]},
            ensure_ascii=False,
        )

    monkeypatch.setattr(mod, "_post", _fake_post)

    res_q = asyncio.run(
        captured["call_tool"](
            "fin_memory_query",
            {"project_id": "prj_demo", "market": "HK", "_hermes_user_id": "u_alice"},
        )
    )
    assert calls[0] == ("/api/internal/fin/memory/query", {"project_id": "prj_demo", "market": "HK"}, "u_alice")
    assert nbytes(res_q[0].text) <= MAX_BYTES
    data_q = json.loads(res_q[0].text)
    assert "_hca_size_guard" in data_q

    res_a = asyncio.run(
        captured["call_tool"](
            "fin_memory_append_evidence",
            {
                "project_id": "prj_demo",
                "kind": "hypothesis",
                "statement": "震荡市缩量回踩支撑位有效性待观察",
                "evidence": [{"evidence_kind": "report", "ref_id": "rpt_1"}],
                "_hermes_user_id": "u_alice",
            },
        )
    )
    assert calls[1][0] == "/api/internal/fin/memory/evidence"
    assert calls[1][2] == "u_alice"
    assert nbytes(res_a[0].text) <= MAX_BYTES


def test_runtime_mcp_size_guard_applied(monkeypatch):
    mod, captured = _load_mcp_script("runtime_mcp.py")

    async def _fake_call(method, path, *, params=None, body=None):
        return json.dumps(
            {"ok": True, "history": [{"step": i, "detail": "x" * 200} for i in range(200)]},
            ensure_ascii=False,
        )

    monkeypatch.setattr(mod, "_call", _fake_call)
    res = asyncio.run(captured["call_tool"]("runtime_workflow_get", {"workflow_id": "wf_1"}))
    assert nbytes(res[0].text) <= MAX_BYTES
    assert "_hca_size_guard" in json.loads(res[0].text)


def test_watchlist_doc_search_and_read_pages_routing(monkeypatch):
    mod, captured = _load_mcp_script("watchlist_mcp.py")
    tools = asyncio.run(captured["list_tools"]())
    names = [t.name for t in tools]
    assert "file_search_in_doc" in names
    assert "file_read_pages" in names

    seen: list[tuple[str, dict, dict]] = []

    class _FakeResp:
        status_code = 200
        text = json.dumps({"ok": True, "matches": [{"page_no": 2, "content_md": "营收 834.51 亿元"}]}, ensure_ascii=False)

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, json=None, headers=None):
            seen.append((url, json, headers))
            return _FakeResp()

    monkeypatch.setattr(mod.httpx, "AsyncClient", _FakeClient)
    res1 = asyncio.run(
        captured["call_tool"](
            "file_search_in_doc",
            {"file_id": "f_123", "keyword": "营收", "_hermes_user_id": "u_bob"},
        )
    )
    assert seen[0][0].endswith("/api/internal/files/search")
    assert seen[0][1] == {"file_id": "f_123", "keyword": "营收"}
    assert seen[0][2]["X-Hunter-User-Id"] == "u_bob"
    assert json.loads(res1[0].text)["ok"] is True

    res2 = asyncio.run(
        captured["call_tool"](
            "file_read_pages",
            {"file_id": "f_123", "start_page": 1, "end_page": 3, "_hermes_user_id": "u_bob"},
        )
    )
    assert seen[1][0].endswith("/api/internal/files/read_pages")
    assert seen[1][1] == {"file_id": "f_123", "start_page": 1, "end_page": 3}
    assert json.loads(res2[0].text)["ok"] is True
