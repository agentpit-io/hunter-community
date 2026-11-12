"""统一文档解析数据结构定义（支持 PDF / Excel / CSV / Word / Markdown / TXT）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParsedPage:
    page_no: int
    page_title: str
    content_md: str
    char_count: int
    table_count: int = 0
    has_table: bool = False
    used_ocr: bool = False


@dataclass
class ParsedDocument:
    filename: str
    file_ext: str
    total_pages: int
    total_chars: int
    table_count: int
    ocr_page_count: int
    inject_mode: str                         # "full" | "paged" | "summary_and_tools"
    toc: list[dict[str, Any]] = field(default_factory=list)
    detected_stocks: list[dict[str, Any]] = field(default_factory=list)
    summary_md: str = ""
    full_md: str = ""
    pages: list[ParsedPage] = field(default_factory=list)
    extra_meta: dict[str, Any] = field(default_factory=dict)
