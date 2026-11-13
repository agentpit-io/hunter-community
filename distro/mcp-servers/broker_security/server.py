#!/usr/bin/env python3
"""`broker_security` —— P0 券商证券主数据只读 MCP 服务。

提供 4 个只读工具：
1. `search_security`：按代码、简称检索股票/ETF/债券
2. `get_security_profile`：获取证券基础档案（上市日期、所属板块、申万/中信行业、交易单位、币种）
3. `get_security_status`：获取证券交易状态（正常上市、停牌、ST/*ST风险警示、涨跌停价）
4. `get_index_constituents`：获取指数或行业成分股及权重（支持 `as_of` 历史时点防幸存者偏差）

全部返回均封装为 `BrokerDataEnvelope` 并经过 `hca_size_guard` 16KB 三档大小闸。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _shared import dump_guarded_envelope, get_router, run_stdio_mcp_server  # noqa: E402

SERVER_NAME = "broker_security"
SERVER_VERSION = "3.1.0"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_security",
        "description": "按代码、简称检索股票/ETF/债券主数据（返回统一数据信封 BrokerDataEnvelope）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词（证券代码或简称，如 600036 或 招商银行）"},
                "market": {"type": "string", "enum": ["CN_A", "HK", "US"], "description": "市场代码（可选）"},
                "asset_type": {"type": "string", "description": "资产类别（如 EQUITY / ETF / BOND，可选）"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_security_profile",
        "description": "查询证券基础档案（上市日期、板块、申万/中信行业分类、交易单位、币种）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "证券代码，如 600000.SH / 600036.SH / 00700.HK"},
                "market": {"type": "string", "enum": ["CN_A", "HK", "US"], "default": "CN_A"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_security_status",
        "description": "查询证券交易状态（正常上市、停牌、ST风险警示标记、涨跌停限制价格）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "证券代码，如 600000.SH"},
                "market": {"type": "string", "enum": ["CN_A", "HK", "US"], "default": "CN_A"},
                "as_of": {"type": "string", "description": "查询时点（ISO-8601，可选）"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_index_constituents",
        "description": "查询指数成分股及权重（支持传入 as_of 历史时点成分，防止回测幸存者偏差）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "index_code": {"type": "string", "description": "指数代码，如 000300.SH（沪深300）"},
                "as_of": {"type": "string", "description": "历史基准时点（ISO-8601 或 YYYY-MM-DD，可选）"},
            },
            "required": ["index_code"],
        },
    },
]


def call_tool_sync(name: str, arguments: dict[str, Any], *, max_bytes: int | None = None) -> str:
    """同步调用 `broker_security` 工具并返回经 16KB 大小闸保护的 JSON 字符串。"""
    router = get_router()
    if name == "search_security":
        env = router.search_security(
            query=str(arguments.get("query", "")),
            market=arguments.get("market"),
            asset_type=arguments.get("asset_type"),
        )
    elif name == "get_security_profile":
        env = router.get_security_profile(
            symbol=str(arguments["symbol"]),
            market=arguments.get("market", "CN_A"),
        )
    elif name == "get_security_status":
        env = router.get_security_status(
            symbol=str(arguments["symbol"]),
            market=arguments.get("market", "CN_A"),
            as_of=arguments.get("as_of"),
        )
    elif name == "get_index_constituents":
        env = router.get_index_constituents(
            index_code=str(arguments["index_code"]),
            as_of=arguments.get("as_of"),
        )
    else:
        raise ValueError(f"未知工具名: {name}")

    return dump_guarded_envelope(env, tool_name=f"{SERVER_NAME}.{name}", max_bytes=max_bytes)


if __name__ == "__main__":
    run_stdio_mcp_server(SERVER_NAME, SERVER_VERSION, TOOLS, call_tool_sync)
