"""runtime-mcp · **控制通道**（L08 · 五期方案 §5.1 / §6.3 / §10.1）

这是**唯一一个控制类 MCP server** —— 与数据类 MCP（`watchlist_mcp` / `screener_mcp` /
`uzi_mcp` / `hunter_capability_mcp` …）**在配置上分开注册**（见 `scripts/opencode/gen-config.py`
的 `_CONTROL_MCP`）。这就是方案 §6.3「控制通道与数据通道分离」的落点：
**控制**（启动 / 查询 / 暂停 / 取消工作流）单独一个 server，**数据**（行情 / 选股 / 自选…）
各是另外几个。

四个工具 = `runtime.workflow_start/get/pause/cancel`（§10.1 的「运行时」一行），
全部转发到 fin-worker 的内部端点（`apps/fin-worker/app/api.py` 的 `/internal/runtime/*`）。

**它为什么不是后门**：

- `workflow_start` 只接受 fin-worker 白名单里的**模板名 + 类型化参数**；模板名不在白名单、
  参数名没登记、类型不对 —— fin-worker 一律拒绝（404 / 400），这里**原样把拒绝转述给模型**。
- 没有「跑一段代码 / 跑一条命令 / 打任意 URL」这类参数。
- 走共享口令 `HUNTER_INTERNAL_KEY`；fin-worker 只绑 127.0.0.1，不经公网。
- 工具描述里明确告诉模型：**只读它自己 `workflow_get` 回来的真实状态**，不要凭记忆编状态。

失败对象带 `type` / `code` / `instruction`（照 `uzi_mcp` 的约定）：fin-worker 连不上时，
明确让模型「如实说连不上控制通道、不要编」。
"""
import asyncio
import json
import os
import sys

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# docker 网络里 fin-worker 的服务名；本机自测可设 FIN_WORKER_URL=http://127.0.0.1:8399
FIN_WORKER = os.getenv("FIN_WORKER_URL", "http://fin-worker:8300").rstrip("/")
INTERNAL_KEY = os.getenv("HUNTER_INTERNAL_KEY", "")

server = Server("runtime-mcp")

# 六个时点模板 + ETL / 标的同步 + 四条回路 + 两条采集 —— 与 fin-worker 的
# `runtime_control.TEMPLATES` 一致（`apps/fin-worker/tests/test_runtime_mcp.py` 盯着，写歪了就红）。
# 这里列出只是为了给模型「写得出模板名」；**判定仍在 fin-worker**（这里写死也不会放宽白名单）。
_TEMPLATES = [
    "fin.point_preopen", "fin.point_decide", "fin.point_match_a", "fin.point_match_b",
    "fin.point_match_c", "fin.point_close", "fin.market_etl", "fin.instrument_sync",
    "fin.review", "fin.shadow", "fin.observe", "fin.propose",
    "fin.news_collect", "fin.fundamental_collect",
]


@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="runtime_workflow_start",
            description=(
                "**启动一个运行时工作流**（只认白名单模板 + 类型化参数）。"
                "用户说『跑一次今天的开盘决策 / 触发一下港股收盘 / 手动跑一次复盘 / "
                "启动标的同步』时用。\n\n"
                f"template 只能是这几个之一：{_TEMPLATES}\n\n"
                "params 按模板选：时点类要 market（CN_A/HK/US）；market_etl / instrument_sync / "
                "两条采集（news_collect / fundamental_collect）的 market 用小写 cn/hk/us。"
                "可选 trade_date(YYYY-MM-DD) / at(HH:MM) / project_id / code / hold_seconds 等。\n\n"
                "**不接受任意工作流名、不接受代码或命令** —— 传别的名字会被拒绝，"
                "把拒绝原文转述给用户即可，不要换别的名字硬试。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "template": {"type": "string", "description": "白名单里的工作流模板名"},
                    "params": {"type": "object", "description": "该模板允许的参数（见描述）"},
                    "workflow_id": {"type": "string",
                                    "description": "可选 · 显式给工作流 id；不给则按模板+市场+日期推导"},
                },
                "required": ["template"],
            },
        ),
        Tool(
            name="runtime_workflow_get",
            description=(
                "**查工作流 / 调度的状态**（状态从 Temporal 现查，不维护第二份）。"
                "用户说『那个任务跑完没有 / 现在什么状态 / 调度是不是暂停着』时用。\n\n"
                "给 workflow_id（查执行：RUNNING / COMPLETED / FAILED / CANCELED …，"
                "以及 paused、current_job）或 schedule_id（查调度：paused / 下次触发时刻）。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string"},
                    "run_id": {"type": "string"},
                    "schedule_id": {"type": "string"},
                },
                "required": [],
            },
        ),
        Tool(
            name="runtime_workflow_pause",
            description=(
                "**暂停 / 恢复**。幂等（重复暂停不报错）。"
                "用户说『暂停一下定时计划 / 先别让它跑 / 恢复刚才暂停的计划』时用。\n\n"
                "· schedule_id：暂停 / 恢复一条 Temporal **调度**（该计划不再触发；最常用）。\n"
                "· workflow_id：暂停 / 恢复一个**正在跑的工作流**（协作式，下一个检查点生效）。\n"
                "resume=true 表示恢复。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "schedule_id": {"type": "string"},
                    "workflow_id": {"type": "string"},
                    "run_id": {"type": "string"},
                    "resume": {"type": "boolean", "description": "true = 恢复；默认 false = 暂停"},
                },
                "required": [],
            },
        ),
        Tool(
            name="runtime_workflow_cancel",
            description=(
                "**取消一个正在跑的工作流，并把它当前的长任务一并取消**（`fin_job`）。"
                "幂等：已经结束的工作流再取消返回 `already_ended`，**不是错误**。"
                "用户说『停掉那个正在跑的任务 / 取消刚才那次运行』时用。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string"},
                    "run_id": {"type": "string"},
                },
                "required": ["workflow_id"],
            },
        ),
    ]


def _headers() -> dict:
    return {"X-Hunter-Internal-Key": INTERNAL_KEY, "Content-Type": "application/json"}


def _fail(kind: str, detail: str) -> str:
    """失败对象（照 `uzi_mcp` 约定）—— 带 type/code/instruction，明确告诉模型别编。"""
    return json.dumps({
        "error": detail,
        "type": "runtime_control_error",
        "code": kind,
        "fin_worker": FIN_WORKER,
        "instruction": "控制通道这次没调通。**如实告诉用户失败原因，不要编造工作流状态或结果**；"
                       "若说连不上，就把这段原话转述。",
    }, ensure_ascii=False)


async def _call(method: str, path: str, *, params: dict | None = None, body: dict | None = None) -> str:
    url = f"{FIN_WORKER}{path}"
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.request(method, url, params=params, json=body, headers=_headers())
        # 非 2xx 也把 body 返回：模型要拿到 fin-worker 的拒绝原文（白名单外 / 参数非法）。
        return r.text
    except Exception as e:  # noqa: BLE001 —— 连不上也要给结构化失败对象
        return _fail("unreachable", f"无法连接控制通道 fin-worker（{type(e).__name__}: {e}）")


@server.call_tool()
async def call_tool(name: str, args: dict):
    args = args or {}
    if name == "runtime_workflow_start":
        body = {"template": args.get("template"), "params": args.get("params") or {}}
        if args.get("workflow_id"):
            body["workflow_id"] = args["workflow_id"]
        text = await _call("POST", "/internal/runtime/workflow_start", body=body)
    elif name == "runtime_workflow_get":
        params = {k: args[k] for k in ("workflow_id", "run_id", "schedule_id") if args.get(k)}
        text = await _call("GET", "/internal/runtime/workflow_get", params=params)
    elif name == "runtime_workflow_pause":
        body = {k: args[k] for k in ("schedule_id", "workflow_id", "run_id") if args.get(k)}
        body["resume"] = bool(args.get("resume"))
        text = await _call("POST", "/internal/runtime/workflow_pause", body=body)
    elif name == "runtime_workflow_cancel":
        body = {"workflow_id": args.get("workflow_id")}
        if args.get("run_id"):
            body["run_id"] = args["run_id"]
        text = await _call("POST", "/internal/runtime/workflow_cancel", body=body)
    else:
        text = _fail("unknown_tool", f"未知工具 {name}")
    return [TextContent(type="text", text=text)]


async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    print(f"[runtime-mcp] boot · fin_worker={FIN_WORKER} key_set={bool(INTERNAL_KEY)}", file=sys.stderr)
    asyncio.run(main())
