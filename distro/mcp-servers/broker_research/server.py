#!/usr/bin/env python3
"""`broker_research` —— P1 券商合规授权研报摘要与行业一致预期只读 MCP 服务。

提供 3 个只读工具：
1. `get_research_reports`：检索经合法授权的券商研究报告摘要列表（支持 PIT `as_of` 过滤）
2. `get_consensus_estimates`：获取分析师一致预期（EPS/营收/ROE 预测均值、区间与调整方向）
3. `get_sector_data`：获取行业景气度与同业可比估值分位、拥挤度指标

全部返回均封装为 `BrokerDataEnvelope` 并经过 `hca_size_guard` 16KB 三档大小闸。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _shared import dump_guarded_envelope, get_router, run_stdio_mcp_server  # noqa: E402

SERVER_NAME = "broker_research"
SERVER_VERSION = "3.3.0"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_research_reports",
        "description": "检索经合法授权的券商研究报告摘要列表（支持 as_of 历史时点过滤）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "证券代码，如 600036.SH（可选）"},
                "sector": {"type": "string", "description": "行业名称或代码，如 银行（可选）"},
                "query": {"type": "string", "description": "检索关键词（可选）"},
                "as_of": {"type": "string", "description": "历史时点过滤（ISO-8601 或 YYYY-MM-DD，可选）"},
            },
            "required": [],
        },
    },
    {
        "name": "get_consensus_estimates",
        "description": "获取分析师一致预期（EPS/营收/净利润预测均值、高低区间、远期PE与30日修正方向）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "证券代码，如 600036.SH"},
                "market": {"type": "string", "enum": ["CN_A", "HK", "US"], "default": "CN_A"},
                "as_of": {"type": "string", "description": "查询时点（ISO-8601，可选）"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_sector_data",
        "description": "获取行业景气度、同业可比估值分位数与行业交易拥挤度指标",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sector_code": {"type": "string", "description": "行业代码或名称，如 SW_BANK 或 银行"},
                "as_of": {"type": "string", "description": "查询时点（ISO-8601，可选）"},
            },
            "required": ["sector_code"],
        },
    },
]


def call_tool_sync(name: str, arguments: dict[str, Any], *, max_bytes: int | None = None) -> str:
    """同步调用 `broker_research` 工具并返回经 16KB 大小闸保护的 JSON 字符串。"""
    router = get_router()
    if name == "get_research_reports":
        env = router.get_research_reports(
            symbol=arguments.get("symbol"),
            sector=arguments.get("sector"),
            query=arguments.get("query"),
            as_of=arguments.get("as_of"),
        )
    elif name == "get_consensus_estimates":
        env = router.get_consensus_estimates(
            symbol=str(arguments["symbol"]),
            market=arguments.get("market", "CN_A"),
            as_of=arguments.get("as_of"),
        )
    elif name == "get_sector_data":
        env = router.get_sector_data(
            sector_code=str(arguments["sector_code"]),
            as_of=arguments.get("as_of"),
        )
    else:
        raise ValueError(f"未知工具名: {name}")

    return dump_guarded_envelope(env, tool_name=f"{SERVER_NAME}.{name}", max_bytes=max_bytes)


if __name__ == "__main__":
    run_stdio_mcp_server(SERVER_NAME, SERVER_VERSION, TOOLS, call_tool_sync)
