"""单元测试：券商统一数据信封（BrokerDataEnvelope）与 8 大业务域抽象 Provider 协议。

覆盖测试方案第一关（TC-MCP-01 ～ TC-MCP-04）：
1. TC-MCP-01：三个时间戳（`as_of`、`published_at`、`retrieved_at`）严格分离，严禁拿抓取时间冒充行情时间。
2. TC-MCP-02：缺失字段与停牌标的处理（保留 `None`/`null`，记入 `missing_fields` 与 `warnings`，严禁填 `0`）。
3. TC-MCP-03：Point-in-Time（PIT）历史当时点防偷看过滤（剔除 `published_at > pit_as_of` 的未来财报/公告）。
4. TC-MCP-04：主备数据源降级标记（`is_fallback=True` 必须带原因并写入 `warnings`，质量状态降为 `degraded`）。
5. 8 大业务域抽象 Provider 接口签名完整性与禁实盘下单红线校验。
"""
from __future__ import annotations

import inspect
import pytest

from app.providers.broker import (
    AccountReadonlyProvider,
    BrokerDataEnvelope,
    DisclosureProvider,
    EducationProvider,
    FundamentalProvider,
    FundsProvider,
    MarketDataProvider,
    ResearchProvider,
    SecurityMasterProvider,
)


def test_tc_mcp_01_three_timestamps_strictly_separated() -> None:
    """TC-MCP-01：收盘后查询 600000.SH，行情时间 `as_of`、披露时间 `published_at` 与抓取时间 `retrieved_at` 严格分离。"""
    env = BrokerDataEnvelope.build(
        provider="galaxy_broker",
        market="CN_A",
        symbol="600000.SH",
        currency="CNY",
        unit="元",
        as_of="2026-11-13T15:00:00+08:00",
        published_at="2026-11-13T16:10:00+08:00",
        retrieved_at="2026-11-13T16:10:03+08:00",
        adjustment="前复权",
        forbid_same_as_retrieved=True,
        data={
            "close": 10.52,
            "volume": 48291000,
            "turnover": 508021320.0,
        },
    )

    assert env.ok is True
    assert env.meta.as_of == "2026-11-13T15:00:00+08:00"
    assert env.meta.published_at == "2026-11-13T16:10:00+08:00"
    assert env.meta.retrieved_at == "2026-11-13T16:10:03+08:00"
    assert env.meta.as_of != env.meta.retrieved_at
    assert env.meta.quality_status == "ok"
    assert env.meta.fields == ["close", "volume", "turnover"]
    assert env.meta.missing_fields == []


def test_tc_mcp_01_forbid_spoofing_as_of_with_retrieved_at() -> None:
    """TC-MCP-01 反向拦截：把接口抓取时间 `retrieved_at` 直接冒充行情时间 `as_of` 时必须抛错拦截。"""
    with pytest.raises(ValueError, match="禁止将接口抓取时间"):
        BrokerDataEnvelope.build(
            provider="galaxy_broker",
            market="CN_A",
            symbol="600000.SH",
            as_of="2026-11-13T16:10:03+08:00",
            retrieved_at="2026-11-13T16:10:03+08:00",
            forbid_same_as_retrieved=True,
            data={"close": 10.52},
        )

    # 行情时间晚于抓取时间（未来时间戳）也必须抛错拦截
    with pytest.raises(ValueError, match="晚于抓取时间"):
        BrokerDataEnvelope.build(
            provider="galaxy_broker",
            market="CN_A",
            symbol="600000.SH",
            as_of="2026-11-13T18:00:00+08:00",
            retrieved_at="2026-11-13T16:10:03+08:00",
            data={"close": 10.52},
        )


def test_tc_mcp_02_missing_fields_keep_none_never_zero() -> None:
    """TC-MCP-02：停牌或未披露字段必须保留 None（JSON null），写入 missing_fields 与 warnings，严禁填 0。"""
    payload = {
        "symbol": "600036.SH",
        "status": "停牌",
        "last_price": None,
        "pct_chg": None,
        "prev_close": 35.20,
    }
    env = BrokerDataEnvelope.build(
        provider="galaxy_broker",
        market="CN_A",
        symbol="600036.SH",
        as_of="2026-11-13T15:00:00+08:00",
        retrieved_at="2026-11-13T15:05:00+08:00",
        expected_fields=["symbol", "status", "last_price", "pct_chg", "prev_close", "pe_ttm"],
        missing_reason_map={
            "last_price": "全天停牌无成交，保留 null（严禁填 0.00）",
            "pct_chg": "全天停牌无涨跌幅，保留 null（严禁填 0%）",
            "pe_ttm": "最新财报尚未披露，保留 null",
        },
        data=payload,
    )

    assert env.data["last_price"] is None
    assert env.data["pct_chg"] is None
    assert env.data["pe_ttm"] is None
    assert env.data["last_price"] != 0
    assert env.data["pct_chg"] != 0
    assert env.meta.missing_fields == ["last_price", "pct_chg", "pe_ttm"]
    assert env.meta.fields == ["symbol", "status", "prev_close"]
    assert env.meta.quality_status == "partial"
    assert any("全天停牌无成交" in w for w in env.meta.warnings)
    assert any("pe_ttm" in w for w in env.meta.warnings)


def test_tc_mcp_03_point_in_time_filtering_prevents_lookahead() -> None:
    """TC-MCP-03：传入历史时点 `as_of=2026-03-15` 时，4 月才披露的 2026Q1 财报必须被剔除，防止回测偷看未来。"""
    statements = [
        {
            "report_period": "2025Q3",
            "published_at": "2025-10-28T18:00:00+08:00",
            "revenue": 1250000000.0,
            "net_profit": 180000000.0,
        },
        {
            "report_period": "2025A",
            "published_at": "2026-03-10T19:30:00+08:00",
            "revenue": 5100000000.0,
            "net_profit": 740000000.0,
        },
        {
            "report_period": "2026Q1",
            "published_at": "2026-04-25T17:00:00+08:00",
            "revenue": 1400000000.0,
            "net_profit": 210000000.0,
        },
    ]

    raw_env = BrokerDataEnvelope.build(
        provider="galaxy_broker",
        market="CN_A",
        symbol="600000.SH",
        as_of="2026-11-13T15:00:00+08:00",
        retrieved_at="2026-11-13T16:00:00+08:00",
        data=statements,
    )

    pit_env = raw_env.filter_point_in_time("2026-03-15T15:00:00+08:00")
    kept_periods = [row["report_period"] for row in pit_env.data]

    assert kept_periods == ["2025Q3", "2025A"]
    assert "2026Q1" not in kept_periods
    assert pit_env.meta.as_of == "2026-03-15T15:00:00+08:00"
    assert pit_env.meta.quality_status == "partial"
    assert any("Point-in-Time 防偷看过滤" in w for w in pit_env.meta.warnings)


def test_tc_mcp_04_fallback_source_requires_reason_and_marks_degraded() -> None:
    """TC-MCP-04：主源超时降级至备用公开源时，必须显式标记 `is_fallback=True` 与原因，且严禁无原因静默切换。"""
    with pytest.raises(ValueError, match="必须填写 `fallback_reason`"):
        BrokerDataEnvelope.build(
            provider="akshare_fallback",
            market="CN_A",
            symbol="600000.SH",
            as_of="2026-11-13T15:00:00+08:00",
            retrieved_at="2026-11-13T15:01:00+08:00",
            is_fallback=True,
            data={"close": 10.52},
        )

    fallback_env = BrokerDataEnvelope.build(
        provider="akshare_fallback",
        market="CN_A",
        symbol="600000.SH",
        as_of="2026-11-13T15:00:00+08:00",
        retrieved_at="2026-11-13T15:01:00+08:00",
        is_fallback=True,
        fallback_reason="合作券商主源 HTTP 504 超时，经策略允许切换至公开源兜底（未跨源取均值）",
        data={"close": 10.52},
    )

    assert fallback_env.meta.is_fallback is True
    assert fallback_env.meta.quality_status == "degraded"
    assert any("数据源已降级至备用源 `akshare_fallback`" in w for w in fallback_env.meta.warnings)


def test_provider_abstract_interfaces_and_no_live_trading_methods() -> None:
    """验证 8 大业务域 Provider 抽象接口齐全，且严禁包含任何实盘下单/撤单/转账方法。"""
    providers = [
        MarketDataProvider,
        SecurityMasterProvider,
        FundamentalProvider,
        DisclosureProvider,
        FundsProvider,
        ResearchProvider,
        EducationProvider,
        AccountReadonlyProvider,
    ]
    forbidden_keywords = {"place_order", "submit_order", "cancel_order", "modify_order", "transfer_cash", "buy", "sell"}

    for cls in providers:
        assert inspect.isabstract(cls), f"{cls.__name__} 应当是抽象基类"
        method_names = {
            name
            for name, member in inspect.getmembers(cls, predicate=inspect.isfunction)
            if not name.startswith("_")
        }
        assert method_names.isdisjoint(forbidden_keywords), f"{cls.__name__} 含有禁止的交易方法"

    # 验证各 Provider 的核心抽象方法签名存在
    assert {
        "get_quote",
        "get_kline",
        "get_orderbook",
        "get_market_status",
        "get_market_overview",
        "get_market_calendar",
    }.issubset(MarketDataProvider.__abstractmethods__)

    assert {
        "search_security",
        "get_security_profile",
        "get_security_status",
        "get_index_constituents",
    }.issubset(SecurityMasterProvider.__abstractmethods__)

    assert {
        "get_financial_statements",
        "get_financial_ratios",
        "get_valuation",
        "get_shareholders",
        "get_corporate_actions",
    }.issubset(FundamentalProvider.__abstractmethods__)

    assert {
        "search_announcements",
        "get_announcement",
        "search_news",
        "get_news_detail",
    }.issubset(DisclosureProvider.__abstractmethods__)

    assert {
        "get_etf_quote",
        "get_fund_nav",
        "get_etf_holdings",
        "get_fund_fee",
        "get_bond_data",
    }.issubset(FundsProvider.__abstractmethods__)

    assert {
        "get_research_reports",
        "get_consensus_estimates",
        "get_sector_data",
    }.issubset(ResearchProvider.__abstractmethods__)

    assert {
        "search_investor_education",
        "get_product_guide",
        "get_risk_disclosure",
    }.issubset(EducationProvider.__abstractmethods__)

    assert {
        "get_accounts",
        "get_account_summary",
        "get_positions",
        "get_trades",
    }.issubset(AccountReadonlyProvider.__abstractmethods__)
