"""portfolio-mcp · 持仓建议 Phase 0

薄代理：3 tool 反调 /api/internal/portfolio/*
业务逻辑复用 api/app/services/subagents/portfolio_agent.py

对应 doc/codex/持仓建议/06-架构断层诊断-opencode-vs-orchestrator.md §5 方案 A。
部署位置：docker 容器内 /mcp/portfolio_mcp.py（host 挂载 /opt/opencode-mcp/）
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

server = Server("portfolio-mcp")


@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="portfolio_rebalance",
            description=(
                "组合级建议 · 拉当前登录用户的持仓（含 shares/cost_price/target_weight_pct），"
                "算当前权重 vs 目标权重的差距，出加/减仓动作。"
                "用户问『我持仓怎么调 / 帮我看看仓位 / 该加减哪只股 / 组合建议』时用。"
                "前置：用户必须已在 /portfolio 页录入至少 2 只票的 shares + cost_price · "
                "否则返回 empty:true + 引导 CTA。"
                "返回富卡片（PortfolioRebalanceCard 渲染 · 深色 LAYER 2 REBALANCE）· "
                "读 user_risk_profile 应用 cash_balance/max_position 等约束。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "cash_available": {"type": "number", "default": 0,
                                       "description": "可用现金 CNY · 缺省时取 user_risk_profile.cash_balance"},
                    "_hermes_user_id": {"type": "string"},
                },
                "required": [],
            },
        ),
        Tool(
            name="portfolio_stress",
            description=(
                "情景模拟 · 假设某只股跌/涨 X%，含行业联动效应，估算组合总损益。"
                "用户问『如果 {股票} 跌 20% 我组合亏多少 / 万一 {股票} 崩了 / 极端情况我怎么样』时用。"
                "前置：用户至少录入 1 只票的 shares + cost。"
                "返回富卡片（PortfolioStressCard 渲染 · 3 张损失卡 + 减半建议）。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "shock_code": {"type": "string",
                                    "description": "冲击源股票代码 · 例：601899（紫金矿业）"},
                    "shock_pct":  {"type": "number",
                                    "description": "冲击幅度百分比 · 例：-20 表示跌 20%"},
                    "sector_pass_through": {"type": "boolean", "default": True,
                                             "description": "是否含板块联动 · 默认 True"},
                    "_hermes_user_id": {"type": "string"},
                },
                "required": ["shock_code", "shock_pct"],
            },
        ),
        Tool(
            name="update_risk_profile",
            description=(
                "读取或更新当前用户的风险偏好（保守/稳健/进取）· 可用现金 · 单票/港股/单行业上限。"
                "用户说『我风险偏保守 / 现金还有 5 万 / 单票别超过 20% / 我风险画像是啥』时用。"
                "所有写入字段可选：只更新提到的字段 · 其他保留旧值。"
                "更新后 portfolio_rebalance / portfolio_stress 会自动应用新约束。"
                "返回富卡片（RiskProfileCard 渲染 · 5 约束 + diff 变化）。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "cash_balance": {"type": "number",
                                     "description": "CNY 可用现金 · 不给则不改"},
                    "risk_tolerance": {"type": "string", "enum": ["low", "medium", "high"],
                                        "description": "low=保守 / medium=稳健 / high=进取"},
                    "max_position": {"type": "number",
                                      "description": "单票上限 0.05-0.40（例：0.20 表示 20%）"},
                    "max_hk_ratio": {"type": "number",
                                      "description": "港股合计上限 0.0-1.0"},
                    "max_sector":   {"type": "number",
                                      "description": "单行业上限 0.0-1.0"},
                    "read_only":    {"type": "boolean", "default": False,
                                      "description": "True 时只读回当前 profile · 不写入"},
                    "_hermes_user_id": {"type": "string"},
                },
                "required": [],
            },
        ),
    ]


def _headers(args: dict) -> dict:
    key = INTERNAL_KEY if (INTERNAL_KEY and INTERNAL_KEY != "hunter-internal-2026") else _resolve_internal_key(INTERNAL_KEY or "hunter-internal-2026")
    h = {"X-Hunter-Internal-Key": key}
    uid = (args or {}).get("_hermes_user_id") or os.getenv("HUNTER_USER_ID", "")
    if uid:
        h["X-Hunter-User-Id"] = uid
    return h


@server.call_tool()
async def call_tool(name: str, args: dict):
    args = args or {}
    body = {k: v for k, v in args.items() if not k.startswith("_")}
    url = f"{HERMES_API}/api/internal/portfolio/{name}"
    try:
        async with httpx.AsyncClient(timeout=45) as c:
            r = await c.post(url, json=body, headers=_headers(args))
        text = r.text
        if r.status_code >= 400:
            try:
                data = json.loads(text)
                if isinstance(data, dict) and "detail" in data and "error" not in data:
                    data["error"] = data["detail"]
                    text = json.dumps(data, ensure_ascii=False)
            except Exception:
                pass
    except Exception as e:
        text = json.dumps({"error": f"hermes-api call failed: {type(e).__name__}: {e}",
                            "hermes_api": HERMES_API, "tool": name},
                          ensure_ascii=False)
    return [TextContent(type="text", text=_fit(text, tool="portfolio"))]


async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    print(f"[portfolio-mcp] boot · hermes_api={HERMES_API}", file=sys.stderr)
    asyncio.run(main())
