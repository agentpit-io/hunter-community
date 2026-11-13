#!/usr/bin/env python3
"""`broker_education` —— P1 券商官方投资者教育与标准风险揭示书只读 MCP 服务。

提供 3 个只读工具：
1. `search_investor_education`：检索券商官方投教文章（定投科学指南、量化回测防坑、防非反诈科普）
2. `get_product_guide`：获取业务规则说明（ETF、可转债、科创板等交易与申赎规则）
3. `get_risk_disclosure`：获取标准风险揭示书与投资者适当性提示文本

全部返回均封装为 `BrokerDataEnvelope` 并经过 `hca_size_guard` 16KB 三档大小闸。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _shared import dump_guarded_envelope, get_router, run_stdio_mcp_server  # noqa: E402

SERVER_NAME = "broker_education"
SERVER_VERSION = "3.3.0"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_investor_education",
        "description": "检索券商官方投资者教育文章（基础概念、交易规则、定投科普、量化防坑、防非反诈）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索关键词，如 定投 / 回测 / 防非"},
                "category": {"type": "string", "description": "投教分类（可选）"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_product_guide",
        "description": "获取业务规则说明（如 ETF、CONVERTIBLE_BOND 可转债、STAR_BOARD 科创板）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "product_type": {"type": "string", "description": "产品或板块类型，如 ETF / CONVERTIBLE_BOND / STAR_BOARD"},
            },
            "required": ["product_type"],
        },
    },
    {
        "name": "get_risk_disclosure",
        "description": "获取标准风险揭示书与适当性提示文本（如 ETF_DCA / QUANT_BACKTEST / ST_DELISTING / GENERAL_EQUITY）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "risk_type": {"type": "string", "description": "风险揭示书类型，如 ETF_DCA / QUANT_BACKTEST / ST_DELISTING"},
            },
            "required": ["risk_type"],
        },
    },
]


def call_tool_sync(name: str, arguments: dict[str, Any], *, max_bytes: int | None = None) -> str:
    """同步调用 `broker_education` 工具并返回经 16KB 大小闸保护的 JSON 字符串。"""
    router = get_router()
    if name == "search_investor_education":
        env = router.search_investor_education(
            query=str(arguments.get("query", "")),
            category=arguments.get("category"),
        )
    elif name == "get_product_guide":
        env = router.get_product_guide(
            product_type=str(arguments["product_type"]),
        )
    elif name == "get_risk_disclosure":
        env = router.get_risk_disclosure(
            risk_type=str(arguments["risk_type"]),
        )
    else:
        raise ValueError(f"未知工具名: {name}")

    return dump_guarded_envelope(env, tool_name=f"{SERVER_NAME}.{name}", max_bytes=max_bytes)


if __name__ == "__main__":
    run_stdio_mcp_server(SERVER_NAME, SERVER_VERSION, TOOLS, call_tool_sync)
