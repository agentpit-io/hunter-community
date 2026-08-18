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
            name="watchlist_rank",
            description=(
                "自选股**多时段排序 / 横向对比**。触发场景:"
                "\n"
                "  · 『把我的自选股排一下 / 从好到坏排序 / 谁最好谁最差』"
                "\n"
                "  · 『分 3 个月 / 6 个月 / 1 年 / 3 年前景』(即使只提到其中 1-2 档也调这个)"
                "\n"
                "  · 『我的自选哪几只最强 / 哪几只应该减』"
                "\n\n"
                "**关键**:遇到上述提问,不要为每支股逐一调 stock_deep_analysis(那样"
                "串行 5+ 分钟且不能横向对比)· 这个 tool 一次调用完成 N 只 × 4 时段的"
                "结构化排序,秒级返回 markdown 表格 + 结构化打分。"
                "\n\n"
                "服务端本地按均线 / 52 周分位 / 量能 / 动量打 3M+6M 分,"
                "1Y/3Y 因当前数据源未接入长期基本面字段,只出定性标签 + 显式方法学声明"
                "(告知用户长期视角建议对个别股单独跑深度分析)· 不会向用户暴露"
                "『数据未 seed』这类内部术语。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "horizons": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["3M", "6M", "1Y", "3Y"]},
                        "description": "要排序的时段 · 用户只提 1-2 档时按用户说的传;"
                                       "未指定就传 [\"3M\",\"6M\",\"1Y\",\"3Y\"] 或省略",
                    },
                    "_hermes_user_id": {
                        "type": "string",
                        "description": "内部字段 · 由 hunter-mcp-context plugin 注入 · 请勿填写",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="watchlist_add_batch",
            description=(
                "**批量**加自选(自动跳过已在自选的股票 · 幂等)。"
                "\n\n"
                "触发场景(命中任一即调,不要拆成多次 watchlist_add):"
                "\n"
                "  · 用户上传的截图/图片,消息里出现 [图片 OCR 抽取内容] 段,里头有多只股票"
                "\n"
                "  · 用户直接列出 2 只及以上股票:『加自选 茅台 · 五粮液 · 腾讯』"
                "\n"
                "  · 用户给了逗号/顿号/换行分隔的清单"
                "\n\n"
                "queries 每一项独立走 search+add · 顺序保留 · 单次上限 30 只。"
                "每条可以是名称或代码,中英文皆可,涵盖 A股/港股/美股/ETF/基金:"
                "\n"
                "  · A 股: '贵州茅台' / '600519' / '紫金矿业' / '601899'"
                "\n"
                "  · 港股: '腾讯控股' / '00700' (若截图给的是 'H01378' 这种前带 H 的同花顺格式,去掉 H 传 '01378')"
                "\n"
                "  · 美股: '苹果' / 'AAPL'"
                "\n"
                "  · ETF/基金: '沪深300ETF' / '510300'"
                "\n\n"
                "返回 summary + results[] · 每条含 success/already_added/error · "
                "已在自选的算成功不算错 · 未找到的会一并列出,你把 not_found 里的原始 query "
                "转述给用户请他确认。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "股票名或代码清单 · 每条独立处理 · 顺序保留 · 最多 30 条",
                        "minItems": 1,
                        "maxItems": 30,
                    },
                    "_hermes_user_id": {
                        "type": "string",
                        "description": "内部字段 · 由 hunter-mcp-context plugin 注入 · 请勿填写",
                    },
                },
                "required": ["queries"],
            },
        ),
        Tool(
            name="watchlist_add",
            description=(
                "把用户口述的股票/ETF/基金加入自选。"
                "\n\n"
                "触发场景(命中任一即调,不要预告不要确认):"
                "\n"
                "  · 『加自选 X / X 加入自选 / 帮我关注 X / 订阅 X / 收藏 X』"
                "\n"
                "  · 『把 X 加到我的自选/股票池/watchlist』"
                "\n\n"
                "query 可以是名称或代码,中英文皆可,涵盖:"
                "\n"
                "  · A 股: '贵州茅台' / '600519' / '紫金矿业' / '601899'"
                "\n"
                "  · 港股: '腾讯控股' / '00700'"
                "\n"
                "  · 美股: '苹果' / 'AAPL' / '英伟达' / 'NVDA'"
                "\n"
                "  · ETF/基金: '沪深300ETF' / '510300' / '纳指ETF'"
                "\n\n"
                "语义:后端复用东财 suggest 解析,取第一个命中直接落库,幂等(重复添加不报错)。"
                "命中 0 条 → success=false + error=not_found,让模型追问用户完整名字。"
                "命中多条 → 返回 candidates 里前 5 条,模型可提示用户是否想加别的。"
                "前置:用户必须已登录。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "股票/ETF/基金的名称或代码 · 如 '贵州茅台' / '600519' / 'AAPL' / '00700'",
                    },
                    "_hermes_user_id": {
                        "type": "string",
                        "description": "内部字段 · 由 hunter-mcp-context plugin 注入 · 请勿填写",
                    },
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
