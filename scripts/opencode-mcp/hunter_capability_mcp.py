"""hunter-capability-mcp · 把平台自有能力暴露成 MCP 工具

`_12` §7 Step 3。**要解决的问题**:K线预测 / 情报 这些是我们最强的能力,
但原来只有 HTTP 接口、不是 MCP —— 模型手上够不着。表现是用户点侧栏卡片能用,
在对话里说"帮我预测茅台走势"却降级成"我无法获取"。

这个 MCP 不在镜像里,通过 docker-compose 的 bind mount 挂进容器,
再由 scripts/opencode/gen-config.py 注册进 opencode.json 的 mcp 段
(镜像自带的 .opencode/opencode.jsonc 注册了另外 4 个,两份配置会合并)。

与镜像内 watchlist_mcp.py 同一套路:POST /api/internal/*,共享 secret 鉴权,
用户身份由 hunter-mcp-context plugin 注入的 _hermes_user_id 带进来。
"""
import asyncio
import json
import os
import sys

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

HERMES_API = os.getenv("HERMES_API_URL", "http://api:8000")
INTERNAL_KEY = os.getenv("HUNTER_INTERNAL_KEY", "")

server = Server("hunter-capability-mcp")

# 各 tool 的超时。scout 是主动全量采集(价格+公告+Gemini 搜索),30-60s 起步,
# 给到 150s;kpred 是 GPU 推理,给 120s。
_TIMEOUT = {"kpred": 120.0, "truesource_scout": 150.0, "truesource_brief": 60.0}


@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="kpred",
            description=(
                "Kronos AI 走势预测 · 清华金融时序大模型。给一只股票预测未来 N 个交易日的"
                "开高低收走势,返回历史 K 线 + 预测点 + 方向判断。"
                "用户问「预测/走势/未来几天怎么走/会涨还是会跌」时用它。"
                "A 股用 6 位代码(600519),港股 5 位(00700),美股用 ticker(AAPL)。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "股票代码,如 600519"},
                    "days": {"type": "integer", "description": "预测天数 1-30,默认 10"},
                },
                "required": ["code"],
            },
        ),
        Tool(
            name="truesource_brief",
            description=(
                "TrueSource 情报简报 · 指定标的最近 3 天的信号摘要与预警级别。"
                "用户问「有什么异动/最近有什么信号/盯一下这几只」时用它。"
                "支持一次传多只,逗号分隔。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbols": {"type": "string",
                                "description": "逗号分隔的股票代码,如 600519,300308"},
                },
                "required": ["symbols"],
            },
        ),
        Tool(
            name="truesource_scout",
            description=(
                "TrueSource 主动情报采集 · 对单只股票并行跑价格+公告+AI 联网搜索,"
                "拿最新的一手信息。**耗时 30-60 秒**,只在用户明确要求"
                "「深挖/查一下最新消息/主动采集」时用,不要在普通问答里调。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票代码"},
                    "name": {"type": "string", "description": "股票名称(可选,提高搜索准确度)"},
                },
                "required": ["symbol"],
            },
        ),
    ]


def _headers(args: dict) -> dict:
    h = {"X-Hunter-Internal-Key": INTERNAL_KEY}
    uid = (args or {}).get("_hermes_user_id") or os.getenv("HUNTER_USER_ID", "")
    if uid:
        h["X-Hunter-User-Id"] = uid
    return h


@server.call_tool()
async def call_tool(name: str, args: dict):
    args = args or {}
    # 剔除内部字段,避免带进 body 让 pydantic 报 422
    body = {k: v for k, v in args.items() if not k.startswith("_")}
    url = f"{HERMES_API}/api/internal/cap/{name}"
    timeout = _TIMEOUT.get(name, 60.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(url, json=body, headers=_headers(args))
        text = r.text
    except httpx.TimeoutException:
        text = json.dumps(
            {"error": "timeout",
             "message": f"{name} 超时({timeout:.0f}s)。这是耗时能力,请稍后重试。"},
            ensure_ascii=False)
    except Exception as e:
        text = json.dumps(
            {"error": "call_failed",
             "message": f"{type(e).__name__}: {e}", "hermes_api": HERMES_API, "tool": name},
            ensure_ascii=False)
    return [TextContent(type="text", text=text)]


async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    print(f"[hunter-capability-mcp] boot · hermes_api={HERMES_API}", file=sys.stderr)
    asyncio.run(main())
