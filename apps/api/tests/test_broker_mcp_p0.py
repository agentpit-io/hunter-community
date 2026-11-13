"""单元测试：4 个 P0 级只读券商 MCP 服务与多源主备路由器（`test_broker_mcp_p0.py`）。

全面覆盖测试方案第一关（TC-MCP-01 ～ TC-MCP-05）及 4 个 P0 MCP 的全部 19 个工具：
- `broker_security`：search_security, get_security_profile, get_security_status, get_index_constituents
- `broker_market`：get_quote, get_kline, get_orderbook, get_market_status, get_market_overview, get_market_calendar
- `broker_disclosure`：search_announcements, get_announcement, search_news, get_news_detail
- `broker_fundamental`：get_financial_statements, get_financial_ratios, get_valuation, get_shareholders, get_corporate_actions
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MCP_SERVERS_DIR = REPO_ROOT / "distro" / "mcp-servers"
if str(MCP_SERVERS_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_SERVERS_DIR))

from app.providers.broker import (  # noqa: E402
    BrokerDataRouter,
    CrossSourceAveragingForbiddenError,
    GalaxyBrokerAdapter,
    PublicFallbackAdapter,
    assert_no_cross_source_averaging,
)
from broker_disclosure import server as disclosure_mcp  # noqa: E402
from broker_fundamental import server as fundamental_mcp  # noqa: E402
from broker_market import server as market_mcp  # noqa: E402
from broker_security import server as security_mcp  # noqa: E402


def test_p0_mcp_tools_registration_consistency() -> None:
    """校验 4 个 P0 券商 MCP 服务在源码、.mcp.json 与 distro/mcp-tools.json 中的工具清单 100% 对齐。"""
    expected = {
        "broker_security": [
            "search_security",
            "get_security_profile",
            "get_security_status",
            "get_index_constituents",
        ],
        "broker_market": [
            "get_quote",
            "get_kline",
            "get_orderbook",
            "get_market_status",
            "get_market_overview",
            "get_market_calendar",
        ],
        "broker_disclosure": [
            "search_announcements",
            "get_announcement",
            "search_news",
            "get_news_detail",
        ],
        "broker_fundamental": [
            "get_financial_statements",
            "get_financial_ratios",
            "get_valuation",
            "get_shareholders",
            "get_corporate_actions",
        ],
    }

    assert [t["name"] for t in security_mcp.TOOLS] == expected["broker_security"]
    assert [t["name"] for t in market_mcp.TOOLS] == expected["broker_market"]
    assert [t["name"] for t in disclosure_mcp.TOOLS] == expected["broker_disclosure"]
    assert [t["name"] for t in fundamental_mcp.TOOLS] == expected["broker_fundamental"]

    mcp_tools_json = json.loads((REPO_ROOT / "distro" / "mcp-tools.json").read_text(encoding="utf-8"))
    for srv_name, tools in expected.items():
        assert mcp_tools_json["servers"].get(srv_name) == tools


def test_tc_mcp_01_and_02_quote_timestamps_and_halted_nulls() -> None:
    """TC-MCP-01 & TC-MCP-02：校验报价快照三时间戳分离，以及停牌股票缺失字段返回 null 绝不填 0。"""
    # 1. 正常股票 600000.SH
    raw_normal = market_mcp.call_tool_sync("get_quote", {"symbol": "600000.SH", "market": "CN_A"})
    obj_normal = json.loads(raw_normal)
    assert obj_normal["ok"] is True
    assert obj_normal["meta"]["symbol"] == "600000.SH"
    assert obj_normal["meta"]["as_of"] == "2026-11-13T15:00:00+08:00"
    assert obj_normal["meta"]["retrieved_at"] != obj_normal["meta"]["as_of"]
    assert obj_normal["data"]["last_price"] == 10.48
    assert obj_normal["data"]["pct_chg"] == 1.26

    # 2. 停牌股票 600898.SH —— 严禁出现 0.00 或 0%
    raw_halted = market_mcp.call_tool_sync("get_quote", {"symbol": "600898.SH", "market": "CN_A"})
    obj_halted = json.loads(raw_halted)
    assert obj_halted["ok"] is True
    assert obj_halted["data"]["status"] == "停牌"
    assert obj_halted["data"]["last_price"] is None
    assert obj_halted["data"]["pct_chg"] is None
    assert obj_halted["data"]["volume"] is None
    assert "last_price" in obj_halted["meta"]["missing_fields"]
    assert "pct_chg" in obj_halted["meta"]["missing_fields"]
    assert any("停牌" in w for w in obj_halted["meta"]["warnings"])


def test_tc_mcp_03_point_in_time_financial_statements() -> None:
    """TC-MCP-03：调用 broker_fundamental.get_financial_statements 传入 as_of='2026-03-15'，绝不返回 4 月才披露的一季报。"""
    raw = fundamental_mcp.call_tool_sync(
        "get_financial_statements",
        {"symbol": "600000.SH", "as_of": "2026-03-15T15:00:00+08:00"},
    )
    obj = json.loads(raw)
    assert obj["ok"] is True
    periods = [row["report_period"] for row in obj["data"]]
    assert "2025Q3" in periods
    assert "2025A" in periods
    assert "2026Q1" not in periods
    assert "2026Q3" not in periods
    for row in obj["data"]:
        assert row["published_at"] <= "2026-03-15T15:00:00+08:00"
    assert any("Point-in-Time 防偷看过滤" in w for w in obj["meta"]["warnings"])


def test_tc_mcp_04_router_primary_timeout_fallback_and_forbid_averaging() -> None:
    """TC-MCP-04：模拟合作券商主源 HTTP 504 超时自动切换备用源，记录切换原因，且严禁跨源取均值。"""
    # 1. 主源命中快照时，直接返回 galaxy_broker 且 is_fallback=False
    primary_with_data = GalaxyBrokerAdapter(
        snapshot_store={
            "quotes": {
                "600000.SH": {
                    "symbol": "600000.SH",
                    "name": "浦发银行",
                    "last_price": 10.50,
                    "prev_close": 10.35,
                    "open": 10.38,
                    "high": 10.56,
                    "low": 10.32,
                    "pct_chg": 1.45,
                    "volume": 50000000,
                    "turnover": 525000000.0,
                    "status": "已收盘",
                    "_as_of": "2026-11-13T15:00:00+08:00",
                }
            }
        }
    )
    router_primary = BrokerDataRouter(primary=primary_with_data, fallback=PublicFallbackAdapter())
    env_p = router_primary.get_quote("600000.SH")
    assert env_p.meta.provider == "galaxy_broker"
    assert env_p.meta.is_fallback is False
    assert env_p.data["last_price"] == 10.50

    # 2. 模拟主源 HTTP 504 超时，自动降级至备用源 akshare_public_fallback（绝不取 10.50 与 10.48 的均值 10.49！）
    primary_timeout = GalaxyBrokerAdapter(simulate_timeout=True)
    router_fb = BrokerDataRouter(primary=primary_timeout, fallback=PublicFallbackAdapter(), allow_fallback=True)
    env_fb = router_fb.get_quote("600000.SH")
    assert env_fb.ok is True
    assert env_fb.meta.provider == "akshare_public_fallback"
    assert env_fb.meta.is_fallback is True
    assert env_fb.meta.quality_status == "degraded"
    assert "HTTP 504" in (env_fb.meta.fallback_reason or "")
    assert env_fb.data["last_price"] == 10.48  # 纯备用源真实值，未与主源取平均
    assert any("数据源已降级至备用源" in w for w in env_fb.meta.warnings)

    # 3. 禁用降级（allow_fallback=False）且主源超时时，诚实报错停止
    router_no_fb = BrokerDataRouter(primary=primary_timeout, fallback=PublicFallbackAdapter(), allow_fallback=False)
    env_err = router_no_fb.get_quote("600000.SH")
    assert env_err.ok is False
    assert env_err.data is None
    assert env_err.errors[0].code == "PROVIDER_TIMEOUT_504"

    # 4. 严禁跨源取均值硬拦截
    with pytest.raises(CrossSourceAveragingForbiddenError, match="严禁对主备数据源的价格或财务指标取平均值"):
        assert_no_cross_source_averaging("average")
    with pytest.raises(CrossSourceAveragingForbiddenError):
        BrokerDataRouter.combine_sources(env_p, env_fb)


def test_tc_mcp_05_size_guard_16kb_truncation_protection() -> None:
    """TC-MCP-05：拉取超长公告列表（原始 > 80KB）时，经 hca_size_guard 裁剪后体积 <= 16KB 且 JSON 100% 合法。"""
    raw_out = disclosure_mcp.call_tool_sync(
        "search_announcements",
        {"symbol": "600036.SH", "query": "oversized_annual_dump"},
    )
    byte_len = len(raw_out.encode("utf-8"))
    assert byte_len <= 16 * 1024, f"返回体积 {byte_len} 超过 16KB 阈值！"
    parsed = json.loads(raw_out)
    assert isinstance(parsed, dict)
    assert "_hca_size_guard" in parsed
    assert parsed["_hca_size_guard"]["total_items"] == 219
    assert parsed["_hca_size_guard"]["kept_items"] < 219
    assert parsed["_hca_size_guard"]["dropped_items"] > 0


def test_all_19_p0_mcp_tools_execution() -> None:
    """逐一调用 4 个 P0 MCP 服务的全部 19 个工具，验证全部返回合法的 BrokerDataEnvelope。"""
    # 1. broker_security (4 tools)
    for tool_name, args in [
        ("search_security", {"query": "招商"}),
        ("get_security_profile", {"symbol": "600036.SH"}),
        ("get_security_status", {"symbol": "600036.SH"}),
        ("get_index_constituents", {"index_code": "000300.SH", "as_of": "2026-11-13"}),
    ]:
        res = json.loads(security_mcp.call_tool_sync(tool_name, args))
        assert res["ok"] is True, f"broker_security.{tool_name} 失败"
        assert "meta" in res and "retrieved_at" in res["meta"]

    # 2. broker_market (6 tools)
    for tool_name, args in [
        ("get_quote", {"symbol": "600036.SH"}),
        ("get_kline", {"symbol": "600036.SH", "adjustment": "前复权", "start": "2026-09-01"}),
        ("get_orderbook", {"symbol": "600036.SH", "levels": 5}),
        ("get_market_status", {"market": "CN_A"}),
        ("get_market_overview", {"market": "CN_A"}),
        ("get_market_calendar", {"market": "CN_A", "start_date": "2026-11-01", "end_date": "2026-11-15"}),
    ]:
        res = json.loads(market_mcp.call_tool_sync(tool_name, args))
        assert res["ok"] is True, f"broker_market.{tool_name} 失败"

    # 3. broker_disclosure (4 tools)
    for tool_name, args in [
        ("search_announcements", {"symbol": "600036.SH"}),
        ("get_announcement", {"announcement_id": "ANN-20261028-600036-Q3", "page_range": "1-2"}),
        ("search_news", {"symbol": "600036.SH"}),
        ("get_news_detail", {"news_id": "NEWS-20261113-001"}),
    ]:
        res = json.loads(disclosure_mcp.call_tool_sync(tool_name, args))
        assert res["ok"] is True, f"broker_disclosure.{tool_name} 失败"
    # 验证公告页码定位与溯源标签
    ann_detail = json.loads(
        disclosure_mcp.call_tool_sync("get_announcement", {"announcement_id": "ANN-20261028-600036-Q3", "page_range": "2"})
    )
    assert len(ann_detail["data"]["pages"]) == 1
    assert ann_detail["data"]["pages"][0]["page_no"] == 2
    assert "第2页" in ann_detail["data"]["pages"][0]["citation_tag"]

    # 4. broker_fundamental (5 tools)
    for tool_name, args in [
        ("get_financial_statements", {"symbol": "600036.SH", "report_period": "2026Q3"}),
        ("get_financial_ratios", {"symbol": "600036.SH", "report_period": "2026Q3"}),
        ("get_valuation", {"symbol": "600036.SH"}),
        ("get_shareholders", {"symbol": "600036.SH"}),
        ("get_corporate_actions", {"symbol": "600036.SH"}),
    ]:
        res = json.loads(fundamental_mcp.call_tool_sync(tool_name, args))
        assert res["ok"] is True, f"broker_fundamental.{tool_name} 失败"


def test_stdio_jsonrpc_protocol_for_all_4_servers() -> None:
    """通过真实子进程启动 4 个 P0 MCP Server，测试 JSON-RPC initialize + tools/list + tools/call。"""
    servers = [
        ("broker_security", MCP_SERVERS_DIR / "broker_security" / "server.py", "get_security_profile", {"symbol": "600036.SH"}),
        ("broker_market", MCP_SERVERS_DIR / "broker_market" / "server.py", "get_quote", {"symbol": "600036.SH"}),
        ("broker_disclosure", MCP_SERVERS_DIR / "broker_disclosure" / "server.py", "get_announcement", {"announcement_id": "ANN-20261028-600036-Q3", "page_range": "1"}),
        ("broker_fundamental", MCP_SERVERS_DIR / "broker_fundamental" / "server.py", "get_valuation", {"symbol": "600036.SH"}),
    ]
    for srv_name, script_path, sample_tool, sample_args in servers:
        requests_str = "\n".join(
            [
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}),
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
                json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": sample_tool, "arguments": sample_args}}),
            ]
        ) + "\n"
        proc = subprocess.run(
            [sys.executable, str(script_path)],
            input=requests_str,
            text=True,
            capture_output=True,
            timeout=15,
            check=True,
        )
        lines = [json.loads(line) for line in proc.stdout.strip().splitlines() if line.strip()]
        assert len(lines) == 3, f"{srv_name} 应返回 3 条 JSON-RPC 响应"
        assert lines[0]["result"]["serverInfo"]["name"] == srv_name
        assert len(lines[1]["result"]["tools"]) >= 4
        call_res = lines[2]["result"]
        assert call_res["isError"] is False
        envelope = json.loads(call_res["content"][0]["text"])
        assert envelope["ok"] is True
