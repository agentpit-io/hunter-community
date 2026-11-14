#!/usr/bin/env python3
"""`broker_funds` —— P1 券商 ETF、公募基金与可转债只读 MCP 服务。

提供 5 个只读工具：
1. `get_etf_quote`：获取 ETF 二级市场行情、IOPV 实时参考净值与折溢价率
2. `get_fund_nav`：获取基金历史单位净值、累计净值与复权净值序列
3. `get_etf_holdings`：获取 ETF 申赎清单（PCF）或重仓成分股列表
4. `get_fund_fee`：获取基金管理费率、托管费率、申购赎回费率结构
5. `get_bond_data`：获取可转债/债券行情、转股价值、转股溢价率、强赎/回售/下修条款

全部返回均封装为 `BrokerDataEnvelope` 并经过 `hca_size_guard` 16KB 三档大小闸。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _shared import dump_guarded_envelope, get_router, run_stdio_mcp_server  # noqa: E402
from app.providers.broker.envelope import BrokerDataEnvelope, format_iso_dt  # noqa: E402
from app.services.etf_dca import calculate_etf_dca_full_plan  # noqa: E402
from app.services.market_time import market_tz  # noqa: E402
from datetime import datetime  # noqa: E402

SERVER_NAME = "broker_funds"
SERVER_VERSION = "3.3.0"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_etf_quote",
        "description": "获取 ETF 二级市场行情、IOPV 实时参考净值与折溢价率（返回统一数据信封 BrokerDataEnvelope）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "ETF 代码，如 510300.SH（沪深300ETF）、510050.SH、159915.SZ"},
                "market": {"type": "string", "enum": ["FUND", "CN_A"], "default": "FUND"},
                "as_of": {"type": "string", "description": "查询时点（ISO-8601，可选）"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_fund_nav",
        "description": "获取基金历史单位净值、累计净值、复权净值与历史分红序列",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "基金/ETF 代码，如 510300.SH"},
                "start_date": {"type": "string", "description": "起始日期 YYYY-MM-DD（可选）"},
                "end_date": {"type": "string", "description": "截止日期 YYYY-MM-DD（可选）"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_etf_holdings",
        "description": "获取 ETF 申赎清单（PCF）或前十大重仓成分股列表与现金替代比例",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "ETF 代码，如 510300.SH"},
                "as_of": {"type": "string", "description": "查询时点（ISO-8601，可选）"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_fund_fee",
        "description": "获取基金管理费率、托管费率、场内佣金与场外申购赎回费率结构",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "基金/ETF 代码，如 510300.SH"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_bond_data",
        "description": "获取可转债/债券行情、转股价值、转股溢价率、强赎/回售/下修条款",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "可转债或债券代码，如 110059.SH（浦发转债）"},
                "as_of": {"type": "string", "description": "查询时点（ISO-8601，可选）"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "calculate_etf_dca",
        "description": "调用确定性计算引擎（app/services/etf_dca/）一站式计算 ETF 定投正向历史回测（累计投入、总份额、持仓均价、IRR、最大回撤）、目标金额反推与定投 vs 一次性买入同区间对比（严禁大模型心算复利或定投 IRR）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "ETF 代码，如 510300.SH"},
                "periodic_amount": {"type": "number", "default": 2000.0, "description": "每期定投金额（元）"},
                "frequency": {"type": "string", "enum": ["monthly", "biweekly", "weekly"], "default": "monthly"},
                "invest_day": {"type": "integer", "default": 15, "description": "定投扣款日（如每月15日）"},
                "start_date": {"type": "string", "default": "2023-11-01"},
                "end_date": {"type": "string", "default": "2026-11-13"},
                "target_amount": {"type": "number", "default": 100000.0, "description": "目标金额反推的终值目标（元）"},
                "target_years": {"type": "number", "default": 3.0, "description": "目标反推年限"},
                "assumed_annual_return_pct": {"type": "number", "default": 6.0, "description": "目标反推假设年化收益率(%)"},
            },
            "required": ["symbol"],
        },
    },
]


def call_tool_sync(name: str, arguments: dict[str, Any], *, max_bytes: int | None = None) -> str:
    """同步调用 `broker_funds` 工具并返回经 16KB 大小闸保护的 JSON 字符串。"""
    router = get_router()
    if name == "get_etf_quote":
        env = router.get_etf_quote(
            symbol=str(arguments["symbol"]),
            market=arguments.get("market", "FUND"),
            as_of=arguments.get("as_of"),
        )
    elif name == "get_fund_nav":
        env = router.get_fund_nav(
            symbol=str(arguments["symbol"]),
            start_date=arguments.get("start_date"),
            end_date=arguments.get("end_date"),
        )
    elif name == "get_etf_holdings":
        env = router.get_etf_holdings(
            symbol=str(arguments["symbol"]),
            as_of=arguments.get("as_of"),
        )
    elif name == "get_fund_fee":
        env = router.get_fund_fee(
            symbol=str(arguments["symbol"]),
        )
    elif name == "get_bond_data":
        env = router.get_bond_data(
            symbol=str(arguments["symbol"]),
            as_of=arguments.get("as_of"),
        )
    elif name == "calculate_etf_dca":
        plan = calculate_etf_dca_full_plan(
            symbol=str(arguments.get("symbol", "510300.SH")),
            periodic_amount=float(arguments.get("periodic_amount", 2000.0)),
            frequency=arguments.get("frequency", "monthly"),
            invest_day=int(arguments.get("invest_day", 15)),
            start_date=str(arguments.get("start_date", "2023-11-01")),
            end_date=str(arguments.get("end_date", "2026-11-13")),
            target_amount=float(arguments.get("target_amount", 100000.0)),
            target_years=float(arguments.get("target_years", 3.0)),
            assumed_annual_return_pct=float(arguments.get("assumed_annual_return_pct", 6.0)),
        )
        if isinstance(plan.get("backtest"), dict):
            eq_curve = plan["backtest"].get("equity_curve") or []
            if len(eq_curve) > 12:
                plan["backtest"]["equity_curve"] = eq_curve[:4] + eq_curve[-4:]
        env = BrokerDataEnvelope.build(
            provider="akshare_public_fallback",
            market="FUND",
            symbol=str(arguments.get("symbol", "510300.SH")),
            currency="CNY",
            unit="元",
            as_of=f"{arguments.get('end_date', '2026-11-13')}T15:00:00+08:00",
            retrieved_at=format_iso_dt(datetime.now(market_tz("CN_A"))),
            data=plan,
        )
    else:
        raise ValueError(f"未知工具名: {name}")

    return dump_guarded_envelope(env, tool_name=f"{SERVER_NAME}.{name}", max_bytes=max_bytes)


if __name__ == "__main__":
    run_stdio_mcp_server(SERVER_NAME, SERVER_VERSION, TOOLS, call_tool_sync)
