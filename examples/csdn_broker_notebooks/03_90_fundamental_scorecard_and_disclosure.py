#!/usr/bin/env python3
"""实战 03：6 大维度 90 项基本面体检与 PDF 公告页码溯源解读。

对应 CSDN × 证券公司开发者实战示例包（M5-2）：
- 演示通过 `broker_fundamental` 获取三大财务报表（支持 `as_of` Point-in-Time 防前视偏差过滤）；
- 调用 `calculate_all_90_fundamentals` 纯代码计算盈利能力、成长能力、偿债与安全性、
  现金流质量、估值水平、公司治理与股东共 6 大维度 90 项指标；
- 演示通过 `broker_disclosure` 检索并拉取公司公告 PDF 页码段落，生成 `[来源：公告标题 · 第X页]` 溯源引用。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.providers.broker import get_default_broker_router  # noqa: E402
from app.services.fundamentals import (  # noqa: E402
    calculate_all_90_fundamentals,
    default_fundamental_raw_input,
    get_fundamental_catalog_summary,
)


def run_demo(symbol: str = "600036.SH") -> dict:
    router = get_default_broker_router()

    # 1. Point-in-Time 财务报表查询（验证 published_at <= as_of）
    pit_fin_env = router.get_financial_statements(
        symbol=symbol,
        report_period="2026Q3",
        as_of="2026-11-13",
    )

    # 2. 运行 90 项基本面体检计算引擎（含重述标注与六维评分卡）
    raw_input = default_fundamental_raw_input(symbol, restated=True)
    scorecard = calculate_all_90_fundamentals(raw_input, symbol=symbol)
    catalog_summary = get_fundamental_catalog_summary()

    # 3. 检索最新公告并定位 PDF 原文页码
    ann_list_env = router.search_announcements(symbol=symbol)
    ann_items = ann_list_env.data if isinstance(ann_list_env.data, list) else (ann_list_env.data or {}).get("announcements", [])
    first_ann_id = ann_items[0]["announcement_id"] if ann_items else "ANN-20261028-600036"
    ann_detail_env = router.get_announcement(announcement_id=first_ann_id, page_range="1-2")
    ann_data = ann_detail_env.data or {}

    pages = ann_data.get("pages", [])
    first_page_no = pages[0].get("page", 1) if pages else 1
    first_page_text = pages[0].get("text", "") if pages else str(ann_data.get("summary", ""))
    citation = f"[来源：{ann_data.get('title', '2026年第三季度报告')} · 第{first_page_no}页]"

    pit_items = pit_fin_env.data if isinstance(pit_fin_env.data, list) else [pit_fin_env.data or {}]
    pit_first = pit_items[0] if pit_items else {}

    return {
        "example": "03_90_fundamental_scorecard_and_disclosure",
        "symbol": symbol,
        "report_period": scorecard["report_period"],
        "published_at": scorecard["published_at"],
        "basis_label": scorecard["basis_label"],
        "total_catalog_metrics": catalog_summary["total_count"],
        "computed_metrics_count": scorecard["computed_metrics"],
        "overall_score": scorecard["scorecard"]["overall_score"],
        "overall_grade": scorecard["scorecard"]["overall_grade"],
        "dimension_scores": scorecard["scorecard"]["dimensions"],
        "pit_statement_check": {
            "report_period": pit_first.get("report_period") or pit_fin_env.meta.report_period,
            "published_at": pit_first.get("published_at") or pit_fin_env.meta.published_at,
        },
        "pdf_disclosure_trace": {
            "announcement_id": ann_data.get("announcement_id", first_ann_id),
            "title": ann_data.get("title"),
            "total_pages": ann_data.get("total_pages"),
            "citation_tag": citation,
            "page_excerpt": first_page_text[:120],
        },
        "disclaimer": "本基本面评分卡与公告摘要仅供研究参考，不构成任何投资建议。",
    }


if __name__ == "__main__":
    output = run_demo()
    print(json.dumps(output, ensure_ascii=False, indent=2))
