"""watchlist-mcp · 覆盖版

镜像 (ghcr.io/agentpit-io/hunter-opencode) 里 /opt/opencode-workspace/mcp/watchlist_mcp.py
只有 3 个 read tool(stock_quickview · stock_news · watchlist_digest),没有 write。
用户在 chat 里说『加紫金矿业到自选』时 LLM 无工具可调,只能吐文本兜底,
指着一个不一定存在的 UI 按钮。

这一份加了 `watchlist_add` · 通过 docker-compose.yml 的 bind mount 盖住镜像内文件:

    volumes:
      - ./scripts/opencode-mcp/watchlist_mcp.py:/opt/opencode-workspace/mcp/watchlist_mcp.py:ro

下游后端 endpoint 见 apps/api/app/routers/internal_tools.py::_api_add,
复用现有 GET /api/watchlist/search(东方财富 suggest)拿完整字段落库。

其余 tool 结构和镜像内一致(不然会掉),diff 只有 list_tools 里多一个 Tool 声明。
"""
import asyncio
import json
import os
import sys

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# docker bridge · 生产 172.17.0.1(host 主机 IP)· 本机 test 可用 host.docker.internal
HERMES_API   = os.getenv("HERMES_API_URL",      "http://172.17.0.1:8000")
INTERNAL_KEY = os.getenv("HUNTER_INTERNAL_KEY", "hunter-internal-2026")

server = Server("watchlist-mcp")


@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="stock_quickview",
            description=(
                "单股速答 · 拉实时股价+估值+52周区间+AI短评。"
                "用户问『{股票} 今天怎么样 / 现在如何 / 值不值得买』时用。"
                "返回富卡片结构(前端 StockQuickviewCard 会自动渲染)· 3 按钮 CTA。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "code": {"type": "string",
                             "description": "6位A股(如 601899) / 5位港股(如 00700) / 美股代码"},
                    "_hermes_user_id": {"type": "string",
                                        "description": "内部字段 · 由 hunter-mcp-context plugin 注入 · 请勿填写"},
                },
                "required": ["code"],
            },
        ),
        Tool(
            name="stock_news",
            description=(
                "拉指定股票近 30 天 5 条精选新闻 · 每条附 AI 影响短评(利好/利空/中性/强利好)。"
                "用户问『{股票} 有什么新闻 / 有啥公告 / 利好利空』时用。"
                "返回富卡片(StockNewsCard 渲染)。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "code":  {"type": "string"},
                    "limit": {"type": "integer", "default": 5, "minimum": 1, "maximum": 10},
                },
                "required": ["code"],
            },
        ),
        Tool(
            name="watchlist_digest",
            description=(
                "拉当前登录用户的自选股清单 · 今日 Top3 涨/跌 + AI 归因。"
                "用户问『我的自选 / 我的股票 今天谁最强/最弱 / 自选股日报』时用。"
                "无 shares 也能用(只需已加自选)。前置:用户必须已登录+已加自选。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "top_n": {"type": "integer", "default": 3, "minimum": 1, "maximum": 10},
                    "_hermes_user_id": {"type": "string"},
                },
                "required": [],
            },
        ),
        Tool(
            name="watchlist_add",
            description=(
                "把股票/ETF/基金加入当前登录用户的自选。"
                "用户说『加XX到自选』『把XX加自选』『XX加入自选股』时用。"
                "支持股票名称(自动查代码,如 '紫金矿业')或直接给代码(如 '601899' / '00700' / 'AAPL')。"
                "前置:用户必须已登录。返回是否成功 + 已添加项详情 + 前 5 候选(便于名字含糊时追问)。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "股票名称或代码,如 '紫金矿业' / '601899' / '00700' / 'AAPL'"
                    },
                    "_hermes_user_id": {"type": "string"},
                },
                "required": ["query"],
            },
        ),
    ]


def _headers(args: dict) -> dict:
    """构造 hermes-api 内部调用 headers · 附 shared secret + user_id。"""
    h = {"X-Hunter-Internal-Key": INTERNAL_KEY}
    uid = (args or {}).get("_hermes_user_id") or os.getenv("HUNTER_USER_ID", "")
    if uid:
        h["X-Hunter-User-Id"] = uid
    return h


@server.call_tool()
async def call_tool(name: str, args: dict):
    args = args or {}
    # 剔除内部字段(避免带到 hermes-api body 里报 422)
    body = {k: v for k, v in args.items() if not k.startswith("_")}
    url = f"{HERMES_API}/api/internal/watchlist/{name}"
    try:
        async with httpx.AsyncClient(timeout=45) as c:
            r = await c.post(url, json=body, headers=_headers(args))
        text = r.text
        # 非 2xx 也把 body 返回 · 便于 LLM 拿到错误信息
    except Exception as e:
        text = json.dumps({"error": f"hermes-api call failed: {type(e).__name__}: {e}",
                            "hermes_api": HERMES_API, "tool": name},
                          ensure_ascii=False)
    return [TextContent(type="text", text=text)]


async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    print(f"[watchlist-mcp] boot · hermes_api={HERMES_API}", file=sys.stderr)
    asyncio.run(main())
