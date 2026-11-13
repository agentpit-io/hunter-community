#!/usr/bin/env python3
"""`broker_account_readonly` —— P2 用户单独 OAuth 授权后的券商账户只读连接器 MCP 服务。

提供 4 个纯只读工具（默认关闭，需有效 OAuth 授权 Scope）：
1. `get_accounts`：获取已绑定账户列表（账号强制脱敏，所需 Scope: `account.read`）
2. `get_account_summary`：获取账户资金概览（总资产、证券市值、可用现金，所需 Scope: `summary.read`）
3. `get_positions`：获取当前持仓快照（所需 Scope: `positions.read`）
4. `get_trades`：获取历史成交交割记录（仅用于交易归因与纪律复盘，所需 Scope: `trades.read`）

安全与合规硬红线：
- **不包含任何下单、撤单、改单或银证转账接口**；
- 每次调用强制使用 `hookd` 在 `PreToolUse` 阶段注入并校验的真实登录用户身份 `_hermes_user_id`；
- 若模型试图伪造 `user_id` 越权读取他人账户（TC-SEC-01），或用户未授权/已撤销授权（TC-SEC-02），
  100% 刚性拒绝并记录 `broker_audit_log`；
- 所有返回均封装为 `BrokerDataEnvelope` 并经过 `hca_size_guard` 16KB 三档大小闸。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _shared import dump_guarded_envelope, run_stdio_mcp_server  # noqa: E402
from app.providers.broker import (  # noqa: E402
    BrokerAccountUnauthorizedError,
    BrokerDataEnvelope,
    BrokerEnvelopeError,
    format_iso_dt,
    get_default_account_readonly_adapter,
)
from datetime import datetime  # noqa: E402
from app.services.market_time import market_tz  # noqa: E402

SERVER_NAME = "broker_account_readonly"
SERVER_VERSION = "3.4.0"
CN_TZ = market_tz("CN_A")

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_accounts",
        "description": "获取当前已授权用户的券商只读账户列表（账号脱敏展示，需 OAuth Scope: account.read，默认关闭）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "_hermes_user_id": {
                    "type": "string",
                    "description": "由 hookd 安全门卫在 PreToolUse 阶段强制覆写的真实登录用户 ID",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_account_summary",
        "description": "获取已授权券商账户的只读资金概览（总资产、证券市值、可用现金，需 OAuth Scope: summary.read）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {
                    "type": "string",
                    "description": "可选账户标识（默认查询主授权账户）",
                },
                "_hermes_user_id": {
                    "type": "string",
                    "description": "由 hookd 安全门卫在 PreToolUse 阶段强制覆写的真实登录用户 ID",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_positions",
        "description": "获取已授权券商账户的当前持仓快照（仅供组合风险巡检与复盘，需 OAuth Scope: positions.read）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {
                    "type": "string",
                    "description": "可选账户标识",
                },
                "_hermes_user_id": {
                    "type": "string",
                    "description": "由 hookd 安全门卫在 PreToolUse 阶段强制覆写的真实登录用户 ID",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_trades",
        "description": "获取已授权券商账户的历史成交交割记录（仅供复盘归因，不含任何下单/撤单功能，需 OAuth Scope: trades.read）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {
                    "type": "string",
                    "description": "可选账户标识",
                },
                "start_date": {
                    "type": "string",
                    "description": "起始日期 YYYY-MM-DD（可选）",
                },
                "end_date": {
                    "type": "string",
                    "description": "截止日期 YYYY-MM-DD（可选）",
                },
                "_hermes_user_id": {
                    "type": "string",
                    "description": "由 hookd 安全门卫在 PreToolUse 阶段强制覆写的真实登录用户 ID",
                },
            },
            "required": [],
        },
    },
]

FORBIDDEN_TRADING_TOOLS = {
    "place_order",
    "submit_order",
    "cancel_order",
    "modify_order",
    "transfer_cash",
    "buy_stock",
    "sell_stock",
}


def call_tool_sync(name: str, arguments: dict[str, Any], *, max_bytes: int | None = None) -> str:
    """同步调用 `broker_account_readonly` 工具并返回经 16KB 大小闸保护的 JSON 字符串。"""
    if name in FORBIDDEN_TRADING_TOOLS:
        raise PermissionError(
            f"安全红线拦截：`broker_account_readonly` 物理隔离实盘交易，严禁调用任何下单/撤单/改单/转账接口 (`{name}`)！"
        )

    hermes_uid = str(arguments.get("_hermes_user_id") or os.getenv("HUNTER_USER_ID") or "").strip()
    requested_uid = str(arguments.get("user_id") or hermes_uid).strip()
    adapter = get_default_account_readonly_adapter()

    try:
        if name == "get_accounts":
            env = adapter.get_accounts(user_id=requested_uid, session_user_id=hermes_uid or None)
        elif name == "get_account_summary":
            env = adapter.get_account_summary(
                user_id=requested_uid,
                account_id=arguments.get("account_id"),
                session_user_id=hermes_uid or None,
            )
        elif name == "get_positions":
            env = adapter.get_positions(
                user_id=requested_uid,
                account_id=arguments.get("account_id"),
                session_user_id=hermes_uid or None,
            )
        elif name == "get_trades":
            env = adapter.get_trades(
                user_id=requested_uid,
                account_id=arguments.get("account_id"),
                start_date=arguments.get("start_date"),
                end_date=arguments.get("end_date"),
                session_user_id=hermes_uid or None,
            )
        else:
            raise ValueError(f"未知工具名: {name}（本服务仅提供 4 个只读账户查询工具）")
    except BrokerAccountUnauthorizedError as exc:
        now_iso = format_iso_dt(datetime.now(CN_TZ))
        env = BrokerDataEnvelope.build(
            provider=adapter.provider_id,
            market="CN_A",
            as_of="2026-11-13T15:00:00+08:00",
            retrieved_at=now_iso,
            license_scope="unauthorized_blocked",
            ok=False,
            data=None,
            warnings=[str(exc)],
            errors=[
                BrokerEnvelopeError(
                    code=exc.code,
                    message=str(exc),
                    provider=adapter.provider_id,
                    retryable=False,
                )
            ],
        )

    return dump_guarded_envelope(env, tool_name=f"{SERVER_NAME}.{name}", max_bytes=max_bytes)


if __name__ == "__main__":
    run_stdio_mcp_server(SERVER_NAME, SERVER_VERSION, TOOLS, call_tool_sync)
