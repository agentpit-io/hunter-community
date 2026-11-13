"""ETF 定投计算器确定性计算引擎（`apps/api/app/services/etf_dca/calculator.py`）。

核心铁律（对应开发方案 §4.3 与测试方案 TC-CALC-03）：
1. **严禁大模型心算数字**：累计投入本金、总份额、期末市值、持仓均价、内部收益率（IRR/XIRR）、
   最大回撤、目标金额反推、定投 vs 一次性买入同区间对比，100% 由本模块确定性算出。
2. **严禁假数据或用 0 顶替缺失值**：若缺少净值序列或参数非法，明确报错或返回 `None` 缺失标记。
3. **强制附带免责声明**：每次计算输出必须包含 `disclaimer` 字段，且正文包含
   `"基于历史净值与假设参数测算，历史表现不代表未来收益"`。
"""
from __future__ import annotations

import math
from datetime import date
from typing import Any, Literal

from app.providers.broker.router import get_default_broker_router

MANDATORY_DCA_DISCLAIMER = (
    "【重要风险提示与历史假设免责声明】基于历史净值与假设参数测算，历史表现不代表未来收益。"
    "基金定期定额投资（定投）是引导投资人进行长期投资、平均投资成本的一种简单易行的投资方式，"
    "但定期定额投资并不能规避基金投资所固有的风险，不能保证投资人获得收益，也不是替代储蓄的等效理财方式。"
)

FrequencyType = Literal["monthly", "biweekly", "weekly"]


def compute_xirr(cashflows: list[tuple[date, float]], guess: float = 0.08) -> float | None:
    """使用牛顿-拉夫逊法 + 二分法兜底计算不规则现金流的年化内部收益率 XIRR（返回小数，如 0.0825 表示 8.25%）。

    约定：投入现金流为负数（`< 0`），期末赎回或分红流入为正数（`> 0`）。
    """
    if len(cashflows) < 2:
        return None
    has_neg = any(cf < 0 for _, cf in cashflows)
    has_pos = any(cf > 0 for _, cf in cashflows)
    if not (has_neg and has_pos):
        return None

    t0 = cashflows[0][0]
    days_list = [(dt - t0).days / 365.0 for dt, _ in cashflows]
    amounts = [float(cf) for _, cf in cashflows]

    def npv(rate: float) -> float:
        if rate <= -0.9999:
            return 1e18
        total = 0.0
        for amt, yrs in zip(amounts, days_list):
            total += amt / ((1.0 + rate) ** yrs)
        return total

    def d_npv(rate: float) -> float:
        if rate <= -0.9999:
            return -1e18
        total = 0.0
        for amt, yrs in zip(amounts, days_list):
            if yrs != 0:
                total -= yrs * amt / ((1.0 + rate) ** (yrs + 1.0))
        return total

    r = guess
    for _ in range(80):
        f_val = npv(r)
        if abs(f_val) < 1e-7:
            return r
        df_val = d_npv(r)
        if abs(df_val) < 1e-12:
            break
        next_r = r - f_val / df_val
        if next_r <= -0.95:
            next_r = -0.90
        elif next_r > 10.0:
            next_r = 10.0
        if abs(next_r - r) < 1e-8:
            return next_r
        r = next_r

    # 二分法稳健兜底
    low, high = -0.90, 5.0
    f_low, f_high = npv(low), npv(high)
    if f_low * f_high > 0:
        return None
    for _ in range(120):
        mid = (low + high) / 2.0
        f_mid = npv(mid)
        if abs(f_mid) < 1e-7 or (high - low) < 1e-8:
            return mid
        if f_low * f_mid <= 0:
            high = mid
            f_high = f_mid
        else:
            low = mid
            f_low = f_mid
    return (low + high) / 2.0


def _select_dca_schedule_points(
    nav_series: list[dict[str, Any]],
    frequency: FrequencyType = "monthly",
    invest_day: int = 15,
) -> list[dict[str, Any]]:
    """从交易日净值序列中按定投频率筛选实际扣款交易日（遇非交易日顺延至该周期首个交易日）。"""
    if not nav_series:
        return []

    if frequency == "weekly":
        selected: list[dict[str, Any]] = []
        seen_year_week: set[tuple[int, int]] = set()
        target_weekday = min(max(invest_day - 1, 0), 4)  # 1..5 -> 周一..周五
        for row in nav_series:
            dt = date.fromisoformat(row["nav_date"][:10])
            iso_yr, iso_wk, _ = dt.isocalendar()
            if (iso_yr, iso_wk) in seen_year_week:
                continue
            if dt.weekday() >= target_weekday:
                selected.append(row)
                seen_year_week.add((iso_yr, iso_wk))
        return selected

    if frequency == "biweekly":
        weekly = _select_dca_schedule_points(nav_series, frequency="weekly", invest_day=invest_day)
        return weekly[::2]

    # 默认按月定投：每月取 >= invest_day 的首个交易日；若该月最后交易日仍 < invest_day，则取该月最后交易日
    by_month: dict[str, list[dict[str, Any]]] = {}
    for row in nav_series:
        ym = row["nav_date"][:7]
        by_month.setdefault(ym, []).append(row)

    selected_monthly: list[dict[str, Any]] = []
    for ym in sorted(by_month.keys()):
        month_rows = by_month[ym]
        chosen = None
        for r in month_rows:
            d_num = int(r["nav_date"][8:10])
            if d_num >= invest_day:
                chosen = r
                break
        if chosen is None:
            chosen = month_rows[-1]
        selected_monthly.append(chosen)
    return selected_monthly


def calculate_etf_dca_backtest(
    *,
    symbol: str = "510300.SH",
    periodic_amount: float = 2000.0,
    frequency: FrequencyType = "monthly",
    invest_day: int = 15,
    start_date: str = "2023-11-01",
    end_date: str = "2026-11-13",
    subscription_fee_pct: float = 0.10,
    dividend_reinvest: bool = True,
    nav_series: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """执行 ETF 正向定投现金流与历史净值回测（TC-CALC-03）。

    精确算出：
    - `total_invested`（累计投入本金，元）
    - `total_fees_paid`（累计申购手续费，元）
    - `total_shares`（期末总持有份额，份）
    - `average_holding_cost`（持仓均价 = 累计投入本金 / 总份额，元/份）
    - `final_nav`（期末单位净值，元/份）
    - `final_market_value`（期末总市值 + 累计现金分红，元）
    - `total_profit`（累计净收益，元）
    - `total_return_pct`（累计总收益率，%）
    - `irr_annualized_pct`（年化内部收益率 XIRR，%）
    - `periodic_irr_pct`（单期内部收益率，%）
    - `max_drawdown_pct`（定投组合市值相对峰值最大回撤，%）
    """
    if periodic_amount <= 0:
        raise ValueError("每期定投金额 `periodic_amount` 必须大于 0")
    if subscription_fee_pct < 0 or subscription_fee_pct >= 10.0:
        raise ValueError("申购费率 `subscription_fee_pct` 必须在 [0, 10) 百分数范围内")

    provider_meta: dict[str, Any] = {}
    fund_name = "华泰柏瑞沪深300ETF" if "510300" in symbol else symbol
    if nav_series is None:
        router = get_default_broker_router()
        nav_env = router.get_fund_nav(symbol=symbol, start_date=start_date, end_date=end_date)
        provider_meta = nav_env.meta.model_dump(mode="json")
        if nav_env.data and isinstance(nav_env.data, dict):
            fund_name = nav_env.data.get("name") or fund_name
            nav_series = nav_env.data.get("nav_series") or []
        else:
            nav_series = []

    if not nav_series:
        return {
            "ok": False,
            "symbol": symbol,
            "error": "未获取到有效历史净值序列，无法执行定投回测（严禁编造假数据）",
            "total_invested": None,
            "total_shares": None,
            "final_market_value": None,
            "average_holding_cost": None,
            "irr_annualized_pct": None,
            "max_drawdown_pct": None,
            "disclaimer": MANDATORY_DCA_DISCLAIMER,
        }

    schedule_rows = _select_dca_schedule_points(nav_series, frequency=frequency, invest_day=invest_day)
    schedule_dates = {r["nav_date"][:10] for r in schedule_rows}

    fee_rate = subscription_fee_pct / 100.0
    total_invested = 0.0
    total_fees_paid = 0.0
    total_shares = 0.0
    accumulated_cash_dividend = 0.0
    reinvested_dividend_shares = 0.0

    cashflows: list[tuple[date, float]] = []
    cashflow_records: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []

    peak_value_ratio = 1.0  # 组合市值 / 累计投入本金 的峰值或组合净值峰值
    max_drawdown_pct = 0.0
    peak_nav_like = 1.0

    for row in nav_series:
        dt_str = row["nav_date"][:10]
        dt_obj = date.fromisoformat(dt_str)
        unit_nav = float(row["unit_nav"])
        div_per_unit = float(row.get("dividend_per_unit") or 0.0)

        # 1) 若当日有分红且已有持仓份额
        if div_per_unit > 0 and total_shares > 0:
            div_cash = total_shares * div_per_unit
            if dividend_reinvest:
                add_shares = div_cash / unit_nav
                total_shares += add_shares
                reinvested_dividend_shares += add_shares
            else:
                accumulated_cash_dividend += div_cash

        # 2) 若当日命中定投扣款日
        if dt_str in schedule_dates:
            fee_amt = round(periodic_amount * fee_rate, 4)
            net_invest = periodic_amount - fee_amt
            bought_shares = net_invest / unit_nav

            total_invested = round(total_invested + periodic_amount, 2)
            total_fees_paid = round(total_fees_paid + fee_amt, 2)
            total_shares += bought_shares

            cashflows.append((dt_obj, -periodic_amount))
            cur_mv = round(total_shares * unit_nav + accumulated_cash_dividend, 2)
            avg_cost = round(total_invested / total_shares, 4) if total_shares > 0 else None
            cashflow_records.append(
                {
                    "period_no": len(cashflow_records) + 1,
                    "date": dt_str,
                    "unit_nav": round(unit_nav, 4),
                    "invest_amount": round(periodic_amount, 2),
                    "fee_amount": round(fee_amt, 2),
                    "shares_bought": round(bought_shares, 4),
                    "cumulative_invested": total_invested,
                    "cumulative_shares": round(total_shares, 4),
                    "average_cost": avg_cost,
                    "market_value": cur_mv,
                    "profit": round(cur_mv - total_invested, 2),
                    "return_pct": round((cur_mv / total_invested - 1.0) * 100.0, 2) if total_invested > 0 else 0.0,
                }
            )

        # 3) 每日跟踪定投账户总价值与最大回撤
        if total_invested > 0 and total_shares > 0:
            cur_total_val = total_shares * unit_nav + accumulated_cash_dividend
            # 采用资金加权净值比率 cur_total_val / total_invested 衡量持仓水位回撤
            val_ratio = cur_total_val / total_invested
            if val_ratio > peak_value_ratio:
                peak_value_ratio = val_ratio
            dd = (peak_value_ratio - val_ratio) / peak_value_ratio * 100.0
            if dd > max_drawdown_pct:
                max_drawdown_pct = dd

            # 同时跟踪标的自身净值最大回撤以确保风险充分揭示
            if unit_nav > peak_nav_like:
                peak_nav_like = unit_nav
            nav_dd = (peak_nav_like - unit_nav) / peak_nav_like * 100.0
            if nav_dd > max_drawdown_pct:
                max_drawdown_pct = nav_dd

    final_row = nav_series[-1]
    final_dt = date.fromisoformat(final_row["nav_date"][:10])
    final_nav = float(final_row["unit_nav"])
    final_market_value = round(total_shares * final_nav + accumulated_cash_dividend, 2)
    total_profit = round(final_market_value - total_invested, 2)
    total_return_pct = round((final_market_value / total_invested - 1.0) * 100.0, 2) if total_invested > 0 else 0.0
    average_holding_cost = round(total_invested / total_shares, 4) if total_shares > 0 else None

    # 期末视同赎回现金流以计算 XIRR
    xirr_cashflows = list(cashflows)
    if final_market_value > 0:
        xirr_cashflows.append((final_dt, final_market_value))
    xirr_decimal = compute_xirr(xirr_cashflows)
    irr_annualized_pct = round(xirr_decimal * 100.0, 2) if xirr_decimal is not None else None

    periods_per_year = 12.0 if frequency == "monthly" else (26.0 if frequency == "biweekly" else 52.0)
    periodic_irr_pct = (
        round(((1.0 + xirr_decimal) ** (1.0 / periods_per_year) - 1.0) * 100.0, 4)
        if xirr_decimal is not None and xirr_decimal > -0.99
        else None
    )

    # 生成精简月度/周期曲线供前端绘图
    for rec in cashflow_records:
        equity_curve.append(
            {
                "date": rec["date"],
                "cumulative_invested": rec["cumulative_invested"],
                "market_value": rec["market_value"],
                "unit_nav": rec["unit_nav"],
                "average_cost": rec["average_cost"],
                "return_pct": rec["return_pct"],
            }
        )

    return {
        "ok": True,
        "symbol": symbol,
        "name": fund_name,
        "parameters": {
            "periodic_amount": round(periodic_amount, 2),
            "frequency": frequency,
            "invest_day": invest_day,
            "start_date": nav_series[0]["nav_date"][:10],
            "end_date": final_row["nav_date"][:10],
            "subscription_fee_pct": subscription_fee_pct,
            "dividend_reinvest": dividend_reinvest,
        },
        "summary": {
            "total_periods": len(cashflow_records),
            "total_invested": round(total_invested, 2),
            "total_fees_paid": round(total_fees_paid, 2),
            "total_shares": round(total_shares, 4),
            "reinvested_dividend_shares": round(reinvested_dividend_shares, 4),
            "accumulated_cash_dividend": round(accumulated_cash_dividend, 2),
            "average_holding_cost": average_holding_cost,
            "final_nav": round(final_nav, 4),
            "final_market_value": final_market_value,
            "total_profit": total_profit,
            "total_return_pct": total_return_pct,
            "irr_annualized_pct": irr_annualized_pct,
            "periodic_irr_pct": periodic_irr_pct,
            "max_drawdown_pct": round(max_drawdown_pct, 2),
        },
        "equity_curve": equity_curve,
        "cashflow_records_head": cashflow_records[:6],
        "cashflow_records_tail": cashflow_records[-6:],
        "provider_meta": provider_meta,
        "disclaimer": MANDATORY_DCA_DISCLAIMER,
    }


def calculate_target_reverse_plan(
    *,
    target_amount: float = 100000.0,
    years: float = 3.0,
    frequency: FrequencyType = "monthly",
    assumed_annual_return_pct: float = 6.0,
    subscription_fee_pct: float = 0.10,
) -> dict[str, Any]:
    """目标金额反推计算器：给定未来目标金额与投资年限，在不同历史年化假设下反推每期需投入金额。"""
    if target_amount <= 0:
        raise ValueError("目标金额 `target_amount` 必须大于 0")
    if years <= 0:
        raise ValueError("投资期限 `years` 必须大于 0")

    periods_per_year = 12 if frequency == "monthly" else (26 if frequency == "biweekly" else 52)
    total_periods = max(1, int(round(years * periods_per_year)))
    fee_factor = max(0.0001, 1.0 - subscription_fee_pct / 100.0)

    def _solve_required_payment(annual_ret_pct: float) -> dict[str, Any]:
        r_annual = annual_ret_pct / 100.0
        # 期初定投（Annuity Due）：每期净投入 P * fee_factor，复利计息
        r_period = (1.0 + r_annual) ** (1.0 / periods_per_year) - 1.0
        if abs(r_period) < 1e-9:
            fv_factor = float(total_periods)
        else:
            fv_factor = ((1.0 + r_period) ** total_periods - 1.0) / r_period * (1.0 + r_period)

        required_periodic = target_amount / (fv_factor * fee_factor)
        total_principal = required_periodic * total_periods
        compound_gain = target_amount - total_principal
        return {
            "assumed_annual_return_pct": round(annual_ret_pct, 2),
            "total_periods": total_periods,
            "required_periodic_amount": round(required_periodic, 2),
            "total_principal_needed": round(total_principal, 2),
            "estimated_compound_gain": round(compound_gain, 2),
            "principal_share_pct": round(total_principal / target_amount * 100.0, 2),
        }

    primary_plan = _solve_required_payment(assumed_annual_return_pct)
    scenario_matrix = [
        _solve_required_payment(3.0),
        _solve_required_payment(6.0),
        _solve_required_payment(8.0),
        _solve_required_payment(10.0),
    ]

    return {
        "ok": True,
        "target_amount": round(target_amount, 2),
        "years": years,
        "frequency": frequency,
        "subscription_fee_pct": subscription_fee_pct,
        "primary_plan": primary_plan,
        "scenario_matrix": scenario_matrix,
        "disclaimer": MANDATORY_DCA_DISCLAIMER,
    }


def calculate_dca_vs_lump_sum(
    *,
    symbol: str = "510300.SH",
    periodic_amount: float = 2000.0,
    frequency: FrequencyType = "monthly",
    invest_day: int = 15,
    start_date: str = "2023-11-01",
    end_date: str = "2026-11-13",
    subscription_fee_pct: float = 0.10,
    dividend_reinvest: bool = True,
    nav_series: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """定投（DCA）vs 期初一次性买入（Lump Sum）同区间客观对比。"""
    dca_res = calculate_etf_dca_backtest(
        symbol=symbol,
        periodic_amount=periodic_amount,
        frequency=frequency,
        invest_day=invest_day,
        start_date=start_date,
        end_date=end_date,
        subscription_fee_pct=subscription_fee_pct,
        dividend_reinvest=dividend_reinvest,
        nav_series=nav_series,
    )
    if not dca_res.get("ok"):
        return dca_res

    if nav_series is None:
        router = get_default_broker_router()
        nav_env = router.get_fund_nav(symbol=symbol, start_date=start_date, end_date=end_date)
        nav_series = (nav_env.data or {}).get("nav_series") or []

    dca_sum = dca_res["summary"]
    lump_principal = float(dca_sum["total_invested"])
    fee_rate = subscription_fee_pct / 100.0
    lump_fee = round(lump_principal * fee_rate, 2)
    init_nav = float(nav_series[0]["unit_nav"])
    final_nav = float(nav_series[-1]["unit_nav"])
    lump_shares = (lump_principal - lump_fee) / init_nav
    lump_cash_div = 0.0

    peak_val = lump_principal
    lump_max_dd = 0.0
    for row in nav_series:
        u_nav = float(row["unit_nav"])
        div_u = float(row.get("dividend_per_unit") or 0.0)
        if div_u > 0 and lump_shares > 0:
            div_cash = lump_shares * div_u
            if dividend_reinvest:
                lump_shares += div_cash / u_nav
            else:
                lump_cash_div += div_cash
        cur_val = lump_shares * u_nav + lump_cash_div
        if cur_val > peak_val:
            peak_val = cur_val
        dd = (peak_val - cur_val) / peak_val * 100.0 if peak_val > 0 else 0.0
        if dd > lump_max_dd:
            lump_max_dd = dd

    lump_final_mv = round(lump_shares * final_nav + lump_cash_div, 2)
    lump_profit = round(lump_final_mv - lump_principal, 2)
    lump_return_pct = round((lump_final_mv / lump_principal - 1.0) * 100.0, 2) if lump_principal > 0 else 0.0

    d0 = date.fromisoformat(nav_series[0]["nav_date"][:10])
    d1 = date.fromisoformat(nav_series[-1]["nav_date"][:10])
    yrs = max(0.01, (d1 - d0).days / 365.0)
    lump_cagr_pct = round(((lump_final_mv / lump_principal) ** (1.0 / yrs) - 1.0) * 100.0, 2) if lump_principal > 0 else 0.0

    cost_diff_pct = round((dca_sum["average_holding_cost"] / init_nav - 1.0) * 100.0, 2) if init_nav > 0 else 0.0

    return {
        "ok": True,
        "symbol": symbol,
        "name": dca_res["name"],
        "dca_summary": dca_sum,
        "lump_sum_summary": {
            "total_invested": round(lump_principal, 2),
            "initial_nav": round(init_nav, 4),
            "final_nav": round(final_nav, 4),
            "total_shares": round(lump_shares, 4),
            "final_market_value": lump_final_mv,
            "total_profit": lump_profit,
            "total_return_pct": lump_return_pct,
            "annualized_cagr_pct": lump_cagr_pct,
            "max_drawdown_pct": round(lump_max_dd, 2),
        },
        "comparison_insights": {
            "dca_avg_cost_vs_initial_nav_pct": cost_diff_pct,
            "drawdown_reduction_pct": round(lump_max_dd - dca_sum["max_drawdown_pct"], 2),
            "objective_interpretation": (
                f"在 {nav_series[0]['nav_date'][:10]} 至 {nav_series[-1]['nav_date'][:10]} 期间，"
                f"定投累计投入 {dca_sum['total_invested']:.2f} 元，持仓均价 {dca_sum['average_holding_cost']:.4f} 元/份，"
                f"期末市值 {dca_sum['final_market_value']:.2f} 元（IRR {dca_sum['irr_annualized_pct']}%，最大回撤 {dca_sum['max_drawdown_pct']}%）；"
                f"同等本金期初一次性买入期末市值 {lump_final_mv:.2f} 元（年化 {lump_cagr_pct}%，最大回撤 {lump_max_dd:.2f}%）。"
                "定投通过分批建仓平滑了单次择时成本波动，但不能消除市场系统性风险。"
            ),
        },
        "disclaimer": MANDATORY_DCA_DISCLAIMER,
    }


def calculate_etf_dca_full_plan(
    *,
    symbol: str = "510300.SH",
    periodic_amount: float = 2000.0,
    frequency: FrequencyType = "monthly",
    invest_day: int = 15,
    start_date: str = "2023-11-01",
    end_date: str = "2026-11-13",
    subscription_fee_pct: float = 0.10,
    dividend_reinvest: bool = True,
    target_amount: float = 100000.0,
    target_years: float = 3.0,
    assumed_annual_return_pct: float = 6.0,
) -> dict[str, Any]:
    """一站式生成 ETF 定投完整方案（含正向回测、目标反推、定投 vs 一次性买入对比、ETF 费率与风险揭示）。"""
    router = get_default_broker_router()
    quote_env = router.get_etf_quote(symbol=symbol)
    fee_env = router.get_fund_fee(symbol=symbol)
    nav_env = router.get_fund_nav(symbol=symbol, start_date=start_date, end_date=end_date)
    risk_env = router.get_risk_disclosure("ETF_DCA")

    nav_series = (nav_env.data or {}).get("nav_series") if isinstance(nav_env.data, dict) else None

    backtest = calculate_etf_dca_backtest(
        symbol=symbol,
        periodic_amount=periodic_amount,
        frequency=frequency,
        invest_day=invest_day,
        start_date=start_date,
        end_date=end_date,
        subscription_fee_pct=subscription_fee_pct,
        dividend_reinvest=dividend_reinvest,
        nav_series=nav_series,
    )
    reverse_plan = calculate_target_reverse_plan(
        target_amount=target_amount,
        years=target_years,
        frequency=frequency,
        assumed_annual_return_pct=assumed_annual_return_pct,
        subscription_fee_pct=subscription_fee_pct,
    )
    vs_lump = calculate_dca_vs_lump_sum(
        symbol=symbol,
        periodic_amount=periodic_amount,
        frequency=frequency,
        invest_day=invest_day,
        start_date=start_date,
        end_date=end_date,
        subscription_fee_pct=subscription_fee_pct,
        dividend_reinvest=dividend_reinvest,
        nav_series=nav_series,
    )

    return {
        "ok": True,
        "symbol": symbol,
        "etf_quote": quote_env.data,
        "fund_fee": fee_env.data,
        "backtest": backtest,
        "target_reverse_plan": reverse_plan,
        "dca_vs_lump_sum": {
            "lump_sum_summary": vs_lump.get("lump_sum_summary"),
            "comparison_insights": vs_lump.get("comparison_insights"),
        },
        "risk_disclosure": risk_env.data,
        "disclaimer": MANDATORY_DCA_DISCLAIMER,
    }
