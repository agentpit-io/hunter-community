# ⚠️ 本文件是 huntercode(agentpit-io/huntercode · dev 分支)mcp/hunter_user_mcp.py 的**部署副本**,
# 通过 docker-compose.yml 的 bind mount 覆盖镜像里的同名文件 —— 与 uzi_mcp.py / watchlist_mcp.py 同一套路。
#
# 为什么要副本:镜像里那份的 list_my_sources 描述让模型「预测走势调 hunter_cap_kpred」,
# 而 kpred 在社区版已经删了(_24 §4.1),模型被引向一个不存在的工具。服务器 GHCR 登录过期拉不动新镜像,
# 只能挂文件。镜像能更新之后,这个挂载和本文件可以一起删掉。
#
# 改动只在 huntercode 仓做,改完再把文件整个拷过来(保留本段头注释),不要在这里单独改。
# 当前对应 huntercode 提交:346f1119cc(2026-09-17)
"""hunter-user-mcp · 用户自定义 MCP 路由桥

opencode 侧只看到这一个 MCP · 实际按当前 chat 用户的注册聚合所有 tool 暴露。
tool 命名空间：`{slug}_{tool_name}`（opencode 会自动加 `usermcp_` 前缀 · 变成
`usermcp_{slug}_{tool_name}` · 前端 dispatch 可选剥离）

user_id 传递：与 watchlist_mcp / portfolio_mcp 同一套 · 走 hunter-mcp-context.ts
plugin 注入 _hermes_user_id · 本 MCP 拿到后作 X-Hunter-User-Id header 转 hermes-api。

对应 doc/codex/自定义MCP/01-方案总纲.md §3.3
部署位置：docker 容器内 /mcp/hunter_user_mcp.py（host: /opt/opencode-mcp/）
"""
from __future__ import annotations
import asyncio
import json
import os
import sys

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# 大小闸（待办池 P0-11）：AtomCode 内核对超过 16 KB 的工具返回会砍成头尾各
# 4 KB、中间不可见且 JSON 语法断裂，模型会拿记忆补中间那段还标成工具来源。
# 这里在 MCP 侧先裁成**合法 JSON + 明确的裁剪说明**。
# 跟这个文件一起被 COPY 到 /opt/hca/mcp/，所以同目录 import 得到。
try:
    from hca_size_guard import fit as _fit
except ImportError:                                            # pragma: no cover
    # 静默降级是危险的：闸没了照样能跑，只是又回到「被内核砍成半截 JSON」。
    # 所以这里必须往 stderr 喊一声（stdio server 的 stderr 进 daemon 日志）。
    import sys as _sys
    print("[hca] ⚠️ 没找到 hca_size_guard，MCP 返回的大小闸**未生效**"
          "（镜像里应当在 /opt/hca/mcp/hca_size_guard.py）", file=_sys.stderr, flush=True)

    def _fit(text, tool="", max_bytes=None):                   # type: ignore[misc]
        return text


HERMES_API = os.getenv("HERMES_API_URL", "http://172.17.0.1:8000")


def _resolve_internal_key(default: str = "hunter-internal-2026") -> str:
    """读取内部鉴权密钥：优先环境变量 HUNTER_INTERNAL_KEY，为空时回退读取共享密钥卷。"""
    val = (os.getenv("HUNTER_INTERNAL_KEY") or "").strip()
    if val:
        return val
    key_file = (os.getenv("HUNTER_INTERNAL_KEY_FILE") or "/opt/hunter-secrets/internal_key").strip()
    try:
        if os.path.isfile(key_file):
            with open(key_file, encoding="utf-8") as f:
                v = f.read().strip().strip("'\"")
                if v:
                    return v
    except OSError:
        pass
    sec_file = (os.getenv("HUNTER_SECRETS_FILE") or "/opt/hunter-secrets/secrets.env").strip()
    try:
        if os.path.isfile(sec_file):
            with open(sec_file, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if line.startswith("HUNTER_INTERNAL_KEY="):
                        v = line.split("=", 1)[1].strip().strip("'\"")
                        if v:
                            return v
    except OSError:
        pass
    return default


INTERNAL_KEY = _resolve_internal_key("hunter-internal-2026")

server = Server("hunter-user-mcp")


def _headers(uid: str) -> dict:
    key = INTERNAL_KEY if (INTERNAL_KEY and INTERNAL_KEY != "hunter-internal-2026") else _resolve_internal_key(INTERNAL_KEY or "hunter-internal-2026")
    h = {"X-Hunter-Internal-Key": key}
    if uid:
        h["X-Hunter-User-Id"] = uid
    return h


def _extract_uid(args: dict) -> str:
    """从 tool args 拿 _hermes_user_id · 若无则拉环境变量兜底。"""
    return (args or {}).get("_hermes_user_id") or os.getenv("HUNTER_USER_ID", "")


# ═══════════════════════════════════════════════════════════════════
# 为什么是「壳工具」而不是把用户的 tool 逐个暴露
#
# MCP 协议里 list_tools 是**无参调用** —— opencode 启进程时问一次"你有什么工具",
# 这时根本没有"当前是哪个用户在聊天"的概念。原实现试图用环境变量 HUNTER_USER_ID
# 兜底,但那只能塞死一个人:
#   塞 A 的 → 所有人都看到 A 的数据源, 还花 A 的 API key
#   不塞    → 所有人都看不到(线上就是这个状态, 永远 return [])
#
# 而 call_tool 反过来没有这个问题 —— hunter-mcp-context.ts 会往 args 注入
# _hermes_user_id, 拿得到真实用户。
#
# 所以这里只暴露两个与用户无关的"壳",把按人取数推迟到 call_tool:
#   list_my_sources  先看我配了什么
#   invoke           再调其中某个
# 代价是模型要"先问再用",多一次往返;换来的是多租户天然正确。
# ═══════════════════════════════════════════════════════════════════

SHELL_TOOLS = [
    Tool(
        name="list_my_sources",
        description=(
            "【仅用于查询当前用户配置的第三方 MCP 组件清单 · 不是通用工具入口】"
            "\n\n"
            "触发条件(必须匹配其一 · 否则勿调):"
            "\n"
            "  ① 用户明确询问'我配了哪些 MCP / 数据源'、'我的组件列表'、'看下我的自定义工具'\n"
            "  ② 内置 hunter/watchlist/uzi 工具全都试过但都不匹配用户请求(如美股逐笔、加密货币行情、第三方新闻等非内置数据类型)\n"
            "\n"
            "❌ **不要**用于以下场景 · 它们在你的工具列表里已有专用 tool(直接调即可):\n"
            "  · '加自选' / '关注' / '订阅股票' → 调 `watchlist_watchlist_add`\n"
            "  · '预测走势' / '预测涨跌' / '未来 N 天怎么走' → 有 K 线预测工具(SaaS 叫 `kronos_kronos_forecast`)就调它 · 没有就如实说本部署不提供预测\n"
            "  · '当前股价' / '实时行情' / '快照' → 调 `watchlist_stock_quickview`\n"
            "  · '最近新闻' / '相关消息' → 调 `watchlist_stock_news`\n"
            "  · 'K 线' / '走势图' → 调 `watchlist_stock_quickview`(它已返 30 天历史 K 线)\n"
            "  · '深度分析' / '基本面 + 估值' → 调 `uzi_stock_deep_analysis`\n"
            "\n"
            "若返回为空说明用户没配任何第三方数据源 · 不要再调 invoke · 直接回复用户可去「MCP 组件」页面添加。"
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="invoke",
        description=(
            "调用当前用户某个第三方 MCP 组件提供的工具。"
            "\n\n"
            "❌ **不要**用于内置能力(watchlist/hunter/uzi/stock_*)· 那些直接调即可,不需要走 invoke。"
            "\n"
            "本工具只用于 list_my_sources 返回的第三方 source 里的 tool。"
            "调用前必须已通过 list_my_sources 拿到 source 与 tool 的准确名称与参数结构。"
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "数据源标识(list_my_sources 返回的 source)"},
                "tool": {"type": "string", "description": "该数据源下的工具名"},
                "args": {"type": "object", "description": "工具参数, 结构见 list_my_sources 的 inputSchema"},
            },
            "required": ["source", "tool"],
        },
    ),
]


@server.list_tools()
async def list_tools() -> list[Tool]:
    """只暴露两个与用户无关的壳工具 —— 原因见上方注释。"""
    return SHELL_TOOLS


async def _fetch_bundle(uid: str) -> dict:
    """按 user_id 拉该用户的全部数据源与工具。"""
    async with httpx.AsyncClient(timeout=12) as c:
        r = await c.get(f"{HERMES_API}/api/internal/user_mcp/tools_bundle",
                        headers=_headers(uid))
    if r.status_code != 200:
        raise RuntimeError(f"tools_bundle {r.status_code}: {r.text[:200]}")
    return r.json()


async def _list_my_sources(uid: str) -> str:
    """壳工具 ①:把用户的数据源与工具压成模型能读懂的结构。"""
    try:
        bundle = await _fetch_bundle(uid)
    except Exception as e:
        print(f"[hunter-user-mcp] bundle err: {e}", file=sys.stderr)
        return json.dumps({"error": f"读取数据源失败: {e}"}, ensure_ascii=False)

    sources = []
    for mcp in bundle.get("mcps", []):
        tools = [{
            "tool": t.get("name", ""),
            "description": (t.get("description") or "")[:300],
            "inputSchema": t.get("inputSchema") or {"type": "object", "properties": {}},
        } for t in mcp.get("tools", []) if t.get("name")]
        sources.append({
            "source": mcp.get("slug", ""),
            "display_name": mcp.get("display_name", ""),
            "tools": tools,
        })

    if not sources:
        return json.dumps({
            "sources": [],
            "hint": "该用户尚未接入任何外部数据源,请勿调用 invoke;"
                    "可提示用户到「MCP 组件」页面添加。",
        }, ensure_ascii=False)
    return json.dumps({"sources": sources}, ensure_ascii=False)


async def _invoke(uid: str, args: dict) -> str:
    """壳工具 ②:转发到 hermes-api,由它解密 key 并调真正的上游 MCP。"""
    source = (args.get("source") or "").strip()
    tool = (args.get("tool") or "").strip()
    if not source or not tool:
        return json.dumps({"error": "source 与 tool 均不能为空,请先调用 list_my_sources"},
                          ensure_ascii=False)
    inner = args.get("args")
    if not isinstance(inner, dict):
        inner = {}
    try:
        async with httpx.AsyncClient(timeout=45) as c:
            r = await c.post(f"{HERMES_API}/api/internal/user_mcp/call",
                             json={"slug": source, "tool": tool, "args": inner},
                             headers=_headers(uid))
        return r.text
    except Exception as e:
        return json.dumps({"error": f"调用失败: {type(e).__name__}: {e}"}, ensure_ascii=False)


@server.call_tool()
async def call_tool(name: str, args: dict) -> list[TextContent]:
    """两个壳工具的入口。user_id 由 hunter-mcp-context.ts 注入进 args。"""
    args = args or {}
    uid = _extract_uid(args)
    if not uid:
        return [TextContent(type="text", text=json.dumps(
            {"error": "未获取到当前用户身份,无法访问其数据源。"
                      "若持续出现请检查 hunter-mcp-context 插件是否覆盖本 MCP。"},
            ensure_ascii=False))]

    # opencode 可能把工具名前缀成 `usermcp_xxx`,两种都认
    bare = name[len("usermcp_"):] if name.startswith("usermcp_") else name

    if bare == "list_my_sources":
        return [TextContent(type="text", text=_fit(await _list_my_sources(uid), tool="hunter_user"))]
    if bare == "invoke":
        return [TextContent(type="text", text=_fit(await _invoke(uid, args), tool="hunter_user"))]

    return [TextContent(type="text", text=json.dumps(
        {"error": f"未知工具 {name};本 MCP 只提供 list_my_sources 与 invoke"},
        ensure_ascii=False))]


async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    print(f"[hunter-user-mcp] boot · hermes_api={HERMES_API}", file=sys.stderr)
    asyncio.run(main())
