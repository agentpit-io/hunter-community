#!/usr/bin/env python3
"""`broker_fundamental` —— P0 券商财务报表、估值与公司行动只读 MCP 服务。

提供 5 个只读工具：
1. `get_financial_statements`：获取三大财务报表（利润表/资产负债表/现金流量表），严格支持
   `as_of` Point-in-Time 防偷看未来函数过滤（剔除 `published_at > as_of` 的未来财报）与重述标识
2. `get_financial_ratios`：获取核心财务比率（盈利、成长、偿债、现金流质量）
3. `get_valuation`：获取估值指标与历史分位数（PE-TTM、PB-MRQ、PS、EV/EBITDA、股息率）
4. `get_shareholders`：获取前十大股东、股东户数变化、股权质押与限售解禁数据
5. `get_corporate_actions`：获取分红送转、配股、回购等公司行动记录

全部返回均封装为 `BrokerDataEnvelope` 并经过 `hca_size_guard` 16KB 三档大小闸。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _shared import dump_guarded_envelope, get_router, run_stdio_mcp_server  # noqa: E402

SERVER_NAME = "broker_fundamental"
SERVER_VERSION = "3.1.0"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_financial_statements",
        "description": "查询上市公司三大财务报表（严格支持 as_of Point-in-Time 过滤，仅返回在 as_of 及之前已公开披露 published_at <= as_of 的财报，防止回测偷看未来）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "证券代码，如 600000.SH / 600036.SH"},
                "market": {"type": "string", "enum": ["CN_A", "HK", "US"], "default": "CN_A"},
                "report_period": {"type": "string", "description": "报告期，如 2026Q3 / 2025A（可选）"},
                "statement_type": {
                    "type": "string",
                    "enum": ["all", "income", "balance", "cashflow"],
                    "default": "all",
                    "description": "报表类型：all / income / balance / cashflow",
                },
                "as_of": {
                    "type": "string",
                    "description": "Point-in-Time 历史观察时点（ISO-8601 或 YYYY-MM-DD，可选；传入后自动剔除晚于该时点才披露的财报）",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_financial_ratios",
        "description": "查询核心财务比率（ROE、ROIC、毛利率、净利率、净现比、资产负债率等，支持 as_of PIT 过滤）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "证券代码，如 600036.SH"},
                "market": {"type": "string", "enum": ["CN_A", "HK", "US"], "default": "CN_A"},
                "report_period": {"type": "string", "description": "报告期，如 2026Q3（可选）"},
                "as_of": {"type": "string", "description": "Point-in-Time 历史观察时点（可选）"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_valuation",
        "description": "查询估值指标与 5 年历史分位数（PE-TTM、PB-MRQ、PS、EV/EBITDA、PEG、股息率）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "证券代码，如 600036.SH"},
                "market": {"type": "string", "enum": ["CN_A", "HK", "US"], "default": "CN_A"},
                "as_of": {"type": "string", "description": "估值时点（ISO-8601，可选）"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_shareholders",
        "description": "查询前十大股东/流通股东、股东户数环比变化、股权质押比例与限售解禁日历",
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
        "name": "get_corporate_actions",
        "description": "查询分红派息、送转股、配股、股份回购等公司行动历史记录（区分公告日与除权除息日）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "证券代码，如 600036.SH"},
                "market": {"type": "string", "enum": ["CN_A", "HK", "US"], "default": "CN_A"},
                "start_date": {"type": "string", "description": "除权日起始日期 YYYY-MM-DD（可选）"},
                "end_date": {"type": "string", "description": "除权日结束日期 YYYY-MM-DD（可选）"},
            },
            "required": ["symbol"],
        },
    },
]


def call_tool_sync(name: str, arguments: dict[str, Any], *, max_bytes: int | None = None) -> str:
    """同步调用 `broker_fundamental` 工具并返回经 16KB 大小闸保护的 JSON 字符串。"""
    router = get_router()
    if name == "get_financial_statements":
        env = router.get_financial_statements(
            symbol=str(arguments["symbol"]),
            market=arguments.get("market", "CN_A"),
            report_period=arguments.get("report_period"),
            statement_type=str(arguments.get("statement_type", "all")),
            as_of=arguments.get("as_of"),
        )
    elif name == "get_financial_ratios":
        env = router.get_financial_ratios(
            symbol=str(arguments["symbol"]),
            market=arguments.get("market", "CN_A"),
            report_period=arguments.get("report_period"),
            as_of=arguments.get("as_of"),
        )
    elif name == "get_valuation":
        env = router.get_valuation(
            symbol=str(arguments["symbol"]),
            market=arguments.get("market", "CN_A"),
            as_of=arguments.get("as_of"),
        )
    elif name == "get_shareholders":
        env = router.get_shareholders(
            symbol=str(arguments["symbol"]),
            market=arguments.get("market", "CN_A"),
            as_of=arguments.get("as_of"),
        )
    elif name == "get_corporate_actions":
        env = router.get_corporate_actions(
            symbol=str(arguments["symbol"]),
            market=arguments.get("market", "CN_A"),
            start_date=arguments.get("start_date"),
            end_date=arguments.get("end_date"),
        )
    else:
        raise ValueError(f"未知工具名: {name}")

    return dump_guarded_envelope(env, tool_name=f"{SERVER_NAME}.{name}", max_bytes=max_bytes)


if __name__ == "__main__":
    run_stdio_mcp_server(SERVER_NAME, SERVER_VERSION, TOOLS, call_tool_sync)
