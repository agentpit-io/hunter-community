"""MCP 工具 · get_quote/get_kline/get_pe_history/get_fx/get_ah_premium/... 冒烟测试

只测注册与错误分支（NOT_IMPLEMENTED / BAD_ARGS），不打真数据源。
"""
import pytest


@pytest.fixture(autouse=True)
def _load_market_tools():
    from app.services.agent.tool_registry import ToolRegistry
    # 若还没注册，import 触发
    try:
        from app.services.mcp import market_tools  # noqa: F401
    except Exception:
        pytest.skip("market_tools 依赖 finance_data_client (py3.10+ 语法)")
    yield
    # 不清理 — 让其他测试共享注册


def test_all_mcp_tools_registered():
    from app.services.agent.tool_registry import ToolRegistry
    known = set(ToolRegistry.known_tools())
    for t in ["get_quote", "get_kline", "get_pe_history",
               "get_fx", "get_ah_premium",
               "get_analyst_target", "get_earnings_consensus"]:
        assert t in known, f"{t} 未注册"


@pytest.mark.asyncio
async def test_get_fx_hkdcny_returns_stub_rate():
    from app.services.agent.tool_registry import ToolRegistry, new_tool_call
    tc = new_tool_call("get_fx", {"pair": "HKDCNY"})
    result = await ToolRegistry.dispatch(tc, bus=None)
    assert result.status == "ok"
    assert 0.8 < result.summary["rate"] < 1.0


@pytest.mark.asyncio
async def test_get_fx_unknown_pair_returns_error():
    from app.services.agent.tool_registry import ToolRegistry, new_tool_call
    tc = new_tool_call("get_fx", {"pair": "GBPCNY"})
    result = await ToolRegistry.dispatch(tc, bus=None)
    assert result.status == "error"
    assert result.error["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_get_analyst_target_stub():
    from app.services.agent.tool_registry import ToolRegistry, new_tool_call
    tc = new_tool_call("get_analyst_target", {"code": "600519"})
    result = await ToolRegistry.dispatch(tc, bus=None)
    assert result.status == "error"
    assert result.error["code"] == "NOT_IMPLEMENTED"


@pytest.mark.asyncio
async def test_get_ah_premium_missing_args():
    from app.services.agent.tool_registry import ToolRegistry, new_tool_call
    tc = new_tool_call("get_ah_premium", {"code_a": "601398"})
    result = await ToolRegistry.dispatch(tc, bus=None)
    assert result.status == "error"
    assert result.error["code"] == "BAD_ARGS"


@pytest.mark.asyncio
async def test_get_kline_missing_code():
    from app.services.agent.tool_registry import ToolRegistry, new_tool_call
    tc = new_tool_call("get_kline", {"days": 60})
    result = await ToolRegistry.dispatch(tc, bus=None)
    assert result.status == "error"
    assert result.error["code"] == "BAD_ARGS"
