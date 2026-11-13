#!/usr/bin/env python3
"""`broker_disclosure` —— P0 券商上市公司公告与合规资讯只读 MCP 服务。

提供 4 个只读工具：
1. `search_announcements`：按证券代码、关键词、公告分类检索全市场公告列表（支持 Point-in-Time `as_of`）
2. `get_announcement`：拉取单篇公告详情与 PDF 分页正文/表格（保留 `[来源：公告 · 第X页]` 溯源标签）
3. `search_news`：检索经合规授权的财经新闻摘要列表
4. `get_news_detail`：获取单条合规财经资讯详情及关联证券标签

全部返回均封装为 `BrokerDataEnvelope` 并经过 `hca_size_guard` 16KB 三档大小闸（防超长公告截断成烂 JSON）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _shared import dump_guarded_envelope, get_router, run_stdio_mcp_server  # noqa: E402

SERVER_NAME = "broker_disclosure"
SERVER_VERSION = "3.1.0"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_announcements",
        "description": "按股票代码、关键词、公告分类检索全市场公告列表（超长列表自动经 16KB 大小闸裁剪为合法 JSON）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "标题或摘要搜索关键词（可选）"},
                "symbol": {"type": "string", "description": "证券代码，如 600036.SH（可选）"},
                "category": {"type": "string", "description": "公告分类，如 定期报告 / 回购与增减持 / 重大合同（可选）"},
                "start_date": {"type": "string", "description": "起始日期 YYYY-MM-DD（可选）"},
                "end_date": {"type": "string", "description": "结束日期 YYYY-MM-DD（可选）"},
                "as_of": {"type": "string", "description": "Point-in-Time 历史截止时间（ISO-8601，可选）"},
            },
        },
    },
    {
        "name": "get_announcement",
        "description": "获取单篇公告详情及按页码定位的 PDF 正文与表格（支持 page_range 如 '1-3' 或 '2'）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "announcement_id": {"type": "string", "description": "公告唯一 ID，如 ANN-20261028-600036-Q3"},
                "page_range": {"type": "string", "description": "PDF 页码范围，如 '1-3' 或 '2'（可选，默认返回全部页）"},
            },
            "required": ["announcement_id"],
        },
    },
    {
        "name": "search_news",
        "description": "检索经合规授权的财经新闻摘要列表",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "新闻搜索关键词（可选）"},
                "symbol": {"type": "string", "description": "关联证券代码，如 600036.SH（可选）"},
                "start_date": {"type": "string", "description": "起始日期 YYYY-MM-DD（可选）"},
                "end_date": {"type": "string", "description": "结束日期 YYYY-MM-DD（可选）"},
            },
        },
    },
    {
        "name": "get_news_detail",
        "description": "获取单条合规授权财经新闻的正文详情、来源与关联股票列表",
        "inputSchema": {
            "type": "object",
            "properties": {
                "news_id": {"type": "string", "description": "新闻唯一 ID，如 NEWS-20261113-001"},
            },
            "required": ["news_id"],
        },
    },
]


def call_tool_sync(name: str, arguments: dict[str, Any], *, max_bytes: int | None = None) -> str:
    """同步调用 `broker_disclosure` 工具并返回经 16KB 大小闸保护的 JSON 字符串。"""
    router = get_router()
    if name == "search_announcements":
        env = router.search_announcements(
            query=arguments.get("query"),
            symbol=arguments.get("symbol"),
            category=arguments.get("category"),
            start_date=arguments.get("start_date"),
            end_date=arguments.get("end_date"),
            as_of=arguments.get("as_of"),
        )
    elif name == "get_announcement":
        env = router.get_announcement(
            announcement_id=str(arguments["announcement_id"]),
            page_range=arguments.get("page_range"),
        )
    elif name == "search_news":
        env = router.search_news(
            query=arguments.get("query"),
            symbol=arguments.get("symbol"),
            start_date=arguments.get("start_date"),
            end_date=arguments.get("end_date"),
        )
    elif name == "get_news_detail":
        env = router.get_news_detail(
            news_id=str(arguments["news_id"]),
        )
    else:
        raise ValueError(f"未知工具名: {name}")

    return dump_guarded_envelope(env, tool_name=f"{SERVER_NAME}.{name}", max_bytes=max_bytes)


if __name__ == "__main__":
    run_stdio_mcp_server(SERVER_NAME, SERVER_VERSION, TOOLS, call_tool_sync)
