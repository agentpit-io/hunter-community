"""单元测试：6 大维度 90 项基本面体检计算引擎（`test_fundamentals_90.py`）。

覆盖测试方案第二关（TC-CALC-02）：
1. 6 大维度（盈利能力 18、成长能力 15、偿债与安全性 16、现金流质量 15、估值水平 14、公司治理与股东 12）
   共 90 项基本面指标目录完整性校验。
2. 90 项基本面指标真值与会计准则重述（调整前 vs 调整后）及报告期标注对齐（TC-CALC-02）。
3. 缺失底层财务字段时返回 `None` 并写入 `missing_metrics`（严禁填 `0`，TC-MCP-02）。
4. 六维评分卡（Scorecard）与综合评级输出校验。
"""
from __future__ import annotations

from app.services.fundamentals import (
    FUNDAMENTAL_CATALOG,
    FUNDAMENTAL_DIMENSIONS_ORDER,
    calculate_all_90_fundamentals,
    default_fundamental_raw_input,
    get_fundamental_catalog_summary,
)


def test_fundamental_catalog_has_exact_6_dimensions_and_90_metrics() -> None:
    """校验基本面指标字典严格包含 6 大维度、90 项指标。"""
    summary = get_fundamental_catalog_summary()
    assert summary["total_count"] == 90
    assert summary["dimension_count"] == 6
    assert list(summary["counts_by_dimension"].keys()) == FUNDAMENTAL_DIMENSIONS_ORDER
    assert summary["counts_by_dimension"] == {
        "盈利能力": 18,
        "成长能力": 15,
        "偿债与安全性": 16,
        "现金流质量": 15,
        "估值水平": 14,
        "公司治理与股东": 12,
    }
    codes = [spec.code for spec in FUNDAMENTAL_CATALOG]
    assert len(set(codes)) == 90


def test_tc_calc_02_all_90_metrics_and_restatement_alignment() -> None:
    """TC-CALC-02：取包含会计准则调整重述的上市公司财报，验证 90 项指标全部精确算出且明确标注调整前/调整后及报告期。"""
    raw = default_fundamental_raw_input("600036.SH", restated=True)
    res = calculate_all_90_fundamentals(raw, symbol="600036.SH")

    assert res["symbol"] == "600036.SH"
    assert res["report_period"] == "2026Q3"
    assert res["published_at"] == "2026-10-28T18:00:00+08:00"
    assert res["restated"] is True
    assert res["basis_label"] == "调整后(重述后)"
    assert res["total_metrics"] == 90
    assert res["computed_metrics"] == 90
    assert res["missing_metrics"] == []

    # 校验核心财务指标真值
    # ROE_WEIGHTED = 425.80 / 908.50 * 100 = 46.87%
    assert res["metrics"]["ROE_WEIGHTED"]["value"] == round(425.80 / 908.50 * 100.0, 2)
    # GROSS_MARGIN = (1389.20 - 791.15) / 1389.20 * 100 = 43.05%
    assert res["metrics"]["GROSS_MARGIN"]["value"] == round((1389.20 - 791.15) / 1389.20 * 100.0, 2)
    # OCF_TO_NET_PROFIT = 568.00 / 431.20 * 100 = 131.73%
    assert res["metrics"]["OCF_TO_NET_PROFIT"]["value"] == round(568.00 / 431.20 * 100.0, 2)
    # DEBT_TO_ASSET = 968.00 / 2120.00 * 100 = 45.66%
    assert res["metrics"]["DEBT_TO_ASSET"]["value"] == round(968.00 / 2120.00 * 100.0, 2)
    # FREE_CASH_FLOW = 568.00 - 51.00 = 517.00
    assert res["metrics"]["FREE_CASH_FLOW"]["value"] == 517.00
    # PE_TTM = 6850.00 / 548.00 = 12.50
    assert res["metrics"]["PE_TTM"]["value"] == 12.50

    # 校验重述前后对照表（调整前 vs 调整后）
    comp = res["restatement_comparison"]
    assert comp is not None
    assert comp["report_period"] == "2026Q3"
    assert comp["restated"] is True
    assert comp["items"]["revenue"]["调整前"] == 1384.50
    assert comp["items"]["revenue"]["调整后"] == 1389.20
    assert comp["items"]["revenue"]["差异"] == 4.70
    assert comp["items"]["net_profit_parent"]["调整前"] == 421.90
    assert comp["items"]["net_profit_parent"]["调整后"] == 425.80

    # 校验六维评分卡
    sc = res["scorecard"]
    assert 0.0 <= sc["overall_score"] <= 100.0
    assert set(sc["dimensions"].keys()) == set(FUNDAMENTAL_DIMENSIONS_ORDER)
    for dim_name, dim_info in sc["dimensions"].items():
        assert 0.0 <= dim_info["score"] <= 100.0, f"{dim_name} 评分超出范围"
        assert dim_info["grade"][0] in ("A", "B", "C", "D")


def test_missing_fundamental_fields_return_none_never_zero() -> None:
    """当底层财报缺失研发费用或商誉字段时，对应指标必须返回 None 并记入 missing_metrics，严禁填 0。"""
    raw = default_fundamental_raw_input("600000.SH", restated=False)
    raw["rd_expense"] = None
    raw["rd_expense_prev_yoy"] = None
    raw["goodwill"] = None
    raw["pledge_ratio_pct"] = None

    res = calculate_all_90_fundamentals(raw, symbol="600000.SH")
    assert res["restated"] is False
    assert res["basis_label"] == "调整前(原始披露)"
    assert res["restatement_comparison"] is None

    for missing_code in ("RD_EXPENSE_RATIO", "RD_EXPENSE_YOY", "GOODWILL_TO_EQUITY", "PLEDGE_RATIO_PCT"):
        assert res["metrics"][missing_code]["value"] is None
        assert res["metrics"][missing_code]["value"] != 0
        assert missing_code in res["missing_metrics"]
        assert "保留 null" in (res["metrics"][missing_code]["missing_reason"] or "")
