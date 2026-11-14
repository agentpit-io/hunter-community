"""单元测试：M5-2 CSDN × 证券公司金融数据实战示例包 (`examples/csdn_broker_notebooks/`)。"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
NB_DIR = REPO_ROOT / "examples" / "csdn_broker_notebooks"


def _load_and_run(script_name: str) -> dict:
    script_path = NB_DIR / script_name
    assert script_path.is_file(), f"缺少实战脚本 {script_path}"
    ipynb_path = script_path.with_suffix(".ipynb")
    assert ipynb_path.is_file(), f"缺少配套 Notebook {ipynb_path}"
    nb_json = json.loads(ipynb_path.read_text(encoding="utf-8"))
    assert nb_json.get("nbformat") == 4
    assert len(nb_json.get("cells", [])) >= 2

    spec = importlib.util.spec_from_file_location(script_path.stem, script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.run_demo()


def test_01_broker_market_and_calendar_example() -> None:
    res = _load_and_run("01_broker_market_and_calendar.py")
    assert res["example"] == "01_broker_market_and_calendar"
    assert res["calendar_days"] == 7
    assert res["quote_600519"]["symbol"] == "600519.SH"
    assert res["quote_600519"]["last_price"] > 0
    assert res["quote_600519"]["as_of"] != res["quote_600519"]["retrieved_at"]
    assert res["kline_qfq_bars"] >= 60
    assert res["suspended_check"]["halted"] is True
    assert res["suspended_check"]["last_price"] is None


def test_02_56_technical_indicators_example() -> None:
    res = _load_and_run("02_56_technical_indicators.py")
    assert res["example"] == "02_56_technical_indicators"
    assert res["total_catalog_indicators"] == 56
    assert res["computed_indicators_count"] == 56
    assert len(res["counts_by_category"]) == 7
    assert "MA20" in res["sample_indicators"]
    assert "MACD" in res["sample_indicators"]


def test_03_90_fundamental_scorecard_and_disclosure_example() -> None:
    res = _load_and_run("03_90_fundamental_scorecard_and_disclosure.py")
    assert res["example"] == "03_90_fundamental_scorecard_and_disclosure"
    assert res["total_catalog_metrics"] == 90
    assert res["computed_metrics_count"] == 90
    assert res["overall_score"] > 0
    assert "第1页" in res["pdf_disclosure_trace"]["citation_tag"]


def test_04_factor_ic_and_backtest_audit_example() -> None:
    res = _load_and_run("04_factor_ic_and_backtest_audit.py")
    assert res["example"] == "04_factor_ic_and_backtest_audit"
    assert res["factor_analysis"]["q5_return_pct"] > res["factor_analysis"]["q1_return_pct"]
    assert res["compliant_backtest_audit"]["audit_score"] == 100
    assert res["flawed_backtest_audit"]["audit_score"] < 50
    assert len(res["flawed_backtest_audit"]["failed_checks"]) >= 4


def test_05_etf_dca_and_daily_workflow_example() -> None:
    res = _load_and_run("05_etf_dca_and_daily_workflow.py")
    assert res["example"] == "05_etf_dca_and_daily_workflow"
    assert res["etf_dca_plan"]["symbol"] == "510300.SH"
    assert res["etf_dca_plan"]["backtest_summary"]["total_periods"] >= 36
    assert "历史表现不代表未来收益" in res["etf_dca_plan"]["disclaimer"]
    assert res["pre_market_workflow"]["number_verification_ok"] is True
    assert res["after_close_workflow"]["number_verification_ok"] is True
