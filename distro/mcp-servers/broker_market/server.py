#!/usr/bin/env python3
"""`broker_market` —— P0 券商行情与交易日历只读 MCP 服务。

提供 6 个只读工具：
1. `get_quote`：获取实时或延时报价快照（含最新价、昨收、开高低、成交量额、涨跌停价、停复牌状态）
2. `get_kline`：获取日/周/月/分钟历史 K 线（强制显式声明复权方式：前复权/后复权/不复权）
3. `get_orderbook`：获取五档/十档买卖盘口深度与委比
4. `get_market_status`：获取市场当前交易阶段（盘前/交易中/午休/已收盘/休市）
5. `get_market_overview`：获取核心宽基指数、两市成交额、涨跌家数与领涨领跌行业概览
6. `get_market_calendar`：获取交易日历（区分交易日、节假日、半日市）

全部返回均封装为 `BrokerDataEnvelope` 并经过 `hca_size_guard` 16KB 三档大小闸。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _shared import dump_guarded_envelope, get_router, run_stdio_mcp_server  # noqa: E402

SERVER_NAME = "broker_market"
SERVER_VERSION = "3.1.0"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_quote",
        "description": "查询股票/ETF实时或收盘报价快照（严格区分行情时间 as_of 与抓取时间 retrieved_at，停牌字段返回 null 绝不填 0）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "证券代码，如 600000.SH / 600036.SH / 510300.SH"},
                "market": {"type": "string", "enum": ["CN_A", "HK", "US"], "default": "CN_A"},
                "as_of": {"type": "string", "description": "指定历史行情时点（ISO-8601，可选）"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_kline",
        "description": "查询历史 K 线序列（必须显式指定 adjustment 复权方式：前复权 / 后复权 / 不复权）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "证券代码，如 600036.SH"},
                "market": {"type": "string", "enum": ["CN_A", "HK", "US"], "default": "CN_A"},
                "period": {"type": "string", "default": "1d", "description": "K线周期：1d / 1w / 1m / 60m"},
                "start": {"type": "string", "description": "起始日期 YYYY-MM-DD（可选）"},
                "end": {"type": "string", "description": "结束日期 YYYY-MM-DD（可选）"},
                "adjustment": {
                    "type": "string",
                    "enum": ["前复权", "后复权", "不复权", "qfq", "hfq", "none"],
                    "default": "前复权",
                    "description": "复权方式（必填，默认前复权）",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_orderbook",
        "description": "查询五档/十档买卖盘口深度、买卖价差与委比",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "证券代码，如 600000.SH"},
                "market": {"type": "string", "enum": ["CN_A", "HK", "US"], "default": "CN_A"},
                "levels": {"type": "integer", "default": 5, "description": "盘口档位数（5 或 10）"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_market_status",
        "description": "查询市场当前交易状态（是否交易日、当前所处时段：盘前/集合竞价/连续竞价/已收盘/休市）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "market": {"type": "string", "enum": ["CN_A", "HK", "US"], "default": "CN_A"},
                "as_of": {"type": "string", "description": "查询时点（ISO-8601，可选）"},
            },
        },
    },
    {
        "name": "get_market_overview",
        "description": "查询全市场概览（核心宽基指数表现、两市总成交额、涨跌停家数、领涨领跌行业）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "market": {"type": "string", "enum": ["CN_A", "HK", "US"], "default": "CN_A"},
                "as_of": {"type": "string", "description": "查询时点（ISO-8601，可选）"},
            },
        },
    },
    {
        "name": "get_market_calendar",
        "description": "查询市场交易日历（区分交易日、节假日休市与半日市）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "market": {"type": "string", "enum": ["CN_A", "HK", "US"], "default": "CN_A"},
                "start_date": {"type": "string", "description": "起始日期 YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "结束日期 YYYY-MM-DD"},
            },
        },
    },
]


def call_tool_sync(name: str, arguments: dict[str, Any], *, max_bytes: int | None = None) -> str:
    """同步调用 `broker_market` 工具并返回经 16KB 大小闸保护的 JSON 字符串。"""
    router = get_router()
    if name == "get_quote":
        env = router.get_quote(
            symbol=str(arguments["symbol"]),
            market=arguments.get("market", "CN_A"),
            as_of=arguments.get("as_of"),
        )
    elif name == "get_kline":
        adj = arguments.get("adjustment", "前复权")
        env = router.get_kline(
            symbol=str(arguments["symbol"]),
            market=arguments.get("market", "CN_A"),
            period=str(arguments.get("period", "1d")),
            start=arguments.get("start"),
            end=arguments.get("end"),
            adjustment=adj,
        )
    elif name == "get_orderbook":
        env = router.get_orderbook(
            symbol=str(arguments["symbol"]),
            market=arguments.get("market", "CN_A"),
            levels=int(arguments.get("levels", 5)),
        )
    elif name == "get_market_status":
        env = router.get_market_status(
            market=arguments.get("market", "CN_A"),
            as_of=arguments.get("as_of"),
        )
    elif name == "get_market_overview":
        env = router.get_market_overview(
            market=arguments.get("market", "CN_A"),
            as_of=arguments.get("as_of"),
        )
    elif name == "get_market_calendar":
        env = router.get_market_calendar(
            market=arguments.get("market", "CN_A"),
            start_date=arguments.get("start_date"),
            end_date=arguments.get("end_date"),
        )
    else:
        raise ValueError(f"未知工具名: {name}")

    return dump_guarded_envelope(env, tool_name=f"{SERVER_NAME}.{name}", max_bytes=max_bytes)


if __name__ == "__main__":
    run_stdio_mcp_server(SERVER_NAME, SERVER_VERSION, TOOLS, call_tool_sync)
