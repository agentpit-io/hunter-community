"""Word (.docx) 解析器：保留标题层级 + 原位还原内嵌 Markdown 表格 + 按章节分页。"""
from __future__ import annotations

import io
from typing import Any
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.services.doc_parser.base import ParsedDocument, ParsedPage
from app.services.doc_parser.pdf_parser import _normalize_fin_text, _table_to_markdown

MAX_DOCX_BYTES = 30 * 1024 * 1024  # 30MB
PAGE_CHAR_BUCKET = 3_000           # 每约 3000 字或遇到 Heading 1 切为一页


def parse_docx_bytes(raw_bytes: bytes, filename: str) -> ParsedDocument:
    if len(raw_bytes) > MAX_DOCX_BYTES:
        raise ValueError("Word 文档超过 30MB 上限")
    try:
        doc = Document(io.BytesIO(raw_bytes))
    except Exception as e:
        raise ValueError(f"无法读取 Word(.docx) 文件（若为旧版 .doc 请另存为 .docx）：{e}")

    pages: list[ParsedPage] = []
    toc: list[dict[str, Any]] = []
    cur_lines: list[str] = []
    cur_chars = 0
    cur_title = "第 1 节"
    cur_table_cnt = 0
    total_tables = 0

    def _flush_page():
        nonlocal cur_lines, cur_chars, cur_table_cnt
        if not cur_lines:
            return
        pno = len(pages) + 1
        body = "\n\n".join(cur_lines).strip()
        with_anchor = (
            f"---\n"
            f"### 📄 [来源：{filename} · 第 {pno} 页]\n"
            f"=== [来源：{filename} · 第 {pno} 节（{cur_title}）] ===\n"
            f"{body}"
        )
        pages.append(
            ParsedPage(
                page_no=pno,
                page_title=cur_title,
                content_md=with_anchor,
                char_count=len(body),
                table_count=cur_table_cnt,
                has_table=(cur_table_cnt > 0),
            )
        )
        cur_lines = []
        cur_chars = 0
        cur_table_cnt = 0

    for item in doc.iter_inner_content():
        if isinstance(item, Paragraph):
            txt = _normalize_fin_text(item.text)
            if not txt:
                continue
            style_name = (item.style.name if item.style else "") or ""
            if style_name.startswith("Heading 1") or style_name == "Title":
                if cur_chars >= 800:
                    _flush_page()
                cur_title = txt[:60]
                toc.append({"level": 1, "title": cur_title, "page": len(pages) + 1})
                cur_lines.append(f"## {txt}")
            elif style_name.startswith("Heading 2"):
                toc.append({"level": 2, "title": txt[:60], "page": len(pages) + 1})
                cur_lines.append(f"### {txt}")
            elif style_name.startswith("Heading"):
                cur_lines.append(f"#### {txt}")
            else:
                cur_lines.append(txt)
            cur_chars += len(txt)
        elif isinstance(item, Table):
            rows = [[cell.text for cell in row.cells] for row in item.rows]
            md_tb = _table_to_markdown(rows)
            if md_tb:
                cur_lines.append(md_tb)
                cur_chars += len(md_tb)
                cur_table_cnt += 1
                total_tables += 1

        if cur_chars >= PAGE_CHAR_BUCKET:
            _flush_page()

    _flush_page()
    if not pages:
        raise ValueError("Word 文档内容为空")

    total_chars = sum(p.char_count for p in pages)
    inject_mode = "full" if total_chars <= 30_000 else "paged"
    full_md = "\n\n".join(p.content_md for p in pages)
    summary_md = "\n\n".join(p.content_md for p in (pages if inject_mode == "full" else pages[:6]))[:30_000]

    return ParsedDocument(
        filename=filename,
        file_ext="docx",
        total_pages=len(pages),
        total_chars=total_chars,
        table_count=total_tables,
        ocr_page_count=0,
        inject_mode=inject_mode,
        toc=toc,
        summary_md=summary_md,
        full_md=full_md,
        pages=pages,
    )
