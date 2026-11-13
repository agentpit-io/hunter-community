"""阶段 M3 专项单元测试：3 个 P1 券商 MCP、ETF 定投计算器、7 个 P1 金融 SKILL 与量化/组合复盘工作台。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))
sys.path.insert(0, str(REPO_ROOT / "distro" / "mcp-servers"))

from app.routers.daily_workflows import get_workflows_and_skills_catalog  # noqa: E402
from app.services.etf_dca import (  # noqa: E402
    MANDATORY_DCA_DISCLAIMER,
    calculate_dca_vs_lump_sum,
    calculate_etf_dca_backtest,
    calculate_etf_dca_full_plan,
    calculate_target_reverse_plan,
)
from app.services.workflows import (  # noqa: E402
    build_natural_language_screener,
    run_backtest_quality_audit,
    run_factor_research_analysis,
    run_investment_thesis_tracker,
    run_news_signal_digest,
    run_portfolio_review_workbench_bundle,
    run_quant_workbench_bundle,
    run_trading_journal_review,
)
from broker_education import TOOLS as EDU_TOOLS, call_tool_sync as call_edu  # noqa: E402
from broker_funds import TOOLS as FUNDS_TOOLS, call_tool_sync as call_funds  # noqa: E402
from broker_research import TOOLS as RES_TOOLS, call_tool_sync as call_res  # noqa: E402


# ── 1. P1 券商 MCP 服务测试（broker_funds / broker_research / broker_education） ──


def test_p1_mcp_tools_list_contract() -> None:
    assert [t["name"] for t in FUNDS_TOOLS] == [
        "get_etf_quote",
        "get_fund_nav",
        "get_etf_holdings",
        "get_fund_fee",
        "get_bond_data",
    ]
    assert [t["name"] for t in RES_TOOLS] == [
        "get_research_reports",
        "get_consensus_estimates",
        "get_sector_data",
    ]
    assert [t["name"] for t in EDU_TOOLS] == [
        "search_investor_education",
        "get_product_guide",
        "get_risk_disclosure",
    ]


def test_broker_funds_tools_and_missing_null_guard() -> None:
    # 1) 正常 ETF 报价
    raw_quote = call_funds("get_etf_quote", {"symbol": "510300.SH"})
    env_quote = json.loads(raw_quote)
    assert env_quote["ok"] is True
    assert env_quote["data"]["symbol"] == "510300.SH"
    assert env_quote["data"]["price"] > 0
    assert env_quote["data"]["iopv"] > 0
    assert env_quote["meta"]["as_of"] != env_quote["meta"]["retrieved_at"]

    # 2) 未收录 ETF 必须返回 null，严禁填 0
    raw_unknown = call_funds("get_etf_quote", {"symbol": "599999.SH"})
    env_unknown = json.loads(raw_unknown)
    assert env_unknown["data"]["price"] is None
    assert "price" in env_unknown["meta"]["missing_fields"]

    # 3) 基金历史净值（正常范围 + 超长范围触发 16KB 大小闸）
    raw_nav_norm = call_funds("get_fund_nav", {"symbol": "510300.SH", "start_date": "2026-09-01", "end_date": "2026-11-13"})
    env_nav_norm = json.loads(raw_nav_norm)
    assert env_nav_norm["ok"] is True
    assert len(env_nav_norm["data"]["nav_series"]) > 20

    raw_nav_large = call_funds("get_fund_nav", {"symbol": "510300.SH", "start_date": "2020-01-01", "end_date": "2026-11-13"}, max_bytes=15000)
    assert len(raw_nav_large.encode("utf-8")) <= 16384
    env_nav_large = json.loads(raw_nav_large)
    assert "_hca_size_guard" in env_nav_large or env_nav_large.get("ok") is True

    # 4) ETF 申赎成分、基金费率、可转债数据
    env_hold = json.loads(call_funds("get_etf_holdings", {"symbol": "510300.SH"}))
    assert env_hold["ok"] is True
    assert len(env_hold["data"]["holdings"]) >= 5

    env_fee = json.loads(call_funds("get_fund_fee", {"symbol": "510300.SH"}))
    assert env_fee["ok"] is True
    assert env_fee["data"]["management_fee_annual_pct"] == 0.15

    env_bond = json.loads(call_funds("get_bond_data", {"symbol": "110059.SH"}))
    assert env_bond["ok"] is True
    assert env_bond["data"]["name"] == "浦发转债"
    assert env_bond["data"]["putback_trigger_price"] is None


def test_broker_research_and_education_tools() -> None:
    # 研报 PIT 时点过滤校验
    env_rep = json.loads(call_res("get_research_reports", {"symbol": "600036.SH", "as_of": "2026-10-30"}))
    assert env_rep["ok"] is True
    assert len(env_rep["data"]) >= 1

    env_con = json.loads(call_res("get_consensus_estimates", {"symbol": "600036.SH"}))
    assert env_con["ok"] is True
    assert len(env_con["data"]["estimates"]) >= 2

    env_sec = json.loads(call_res("get_sector_data", {"sector_code": "SW_BANK"}))
    assert env_sec["ok"] is True
    assert env_sec["data"]["pe_ttm_median"] > 0

    # 投教与风险揭示
    env_edu = json.loads(call_edu("search_investor_education", {"query": "定投"}))
    assert env_edu["ok"] is True
    assert len(env_edu["data"]) >= 1

    env_guide = json.loads(call_edu("get_product_guide", {"product_type": "ETF"}))
    assert env_guide["ok"] is True
    assert env_guide["data"]["product_type"] == "ETF"

    env_risk = json.loads(call_edu("get_risk_disclosure", {"risk_type": "ETF_DCA"}))
    assert env_risk["ok"] is True
    assert "历史表现不代表未来收益" in env_risk["data"]["mandatory_warning"]


# ── 2. ETF 定投计算器真值测试（TC-CALC-03） ────────────────────────────────────


def test_tc_calc_03_etf_dca_forward_reverse_and_lump_sum() -> None:
    # 输入沪深300 ETF (510300.SH)，每月定投 2000 元，期限 3 年，申购费率 0.1%
    bt = calculate_etf_dca_backtest(
        symbol="510300.SH",
        periodic_amount=2000.0,
        frequency="monthly",
        invest_day=15,
        start_date="2023-11-01",
        end_date="2026-11-13",
        subscription_fee_pct=0.10,
        dividend_reinvest=True,
    )
    assert bt["ok"] is True
    summary = bt["summary"]
    assert summary["total_periods"] >= 36
    assert summary["total_invested"] == pytest.approx(summary["total_periods"] * 2000.0, rel=1e-6)
    assert summary["total_shares"] > 0
    assert summary["final_market_value"] > 0
    assert summary["average_holding_cost"] == pytest.approx(
        summary["total_invested"] / summary["total_shares"], abs=1e-3
    )
    assert summary["irr_annualized_pct"] is not None
    assert summary["max_drawdown_pct"] >= 0
    assert "基于历史净值与假设参数测算，历史表现不代表未来收益" in bt["disclaimer"]

    # 目标金额反推
    rev = calculate_target_reverse_plan(
        target_amount=100000.0,
        years=3.0,
        frequency="monthly",
        assumed_annual_return_pct=6.0,
        subscription_fee_pct=0.10,
    )
    assert rev["ok"] is True
    assert rev["primary_plan"]["total_periods"] == 36
    assert 2000.0 < rev["primary_plan"]["required_periodic_amount"] < 2800.0
    assert len(rev["scenario_matrix"]) == 4
    assert MANDATORY_DCA_DISCLAIMER in rev["disclaimer"]

    # 定投 vs 一次性买入对比
    cmp_res = calculate_dca_vs_lump_sum(
        symbol="510300.SH",
        periodic_amount=2000.0,
        frequency="monthly",
        start_date="2023-11-01",
        end_date="2026-11-13",
        subscription_fee_pct=0.10,
    )
    assert cmp_res["ok"] is True
    assert cmp_res["lump_sum_summary"]["total_invested"] == summary["total_invested"]
    assert "历史表现不代表未来收益" in cmp_res["disclaimer"]

    full_plan = calculate_etf_dca_full_plan(symbol="510300.SH", periodic_amount=2000.0)
    assert full_plan["ok"] is True
    assert full_plan["etf_quote"]["symbol"] == "510300.SH"


# ── 3. P1 七大进阶金融 SKILL 与两大新增工作台测试（TC-SK-09 等） ────────────────


def test_tc_sk_09_factor_research_and_backtest_quality_auditor() -> None:
    fac = run_factor_research_analysis(factor_id="low_pe_high_div", universe="000300.SH")
    assert fac["ok"] is True
    assert fac["metrics"]["ic_mean"] > 0.03
    assert fac["metrics"]["rank_ic_mean"] > 0.05
    assert fac["metrics"]["ir"] > 0.5
    assert fac["metrics"]["monotonicity_score"] == 1.0
    assert len(fac["layer_backtest"]) == 5

    # 标准合规回测审计（满分 100）
    audit_pass = run_backtest_quality_audit(
        use_pit_fundamentals=True,
        include_delisted_stocks=True,
        adjustment_type="前复权",
        commission_bps=3.0,
        stamp_duty_bps=5.0,
        slippage_bps=10.0,
        filter_limit_up_down=True,
        filter_halted_stocks=True,
    )
    assert audit_pass["ok"] is True
    assert audit_pass["audit_score"] == 100
    assert audit_pass["passed_checks"] == 6

    # 故意引入未来函数与幸存者偏差，必须被拦截扣分
    audit_fail = run_backtest_quality_audit(
        use_pit_fundamentals=False,
        include_delisted_stocks=False,
        filter_limit_up_down=False,
    )
    assert audit_fail["audit_score"] <= 40
    statuses = {c["check_id"]: c["status"] for c in audit_fail["checks"]}
    assert statuses["AUDIT-01-PIT"] == "FAIL"
    assert statuses["AUDIT-02-SURVIVORSHIP"] == "FAIL"
    assert statuses["AUDIT-05-LIMIT"] == "FAIL"


def test_p1_remaining_skills_and_workbenches() -> None:
    # 投资论点跟踪器
    thesis = run_investment_thesis_tracker()
    assert thesis["ok"] is True
    assert thesis["total_theses"] == 3
    assert thesis["intact_count"] >= 1
    assert thesis["broken_or_halted_count"] >= 1

    # 自然语言选股器（含不可用字段诚实标记）
    scr = build_natural_language_screener(
        natural_query="筛选沪深300低PE高股息且包含主力资金净流入预测的股票"
    )
    assert scr["ok"] is True
    assert len(scr["unsupported_fields"]) == 1
    assert scr["matched_count"] >= 2

    # 多源新闻水位线去重
    digest = run_news_signal_digest(watermark_timestamp="2026-11-12T00:00:00+08:00")
    assert digest["ok"] is True
    assert digest["dropped_by_watermark"] >= 1
    assert digest["dropped_by_cluster_dedup"] >= 1
    assert digest["unique_events_count"] == 2

    # 交易与研究日志
    jnl = run_trading_journal_review()
    assert jnl["ok"] is True
    assert jnl["average_discipline_score"] >= 90

    # 量化研究工作台与组合复盘工作台聚合包
    quant_wb = run_quant_workbench_bundle()
    assert quant_wb["ok"] is True
    assert quant_wb["workbench"] == "quant"
    assert quant_wb["etf_dca"]["ok"] is True

    rev_wb = run_portfolio_review_workbench_bundle()
    assert rev_wb["ok"] is True
    assert rev_wb["workbench"] == "portfolio_review"

    cat = get_workflows_and_skills_catalog()
    assert cat["version"] == "3.3.0"
    assert cat["total_skills_count"] == 22
    assert cat["p0_daily_skills_count"] == 8
    assert cat["p1_advanced_skills_count"] == 7
    assert cat["upgraded_skills_count"] == 7
    assert len(cat["workbenches"]) == 5
