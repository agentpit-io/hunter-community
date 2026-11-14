# ⚠️ 本文件是 huntercode(agentpit-io/huntercode · dev 分支)mcp/uzi_mcp.py 的**部署副本**,
# 通过 docker-compose.yml 的 bind mount 覆盖镜像里的同名文件 —— 与 watchlist_mcp.py 同一套路。
#
# 为什么要副本:镜像 ghcr.io/agentpit-io/hunter-opencode:dev 由 huntercode 的 GitHub Action
# 在 push dev 时重建,但服务器上的 GHCR 登录会过期(2026-09-07 实测 `docker compose pull`
# 返 denied,机器上也没有 gh),等镜像不如直接挂文件。
#
# 改动只在 huntercode 仓做,改完再把文件整个拷过来(保留本段头注释),不要在这里单独改。
# 当前对应 huntercode 提交:346f1119cc(2026-09-17 · 描述不再让模型调已删除的 kpred)
"""uzi-mcp · hunter-UZI-Skill 深度分析入口 · Sprint 3 P2 · Phase 1 MVP

薄代理：把 opencode LLM 的 tool_call 转发到 hermes-api /api/internal/uzi/*。
Phase 1 走 finance-data 7 数据 + Gemini 秒级合成 markdown（不依赖 SG UZI worker）。
Phase 2 会加 full_analysis · 触发 SG 完整 22 dim pipeline · 后台 + poll。

对应文档：
- hermes-1/doc/codex/自定义MCP/UZI/Sprint-1-2-完成报告.md §四 P2
- hermes-1/doc/codex/自定义MCP/UZI/04-UZI-Skill集成到hunter-chat方案.md

部署位置：docker 容器内 /mcp/uzi_mcp.py（host 挂载 /opt/opencode-mcp/）
"""
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


def _http_timeout() -> float:
    """深度分析的 httpx 读超时(秒)· 默认 170,可用 UZI_HTTP_TIMEOUT 覆盖。

    为什么要能改:这个值必须**压在** opencode 那侧的 MCP timeout 之下(镜像里是
    180000 ms),否则 opencode 先掐断,模型拿到的是 "(pending / no output)" ——
    看不出是超时。谁调大了 MCP timeout,就得同步调大这里,所以做成环境变量而不是
    写死。给了非数字或 <=0 的值时按默认值走,不让一个笔误把整条链路变成不超时。
    """
    raw = (os.getenv("UZI_HTTP_TIMEOUT") or "").strip()
    if not raw:
        return 170.0
    try:
        val = float(raw)
    except ValueError:
        print(f"[uzi-mcp] UZI_HTTP_TIMEOUT={raw!r} 不是数字,回落 170s", file=sys.stderr)
        return 170.0
    if val <= 0:
        print(f"[uzi-mcp] UZI_HTTP_TIMEOUT={raw!r} 非正数,回落 170s", file=sys.stderr)
        return 170.0
    return val


HTTP_TIMEOUT = _http_timeout()

server = Server("uzi-mcp")


@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="stock_deep_analysis",
            description=(
                "⚠️ **重量级深度分析** · 内部长任务 **60-300 秒 · 消耗 8k-15k tokens**。"
                "**仅当**用户明确要求「深度分析 / 深度看看 / 深挖 / 基本面分析 / 投资论点 / 多空辩论」时用。"
                "**不要用来做以下场景**（有更合适的轻量 tool）："
                "查行情/最新价 → 用 stock_quickview（3-8 秒 · 也返 30 天 K 线）；"
                "走势预测/涨跌预测/未来 N 天怎么走 → 看你的工具列表里有没有 K 线预测工具"
                "（SaaS 部署叫 kronos_kronos_forecast）· 有就用它,没有就如实告诉用户本部署不提供走势预测,"
                "**不要**拿本工具凑数；"
                "拉新闻 → 用 stock_news。"
                "**做什么**：拉实时行情+30日K线+财务+龙虎榜+十大股东+治理+新闻 → Gemini 合成结构化 markdown。"
                "**正在按某个 SKILL 分析时,必须把该 SKILL 要求的报告结构填进 `outline`** —— "
                "不填的话所有 SKILL 都会得到同一份「多空/技术/基本面/资金/催化风险/结论」六段报告,"
                "SKILL 的方法论等于没生效。"
                "适合：A 股、港股、美股 · 数据源全部 finance-data 内部。"
                "限制：龙虎榜/十大股东/治理表尚未 seed，会显示'数据未 seed'。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "code":  {"type": "string",
                              "description": "6位A股(如 601899) / 5位港股(如 00700) / 美股代码(如 AAPL)"},
                    "depth": {"type": "string",
                              "enum": ["lite"],
                              "default": "lite",
                              "description": "分析深度 · Phase 1 只支持 lite（秒级）· medium/deep 待 Phase 2 SG worker"},
                    "outline": {"type": "string",
                                "description": (
                                    "报告的小标题结构 · **正在按某个 SKILL 分析时必填**。"
                                    "把该 SKILL 要求的输出结构写成几行 markdown 三级标题"
                                    "（如 `### 一、投资论点` / `### 二、估值锚点` …），"
                                    "每行可在括号里补一句这一节要写什么。不填则用默认的"
                                    "「多空/技术/基本面/资金/催化风险/结论」六段。"
                                    "⚠️ 只决定**结构**：不给买卖评级、只用工具返回的数据、"
                                    "不编数字这些硬约束由后端强制,写在这里也不会被采纳。"
                                )},
                    "_hermes_user_id": {"type": "string",
                                        "description": "内部字段 · 由 hunter-mcp-context plugin 注入 · 请勿填写"},
                },
                "required": ["code"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    user_id = (arguments or {}).pop("_hermes_user_id", "") or ""

    if name == "stock_deep_analysis":
        code = (arguments.get("code") or "").strip()
        depth = arguments.get("depth", "lite")
        outline = (arguments.get("outline") or "").strip()
        if not code:
            return [TextContent(type="text", text=json.dumps(
                {"error": "code 必填 · 如 601899 / 00700 / AAPL"}, ensure_ascii=False))]

        key = INTERNAL_KEY if (INTERNAL_KEY and INTERNAL_KEY != "hunter-internal-2026") else _resolve_internal_key(INTERNAL_KEY or "hunter-internal-2026")
        headers = {
            "X-Hunter-Internal-Key": key,
            "X-Hunter-User-Id":      user_id,
            "Content-Type":          "application/json",
        }
        def _fail(error: str, detail: str = "") -> list[TextContent]:
            # 失败对象也带 type/code:前端富卡片按 tool 名分发,拿到这份就能画出
            # "调用失败"态而不是 NaN + 假计时。
            # instruction 是写给 chat 模型看的:2026-09-07 茅台事故里,工具超时后模型
            # 转头凭记忆写了一篇"分析",里面"营收增速放缓至 1.30%"是编的 —— 用户看到的
            # 是一张报错卡下面跟着一篇有数字的正文,分不清哪个可信。
            payload = {
                "type":  "uzi_deep_analysis",
                "code":  code,
                "error": error,
                "instruction": (
                    "深度分析工具本次失败,没有拿到任何数据。请如实告诉用户失败原因、"
                    "建议稍后重试;**严禁**凭记忆自行撰写分析报告,**严禁**给出任何"
                    "营收 / 增速 / 涨跌幅 / 估值等数字。"
                ),
            }
            if detail:
                payload["detail"] = detail
            return [TextContent(type="text", text=_fit(json.dumps(payload, ensure_ascii=False), tool="uzi"))]

        try:
            # 后端三段预算 拉数 40s + 兜底 30s + LLM 60s = 130s(见 hunter-community
            # internal_uzi.py 顶部注释)。这里 170s 压在 opencode MCP timeout 180s 之下、
            # 又给后端留 40s 余量 —— 2026-09-07 前是 120s,后端一次拉数卡了 137s 就把
            # 整条链路打穿(ReadTimeout → 模型拿到 error → 前端卡片 NaN)。
            # 2026-08-14 · community 踩坑后回流;2026-09-07 · 再抬并对齐后端预算。
            # 2026-09-17 · 数值改由 UZI_HTTP_TIMEOUT 决定(默认仍是 170),见文件头 _http_timeout()。
            async with httpx.AsyncClient(timeout=httpx.Timeout(HTTP_TIMEOUT, connect=10.0)) as client:
                r = await client.post(
                    f"{HERMES_API}/api/internal/uzi/stock_deep_analysis",
                    headers=headers,
                    json={"code": code, "depth": depth, "outline": outline},
                )
                r.raise_for_status()
                data = r.json()
        except httpx.HTTPStatusError as e:
            return _fail(f"hermes-api {e.response.status_code}", e.response.text[:500])
        except httpx.TimeoutException as e:
            # str(httpx.ReadTimeout) 是空串 · 原来的报错只有 "ReadTimeout: " 五个字
            return _fail(
                f"hermes-api 超过 170s 未响应 ({type(e).__name__}) · "
                "上游数据源或 LLM 卡住 · 请稍后重试")
        except Exception as e:
            return _fail(f"UZI 深度分析失败: {type(e).__name__}: {e}")

        # 前端可选接 UziDeepAnalysisCard.tsx · 但 markdown 直接展示也可用
        result = {
            "type":         "uzi_deep_analysis",
            "code":         data.get("code"),
            "name":         data.get("name"),
            "depth":        data.get("depth"),
            "markdown":     data.get("markdown"),
            "dims_covered": data.get("dims_covered", []),
            "dims_missing": data.get("dims_missing", []),
            "duration_ms":  data.get("duration_ms"),
            "timing":       data.get("timing"),
            "model":        data.get("model"),
            "note":         data.get("note"),
        }
        return [TextContent(type="text", text=_fit(json.dumps(result, ensure_ascii=False, indent=2), tool="uzi"))]

    return [TextContent(type="text", text=json.dumps(
        {"error": f"unknown tool: {name}"}, ensure_ascii=False))]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
