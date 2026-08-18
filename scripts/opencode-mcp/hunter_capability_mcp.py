"""hunter-capability-mcp · 把平台自有能力暴露成 MCP 工具

`_12` §7 Step 3。**要解决的问题**:K线预测 / 情报 这些是我们最强的能力,
但原来只有 HTTP 接口、不是 MCP —— 模型手上够不着。表现是用户点侧栏卡片能用,
在对话里说"帮我预测茅台走势"却降级成"我无法获取"。

⚠️ **改完这个文件必须 `docker compose restart opencode`。**

bind mount 让文件立刻更新,但 **MCP 是 opencode 启动时 spawn 的子进程** ——
它跑的还是启动那一刻的那份代码。而 `docker compose up -d opencode`
对**没有变化的服务是空操作**,不会重启。

这个坑的表现极具误导性:文件明明是新的、工具明明写好了,
但模型说「我无法访问外部 GitHub」——看起来像是模型能力不足或提示词没写对,
实际是它压根没看到那几个工具。2026-08-18 `_23` 步 4 实测踩到,
当时 opencode 已经连续跑了 25 小时。

验证办法(不要靠猜):
    docker compose exec -T opencode sh -c 'cd /opt/hunter-mcp && python3 -c "
    import asyncio,sys; sys.path.insert(0,\".\")
    import hunter_capability_mcp as m
    asyncio.run(m.list_tools())"'

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
_TIMEOUT = {
    "kpred": 120.0, "truesource_scout": 150.0, "truesource_brief": 60.0,
    # 仓库操作要打 GitHub API + raw,国内网络下慢,给宽一点。
    # repo_open 一次取 树 + README + INSTALL.md 三样
    "skill_repo_open": 90.0, "skill_repo_read": 60.0,
    "skill_stage": 20.0, "skill_staged": 20.0,
}


@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="kpred",
            description=(
                "⚠️ **仅用于未来价格预测** · Kronos 清华金融时序大模型 GPU 推理。"
                "输入历史 K 线 → 预测未来 N 个交易日(默认 10 天)的开高低收 + 涨跌幅 + 方向判断。"
                "**必须包含明确的未来时间导向词才用**:"
                "「未来 N 天 / 下周 / 明天 / 后市 / 会涨会跌 / 涨跌预测 / 走势预测 / 预测走势」。"
                "**不要用来做以下事**(即使 prompt 里有'走势'两字):"
                "  · 查历史 K 线数据 / 看走势数据 / 最近 N 天走势 → 用 stock_quickview(它已返 30 天历史 K 线)"
                "  · 查实时股价 / 最新价 → 用 stock_quickview"
                "  · 深度基本面分析 → 用 stock_deep_analysis"
                "**耗时 10-120 秒**(GPU 推理 · 首次冷启可能更久)。"
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
        Tool(
            name="skill_repo_open",
            description=(
                "打开一个 GitHub 上的 SKILL 仓库,拿到三样东西:文件树、README 全文、"
                "以及**作者写给 opencode 的安装说明全文**(仓库带 .opencode/INSTALL.md 时)。"
                " 用户说「请按照 <github地址> 安装这个 SKILL」时用这个。"
                " **拿到之后请按作者的说明办** —— 很多作者会按 agent 分别写安装方式,"
                "他知道该装哪几个、怎么按市场挑,我们不知道。返回里的 install_doc 就是他写的那份;"
                "没有的话读 readme 自己判断。"
                " 要看别的文件用 skill_repo_read;决定装什么之后用 skill_stage 逐个暂存。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string",
                             "description": "GitHub 地址或 owner/repo,如 wbh604/UZI-Skill"},
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="skill_repo_read",
            description=(
                "读上一步那个仓库里的某个文件(SKILL.md / 文档 / 示例都行)。"
                "**只能读 repo 指定的那个仓库** —— 站外链接一律读不到。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "同 skill_repo_open 的 repo"},
                    "path": {"type": "string", "description": "仓库内路径,如 skills/dcf/SKILL.md"},
                },
                "required": ["repo", "path"],
            },
        ),
        Tool(
            name="skill_stage",
            description=(
                "暂存一个 SKILL,**不写磁盘** —— 用户看完确认卡才真正安装。"
                " content 传完整的 SKILL.md(含 frontmatter):从仓库直接取的原样传,"
                "需要改写 frontmatter 的(比如 Claude Code 的 slash command)改好再传。"
                " **frontmatter 必须带 hunter: 段**,否则装进去用户在能力列表里点不动它 ——"
                "别人仓库的 SKILL 基本都没有,需要你补:display_name(给人看的名字)、"
                "icon、category(快速判断/综合分析/投研报告/估值建模/事件与筛选/组合级/尽调风控 之一)、"
                "prompt_tpl(用户点它时填进输入框的话,中文占位符如 {股票})。"
                " note 写**你为什么装它**,一句话即可 —— 用户在确认卡上看这句决定要不要留。"
                " 全部暂存完之后告诉用户「已准备好 N 个,请确认」,**不要自己宣布安装完成**。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "name": {"type": "string",
                             "description": "SKILL 名 · 小写字母数字下划线,会成为目录名"},
                    "content": {"type": "string", "description": "完整 SKILL.md 内容"},
                    "source_path": {"type": "string", "description": "来自仓库的哪个文件(可选)"},
                    "note": {"type": "string", "description": "为什么装它 · 给用户看"},
                },
                "required": ["name", "content"],
            },
        ),
        Tool(
            name="skill_staged",
            description="查你已经暂存了哪些(装到一半忘了装过什么时用)。不需要参数。",
            inputSchema={"type": "object", "properties": {}},
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
