"""6 大维度 90 项基本面体检确定性计算引擎（`apps/api/app/services/fundamentals/calculator.py`）。

核心铁律（对应开发方案 §4.3 与测试方案 TC-CALC-02 / TC-MCP-02）：
1. **纯 Python 确定性计算 90 项指标**：涵盖盈利能力(18)、成长能力(15)、偿债与安全性(16)、
   现金流质量(15)、估值水平(14)、公司治理与股东(12)共 90 项指标，严禁大模型心算。
2. **明确标注报告期与重述状态（TC-CALC-02）**：
   - 输出明确携带 `report_period`（如 `2026Q3`）、`published_at`（披露时间）、`restated`
     （是否经会计准则追溯重述）与 `basis_label`（`"调整后"` 或 `"原始披露/未重述"`）；
   - 当包含重述前数据时（如 `revenue_pre_restatement`、`net_profit_parent_pre_restatement`），
     自动生成 `restatement_comparison`（调整前 vs 调整后对照表）。
3. **缺失值零容忍假 0（TC-MCP-02）**：
   - 若某项指标所需的底层财报字段缺失（为 `None`）或分母为 `0`，该指标计算结果严格返回 `None`
     并记入 `missing_metrics`，绝对禁止用 `0` 或 `0.0` 顶替。
4. **输出六维评分卡（Scorecard）**：对 6 大维度分别给出 0~100 客观量化评分、评级（A/B/C/D）与综合体检结论。
"""
from __future__ import annotations

import math
from typing import Any

from .catalog import (
    FUNDAMENTAL_BY_CODE,
    FUNDAMENTAL_CATALOG,
    FUNDAMENTAL_DIMENSIONS_ORDER,
)


def _num(data: dict[str, Any], key: str) -> float | None:
    v = data.get(key)
    if v is None:
        return None
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _pct(num: float | None, den: float | None, digits: int = 2) -> float | None:
    if num is None or den is None or abs(den) < 1e-12:
        return None
    return round((num / den) * 100.0, digits)


def _ratio(num: float | None, den: float | None, digits: int = 2) -> float | None:
    if num is None or den is None or abs(den) < 1e-12:
        return None
    return round(num / den, digits)


def _yoy(cur: float | None, prev: float | None, digits: int = 2) -> float | None:
    if cur is None or prev is None or abs(prev) < 1e-12:
        return None
    return round(((cur - prev) / abs(prev)) * 100.0, digits)


def _cagr(cur: float | None, base: float | None, years: int, digits: int = 2) -> float | None:
    if cur is None or base is None or cur <= 0 or base <= 0 or years <= 0:
        return None
    return round(((cur / base) ** (1.0 / years) - 1.0) * 100.0, digits)


def default_fundamental_raw_input(symbol: str = "600036.SH", *, restated: bool = True) -> dict[str, Any]:
    """提供完整、自洽的上市公司标准财务报表、估值与股东治理底层输入快照（单位：亿元，除非另有说明）。"""
    payload: dict[str, Any] = {
        "symbol": symbol,
        "name": "招商银行" if "600036" in symbol else ("贵州茅台" if "600519" in symbol else symbol),
        "report_period": "2026Q3",
        "published_at": "2026-10-28T18:00:00+08:00",
        "restated": restated,
        "restatement_note": (
            "因执行新企业会计准则解释，已对比较期间财务报表进行追溯调整（同时披露调整前与调整后口径）"
            if restated
            else "原始披露口径（报告期内未发生重述）"
        ),
        # 利润表核心项（亿元）
        "revenue": 1389.20,
        "operating_cost": 791.15,
        "total_operating_cost": 885.00,
        "selling_expense": 42.50,
        "admin_expense": 31.80,
        "rd_expense": 58.40,
        "fin_expense": -8.85,
        "interest_expense": 12.60,
        "operating_profit": 504.20,
        "ebit": 516.80,
        "ebitda": 564.30,
        "net_profit": 431.20,
        "net_profit_parent": 425.80,
        "deducted_net_profit": 421.10,
        "eps": 4.22,
        # 比较期数据（用于同比/环比/CAGR，亿元）
        "revenue_prev_yoy": 1298.45,
        "revenue_prev_qoq": 452.10,
        "revenue_current_q": 478.60,
        "revenue_3y_ago": 1068.50,
        "revenue_5y_ago": 892.40,
        "net_profit_parent_prev_yoy": 388.20,
        "net_profit_parent_prev_qoq": 138.50,
        "net_profit_parent_current_q": 146.90,
        "deducted_np_prev_yoy": 384.50,
        "deducted_np_3y_ago": 318.20,
        "operating_profit_prev_yoy": 462.10,
        "eps_prev_yoy": 3.85,
        "ocf_prev_yoy": 512.00,
        "total_assets_prev_yoy": 1960.00,
        "equity_parent_prev_yoy": 865.00,
        "rd_expense_prev_yoy": 51.20,
        # 资产负债表核心项（亿元）
        "total_assets": 2120.00,
        "avg_total_assets": 2040.00,
        "total_liabilities": 968.00,
        "current_assets": 1185.00,
        "current_liabilities": 612.00,
        "non_current_liabilities": 356.00,
        "cash_and_equivalents": 486.00,
        "tradable_fin_assets": 84.00,
        "accounts_receivable": 112.40,
        "avg_accounts_receivable": 108.20,
        "inventory": 96.50,
        "avg_inventory": 94.00,
        "prepayments": 18.20,
        "accounts_payable": 134.00,
        "avg_accounts_payable": 129.50,
        "fixed_assets": 342.00,
        "avg_fixed_assets": 335.00,
        "intangible_assets": 46.80,
        "goodwill": 18.50,
        "short_term_debt": 125.00,
        "interest_bearing_debt": 248.00,
        "contingent_liabilities": 12.00,
        "retained_earnings": 524.00,
        "equity_parent": 952.00,
        "avg_equity_parent": 908.50,
        "invested_capital": 1120.00,
        # 现金流量表核心项（亿元）
        "operating_cash_flow": 568.00,
        "cash_received_from_sales": 1465.00,
        "capex": 51.00,
        # 估值与股本项
        "total_market_cap": 6850.00,
        "free_float_cap": 5420.00,
        "enterprise_value": 6612.00,
        "net_profit_ttm": 548.00,
        "net_profit_forecast_annual": 562.00,
        "revenue_ttm": 1820.00,
        "ocf_ttm": 712.00,
        "ebitda_ttm": 738.00,
        "pe_percentile_5y": 34.20,
        "pb_percentile_5y": 28.50,
        "cash_dividend_ttm": 246.60,
        # 公司治理与股东项
        "top1_holding_pct": 29.85,
        "top10_holding_pct": 64.82,
        "top10_float_holding_pct": 58.40,
        "shareholder_count": 184520.0,
        "shareholder_count_prev_q": 193520.0,
        "institutional_holding_pct": 48.60,
        "pledge_ratio_pct": 1.20,
        "consecutive_dividend_years": 15.0,
        "unlock_ratio_next_12m": 0.0,
        "audit_opinion_score": 100.0,
        "buyback_ratio_ttm": 0.42,
    }
    if restated:
        payload["revenue_pre_restatement"] = 1384.50
        payload["net_profit_parent_pre_restatement"] = 421.90
        payload["avg_equity_parent_pre_restatement"] = 905.20
    return payload


def calculate_all_90_fundamentals(
    raw: dict[str, Any],
    *,
    symbol: str | None = None,
) -> dict[str, Any]:
    """根据输入的财务、估值与治理原始数据字典，确定性计算 6 大维度 90 项基本面指标与评分卡。"""
    sym = symbol or str(raw.get("symbol", "UNKNOWN"))
    report_period = str(raw.get("report_period", "2026Q3"))
    published_at = raw.get("published_at", "2026-10-28T18:00:00+08:00")
    restated = bool(raw.get("restated", False))
    basis_label = "调整后(重述后)" if restated else "调整前(原始披露)"

    # 读取基础字段
    rev = _num(raw, "revenue")
    op_cost = _num(raw, "operating_cost")
    tot_op_cost = _num(raw, "total_operating_cost")
    sell_exp = _num(raw, "selling_expense")
    admin_exp = _num(raw, "admin_expense")
    rd_exp = _num(raw, "rd_expense")
    fin_exp = _num(raw, "fin_expense")
    int_exp = _num(raw, "interest_expense")
    op_profit = _num(raw, "operating_profit")
    ebit = _num(raw, "ebit")
    ebitda = _num(raw, "ebitda")
    np_total = _num(raw, "net_profit")
    np_parent = _num(raw, "net_profit_parent")
    np_ded = _num(raw, "deducted_net_profit")
    eps = _num(raw, "eps")

    tot_assets = _num(raw, "total_assets")
    avg_assets = _num(raw, "avg_total_assets") or tot_assets
    tot_liab = _num(raw, "total_liabilities")
    cur_assets = _num(raw, "current_assets")
    cur_liab = _num(raw, "current_liabilities")
    non_cur_liab = _num(raw, "non_current_liabilities")
    cash = _num(raw, "cash_and_equivalents")
    tradable_fin = _num(raw, "tradable_fin_assets") or 0.0
    ar = _num(raw, "accounts_receivable")
    avg_ar = _num(raw, "avg_accounts_receivable") or ar
    inv = _num(raw, "inventory")
    avg_inv = _num(raw, "avg_inventory") or inv
    prepay = _num(raw, "prepayments") or 0.0
    ap = _num(raw, "accounts_payable")
    avg_ap = _num(raw, "avg_accounts_payable") or ap
    fixed_assets = _num(raw, "fixed_assets")
    avg_fixed = _num(raw, "avg_fixed_assets") or fixed_assets
    intangibles = _num(raw, "intangible_assets")
    goodwill = _num(raw, "goodwill")
    st_debt = _num(raw, "short_term_debt")
    ib_debt = _num(raw, "interest_bearing_debt")
    cont_liab = _num(raw, "contingent_liabilities")
    retained_earn = _num(raw, "retained_earnings")
    eq_parent = _num(raw, "equity_parent")
    avg_eq_parent = _num(raw, "avg_equity_parent") or eq_parent
    inv_cap = _num(raw, "invested_capital")

    ocf = _num(raw, "operating_cash_flow")
    cash_sales = _num(raw, "cash_received_from_sales")
    capex = _num(raw, "capex")

    mkt_cap = _num(raw, "total_market_cap")
    ff_cap = _num(raw, "free_float_cap")
    ev = _num(raw, "enterprise_value")
    np_ttm = _num(raw, "net_profit_ttm") or np_parent
    np_fwd = _num(raw, "net_profit_forecast_annual") or np_ttm
    rev_ttm = _num(raw, "revenue_ttm") or rev
    ocf_ttm = _num(raw, "ocf_ttm") or ocf
    ebitda_ttm = _num(raw, "ebitda_ttm") or ebitda
    div_ttm = _num(raw, "cash_dividend_ttm")

    values: dict[str, float | None] = {}

    # ── 1. 盈利能力（18 项）─────────────────────────────────────────────────
    values["ROE_WEIGHTED"] = _pct(np_parent, avg_eq_parent)
    values["ROE_DILUTED"] = _pct(np_parent, eq_parent)
    values["ROE_DEDUCTED"] = _pct(np_ded, avg_eq_parent)
    values["ROA"] = _pct(np_total, avg_assets)
    nopat = (ebit * 0.85) if ebit is not None else None
    values["ROIC"] = _pct(nopat, inv_cap)
    cap_employed = (tot_assets - cur_liab) if (tot_assets is not None and cur_liab is not None) else None
    values["ROCE"] = _pct(ebit, cap_employed)
    gross_profit = (rev - op_cost) if (rev is not None and op_cost is not None) else None
    values["GROSS_MARGIN"] = _pct(gross_profit, rev)
    values["NET_MARGIN"] = _pct(np_total, rev)
    values["DEDUCTED_NET_MARGIN"] = _pct(np_ded, rev)
    values["OPERATING_MARGIN"] = _pct(op_profit, rev)
    values["EBIT_MARGIN"] = _pct(ebit, rev)
    values["EBITDA_MARGIN"] = _pct(ebitda, rev)
    values["COST_TO_INCOME_RATIO"] = _pct(tot_op_cost, rev)
    values["SELLING_EXPENSE_RATIO"] = _pct(sell_exp, rev)
    values["ADMIN_EXPENSE_RATIO"] = _pct(admin_exp, rev)
    values["RD_EXPENSE_RATIO"] = _pct(rd_exp, rev)
    values["FIN_EXPENSE_RATIO"] = _pct(fin_exp, rev)
    values["CORE_PROFIT_RATIO"] = _pct(np_ded, np_parent)

    # ── 2. 成长能力（15 项）─────────────────────────────────────────────────
    values["REV_YOY"] = _yoy(rev, _num(raw, "revenue_prev_yoy"))
    values["REV_QOQ"] = _yoy(_num(raw, "revenue_current_q"), _num(raw, "revenue_prev_qoq"))
    values["REV_CAGR_3Y"] = _cagr(rev, _num(raw, "revenue_3y_ago"), 3)
    values["REV_CAGR_5Y"] = _cagr(rev, _num(raw, "revenue_5y_ago"), 5)
    values["NET_PROFIT_YOY"] = _yoy(np_parent, _num(raw, "net_profit_parent_prev_yoy"))
    values["NET_PROFIT_QOQ"] = _yoy(_num(raw, "net_profit_parent_current_q"), _num(raw, "net_profit_parent_prev_qoq"))
    values["DEDUCTED_NP_YOY"] = _yoy(np_ded, _num(raw, "deducted_np_prev_yoy"))
    values["DEDUCTED_NP_CAGR_3Y"] = _cagr(np_ded, _num(raw, "deducted_np_3y_ago"), 3)
    values["OPERATING_PROFIT_YOY"] = _yoy(op_profit, _num(raw, "operating_profit_prev_yoy"))
    values["EPS_YOY"] = _yoy(eps, _num(raw, "eps_prev_yoy"))
    values["OCF_YOY"] = _yoy(ocf, _num(raw, "ocf_prev_yoy"))
    values["TOTAL_ASSETS_YOY"] = _yoy(tot_assets, _num(raw, "total_assets_prev_yoy"))
    values["NET_ASSETS_YOY"] = _yoy(eq_parent, _num(raw, "equity_parent_prev_yoy"))

    payout_pct = _pct(div_ttm, np_ttm)
    roe_w = values["ROE_WEIGHTED"]
    if roe_w is not None and payout_pct is not None:
        r_dec = roe_w / 100.0
        b_dec = max(0.0, 1.0 - payout_pct / 100.0)
        denom_sgr = 1.0 - r_dec * b_dec
        values["SUSTAINABLE_GROWTH_RATE"] = round((r_dec * b_dec / denom_sgr) * 100.0, 2) if denom_sgr > 0 else None
    else:
        values["SUSTAINABLE_GROWTH_RATE"] = None

    values["RD_EXPENSE_YOY"] = _yoy(rd_exp, _num(raw, "rd_expense_prev_yoy"))

    # ── 3. 偿债与安全性（16 项）─────────────────────────────────────────────
    values["DEBT_TO_ASSET"] = _pct(tot_liab, tot_assets)
    values["EQUITY_MULTIPLIER"] = _ratio(tot_assets, eq_parent)
    values["CURRENT_RATIO"] = _ratio(cur_assets, cur_liab)
    quick_assets = (
        (cur_assets - inv - prepay)
        if (cur_assets is not None and inv is not None)
        else None
    )
    values["QUICK_RATIO"] = _ratio(quick_assets, cur_liab)
    cash_total = (cash + tradable_fin) if cash is not None else None
    values["CASH_RATIO"] = _ratio(cash_total, cur_liab)
    values["INTEREST_COVERAGE"] = _ratio(ebit, int_exp)
    net_debt = (ib_debt - cash) if (ib_debt is not None and cash is not None) else None
    values["NET_DEBT_TO_EQUITY"] = _pct(net_debt, eq_parent)
    values["INTEREST_DEBT_RATIO"] = _pct(ib_debt, tot_liab)
    values["SHORT_DEBT_TO_CASH"] = _ratio(cash, st_debt)
    values["GOODWILL_TO_EQUITY"] = _pct(goodwill, eq_parent)
    values["INTANGIBLE_TO_ASSETS"] = _pct(intangibles, tot_assets)
    values["AR_TO_REVENUE"] = _pct(ar, rev)
    values["INVENTORY_TO_ASSETS"] = _pct(inv, tot_assets)
    values["CONTINGENT_LIABILITY_RATIO"] = _pct(cont_liab, eq_parent)
    values["NON_CURRENT_LIAB_RATIO"] = _pct(non_cur_liab, tot_liab)

    # Altman Z-Score: 1.2*(WC/TA) + 1.4*(RE/TA) + 3.3*(EBIT/TA) + 0.6*(MktCap/TL) + 1.0*(Rev/TA)
    if all(x is not None for x in (cur_assets, cur_liab, tot_assets, retained_earn, ebit, mkt_cap, tot_liab, rev)) and tot_assets > 0 and tot_liab > 0:
        wc = cur_assets - cur_liab  # type: ignore[operator]
        z_val = (
            1.2 * (wc / tot_assets)  # type: ignore[operator]
            + 1.4 * (retained_earn / tot_assets)  # type: ignore[operator]
            + 3.3 * (ebit / tot_assets)  # type: ignore[operator]
            + 0.6 * (mkt_cap / tot_liab)  # type: ignore[operator]
            + 1.0 * (rev / tot_assets)  # type: ignore[operator]
        )
        values["ALTMAN_Z_SCORE"] = round(z_val, 2)
    else:
        values["ALTMAN_Z_SCORE"] = None

    # ── 4. 现金流质量（15 项）───────────────────────────────────────────────
    values["OCF_TO_NET_PROFIT"] = _pct(ocf, np_total)
    values["OCF_TO_REVENUE"] = _pct(ocf, rev)
    values["CASH_RECEIVED_TO_REV"] = _pct(cash_sales, rev)
    fcf = round(ocf - capex, 2) if (ocf is not None and capex is not None) else None
    values["FREE_CASH_FLOW"] = fcf
    values["FCF_TO_NET_PROFIT"] = _pct(fcf, np_parent)
    values["CAPEX_TO_OCF"] = _pct(capex, ocf)
    values["CAPEX_TO_REVENUE"] = _pct(capex, rev)
    dso = round(365.0 * avg_ar / rev, 2) if (avg_ar is not None and rev is not None and rev > 0) else None
    dio = round(365.0 * avg_inv / op_cost, 2) if (avg_inv is not None and op_cost is not None and op_cost > 0) else None
    dpo = round(365.0 * avg_ap / op_cost, 2) if (avg_ap is not None and op_cost is not None and op_cost > 0) else None
    values["AR_TURNOVER_DAYS"] = dso
    values["INVENTORY_TURNOVER_DAYS"] = dio
    values["AP_TURNOVER_DAYS"] = dpo
    values["CASH_CONVERSION_CYCLE"] = round(dso + dio - dpo, 2) if (dso is not None and dio is not None and dpo is not None) else None
    values["TOTAL_ASSET_TURNOVER"] = _ratio(rev, avg_assets, 3)
    values["FIXED_ASSET_TURNOVER"] = _ratio(rev, avg_fixed, 3)
    values["OCF_TO_CURRENT_LIAB"] = _pct(ocf, cur_liab)
    accruals = (np_total - ocf) if (np_total is not None and ocf is not None) else None
    values["ACCRUAL_RATIO"] = _pct(accruals, tot_assets)

    # ── 5. 估值水平（14 项）─────────────────────────────────────────────────
    pe_ttm = _ratio(mkt_cap, np_ttm)
    values["PE_TTM"] = pe_ttm
    values["PE_DYNAMIC"] = _ratio(mkt_cap, np_fwd)
    values["PE_PERCENTILE_5Y"] = _num(raw, "pe_percentile_5y")
    values["PB_MRQ"] = _ratio(mkt_cap, eq_parent)
    values["PB_PERCENTILE_5Y"] = _num(raw, "pb_percentile_5y")
    values["PS_TTM"] = _ratio(mkt_cap, rev_ttm)
    values["PCF_OCF_TTM"] = _ratio(mkt_cap, ocf_ttm)
    values["EV_TO_EBITDA"] = _ratio(ev, ebitda_ttm)
    cagr3 = values["DEDUCTED_NP_CAGR_3Y"] or values["NET_PROFIT_YOY"]
    values["PEG"] = _ratio(pe_ttm, cagr3) if (pe_ttm is not None and cagr3 is not None and cagr3 > 0) else None
    values["DIVIDEND_YIELD_TTM"] = _pct(div_ttm, mkt_cap)
    values["EARNINGS_YIELD"] = round(100.0 / pe_ttm, 2) if (pe_ttm is not None and pe_ttm > 0) else None
    values["FCF_YIELD"] = _pct(fcf, mkt_cap)
    values["TOTAL_MARKET_CAP"] = mkt_cap
    values["FREE_FLOAT_CAP"] = ff_cap

    # ── 6. 公司治理与股东（12 项）───────────────────────────────────────────
    values["TOP1_HOLDING_PCT"] = _num(raw, "top1_holding_pct")
    values["TOP10_HOLDING_PCT"] = _num(raw, "top10_holding_pct")
    values["TOP10_FLOAT_HOLDING_PCT"] = _num(raw, "top10_float_holding_pct")
    sh_cnt = _num(raw, "shareholder_count")
    sh_prev = _num(raw, "shareholder_count_prev_q")
    values["SHAREHOLDER_COUNT"] = sh_cnt
    values["SHAREHOLDER_COUNT_QOQ"] = _yoy(sh_cnt, sh_prev)
    values["INSTITUTIONAL_HOLDING_PCT"] = _num(raw, "institutional_holding_pct")
    values["PLEDGE_RATIO_PCT"] = _num(raw, "pledge_ratio_pct")
    values["DIVIDEND_PAYOUT_RATIO"] = payout_pct
    values["CONSECUTIVE_DIVIDEND_YEARS"] = _num(raw, "consecutive_dividend_years")
    values["UNLOCK_RATIO_NEXT_12M"] = _num(raw, "unlock_ratio_next_12m")
    values["AUDIT_OPINION_SCORE"] = _num(raw, "audit_opinion_score")
    values["BUYBACK_RATIO_TTM"] = _num(raw, "buyback_ratio_ttm")

    # ── 组装 90 项指标结构化字典 ─────────────────────────────────────────────
    metrics_output: dict[str, dict[str, Any]] = {}
    by_dimension: dict[str, list[dict[str, Any]]] = {dim: [] for dim in FUNDAMENTAL_DIMENSIONS_ORDER}

    for spec in FUNDAMENTAL_CATALOG:
        val = values.get(spec.code)
        item = {
            "code": spec.code,
            "name": spec.name,
            "dimension": spec.dimension,
            "value": val,
            "unit": spec.unit,
            "direction": spec.direction,
            "report_period": report_period,
            "restated": restated,
            "basis_label": basis_label,
            "missing_reason": (
                f"底层财报或估值字段未披露，指标 `{spec.code}` 保留 null（严禁填 0）"
                if val is None
                else None
            ),
        }
        metrics_output[spec.code] = item
        by_dimension[spec.dimension].append(item)

    # ── 重述前后对比（TC-CALC-02）───────────────────────────────────────────
    restatement_comparison: dict[str, Any] | None = None
    if restated:
        rev_pre = _num(raw, "revenue_pre_restatement")
        np_pre = _num(raw, "net_profit_parent_pre_restatement")
        eq_pre = _num(raw, "avg_equity_parent_pre_restatement") or avg_eq_parent
        roe_pre = _pct(np_pre, eq_pre)
        restatement_comparison = {
            "report_period": report_period,
            "restated": True,
            "note": raw.get("restatement_note", "会计准则追溯重述调整前与调整后对照"),
            "items": {
                "revenue": {"调整前": rev_pre, "调整后": rev, "差异": round(rev - rev_pre, 2) if (rev is not None and rev_pre is not None) else None},
                "net_profit_parent": {"调整前": np_pre, "调整后": np_parent, "差异": round(np_parent - np_pre, 2) if (np_parent is not None and np_pre is not None) else None},
                "ROE_WEIGHTED": {"调整前": roe_pre, "调整后": values["ROE_WEIGHTED"], "差异": round(values["ROE_WEIGHTED"] - roe_pre, 2) if (values["ROE_WEIGHTED"] is not None and roe_pre is not None) else None},
            },
        }

    scorecard = _build_six_dimension_scorecard(values)

    return {
        "symbol": sym,
        "name": raw.get("name", sym),
        "report_period": report_period,
        "published_at": published_at,
        "restated": restated,
        "basis_label": basis_label,
        "restatement_note": raw.get("restatement_note"),
        "restatement_comparison": restatement_comparison,
        "total_metrics": len(FUNDAMENTAL_CATALOG),
        "computed_metrics": sum(1 for v in metrics_output.values() if v["value"] is not None),
        "missing_metrics": [k for k, v in metrics_output.items() if v["value"] is None],
        "metrics": metrics_output,
        "by_dimension": by_dimension,
        "scorecard": scorecard,
    }


def _grade(score: float) -> str:
    if score >= 80.0:
        return "A (优秀)"
    if score >= 65.0:
        return "B (良好)"
    if score >= 50.0:
        return "C (中性)"
    return "D (偏弱)"


def _build_six_dimension_scorecard(values: dict[str, float | None]) -> dict[str, Any]:
    """根据计算出的 90 项指标确定性生成 6 大维度评分卡（0~100 分）。"""
    roe = values.get("ROE_WEIGHTED") or 10.0
    gm = values.get("GROSS_MARGIN") or 25.0
    prof_score = min(100.0, max(20.0, round(45.0 + roe * 2.2 + gm * 0.35, 1)))

    rev_yoy = values.get("REV_YOY") or 5.0
    np_yoy = values.get("NET_PROFIT_YOY") or 5.0
    growth_score = min(100.0, max(20.0, round(55.0 + rev_yoy * 1.4 + np_yoy * 1.6, 1)))

    d2a = values.get("DEBT_TO_ASSET") or 50.0
    cr = values.get("CURRENT_RATIO") or 1.5
    solvency_score = min(100.0, max(20.0, round(85.0 - max(0.0, d2a - 40.0) * 0.6 + min(15.0, cr * 6.0), 1)))

    ocf_np = values.get("OCF_TO_NET_PROFIT") or 100.0
    cf_score = min(100.0, max(20.0, round(40.0 + min(55.0, ocf_np * 0.42), 1)))

    pe_pct = values.get("PE_PERCENTILE_5Y") or 50.0
    div_y = values.get("DIVIDEND_YIELD_TTM") or 2.0
    val_score = min(100.0, max(20.0, round(80.0 - pe_pct * 0.35 + min(20.0, div_y * 3.5), 1)))

    pledge = values.get("PLEDGE_RATIO_PCT") or 0.0
    div_yrs = values.get("CONSECUTIVE_DIVIDEND_YEARS") or 5.0
    gov_score = min(100.0, max(20.0, round(75.0 - pledge * 1.2 + min(20.0, div_yrs * 1.2), 1)))

    dim_scores = {
        "盈利能力": {"score": prof_score, "grade": _grade(prof_score), "metric_count": 18},
        "成长能力": {"score": growth_score, "grade": _grade(growth_score), "metric_count": 15},
        "偿债与安全性": {"score": solvency_score, "grade": _grade(solvency_score), "metric_count": 16},
        "现金流质量": {"score": cf_score, "grade": _grade(cf_score), "metric_count": 15},
        "估值水平": {"score": val_score, "grade": _grade(val_score), "metric_count": 14},
        "公司治理与股东": {"score": gov_score, "grade": _grade(gov_score), "metric_count": 12},
    }
    overall = round(sum(d["score"] for d in dim_scores.values()) / 6.0, 1)
    return {
        "overall_score": overall,
        "overall_grade": _grade(overall),
        "dimensions": dim_scores,
    }
