#!/usr/bin/env python3
"""TrueSource MCP —— 把一手信号采集变成 AI 能直接调的工具。

## 它做什么

TrueSource 是一套爬虫 + AI 检索,盯的是**能查证出处的一手信号**:
交易所公告、政府采购中标、研发扩张动向、北向持仓、宏观口径数据。
它不做研报摘要,也不产生「买/卖」结论 —— 给的是带日期和来源的原始信号。

包成六个工具:

    truesource_procurement()          最近的政采中标(哪些公司在真拿单)
    truesource_macro()                宏观信号(国统局 / 海关 / 行业协会)
    truesource_daily_brief(symbols)   一批票的信号摘要 + 预警级别
    truesource_alert_signals(symbols) 只要达到预警阈值的那些
    truesource_report(symbol)         单票完整报告(35 只预制标的秒回)
    truesource_scout(symbol)          单票实时全量采集(30-60 秒)

## 为什么必须要 API key

上游是我们自己跑的爬虫集群,`truesource_scout` 一次会并行跑价格、公告和
Gemini AI 搜索 —— **每次调用都产生真实的第三方 API 花费**,耗时 30-60 秒。

没有 key 的话谁都能触发,几个循环就能把成本打上去,也没法定位是谁在打。
所以**这个 MCP 不做无 key 模式**,也做不了 —— 上游网关直接返 403。
没配 key 时它不会假装成功、不会返回样例数据,而是明确告诉你去哪申请。

申请:https://hunter.agentpit.io/dev/api-keys
拿到的 key 形如 `hunt_tools_` + 随机串。

## 跑起来

    # 方式一 · uvx(推荐 · 不用装)
    HUNTER_API_KEY=hunt_tools_xxx uvx truesource-mcp

    # 方式二 · pip
    pip install truesource-mcp
    HUNTER_API_KEY=hunt_tools_xxx truesource-mcp

Claude Desktop / Cursor 的配置(`claude_desktop_config.json`):

    {
      "mcpServers": {
        "truesource": {
          "command": "uvx",
          "args": ["truesource-mcp"],
          "env": { "HUNTER_API_KEY": "hunt_tools_xxx" }
        }
      }
    }

## 环境变量

    HUNTER_API_KEY        必填 · hunt_tools_ 开头
    TRUESOURCE_URL        可选 · 默认 https://hunter.agentpit.io/api/saas/truesource
                          自建部署或直连上游时改这个
    TRUESOURCE_TIMEOUT    可选 · 默认 20 秒(scout 单独用 120 秒)
    TRUESOURCE_MAX_ITEMS  可选 · 默认 40 · 单次返回的最大条目数
    TRUESOURCE_MCP_TRANSPORT  可选 · stdio(默认)| streamable-http | sse
    TRUESOURCE_MCP_HOST/PORT  仅远程传输时用 · 默认 0.0.0.0:8933
"""
from __future__ import annotations

import json
import os

import httpx
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("truesource-mcp")

DEFAULT_URL = "https://hunter.agentpit.io/api/saas/truesource"
APPLY_URL = "https://hunter.agentpit.io/dev/api-keys"

# 单次返回的最大条目数。爬虫接口一次能吐几百条,原样塞进模型上下文
# 会把真正有用的挤掉 —— 和 akshare-mcp 的 MAX_ROWS 同一个考虑。
MAX_ITEMS = int(os.getenv("TRUESOURCE_MAX_ITEMS", "40"))

_READ = float(os.getenv("TRUESOURCE_TIMEOUT", "20"))
# scout 要并行跑爬虫 + Gemini 搜索,30-60 秒是常态,单独给一档
_SCOUT_READ = max(_READ, 120.0)


def _base_url() -> str:
    return (os.getenv("TRUESOURCE_URL") or DEFAULT_URL).rstrip("/")


def _key() -> str:
    return (os.getenv("HUNTER_API_KEY") or "").strip()


def _no_key_error() -> str:
    """没配 key 时的回话。**不返回假数据。**"""
    return json.dumps({
        "error": "missing_api_key",
        "message": "没有配置 HUNTER_API_KEY,无法调用 TrueSource。",
        "how_to_fix": [
            f"1. 打开 {APPLY_URL} 登录",
            "2. 点「申请 API Key」",
            "3. 拿到 hunt_tools_ 开头的 key(只显示一次,立即复制)",
            "4. 把它设成环境变量 HUNTER_API_KEY 后重启这个 MCP",
        ],
        "note": "这个服务没有免 key 模式 —— scout 每次调用都会跑真实的爬虫和 "
                "Gemini 搜索,产生第三方 API 花费。",
    }, ensure_ascii=False)


def _http_error(status: int, body: str, path: str) -> str:
    table = {
        401: ("invalid_api_key",
              "key 无效、缺失或已撤销。检查 HUNTER_API_KEY 是否复制完整。"),
        403: ("forbidden",
              f"key 有效但没有访问这个接口的权限。到 {APPLY_URL} 确认 key 的类型和状态。"),
        404: ("not_found",
              "上游没有这条数据。如果查的是个股报告,说明它不在预制的 35 只标的里 —— "
              "改用 truesource_scout 做实时采集。"),
        429: ("rate_limited", "触发速率限制,稍后重试。"),
        502: ("upstream_down", "上游爬虫服务异常,稍后重试。"),
        503: ("upstream_unavailable", "上游暂时不可用,稍后重试。"),
        504: ("upstream_timeout",
              "上游超时。scout 走 Gemini 搜索,慢的时候会这样 —— 稍后重试。"),
    }
    code, msg = table.get(status, ("http_error", f"上游返回 HTTP {status}。"))
    return json.dumps({
        "error": code, "http_status": status, "path": path,
        "message": msg, "upstream_body": body[:300],
    }, ensure_ascii=False)


def _request(method: str, path: str, params: dict | None = None,
             read_timeout: float | None = None) -> str | dict | list:
    """统一请求出口。失败返回 JSON 字符串,成功返回上游的 dict/list。"""
    key = _key()
    if not key:
        return _no_key_error()

    url = f"{_base_url()}{path}"
    timeout = httpx.Timeout(connect=5.0, read=read_timeout or _READ,
                            write=5.0, pool=5.0)
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.request(method, url, params=params or {},
                               headers={"Authorization": f"Bearer {key}"})
    except httpx.TimeoutException:
        return json.dumps({
            "error": "timeout", "path": path,
            "message": f"请求超时({(read_timeout or _READ):.0f}s)。"
                       f"scout 类接口本来就要 30-60 秒 —— 调大 TRUESOURCE_TIMEOUT 再试。",
        }, ensure_ascii=False)
    except httpx.HTTPError as e:
        return json.dumps({
            "error": "network_error", "url": url,
            "message": f"{type(e).__name__}: {str(e)[:200]}",
            "hint": "连不上服务。检查网络,或用 TRUESOURCE_URL 指到你自己的部署。",
        }, ensure_ascii=False)

    if r.status_code != 200:
        return _http_error(r.status_code, r.text, path)

    try:
        return r.json()
    except ValueError:
        return json.dumps({
            "error": "bad_response", "path": path,
            "message": "上游返回的不是 JSON。", "body": r.text[:300],
        }, ensure_ascii=False)


def _cap(data, label: str) -> str:
    """裁剪到 MAX_ITEMS 并**明说裁了**。

    截断不说出来,模型会把「前 40 条」当成「全部 40 条」下结论 ——
    比如「最近 7 天只有 40 个中标」,而真相可能是 300 个。
    """
    if isinstance(data, str):          # 上游已经是错误 JSON
        return data

    items = None
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        for k in ("items", "data", "signals", "results", "list"):
            if isinstance(data.get(k), list):
                items = data[k]
                key_name = k
                break

    if items is None:
        return json.dumps(data, ensure_ascii=False)

    total = len(items)
    if total <= MAX_ITEMS:
        return json.dumps(data, ensure_ascii=False)

    cut = items[:MAX_ITEMS]
    if isinstance(data, list):
        out = {"items": cut}
    else:
        out = dict(data)
        out[key_name] = cut
    out["truncated"] = True
    out["returned"] = len(cut)
    out["total"] = total
    out["note"] = (f"只返回了前 {len(cut)} 条(共 {total} 条)。"
                   f"**不要把这 {len(cut)} 条当成全部来下结论。**"
                   f"缩小天数范围,或调大 TRUESOURCE_MAX_ITEMS。")
    return json.dumps(out, ensure_ascii=False)


def _clean_symbols(symbols: str) -> str | None:
    """规整逗号分隔的代码串。返回 None 表示输入不合法。"""
    parts = [s.strip() for s in (symbols or "").replace("，", ",").split(",")]
    parts = [s for s in parts if s]
    return ",".join(parts) if parts else None


@mcp.tool()
def truesource_procurement(days: int = 7, limit: int = 15) -> str:
    """最近的政府采购中标信号 —— 哪些公司在真拿单。

    days   查最近几天,默认 7
    limit  最多几条,默认 15

    这是 TrueSource 最独有的一块:中标公告是**已经发生的事实**,
    比研报的预测更硬。适合回答「谁在实际拿到订单」这类问题。
    """
    return _cap(_request("GET", "/api/procurement",
                         {"days": days, "limit": limit}), "procurement")


@mcp.tool()
def truesource_macro(days: int = 30) -> str:
    """宏观信号 —— 国家统计局 + 海关 + 行业协会的口径数据。

    days  查最近几天,默认 30

    给的是有明确发布方和日期的原始口径,不是二手解读。
    """
    return _cap(_request("GET", "/api/macro", {"days": days}), "macro")


@mcp.tool()
def truesource_daily_brief(symbols: str) -> str:
    """一批股票的信号摘要 + 预警级别(最近 3 天)。

    symbols  逗号分隔的代码,如 `300308,688041`。一次问一批比逐个问快得多。

    每只票会带一个 alert_level:
      red    有需要立刻看的负面信号
      yellow 有变化,值得留意
      green  正常
      grey   这几天没采到信号(**不等于没事,是没数据**)

    ⚠️ grey 和 green 是两件事。grey 表示这只票最近 3 天没有采到任何信号,
    可能是它确实安静,也可能是爬虫没覆盖到 —— 别把 grey 读成「安全」。
    """
    syms = _clean_symbols(symbols)
    if not syms:
        return json.dumps({
            "error": "bad_symbols",
            "message": "symbols 不能为空。格式:逗号分隔的股票代码,如 300308,688041",
        }, ensure_ascii=False)
    return _cap(_request("GET", "/api/hunter/daily-brief", {"symbols": syms}),
                "daily_brief")


@mcp.tool()
def truesource_alert_signals(symbols: str) -> str:
    """只返回达到预警阈值的信号(最近 26 小时)。

    symbols  逗号分隔的代码

    比 daily_brief 更窄 —— 它只给「需要动作」的那些。
    **空结果是有意义的结论**:这批票最近 26 小时没有触发预警,
    不是查询失败。
    """
    syms = _clean_symbols(symbols)
    if not syms:
        return json.dumps({
            "error": "bad_symbols",
            "message": "symbols 不能为空。格式:逗号分隔的股票代码,如 300308,688041",
        }, ensure_ascii=False)
    return _cap(_request("GET", "/api/hunter/alert-signals", {"symbols": syms}),
                "alert_signals")


@mcp.tool()
def truesource_report(symbol: str) -> str:
    """单只标的的完整研究报告 —— 公告、研发扩张、北向持仓、AI 搜索分类汇总。

    symbol  单个股票代码,如 `300308`

    **只有 35 只预制标的(AI 算力链)能秒回。** 其它票会返回 not_found,
    那时改用 `truesource_scout` 做实时采集(慢,但任意 A 股都能查)。

    这个工具不会自动帮你 fallback 到 scout —— 因为 scout 要跑 30-60 秒
    并产生真实花费,那个决定应该由你(或用户)明确做出,不该悄悄发生。
    """
    sym = (symbol or "").strip()
    if not sym:
        return json.dumps({"error": "bad_symbol",
                           "message": "symbol 不能为空"}, ensure_ascii=False)
    return _cap(_request("GET", f"/api/hunter/report/{sym}"), "report")


@mcp.tool()
def truesource_scout(symbol: str, name: str = "") -> str:
    """对一只票做**实时全量采集** —— 任意 A 股都能查。

    symbol  股票代码,如 `600519`
    name    股票名称(可选,能提高 AI 搜索的命中率)

    并行跑价格 + 公告 + 研发扩张 + 北向 + Gemini AI 搜索。

    ⚠️ **一次要 30-60 秒,且产生真实的第三方 API 花费。**
    先试 `truesource_report`(预制标的秒回),它 not_found 了再用这个。
    不要在循环里对几十只票调用它。
    """
    sym = (symbol or "").strip()
    if not sym:
        return json.dumps({"error": "bad_symbol",
                           "message": "symbol 不能为空"}, ensure_ascii=False)
    params = {"name": name.strip()} if name and name.strip() else None
    return _cap(_request("POST", f"/api/hunter/scout/{sym}", params,
                         read_timeout=_SCOUT_READ), "scout")


def main() -> None:
    """入口 —— PyPI 的 console_scripts 指向这里。

    默认 stdio:Claude Desktop / Cursor / uvx 都走这个。
    streamable-http / sse 留给常驻服务的场景(比如接进 Hunter 的
    「能力 → 接入一个工具」)。MCP 规范已把 SSE 标记为过时,新部署优先用
    streamable-http。
    """
    transport = (os.getenv("TRUESOURCE_MCP_TRANSPORT") or "stdio").strip().lower()
    if transport == "stdio":
        mcp.run(transport="stdio")
        return

    # 远程传输 · host/port 在 mcp 2.x 里是 run() 的关键字参数,
    # 不再是 1.x 那个 mcp.settings.host/port
    host = os.getenv("TRUESOURCE_MCP_HOST", "0.0.0.0")
    port = int(os.getenv("TRUESOURCE_MCP_PORT", "8933"))
    if transport in ("http", "streamable-http"):
        mcp.run(transport="streamable-http", host=host, port=port)
    elif transport == "sse":
        mcp.run(transport="sse", host=host, port=port)
    else:
        raise SystemExit(
            f"未知的 TRUESOURCE_MCP_TRANSPORT={transport!r} —— "
            f"只支持 stdio(默认)/ streamable-http / sse")


if __name__ == "__main__":
    main()
