"""screener-mcp · 全市场扫描筛选

一个 tool:`market_screen` —— 让 LLM 用 thinkScript 子集写筛选条件,
扫全市场快照(美股 7487 / A 股 5237 / 港股 2396 只,秒级)。

## 为什么值得单独一个 MCP

在这之前,用户在 chat 里说「帮我找美股里均线多头排列、逼近 52 周新高的票」,
LLM 手上一个工具都没有:`watchlist_*` 只能看已经在自选里的,
`/quant/scan` 只能按站内因子在 A 股沪深 300 里打分。
**"从全市场里按条件找票"这件事整个系统做不到**,LLM 只能编几个代码出来 ——
这正是仓内铁律最忌讳的那种失败。

## 边界(description 里也写了,这里说为什么)

扫描源只返回**当前横截面快照**,没有历史序列。
`close|1M` 不是"一个月前的收盘",实测它等于 close 本身。
所以扫描结果只能当候选池,不能拿去回测、不能写进因子库 —— 后端 screen_source.py
不提供任何写入路径,这里也不要给 LLM 留想象空间。

## 部署

后端在 hunter-community 的 `apps/api/app/routers/internal_tools.py`
(`POST /api/internal/quant/market_screen`)。SaaS(hermes)那边还没有这个端点,
所以 404 要给一句人能看懂的话,不能让 LLM 拿着一坨 HTML 去猜。

## market_screen **故意不进** hunter-mcp-context.ts 的 HUNTER_TOOLS

那个 Set 的作用是让 plugin 注入 `_hermes_user_id`。扫描是纯只读的市场查询,
跟用户身份无关 —— 后端 `_auth` 允许空 user_id,tool 本身也不读它。
加进去只会让每次调用多一次 `/api/internal/session/{sid}/user` 反查。
(所以看到别的 tool 都在那个 Set 里、这个不在,不是漏了。)
"""
import asyncio
import json
import os
import sys

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# docker bridge · 生产 172.17.0.1(host 主机 IP)
# 容器里没有这两个 env(实测 docker inspect 为空),默认值就是生产值,不要改
HERMES_API = os.getenv("HERMES_API_URL", "http://172.17.0.1:8000")
INTERNAL_KEY = os.getenv("HUNTER_INTERNAL_KEY", "hunter-internal-2026")

server = Server("screener-mcp")


_SYNTAX = (
    "脚本语法(thinkorswim thinkScript 子集):\n"
    "  # 井号是注释\n"
    "  def 变量名 = 表达式;        每句必须以分号结尾\n"
    "  plot scan = 综合条件;       最后一句必须是 plot,它就是筛选结果\n"
    "\n"
    "运算符: > >= < <= == !=  +  -  *  /  and  or  not  ()\n"
    "\n"
    "内置序列: close open high low volume\n"
    "\n"
    "函数(周期必须是常量,映射不上会直接报错并告诉你可用值):\n"
    "  Average(close, N)      N 取 5/10/20/30/50/60/100/120/200/250 等\n"
    "  Average(volume, N)     N 只能是 10 / 30 / 60 / 90\n"
    "  ExpAverage(close, N)   指数均线\n"
    "  Highest(high, N)       N 只能接近 5 / 21 / 63 / 126 / 252(固定窗口)\n"
    "  Lowest(low, N)         同上\n"
    "  RSI()                  14 周期;RSI(N) 支持 2/3/4/5/7/9/10/20/21/30\n"
    "  RS()                   IBD 口径 RS 相对强度评级 1~99,在全市场里排名\n"
    "                         (跑赢全市场 80% 的股票 = 80;次新股与美股 OTC 没有评级)\n"
    "  RSLineUpDays()         RS 线(收盘 ÷ 基准指数)连续站在自身 21 日均线之上的交易日数,\n"
    "                         截至上一交易日收盘;例:RSLineUpDays() > 50\n"
    "\n"
    "也可以直接写扫描源字段名(共 3777 个),常用的:\n"
    "  market_cap_basic  price_earnings_ttm  price_book_fq  return_on_equity\n"
    "  dividends_yield_current  debt_to_equity  gross_margin_ttm\n"
    "  total_revenue_yoy_growth_ttm  relative_volume_10d_calc  change\n"
    "  price_52_week_high  price_52_week_low  ADX  ATR  MACD.hist  Perf.Y  Perf.1M\n"
    "\n"
    "例:\n"
    "  def sma50 = Average(close, 50);\n"
    "  def sma200 = Average(close, 200);\n"
    "  def h52 = Highest(high, 252);\n"
    "  def near_high = (h52 - close) / h52 <= 0.10;\n"
    "  plot scan = close > sma50 and sma50 > sma200 and near_high and volume > 1000000;\n"
)


@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="market_screen",
            description=(
                "全市场扫描选股 · 用筛选脚本从整个市场里找符合条件的股票。\n"
                "\n"
                "触发场景(命中任一即调,不要先反问):\n"
                "  · 『帮我找/扫/筛 X 市场里 <条件> 的股票』\n"
                "  · 『哪些股票 均线多头排列 / 低市盈率 / 超卖 / 逼近新高 / 放量』\n"
                "  · 『美股里 PE 小于 15 且 RSI 小于 30 的有哪些』\n"
                "  · 用户直接贴一段 thinkScript / Stock Hacker 脚本让你跑\n"
                "\n"
                "覆盖:A股 5237 · 港股 2396 · 美股 7487(只开这三个市场,\n"
                "站内的代码归一化与下游能力也只支持它们)。\n"
                "已自动过滤 ETF、优先股份额、权证,只留普通股主上市。\n"
                "\n"
                + _SYNTAX +
                "\n"
                "不用自己写脚本时可以传 preset:\n"
                "  uptrend(上升趋势·美股) · value_oversold(低估值超卖·A股)\n"
                "  breakout_volume(放量突破·美股) · hk_dividend(港股高股息)\n"
                "  rs_leaders(强势股 RS≥80·美股) · rs_line_up(RS线持续向上·美股)\n"
                "\n"
                "重要边界,回答用户时必须一并说明:\n"
                "  1. 数据延迟 15 分钟,不能用于盘中决策。\n"
                "  2. 只有当前快照、没有历史,结果**不能用于回测**,也不写进因子库。\n"
                "     (RSLineUpDays 例外:来自每晚的全市场日线,截至上一交易日收盘。)\n"
                "  3. market_cap_basic 与站内国内源口径不一致(A/H 两地上市股实测差 8~37%),\n"
                "     只可用于排序粗筛,不要当市值真值报给用户。\n"
                "  4. 返回体里的 skipped_incomplete 是『因缺字段算不出』的只数,\n"
                "     不是『不满足条件』。数量大时要如实告诉用户覆盖有限。\n"
                "\n"
                "返回 error 字段时,里面写了错在哪、可用周期是哪些 —— "
                "照着改脚本重试一次,不要把原始错误直接甩给用户。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "script": {
                        "type": "string",
                        "description": "筛选脚本。语法见 tool 描述。留空则必须给 preset。",
                    },
                    "preset": {
                        "type": "string",
                        "enum": ["uptrend", "value_oversold", "breakout_volume", "hk_dividend",
                                 "rs_leaders", "rs_line_up"],
                        "description": "预置脚本 · 只在 script 为空时生效",
                    },
                    "market": {
                        "type": "string",
                        "enum": ["a", "hk", "us"],
                        "default": "us",
                        "description": "a=A股 hk=港股 us=美股",
                    },
                    "limit": {
                        "type": "integer",
                        "default": 30, "minimum": 1, "maximum": 50,
                        "description": "返回前 N 只(按市值降序)· 命中总数在 matched 字段里",
                    },
                    "_hermes_user_id": {
                        "type": "string",
                        "description": "内部字段 · 由 hunter-mcp-context plugin 注入 · 请勿填写",
                    },
                },
                "required": [],
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
    body = {k: v for k, v in args.items() if not k.startswith("_")}
    url = f"{HERMES_API}/api/internal/quant/{name}"
    try:
        # 45s:全市场拉取实测 1~3s,留足上游抖动余量
        async with httpx.AsyncClient(timeout=45) as c:
            r = await c.post(url, json=body, headers=_headers(args))
        if r.status_code == 404:
            text = json.dumps({
                "error": "这个部署没有开启全市场扫描(后端缺 /api/internal/quant/market_screen)。"
                         "请改用站内已有的自选股与因子选股工具,不要编造扫描结果。",
            }, ensure_ascii=False)
        else:
            text = r.text
    except Exception as e:
        text = json.dumps({"error": f"hermes-api call failed: {type(e).__name__}: {e}",
                           "hermes_api": HERMES_API, "tool": name},
                          ensure_ascii=False)
    return [TextContent(type="text", text=text)]


async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    print(f"[screener-mcp] boot · hermes_api={HERMES_API}", file=sys.stderr)
    asyncio.run(main())
