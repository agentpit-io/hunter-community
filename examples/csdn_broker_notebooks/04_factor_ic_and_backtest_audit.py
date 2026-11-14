#!/usr/bin/env python3
"""实战 04：因子 IC 分层检验与回测未来函数 / 幸存者偏差质量审计。

对应 CSDN × 证券公司开发者实战示例包（M5-2）：
- 调用 `run_factor_research_analysis` 执行低市盈率叠加高股息因子（`low_pe_high_div`）的
  IC 均值、Rank IC、IR、5 层分组（Q1~Q5）单调性回测与行业拥挤度分位计算；
- 调用 `run_backtest_quality_audit` 对量化回测开展 6 大维度防坑审计：
  ① 是否按 `published_at <= as_of` 严格使用 PIT 财报（防前视偏差/未来函数）
  ② 是否包含已退市股票与历史时点指数成分（防幸存者偏差）
  ③ 是否显式声明复权因子
  ④ 是否扣除 A 股印花税、佣金与滑点
  ⑤ 是否过滤一字涨跌停不可成交
  ⑥ 是否过滤停牌日虚假成交。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.services.workflows import (  # noqa: E402
    run_backtest_quality_audit,
    run_factor_research_analysis,
)


def run_demo() -> dict:
    # 1. 运行因子 IC / Rank IC 与 5 层分层回测
    factor_res = run_factor_research_analysis(
        factor_id="low_pe_high_div",
        universe="000300.SH",
        neutralize_industry=True,
    )

    # 2. 审计一个合格的 PIT 回测配置
    audit_pass = run_backtest_quality_audit(
        strategy_name="沪深300低估值高股息月度轮动（合规PIT版）",
        use_pit_fundamentals=True,
        include_delisted_stocks=True,
        adjustment_type="前复权",
        commission_bps=3.0,
        stamp_duty_bps=5.0,
        slippage_bps=10.0,
        filter_limit_up_down=True,
        filter_halted_stocks=True,
    )

    # 3. 审计一个存在未来函数与幸存者偏差的缺陷回测配置（对比演示）
    audit_flawed = run_backtest_quality_audit(
        strategy_name="未对齐披露时间且剔除退市股的缺陷回测",
        use_pit_fundamentals=False,
        include_delisted_stocks=False,
        adjustment_type="不复权",
        commission_bps=0.0,
        stamp_duty_bps=0.0,
        slippage_bps=0.0,
        filter_limit_up_down=False,
        filter_halted_stocks=False,
    )

    return {
        "example": "04_factor_ic_and_backtest_audit",
        "factor_analysis": {
            "factor_id": factor_res["factor_id"],
            "factor_name": factor_res["factor_name"],
            "metrics": factor_res["metrics"],
            "q1_return_pct": factor_res["layer_backtest"][0]["annualized_return_pct"],
            "q5_return_pct": factor_res["layer_backtest"][-1]["annualized_return_pct"],
            "three_column_evidence": factor_res["three_column_evidence"],
        },
        "compliant_backtest_audit": {
            "strategy_name": audit_pass["strategy_name"],
            "audit_score": audit_pass["audit_score"],
            "verdict": audit_pass["verdict"],
            "passed_checks": sum(1 for c in audit_pass["checks"] if c["status"] == "PASS"),
        },
        "flawed_backtest_audit": {
            "strategy_name": audit_flawed["strategy_name"],
            "audit_score": audit_flawed["audit_score"],
            "verdict": audit_flawed["verdict"],
            "failed_checks": [
                {"check_id": c["check_id"], "dimension": c["dimension"], "status": c["status"]}
                for c in audit_flawed["checks"]
                if c["status"] != "PASS"
            ],
        },
        "disclaimer": factor_res["disclaimer"],
    }


if __name__ == "__main__":
    output = run_demo()
    print(json.dumps(output, ensure_ascii=False, indent=2))
