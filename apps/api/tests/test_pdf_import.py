"""M0 & M2 自动化测试套件：PDF / Excel / CSV / Word / Markdown 导入、表格还原、特殊负号修复、
长文档双档分流、扫描页 L2 OCR 兜底、股票实体识别、多租户归属隔离、关键词检索与翻页接口测试。
"""
from __future__ import annotations

import io
import os
from pathlib import Path
import sys
import time

# ── sys.path 归一 ──
_API_ROOT = Path(__file__).resolve().parents[1]
for _p in ("", "/", str(_API_ROOT)):
    while _p in sys.path:
        sys.path.remove(_p)
sys.path.insert(0, str(_API_ROOT))
_bad_app = sys.modules.get("app")
if _bad_app is not None and Path(getattr(_bad_app, "__file__", "") or "").parent == _API_ROOT:
    del sys.modules["app"]

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pandas as pd
import pymupdf
import pytest

from app.routers import chat_files, internal_tools, portfolio
from app.services.doc_parser import (
    extract_stocks_from_text,
    parse_docx_bytes,
    parse_excel_bytes,
    parse_md_bytes,
    parse_pdf_bytes,
    parse_uploaded_file,
)


def _build_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(chat_files.router, prefix="/api")
    app.include_router(internal_tools.router, prefix="/api")
    app.include_router(portfolio.router, prefix="/api")
    return app


def _make_sample_fin_pdf_bytes() -> bytes:
    """用 PyMuPDF 动态生成一份包含 3 页正文、合并利润表（含特殊负号 −12.45%）、断字 '贵 州 茅 台' 及关联股票的真实 PDF。"""
    doc = pymupdf.open()
    font = pymupdf.Font("cjk")

    # 第 1 页：研报概览与断字测试
    p1 = doc.new_page(width=595, height=842)
    tw1 = pymupdf.TextWriter(p1.rect)
    tw1.append((50, 50), "某某证券请务必阅读正文之后的免责声明", font=font, fontsize=9)
    tw1.append((50, 90), "第一节 投资核心结论", font=font, fontsize=15)
    tw1.append(
        (50, 125),
        "核心标的：贵 州 茅 台（600519）与 宁德时代(300750) 2025年半年度财务跟踪。",
        font=font,
        fontsize=11,
    )
    tw1.append(
        (50, 155),
        "本期海外可比公司 英伟达(NVDA) 及 腾讯控股(00700.HK) 算力与消费韧性显著。",
        font=font,
        fontsize=11,
    )
    tw1.append(
        (50, 185),
        "其中某细分业务同比变动为 −1,234.56 万元（折合同比 −12.45%），系季节性波动。",
        font=font,
        fontsize=11,
    )
    tw1.write_text(p1)

    # 第 2 页：带真实网格边框的 4×4 合并利润表
    p2 = doc.new_page(width=595, height=842)
    tw2 = pymupdf.TextWriter(p2.rect)
    tw2.append((50, 50), "某某证券请务必阅读正文之后的免责声明", font=font, fontsize=9)
    tw2.append((50, 85), "第二节 合并利润表核心数据", font=font, fontsize=14)

    rows = [
        ["项目", "2025年半年度", "2024年半年度", "同比变动"],
        ["营业收入(亿元)", "834.51", "709.87", "+17.56%"],
        ["归属于上市公司股东的净利润", "416.96", "359.80", "+15.88%"],
        ["存货跌价准备变动", "−12.45%", "−5.20%", "−7.25%"],
    ]
    x0, y0 = 50, 115
    col_w = [170, 110, 110, 100]
    row_h = 28

    shape = p2.new_shape()
    for r_idx, row in enumerate(rows):
        cy = y0 + r_idx * row_h
        cx = x0
        for c_idx, cell_text in enumerate(row):
            w = col_w[c_idx]
            rect = pymupdf.Rect(cx, cy, cx + w, cy + row_h)
            shape.draw_rect(rect)
            tw2.append((cx + 6, cy + 19), cell_text, font=font, fontsize=10)
            cx += w
    shape.finish(color=(0, 0, 0), width=0.8)
    shape.commit()
    tw2.write_text(p2)

    # 第 3 页：风险提示与存货跌价专项说明
    p3 = doc.new_page(width=595, height=842)
    tw3 = pymupdf.TextWriter(p3.rect)
    tw3.append((50, 50), "某某证券请务必阅读正文之后的免责声明", font=font, fontsize=9)
    tw3.append((50, 90), "第三节 风险提示与存货跌价准备明细", font=font, fontsize=14)
    tw3.append(
        (50, 125),
        "报告期内公司存货跌价准备计提充分，渠道库存保持良性水平，终端动销稳健。",
        font=font,
        fontsize=11,
    )
    tw3.write_text(p3)

    doc.set_toc([
        [1, "第一节 投资核心结论", 1],
        [1, "第二节 合并利润表核心数据", 2],
        [1, "第三节 风险提示与存货跌价准备明细", 3],
    ])
    out = doc.tobytes()
    doc.close()
    return out


def _make_long_pdf_bytes(page_count: int = 36) -> bytes:
    """构造超过 30 页的长文档 PDF，验证 paged / summary_and_tools 自动切档与黄金摘要。"""
    doc = pymupdf.open()
    font = pymupdf.Font("cjk")
    for i in range(page_count):
        page = doc.new_page(width=595, height=842)
        tw = pymupdf.TextWriter(page.rect)
        title = f"第{i + 1}节 章节内容" if i != 10 else "第十一节 主要会计数据和财务指标"
        tw.append((50, 70), title, font=font, fontsize=13)
        body = f"贵州茅台(600519) 第 {i + 1} 页年度报告详尽分析正文。" * 45
        y = 105
        for chunk_idx in range(0, len(body), 38):
            tw.append((50, y), body[chunk_idx : chunk_idx + 38], font=font, fontsize=10)
            y += 16
            if y > 760:
                break
        if i == 10:
            # 在第 11 页画一张主要会计数据表
            shape = page.new_shape()
            r1 = pymupdf.Rect(50, 680, 250, 710)
            r2 = pymupdf.Rect(250, 680, 450, 710)
            r3 = pymupdf.Rect(50, 710, 250, 740)
            r4 = pymupdf.Rect(250, 710, 450, 740)
            for r in (r1, r2, r3, r4):
                shape.draw_rect(r)
            shape.finish(color=(0, 0, 0), width=0.8)
            shape.commit()
            tw.append((58, 700), "主要会计数据", font=font, fontsize=10)
            tw.append((258, 700), "2025年", font=font, fontsize=10)
            tw.append((58, 730), "营业收入", font=font, fontsize=10)
            tw.append((258, 730), "1,741.44亿元", font=font, fontsize=10)
        tw.write_text(page)
    out = doc.tobytes()
    doc.close()
    return out


def test_1_electronic_pdf_text_and_table_extraction():
    """断言 1 & 2：电子版财报文本+表格还原、特殊负号与断字修复、页码溯源锚点。"""
    pdf_bytes = _make_sample_fin_pdf_bytes()
    t0 = time.perf_counter()
    parsed = parse_uploaded_file(pdf_bytes, "贵州茅台2025半年报摘要.pdf")
    elapsed = time.perf_counter() - t0

    assert elapsed < 2.0
    assert parsed.total_pages == 3
    assert parsed.table_count >= 1
    assert parsed.inject_mode == "full"

    # 验证第 1 页断字 '贵 州 茅 台' 修复为 '贵州茅台'，特殊减号 '−1,234.56' 修复为 '-1,234.56'
    p1_md = parsed.pages[0].content_md
    assert "贵州茅台" in p1_md
    assert "贵 州 茅 台" not in p1_md
    assert "-1,234.56" in p1_md
    assert "-12.45%" in p1_md
    assert "[来源：贵州茅台2025半年报摘要.pdf · 第 1/3 页]" in p1_md

    # 验证第 2 页表格被还原为标准 Markdown 管道表格且含页码锚点
    p2_md = parsed.pages[1].content_md
    assert "| 项目 | 2025年半年度 | 2024年半年度 | 同比变动 |" in p2_md
    assert "| 营业收入(亿元) | 834.51 | 709.87 | +17.56% |" in p2_md
    assert "-12.45%" in p2_md
    assert "[来源：贵州茅台2025半年报摘要.pdf · 第 2 页]" in p2_md


def test_3_long_pdf_paged_mode_and_golden_summary():
    """断言 3：长文档自动切档与黄金摘要生成（<= 15,000 字）。"""
    pdf_bytes = _make_long_pdf_bytes(35)
    parsed = parse_pdf_bytes(pdf_bytes, "贵州茅台2025年年度报告全文.pdf")
    assert parsed.total_pages == 35
    assert parsed.inject_mode == "paged"
    assert len(parsed.toc) > 0
    assert "文档目录大纲" in parsed.summary_md
    assert "主要会计数据" in parsed.summary_md
    assert len(parsed.summary_md) <= 15_000


def test_4_scan_page_l2_ocr_fallback(monkeypatch):
    """断言 4：扫描件图片页 L2 OCR 自动识别兜底。"""
    from PIL import Image, ImageDraw
    import app.services.doc_parser.pdf_parser as pdf_mod

    # 构造一页只含图片、无原生文字流的 PDF
    img = Image.new("RGB", (600, 300), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 20, 580, 280], outline=(0, 0, 0), width=2)
    img_buf = io.BytesIO()
    img.save(img_buf, format="PNG")

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(pymupdf.Rect(50, 100, 500, 350), stream=img_buf.getvalue())
    scan_pdf = doc.tobytes()
    doc.close()

    # 验证 _extract_page_l1 准确判定 is_scan=True，并触发 _ocr_page_l2
    monkeypatch.setattr(pdf_mod, "_ocr_page_l2", lambda _p: "紫金矿业 601899 营业收入 2,934 亿元")
    parsed = parse_pdf_bytes(scan_pdf, "紫金矿业扫描公告.pdf")
    assert parsed.ocr_page_count == 1
    assert parsed.pages[0].used_ocr is True
    assert "紫金矿业" in parsed.pages[0].content_md
    assert "601899" in parsed.pages[0].content_md


def test_5_encrypted_and_invalid_pdf_interception():
    """断言 5：加密 PDF 与非法文件拦截。"""
    # 1. 非 PDF 字节流
    with pytest.raises(ValueError, match="不是合法的 PDF 文件"):
        parse_pdf_bytes(b"MZ_executable_fake_content", "fake.pdf")

    # 2. 带打开密码的加密 PDF
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_text((50, 50), "Secret Research Report")
    enc_bytes = doc.tobytes(
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="owner123",
        user_pw="user123",
    )
    doc.close()

    with pytest.raises(ValueError, match="密码保护"):
        parse_pdf_bytes(enc_bytes, "encrypted.pdf")


def test_6_stock_entity_extractor_accuracy():
    """断言 6：股票实体识别器精准识别 A 股、港股、美股并排除年份误报。"""
    sample = (
        "202501 行业跟踪：重点看多 贵州茅台（600519）、宁德时代(300750)、"
        "腾讯控股(00700.HK) 以及美股算力龙头 英伟达 (NVDA)，"
        "对比 202412 与 100000 基准。"
    )
    stocks = extract_stocks_from_text("消费与科技龙头研报.pdf", sample, max_results=8)
    codes = {s["code"] for s in stocks}
    markets = {s["code"]: s["market"] for s in stocks}

    assert "600519" in codes
    assert "300750" in codes
    assert "00700" in codes
    assert "NVDA" in codes
    assert markets["600519"] == "CN_A"
    assert markets["00700"] == "HK"
    assert markets["NVDA"] == "US"
    # 年份或整数噪音不应混入
    assert "202501" not in codes
    assert "202412" not in codes
    assert "100000" not in codes


def test_7_and_8_upload_api_isolation_search_and_read_pages():
    """断言 7 & 8：上传入库、多租户归属隔离（403）、关键词搜索与跨页读取（14KB 保护）。"""
    app = _build_test_app()
    client = TestClient(app)

    pdf_bytes = _make_sample_fin_pdf_bytes()
    resp = client.post(
        "/api/chat/files/upload",
        headers={"X-Hunter-User-Id": "user_alice"},
        data={"session_id": "ses_test_001"},
        files={"file": ("贵州茅台2025半年报摘要.pdf", pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    file_info = data["file"]
    file_id = file_info["file_id"]
    assert file_info["total_pages"] == 3
    assert file_info["table_count"] >= 1
    detected_codes = [s["code"] for s in file_info["detected_stocks"]]
    assert "600519" in detected_codes
    assert "300750" in detected_codes

    # 用户 Alice 预览第 2 页成功
    prev_ok = client.get(
        f"/api/chat/files/{file_id}/preview?start_page=2&end_page=2",
        headers={"X-Hunter-User-Id": "user_alice"},
    )
    assert prev_ok.status_code == 200
    assert "合并利润表" in prev_ok.json()["pages"][0]["content_md"]

    # 多租户隔离：用户 Bob 尝试预览或拉取 Alice 的文件上下文必须返回 403
    prev_bob = client.get(
        f"/api/chat/files/{file_id}/preview",
        headers={"X-Hunter-User-Id": "user_bob"},
    )
    assert prev_bob.status_code == 403

    internal_key = os.getenv("HUNTER_INTERNAL_KEY", "hunter-internal-2026")
    ctx_bob = client.post(
        "/api/internal/files/context_bundle",
        headers={"X-Hunter-Internal-Key": internal_key, "X-Hunter-User-Id": "user_bob"},
        json={"session_id": "ses_test_001", "file_ids": [file_id]},
    )
    assert ctx_bob.status_code == 403

    # Alice 正常拉取 context_bundle
    ctx_alice = client.post(
        "/api/internal/files/context_bundle",
        headers={"X-Hunter-Internal-Key": internal_key, "X-Hunter-User-Id": "user_alice"},
        json={"session_id": "ses_test_001", "file_ids": [file_id]},
    )
    assert ctx_alice.status_code == 200
    ctx_data = ctx_alice.json()
    assert "834.51" in ctx_data["context_md"]
    assert any(s["code"] == "600519" for s in ctx_data["detected_stocks"])

    # 关键词搜索接口测试（搜索「存货跌价」）
    srch = client.post(
        "/api/internal/files/search",
        headers={"X-Hunter-Internal-Key": internal_key, "X-Hunter-User-Id": "user_alice"},
        json={"file_id": file_id, "keyword": "存货跌价", "limit": 5},
    )
    assert srch.status_code == 200
    srch_data = srch.json()
    assert srch_data["match_count"] >= 1
    # 含表格的第 2 页优先排第一
    assert srch_data["matches"][0]["page_no"] == 2
    assert srch_data["matches"][0]["has_table"] is True

    # 跨页读取接口测试（严格控制在 14KB 内）
    rd = client.post(
        "/api/internal/files/read_pages",
        headers={"X-Hunter-Internal-Key": internal_key, "X-Hunter-User-Id": "user_alice"},
        json={"file_id": file_id, "start_page": 1, "end_page": 3},
    )
    assert rd.status_code == 200
    rd_data = rd.json()
    assert len(rd_data["content_md"].encode("utf-8")) <= 14_000

    # 删除文件测试
    del_resp = client.delete(
        f"/api/chat/files/{file_id}",
        headers={"X-Hunter-User-Id": "user_alice"},
    )
    assert del_resp.status_code == 200


def test_m2_multi_format_excel_docx_and_md():
    """M2 多格式测试：Excel 持仓单识别与统计、Word 内嵌表格还原、Markdown 策略草稿检测。"""
    # 1. Excel 持仓单
    df = pd.DataFrame(
        {
            "证券代码": ["600519", "2594", "300750"],
            "证券名称": ["贵州茅台", "比亚迪", "宁德时代"],
            "持仓股数": [100, 500, 300],
            "成本价": [1450.5, 248.0, 195.2],
        }
    )
    x_buf = io.BytesIO()
    with pd.ExcelWriter(x_buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="持仓明细", index=False)
    parsed_x = parse_uploaded_file(x_buf.getvalue(), "模拟持仓单.xlsx")
    assert parsed_x.extra_meta.get("is_portfolio_sheet") is True
    pf_rows = parsed_x.extra_meta.get("portfolio_rows") or []
    assert len(pf_rows) == 3
    # 验证 2594 前导零自动补齐为 002594
    assert pf_rows[1]["code"] == "002594"
    assert "Python 精确计算的数值列统计摘要" in parsed_x.summary_md

    # 2. Word (.docx)
    from docx import Document

    d = Document()
    d.add_heading("一、半导体调研核心结论", level=1)
    d.add_paragraph("中芯国际(688981)与海光信息(688041)晶圆产能利用率持续回升。")
    tb = d.add_table(rows=2, cols=2)
    tb.cell(0, 0).text = "季度"
    tb.cell(0, 1).text = "产能利用率"
    tb.cell(1, 0).text = "2025Q2"
    tb.cell(1, 1).text = "89.5%"
    d_buf = io.BytesIO()
    d.save(d_buf)
    parsed_d = parse_uploaded_file(d_buf.getvalue(), "半导体调研纪要.docx")
    assert parsed_d.table_count == 1
    assert "| 季度 | 产能利用率 |" in parsed_d.summary_md
    assert any(s["code"] == "688981" for s in parsed_d.detected_stocks)

    # 3. Markdown (.md)
    md_raw = (
        "# 双均线叠加RSI量化选股策略\n\n"
        "- 买入条件：MA5 上穿 MA20 且 RSI < 30\n"
        "- 卖出条件：跌破止损线 -8% 或 止盈 +25%\n"
        "- 回测标的：贵州茅台(600519)\n"
    ).encode("utf-8")
    parsed_md = parse_uploaded_file(md_raw, "双均线RSI策略笔记.md")
    assert parsed_md.extra_meta.get("has_strategy_draft") is True
