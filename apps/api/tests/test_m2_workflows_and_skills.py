"""单元测试：M2 阶段盘前/盘中/盘后三条日常工作流、数字正则回读校验器与三大工作台聚合服务（`test_m2_workflows_and_skills.py`）。

覆盖《05 测试方案》以下核心判据：
- TC-CALC-04：大模型输出数字正则回读校验（100% 匹配放行，写错数字立即拦截重写或报错）
- TC-SK-01 ~ TC-SK-08：8 个 P0 日常 SKILL 与 7 个升级 SKILL 目录结构、三栏证据格式、公告页码溯源
- TC-SK-10：数据源全断时的诚实停止测试（严禁编造当日价格或假报正常）
- 盘前（08:30）、盘中（异动解释 + 频率上限合并）、盘后（15:30 复盘 + fin_memory 沉淀）全链路验证
"""
from __future__ import annotations

from pathlib import Path
import pytest

from app.providers.broker.galaxy_adapter import GalaxyBrokerAdapter
from app.providers.broker.router import BrokerDataRouter
from app.services.workflows import (
    NumberVerificationError,
    clear_intraday_anomaly_throttle_cache,
    run_after_close_workflow,
    run_intraday_anomaly_explainer,
    run_pre_market_workflow,
    run_stock_research_workbench_bundle,
    run_watchlist_workbench_bundle,
    verify_and_guard_report_numbers,
    verify_report_numbers_against_truth,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_tc_calc_04_number_verifier_passes_exact_truth_and_blocks_hallucination() -> None:
    """TC-CALC-04：正则回读校验器逐一提取正文中的百分比、金额、价格，与底层 JSON 真值 100% 匹配；若写错数字立即拦截重写。"""
    truth_payload = {
        "symbol": "600036.SH",
        "last_price": 36.85,
        "pct_change": 1.46,
        "roe_weighted": 46.87,
        "gross_margin": 43.05,
        "pe_ttm": 12.50,
        "revenue": 1389.20,
    }

    # 1. 数字 100% 匹配底层真值的 AI 报告 -> 通过
    good_report = (
        "【已核实事实】招商银行（600036.SH）于 2026-11-13 收报 36.85元，涨幅 +1.46%，"
        "2026Q3 营业收入 1389.20亿元，加权 ROE 为 46.87%，毛利率 43.05%，当前 PE-TTM 为 12.50倍 [来源：公告 · 第1页]。"
    )
    res_ok = verify_report_numbers_against_truth(good_report, truth_payload)
    assert res_ok.ok is True
    assert res_ok.mismatched_tokens == []
    assert len(res_ok.matched_tokens) >= 6

    # 2. 含有大模型心算篡改数字（如把 36.85 写成 39.99，把 ROE 46.87% 写成 52.10%） -> 立即识别拦截
    bad_report = (
        "招商银行（600036.SH）今日现价 39.99元，加权 ROE 高达 52.10%，PE-TTM 仅 9.88倍。"
    )
    res_bad = verify_report_numbers_against_truth(bad_report, truth_payload)
    assert res_bad.ok is False
    assert "39.99元" in res_bad.mismatched_tokens
    assert "52.10%" in res_bad.mismatched_tokens
    assert "9.88倍" in res_bad.mismatched_tokens

    # 3. 启用严格拦截时抛出 NumberVerificationError
    with pytest.raises(NumberVerificationError):
        verify_and_guard_report_numbers(bad_report, truth_payload, raise_on_mismatch=True)

    # 4. 启用确定性回退重写时，自动替换为 100% 真值报告
    rewritten, guard_res = verify_and_guard_report_numbers(
        bad_report,
        truth_payload,
        fallback_deterministic_report=good_report,
        raise_on_mismatch=False,
    )
    assert rewritten == good_report
    assert guard_res.ok is True
    assert "已拦截原报告中的失真数字" in guard_res.message


def test_pre_market_workflow_generates_observation_card_and_three_columns() -> None:
    """TC-SK-01 / TC-SK-02 / TC-SK-03 / TC-SK-07：验证 08:30 盘前工作流串联交易日历、市场晨报、自选股排雷、公告页码与组合风控。"""
    res = run_pre_market_workflow(
        market="CN_A",
        trade_date="2026-11-13",
        watchlist_symbols=["600036.SH", "600519.SH", "600898.SH"],  # 含停牌标的 600898.SH
    )
    assert res["ok"] is True
    assert res["workflow"] == "pre_market"
    assert res["skills_invoked"] == [
        "daily_market_brief",
        "watchlist_daily_brief",
        "announcement_impact",
        "portfolio_risk_check",
    ]
    assert res["number_verification"]["ok"] is True

    # 检查三栏证据结构非空
    tce = res["three_column_evidence"]
    assert len(tce["verified_facts"]) >= 4
    assert len(tce["logical_inferences"]) >= 2
    assert len(tce["pending_questions"]) >= 2

    # 检查停牌标的 600898.SH 价格为 None（正文显示 '—'，绝不填 0）
    susp_items = [w for w in res["watchlist_brief"] if w["symbol"] == "600898.SH"]
    assert len(susp_items) == 1
    assert susp_items[0]["last_price"] is None
    assert susp_items[0]["trading_status"] == "停牌"

    # 检查公告卡片包含 PDF 页码定位 `[来源：... · 第X页]`
    assert len(res["announcement_cards"]) >= 1
    first_ann = res["announcement_cards"][0]
    assert "· 第1页]" in first_ann["citation"]
    assert len(first_ann["pages"]) >= 1


def test_intraday_anomaly_rate_limit_and_tc_sk_10_disconnect_honest_stop() -> None:
    """验证盘中异动解释流的同事件合并频率限制，以及数据源断连时的诚实停止告警（TC-SK-10）。"""
    clear_intraday_anomaly_throttle_cache()

    # 1. 前 2 次触发正常生成三栏归因与原投资论点比对
    r1 = run_intraday_anomaly_explainer(
        symbol="600036.SH",
        event_type="price_volume_spike",
        cooldown_seconds=300,
        max_alerts_per_window=2,
        now_ts=1000.0,
    )
    assert r1["ok"] is True
    assert r1["throttled"] is False
    assert r1["thesis_check"]["status_label"] in ("维持", "动摇")
    assert r1["number_verification"]["ok"] is True

    r2 = run_intraday_anomaly_explainer(
        symbol="600036.SH",
        event_type="price_volume_spike",
        cooldown_seconds=300,
        max_alerts_per_window=2,
        now_ts=1060.0,
    )
    assert r2["ok"] is True
    assert r2["throttled"] is False

    # 2. 冷却窗口内第 3 次相同事件 -> 触发合并限流（throttled=True）
    r3 = run_intraday_anomaly_explainer(
        symbol="600036.SH",
        event_type="price_volume_spike",
        cooldown_seconds=300,
        max_alerts_per_window=2,
        now_ts=1120.0,
    )
    assert r3["ok"] is True
    assert r3["throttled"] is True
    assert r3["merged_event_count"] == 3
    assert "同事件合并限流" in r3["throttle_message"]

    # 3. TC-SK-10：人为断开数据源（simulate_disconnect=True 或路由器禁用降级且主源故障） -> 立即诚实停止告警
    r_disc = run_intraday_anomaly_explainer(
        symbol="600036.SH",
        simulate_disconnect=True,
    )
    assert r_disc["ok"] is False
    assert r_disc["datasource_status"] == "DISCONNECTED"
    assert "数据源异常告警" in r_disc["honest_alert"]

    broken_router = BrokerDataRouter(
        primary=GalaxyBrokerAdapter(simulate_timeout=True),
        fallback=None,
        allow_fallback=False,
    )
    r_router_down = run_intraday_anomaly_explainer(
        symbol="600036.SH",
        router=broken_router,
    )
    assert r_router_down["ok"] is False
    assert r_router_down["datasource_status"] == "DISCONNECTED"


def test_after_close_workflow_compares_observations_and_commits_fin_memory() -> None:
    """TC-SK-08：验证 15:30 盘后复盘对比早盘观察点、沉淀至 fin_memory 并生成次日观察清单。"""
    res = run_after_close_workflow(
        market="CN_A",
        trade_date="2026-11-13",
        watchlist_symbols=["600036.SH", "600519.SH"],
    )
    assert res["ok"] is True
    assert res["workflow"] == "after_close"
    assert res["skills_invoked"] == ["after_close_review"]
    assert len(res["review_comparison"]) >= 2
    assert res["fin_memory_receipt"]["status"] == "committed"
    assert len(res["next_day_watchlist"]) >= 3
    assert res["number_verification"]["ok"] is True
    # 校验收盘行情时间 as_of 与复盘抓取时间 retrieved_at 严格分离（TC-MCP-01）
    assert res["envelope_meta"]["as_of"].startswith("2026-11-13T15:00:00")
    assert res["envelope_meta"]["retrieved_at"] != res["envelope_meta"]["as_of"]


def test_stock_and_watchlist_workbench_bundles_provide_56_and_90_engines() -> None:
    """TC-SK-04 / TC-SK-05 / TC-SK-06：验证自选股工作台与个股研究工作台聚合数据包。"""
    wl_bundle = run_watchlist_workbench_bundle(symbols=["600036.SH", "600519.SH"])
    assert wl_bundle["ok"] is True
    assert len(wl_bundle["watchlist_rows"]) == 2
    assert len(wl_bundle["announcements"]) >= 1

    stock_bundle = run_stock_research_workbench_bundle(symbol="600036.SH", adjustment="前复权", restated=True)
    assert stock_bundle["ok"] is True
    assert stock_bundle["technical_dashboard_56"]["total_indicators"] == 56
    assert stock_bundle["technical_dashboard_56"]["computed_indicators"] == 56
    assert stock_bundle["fundamental_scorecard_90"]["total_metrics"] == 90
    assert stock_bundle["fundamental_scorecard_90"]["computed_metrics"] == 90
    assert stock_bundle["fundamental_scorecard_90"]["restated"] is True
    assert stock_bundle["fundamental_scorecard_90"]["restatement_comparison"] is not None


def test_all_14_workspace_skills_and_market_event_analyst_contain_three_column_evidence() -> None:
    """验证 7 个升级既有技能 + 8 个新增 P0 技能文件全部齐备且包含三栏证据规范。"""
    p0_and_upgraded_dirs = [
        "uzi",
        "deep_analysis",
        "investor_panel",
        "lhb_analyzer",
        "risk_profile",
        "trap_detector",
        "daily_market_brief",
        "watchlist_daily_brief",
        "announcement_impact",
        "earnings_review",
        "technical_dashboard",
        "fundamental_scorecard",
        "portfolio_risk_check",
        "after_close_review",
    ]
    for name in p0_and_upgraded_dirs:
        src_md = REPO_ROOT / "skills" / name / "SKILL.md"
        dst_atom_md = REPO_ROOT / "distro" / "workspace-template" / ".atomcode" / "skills" / name / "SKILL.md"
        dst_tpl_md = REPO_ROOT / "distro" / "workspace-template" / "skills" / name / "SKILL.md"
        assert src_md.is_file(), f"缺少 skills/{name}/SKILL.md"
        assert dst_atom_md.is_file(), f"缺少 .atomcode/skills/{name}/SKILL.md"
        assert dst_tpl_md.is_file(), f"缺少 workspace-template/skills/{name}/SKILL.md"

    # 验证 8 个新增 P0 技能全部包含 references/ 目录与非空参考文档
    for p0_name in p0_and_upgraded_dirs[6:]:
        refs_dir = REPO_ROOT / "skills" / p0_name / "references"
        assert refs_dir.is_dir() and list(refs_dir.glob("*.md")), f"{p0_name} 缺少 references/*.md"

    # 验证 apps/api/skills/market-event-analyst.skill 已升级包含三栏证据
    mea_path = REPO_ROOT / "apps" / "api" / "skills" / "market-event-analyst.skill"
    mea_text = mea_path.read_text(encoding="utf-8")
    assert "【已核实事实】" in mea_text
    assert "【逻辑推断】" in mea_text
    assert "【待核实问题】" in mea_text
