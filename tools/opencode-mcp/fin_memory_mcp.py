"""fin-memory-mcp · **统一经验库查询与沉淀通道**（四期 R2 · 方案 §4.2）

将 `apps/api/app/routers/fin_memory.py` 暴露的经验库唯一读写入口封装为标准 MCP 工具，
供 AtomCode 对话智能体在投研复盘或决策辅助时直接查阅、沉淀项目经验：

- `fin_memory_query`           → `POST /api/internal/fin/memory/query`
- `fin_memory_append_evidence` → `POST /api/internal/fin/memory/evidence`

**安全与隔离契约**：
1. 用户身份由 `.hooks/guard.py`（PreToolUse hook）根据 `session_id` 反查真实登录用户的
   `_hermes_user_id` 注入工具入参；本脚本将其写入请求头 `X-Hunter-User-Id`，服务端据此
   强制校验 `project_id` 归属该用户（跨用户访问一律 404）。
2. 所有硬过滤（防保底测试集泄露 `holdout_tainted=false`、时间边界 `as_of`、决策严口径
   `for_decision`）与八条写入硬校验（证据真实存在、结论禁裸阿拉伯数字等）均在后端唯一入口
   `services/fin/memory.py` 强制执行，本层只做协议桥接。
3. 工具返回出口统一经过 `hca_size_guard.guard_mcp_text`（16KB 三档大小闸保护）。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    from hca_size_guard import guard_mcp_text
except ImportError:                                            # pragma: no cover
    import sys as _sys
    print("[hca] ⚠️ 没找到 hca_size_guard，MCP 返回的大小闸**未生效**"
          "（镜像里应当在 /opt/hca/mcp/hca_size_guard.py）", file=_sys.stderr, flush=True)

    def guard_mcp_text(text: str, tool_name: str = "", *, tool: str = "", max_bytes=None) -> str:  # type: ignore[misc]
        return text


def _load_internal_key() -> str:
    val = (os.getenv("HUNTER_INTERNAL_KEY") or "").strip()
    if val:
        return val
    key_file = (os.getenv("HUNTER_INTERNAL_KEY_FILE") or "/opt/hunter-secrets/internal_key").strip()
    if key_file:
        try:
            p = Path(key_file)
            if p.is_file():
                return p.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    return "hunter-internal-2026"


HERMES_API = os.getenv("HERMES_API_URL", "http://api:8000").rstrip("/")
INTERNAL_KEY = _load_internal_key()

server = Server("fin-memory-mcp")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="fin_memory_query",
            description=(
                "**查询智能炒股项目的统一经验库**（四期 R2 唯一读入口）。\n"
                "当用户询问『查一下我们项目在震荡市有哪些已验证的止损经验』『看看港股项目的历史复盘结论』"
                "或需要结合项目历史经验辅助判断时调用。\n\n"
                "- 服务端自动过滤掉保底测试集污染条目（`holdout_tainted=false`）及晚于 `as_of` 的记录；\n"
                "- 若 `for_decision=true`，服务端只返回 `kind IN ('fact','verified')` 且 `status='已确认'` 的未过期经验（假设不下单）；\n"
                "- 若 `freeze=true`，服务端会同时将本次命中集合冻结为一条 `fin_memory_snapshot` 并返回 `memory_snapshot_id`。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "智能炒股项目 ID（如 prj_...）",
                    },
                    "market": {
                        "type": "string",
                        "enum": ["CN_A", "HK", "US"],
                        "description": "可选 · 按市场过滤（同时包含跨市场通用经验）",
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["fact", "hypothesis", "verified"],
                        "description": "可选 · 经验类型：fact(事实) / hypothesis(待验证假设) / verified(已验证结论)",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["待验证", "已确认", "已推翻"],
                        "description": "可选 · 经验状态过滤",
                    },
                    "for_decision": {
                        "type": "boolean",
                        "default": False,
                        "description": "是否用于交易决策路径（为 true 时仅返回已确认且未过期的 fact/verified）",
                    },
                    "as_of": {
                        "type": "string",
                        "description": "可选 · 认知基准时间（ISO-8601），不传则取当前时刻",
                    },
                    "freeze": {
                        "type": "boolean",
                        "default": False,
                        "description": "是否顺带冻结本次查询快照并返回 memory_snapshot_id",
                    },
                    "purpose": {
                        "type": "string",
                        "enum": ["decision", "review", "holdout"],
                        "default": "decision",
                        "description": "查询用途标记",
                    },
                    "trade_date": {
                        "type": "string",
                        "description": "可选 · 交易日 YYYY-MM-DD（freeze=true 时记录）",
                    },
                    "point": {
                        "type": "string",
                        "description": "可选 · 时点标识（如 preopen / decide / review）",
                    },
                    "_hermes_user_id": {
                        "type": "string",
                        "description": "内部字段 · 由 guard.py 自动注入当前登录用户 ID · 请勿手工填写",
                    },
                },
                "required": ["project_id"],
            },
        ),
        Tool(
            name="fin_memory_append_evidence",
            description=(
                "**向智能炒股项目经验库追加一条带真实证据的经验**（四期 R2 唯一写入口）。\n"
                "当复盘或投研分析得出需要沉淀到项目经验库的事实、假设或已验证结论时调用。\n\n"
                "**服务端八条硬校验（任一不过返回 400，请如实转述错误原因）**：\n"
                "1. `evidence` 至少包含 1 条真实存在的库内引用（`trade` / `report` / `fact` / `snapshot`）；\n"
                "2. `kind='hypothesis'` 时 `status` 强制为 `'待验证'`；\n"
                "3. 每条证据的 `ref_id` 必须在对应表中真实存在，严禁编造 ID；\n"
                "4. `kind='verified'` 时必须提供 `method` 与 `sample_size`（>=1）；\n"
                "5. **`statement` 结论文本中严禁出现任何阿拉伯数字（0-9）**，具体数字一律放在关联的 `fact`/`report`/`trade` 证据中；\n"
                "6. `memory_layer='strategy'` 时必须提供 `strategy_keys` 且 `kind='verified'`。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "智能炒股项目 ID（如 prj_...）",
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["fact", "hypothesis", "verified"],
                        "description": "经验类别：fact / hypothesis / verified",
                    },
                    "statement": {
                        "type": "string",
                        "description": "一句话定性结论（严禁包含阿拉伯数字 0-9，否则服务端拒绝 400）",
                    },
                    "evidence": {
                        "type": "array",
                        "description": "支撑该经验的真实证据列表（至少 1 条）",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "evidence_kind": {
                                    "type": "string",
                                    "enum": ["trade", "report", "fact", "snapshot"],
                                },
                                "ref_id": {
                                    "type": "string",
                                    "description": "库内真实存在的记录 ID（fact 为 report_id:metric_key）",
                                },
                                "weight": {"type": "number"},
                                "note": {"type": "string"},
                                "holdout_tainted": {"type": "boolean", "default": False},
                            },
                            "required": ["evidence_kind", "ref_id"],
                        },
                    },
                    "market": {
                        "type": "string",
                        "enum": ["CN_A", "HK", "US"],
                        "description": "可选 · 适用市场（不传表示跨市场通用）",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["待验证", "已确认", "已推翻"],
                    },
                    "applicability": {"type": "string", "description": "适用边界说明（可含数字）"},
                    "invalidation_condition": {"type": "string", "description": "失效/证伪条件（可含数字）"},
                    "method": {"type": "string", "description": "验证方法（kind='verified' 时必填）"},
                    "sample_size": {"type": "integer", "description": "样本量（kind='verified' 时必填且 >=1）"},
                    "uncertainty": {"type": "number"},
                    "confidence": {"type": "number"},
                    "as_of": {"type": "string"},
                    "valid_from": {"type": "string"},
                    "valid_until": {"type": "string"},
                    "memory_snapshot_id": {"type": "string"},
                    "supersedes": {"type": "string", "description": "可选 · 被本条推翻或替代的旧 experience_id"},
                    "memory_layer": {
                        "type": "string",
                        "enum": ["episodic", "semantic", "procedural", "strategy"],
                    },
                    "polarity": {
                        "type": "string",
                        "enum": ["support", "refute", "neutral"],
                    },
                    "symbols": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "规范化标的列表（如 ['CN_A:600519', 'HK:00700']）",
                    },
                    "strategy_keys": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "regime_tags": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["bull", "bear", "range", "unknown"]},
                    },
                    "regime_source": {"type": "string"},
                    "importance": {"type": "number"},
                    "_hermes_user_id": {
                        "type": "string",
                        "description": "内部字段 · 由 guard.py 自动注入当前登录用户 ID · 请勿手工填写",
                    },
                },
                "required": ["project_id", "kind", "statement", "evidence"],
            },
        ),
    ]


def _extract_uid(args: dict) -> str:
    return ((args or {}).get("_hermes_user_id") or os.getenv("HUNTER_USER_ID", "")).strip()


def _headers(uid: str) -> dict[str, str]:
    h = {
        "X-Hunter-Internal-Key": INTERNAL_KEY,
        "Content-Type": "application/json",
    }
    if uid:
        h["X-Hunter-User-Id"] = uid
    return h


def _fail(kind: str, detail: str) -> str:
    return json.dumps({
        "error": detail,
        "type": "fin_memory_error",
        "code": kind,
        "hermes_api": HERMES_API,
        "instruction": "经验库接口本次调用未成功。**请如实告知用户原因，严禁凭记忆编造经验条目或写入成功状态**。",
    }, ensure_ascii=False)


async def _post(path: str, body: dict, uid: str) -> str:
    url = f"{HERMES_API}{path}"
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(url, json=body, headers=_headers(uid))
        return r.text
    except Exception as e:  # noqa: BLE001
        return _fail("unreachable", f"无法连接经验库服务（{type(e).__name__}: {e}）")


@server.call_tool()
async def call_tool(name: str, args: dict) -> list[TextContent]:
    args = args or {}
    uid = _extract_uid(args)
    body = {k: v for k, v in args.items() if not k.startswith("_") and v is not None}

    if name == "fin_memory_query":
        text = await _post("/api/internal/fin/memory/query", body, uid)
    elif name == "fin_memory_append_evidence":
        text = await _post("/api/internal/fin/memory/evidence", body, uid)
    else:
        text = _fail("unknown_tool", f"未知工具 {name}；本服务仅提供 fin_memory_query 与 fin_memory_append_evidence")

    return [TextContent(type="text", text=guard_mcp_text(text, tool_name=name))]


async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    print(f"[fin-memory-mcp] boot · hermes_api={HERMES_API} key_set={bool(INTERNAL_KEY)}", file=sys.stderr)
    asyncio.run(main())
