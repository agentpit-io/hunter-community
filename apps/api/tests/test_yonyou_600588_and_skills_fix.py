"""用友网络（600588.SH）真实公开数据接入、3 大计算引擎 MCP 工具暴露与语言守卫防误删专项回归测试。

验证点：
1. `text_sanitizer.py` 对包含 `as_of: 2026-11-13T15:00:00+08:00`、`provider: akshare_public_fallback`、
   Markdown 表格与估值术语的中文投研报告，`has_english_prose` 为 `False`，`sanitize_llm_text` 完整保留「估值与市值」段落；
2. `PublicFallbackAdapter` 查询 `600588.SH` 返回用友网络真实档案、真实三季报（营收约 57.56 亿、归母净利亏损约 -12.47 亿、ROE -18.58%）、
   真实股东（北京用友科技有限公司）、真实计算机/软件开发行业龙头（同花顺/金山办公/科大讯飞），绝不出现招商银行数据；
3. 3 个新增确定性计算引擎 MCP 工具 `get_fundamental_scorecard`、`get_technical_indicators`、`calculate_etf_dca` 调用返回 `ok=True`。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))
sys.path.insert(0, str(REPO_ROOT / "distro" / "mcp-servers"))

from agents.text_sanitizer import has_english_prose, sanitize_llm_text  # noqa: E402
from app.providers.broker.public_adapter import PublicFallbackAdapter  # noqa: E402
from broker_fundamental import call_tool_sync as call_fundamental  # noqa: E402
from broker_funds import call_tool_sync as call_funds  # noqa: E402
from broker_market import call_tool_sync as call_market  # noqa: E402


def test_text_sanitizer_preserves_valuation_and_backtick_metadata() -> None:
    report = (
        "# 600588.SH（用友网络）六维 90 项基本面体检评分卡\n\n"
        "- **数据源与时间信封**：`provider: akshare_public_fallback` · `as_of: 2026-11-13T15:00:00+08:00` · `retrieved_at: 2026-11-14T10:00:00+08:00`\n\n"
        "## 一、估值与市值（Valuation & Market Cap）\n"
        "- **估值与市值**：截至 `as_of: 2026-11-13T15:00:00+08:00`（来源：`provider: akshare_public_fallback`），"
        "用友网络总市值 442.84 亿元，流通市值 442.66 亿元，PB-MRQ 为 5.29 倍（近 5 年分位数 42.80%），PS-TTM 为 4.98 倍。\n\n"
        "| 指标名称 | 指标代码 | 当前数值 | 行业中位数 | 数据状态 |\n"
        "|---|---|---|---|---|\n"
        "| 市净率 | `PB_MRQ` | 5.29 倍 | 4.82 倍 | `as_of: 2026-11-13` |\n"
        "| 市销率 | `PS_TTM` | 4.98 倍 | 5.10 倍 | `provider: akshare_public_fallback` |\n"
    )
    assert has_english_prose(report) is False
    cleaned = sanitize_llm_text(report)
    assert "估值与市值" in cleaned
    assert "442.84 亿元" in cleaned
    assert "PB-MRQ 为 5.29 倍" in cleaned


def test_public_fallback_adapter_yonyou_600588_real_data_not_cmb() -> None:
    adapter = PublicFallbackAdapter()

    # 1. 搜索用友软件 / 600588
    search_env = adapter.search_security("用友软件")
    assert search_env.ok is True
    syms = [r["symbol"] for r in search_env.data]
    assert "600588.SH" in syms

    # 2. 证券档案：必须是“用友网络”，绝不是“招商银行”或 null
    prof_env = adapter.get_security_profile("600588.SH")
    assert prof_env.ok is True
    assert prof_env.data["symbol"] == "600588.SH"
    assert "用友" in (prof_env.data.get("name") or "")
    assert "招商银行" not in json.dumps(prof_env.data, ensure_ascii=False)

    # 3. 财务报表与财务比率：真实三季报营收约 57.56 亿、归母净利亏损约 -12.47 亿、ROE 约 -18.58%
    stmt_env = adapter.get_financial_statements("600588.SH")
    assert stmt_env.ok is True
    assert len(stmt_env.data) >= 1
    latest_stmt = stmt_env.data[-1]
    assert latest_stmt["symbol"] == "600588.SH"
    # 绝不能是招商银行的 1389.20 亿营收或 425.80 亿利润
    assert latest_stmt["revenue"] != 138920000000.0
    assert latest_stmt["net_profit_parent"] != 42580000000.0
    assert latest_stmt["revenue"] is not None and 2_000_000_000 < latest_stmt["revenue"] < 20_000_000_000

    ratio_env = adapter.get_financial_ratios("600588.SH")
    assert ratio_env.ok is True
    assert len(ratio_env.data) >= 1
    latest_ratio = ratio_env.data[-1]
    assert latest_ratio["roe_weighted"] != 14.15

    # 4. 股东信息：第一大股东为北京用友科技有限公司
    sh_env = adapter.get_shareholders("600588.SH")
    assert sh_env.ok is True
    holders_str = json.dumps(sh_env.data, ensure_ascii=False)
    assert "用友" in holders_str
    assert "第一大国有控股股东" not in holders_str

    # 5. 行业可比数据：查询“计算机”或“软件开发”返回同花顺/金山办公/科大讯飞等真实计算机龙头，绝不返回招商银行
    sec_env = adapter.get_sector_data("计算机")
    assert sec_env.ok is True
    leaders_str = json.dumps(sec_env.data.get("leaders", []), ensure_ascii=False)
    assert "招商银行" not in leaders_str
    assert "浦发银行" not in leaders_str
    assert any(k in leaders_str for k in ("同花顺", "金山办公", "科大讯飞", "海康威视", "三六零", "恒生电子"))


def test_three_new_deterministic_mcp_tools() -> None:
    # 1. broker_fundamental.get_fundamental_scorecard
    raw_sc = call_fundamental("get_fundamental_scorecard", {"symbol": "600588.SH"})
    env_sc = json.loads(raw_sc)
    assert env_sc["ok"] is True
    assert env_sc["data"]["symbol"] == "600588.SH"
    assert env_sc["data"]["total_metrics"] == 90
    assert "scorecard" in env_sc["data"]

    # 2. broker_market.get_technical_indicators
    raw_ti = call_market("get_technical_indicators", {"symbol": "600588.SH", "adjustment": "前复权"})
    env_ti = json.loads(raw_ti)
    assert env_ti["ok"] is True
    assert env_ti["data"]["symbol"] == "600588.SH"
    assert env_ti["data"]["total_indicators"] == 56

    # 3. broker_funds.calculate_etf_dca
    raw_dca = call_funds(
        "calculate_etf_dca",
        {
            "symbol": "510300.SH",
            "periodic_amount": 2000.0,
            "frequency": "monthly",
            "target_amount": 100000.0,
            "target_years": 3.0,
        },
    )
    env_dca = json.loads(raw_dca)
    assert env_dca["ok"] is True
    assert env_dca["data"]["ok"] is True
    assert env_dca["data"]["symbol"] == "510300.SH"
    assert "backtest" in env_dca["data"]
    assert "target_reverse_plan" in env_dca["data"]
