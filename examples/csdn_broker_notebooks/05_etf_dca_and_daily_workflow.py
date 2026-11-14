#!/usr/bin/env python3
"""实战 05：ETF 定投正向/反推测算与盘前盘后工作流编排。

对应 CSDN × 证券公司开发者实战示例包（M5-2）：
- 调用 `calculate_etf_dca_full_plan` 对沪深300 ETF (`510300.SH`) 执行每月定投 2000 元、期限 3 年、
  申购费率 0.1% 的正向历史回测、10万目标金额反推与一次性买入对比（强制附带历史假设免责声明）；
- 调用 `run_pre_market_workflow`（08:30 盘前观察卡）与 `run_after_close_workflow`（15:30 盘后复盘卡），
  演示每日三段式工作流的端到端编排与 `fin_memory` 经验沉淀。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.services.etf_dca import (  # noqa: E402
    MANDATORY_DCA_DISCLAIMER,
    calculate_etf_dca_full_plan,
)
from app.services.workflows import (  # noqa: E402
    run_after_close_workflow,
    run_pre_market_workflow,
)


def run_demo() -> dict:
    # 1. ETF 定投正向测算 + 目标反推 + 一次性买入对比
    dca_plan = calculate_etf_dca_full_plan(
        symbol="510300.SH",
        periodic_amount=2000.0,
        frequency="monthly",
        invest_day=15,
        start_date="2023-11-01",
        end_date="2026-11-13",
        subscription_fee_pct=0.10,
        dividend_reinvest=True,
        target_amount=100000.0,
        target_years=3.0,
        assumed_annual_return_pct=6.0,
    )

    # 2. 08:30 盘前观察卡工作流
    pre_market = run_pre_market_workflow(
        market="CN_A",
        trade_date="2026-11-13",
        watchlist_symbols=["600036.SH", "600519.SH", "600898.SH"],
    )

    # 3. 15:30 盘后复盘与 fin_memory 证据沉淀工作流
    after_close = run_after_close_workflow(
        market="CN_A",
        trade_date="2026-11-13",
        watchlist_symbols=["600036.SH", "600519.SH"],
    )

    return {
        "example": "05_etf_dca_and_daily_workflow",
        "etf_dca_plan": {
            "symbol": dca_plan["symbol"],
            "backtest_summary": dca_plan["backtest"]["summary"],
            "dca_vs_lump_sum": dca_plan["dca_vs_lump_sum"],
            "target_reverse_plan": dca_plan["target_reverse_plan"]["primary_plan"],
            "disclaimer": dca_plan["disclaimer"],
        },
        "pre_market_workflow": {
            "workflow": pre_market["workflow"],
            "trade_date": pre_market["trade_date"],
            "skills_invoked": pre_market["skills_invoked"],
            "three_column_evidence": pre_market["three_column_evidence"],
            "number_verification_ok": pre_market["number_verification"]["ok"],
        },
        "after_close_workflow": {
            "workflow": after_close["workflow"],
            "trade_date": after_close["trade_date"],
            "skills_invoked": after_close["skills_invoked"],
            "fin_memory_receipt": after_close["fin_memory_receipt"],
            "number_verification_ok": after_close["number_verification"]["ok"],
        },
        "disclaimer": MANDATORY_DCA_DISCLAIMER,
    }


if __name__ == "__main__":
    output = run_demo()
    print(json.dumps(output, ensure_ascii=False, indent=2))
