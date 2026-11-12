"""M2 多格式文档导入专项测试套件（Excel / CSV / Word / Markdown + 持仓一键导入接口）。"""
from __future__ import annotations

import io
from pathlib import Path
import sys

# ── sys.path 归一 ──
_API_ROOT = Path(__file__).resolve().parents[1]
for _p in ("", "/", str(_API_ROOT)):
    while _p in sys.path:
        sys.path.remove(_p)
sys.path.insert(0, str(_API_ROOT))
_bad_app = sys.modules.get("app")
if _bad_app is not None and Path(getattr(_bad_app, "__file__", "") or "").parent == _API_ROOT:
    del sys.modules["app"]

import pandas as pd

from app.services.doc_parser import parse_uploaded_file


def test_excel_multi_sheet_and_gbk_csv_portfolio():
    """验证 Excel 多 Sheet 解析、pandas 统计准确性、无 NaN 泄漏以及 GBK CSV 交割单识别。"""
    df_sheet1 = pd.DataFrame(
        {
            "证券代码": ["600519", "2594", "300750"],
            "证券名称": ["贵州茅台", "比亚迪", "宁德时代"],
            "持仓数量": [100, 500, 300],
            "成本价": [1450.0, 240.0, 190.0],
            "备注": [None, "核心仓", None],
        }
    )
    df_sheet2 = pd.DataFrame(
        {
            "指标": ["ROE", "毛利率"],
            "2025Q2": [16.8, 89.5],
            "2024Q2": [16.2, 89.1],
        }
    )
    x_buf = io.BytesIO()
    with pd.ExcelWriter(x_buf, engine="openpyxl") as writer:
        df_sheet1.to_excel(writer, sheet_name="持仓明细", index=False)
        df_sheet2.to_excel(writer, sheet_name="财务对比", index=False)

    doc = parse_uploaded_file(x_buf.getvalue(), "多工作表组合分析.xlsx")
    assert doc.total_pages == 2
    assert doc.table_count == 2
    assert "NaN" not in doc.summary_md
    assert "—" in doc.summary_md
    assert doc.extra_meta.get("is_portfolio_sheet") is True
    rows = doc.extra_meta.get("portfolio_rows") or []
    assert rows[1]["code"] == "002594"
    assert rows[0]["shares"] == 100
    assert rows[0]["cost_price"] == 1450.0

    # GBK 编码 CSV 交割单测试
    csv_gbk = "股票代码,股票名称,可用股数,买入均价\n601899,紫金矿业,1000,16.55\n00700,腾讯控股,200,385.20\n".encode("gb18030")
    doc_csv = parse_uploaded_file(csv_gbk, "券商导出交割单.csv")
    assert doc_csv.extra_meta.get("is_portfolio_sheet") is True
    assert len(doc_csv.extra_meta.get("portfolio_rows") or []) == 2


def test_word_and_markdown_parsing():
    """验证 Word 章节与表格还原、Markdown 策略草稿检测。"""
    from docx import Document

    d = Document()
    d.add_heading("一、调研纪要背景", level=1)
    d.add_paragraph("调研对象：贵州茅台(600519)，渠道反馈批价企稳。")
    d.add_heading("二、核心财务预测表", level=2)
    tb = d.add_table(rows=2, cols=3)
    tb.cell(0, 0).text = "年度"
    tb.cell(0, 1).text = "营收预测"
    tb.cell(0, 2).text = "净利预测"
    tb.cell(1, 0).text = "2025E"
    tb.cell(1, 1).text = "1,740亿"
    tb.cell(1, 2).text = "862亿"
    buf = io.BytesIO()
    d.save(buf)

    parsed_docx = parse_uploaded_file(buf.getvalue(), "茅台实地调研纪要.docx")
    assert parsed_docx.table_count == 1
    assert "| 年度 | 营收预测 | 净利预测 |" in parsed_docx.summary_md
    assert any(s["code"] == "600519" for s in parsed_docx.detected_stocks)

    md_bytes = (
        "# 趋势突破选股策略\n"
        "## 1. 买入条件\n"
        "当 MA20 金叉 MA60 且 RSI > 50、MACD 红柱放大时买入。\n"
        "## 2. 卖出条件\n"
        "跌破止损位 7% 或触发动态止盈退出。\n"
    ).encode("utf-8")
    parsed_md = parse_uploaded_file(md_bytes, "趋势突破策略.md")
    assert parsed_md.extra_meta.get("has_strategy_draft") is True
