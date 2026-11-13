"""统一文档解析分发器 · 支持 .pdf / .xlsx / .xls / .csv / .docx / .md / .txt"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable

from app.services.doc_parser.base import ParsedDocument, ParsedPage
from app.services.doc_parser.docx_parser import parse_docx_bytes
from app.services.doc_parser.excel_parser import parse_excel_bytes
from app.services.doc_parser.md_parser import parse_md_bytes
from app.services.doc_parser.pdf_parser import parse_pdf_bytes
from app.services.doc_parser.stock_extractor import extract_stocks_from_text

ALLOWED_EXTENSIONS = {"pdf", "xlsx", "xls", "csv", "docx", "md", "txt"}

_DISPATCH_MAP: dict[str, Callable[[bytes, str], ParsedDocument]] = {
    "pdf": parse_pdf_bytes,
    "xlsx": parse_excel_bytes,
    "xls": parse_excel_bytes,
    "csv": parse_excel_bytes,
    "docx": parse_docx_bytes,
    "md": parse_md_bytes,
    "txt": parse_md_bytes,
}


def _strip_nul(val: Any) -> Any:
    """递归剥离字符串中的 NUL (\\x00) 空字节与非法 UTF-8 surrogate，并归一化 NaN/Infinity。"""
    if isinstance(val, str):
        if "\x00" in val:
            val = val.replace("\x00", "")
        try:
            val.encode("utf-8")
            return val
        except UnicodeEncodeError:
            return val.encode("utf-8", "replace").decode("utf-8")
    if isinstance(val, float):
        if math.isnan(val) or math.isinf(val):
            return None
        return val
    if isinstance(val, list):
        return [_strip_nul(item) for item in val]
    if isinstance(val, dict):
        return {_strip_nul(str(k)): _strip_nul(v) for k, v in val.items()}
    return val


def parse_uploaded_file(raw_bytes: bytes, filename: str) -> ParsedDocument:
    clean_filename = _strip_nul(filename or "")
    ext = Path(clean_filename).suffix.lower().lstrip(".")
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"暂不支持 .{ext} 格式，请上传 PDF、Excel(.xlsx/.xls/.csv)、Word(.docx) 或 Markdown(.md/.txt) 文件"
        )

    parser_fn = _DISPATCH_MAP[ext]
    doc = parser_fn(raw_bytes, clean_filename)

    # 统一跑一遍关联股票实体识别（扫描文件名 + 前 10 页/分片文本）
    sample_text = "\n".join(_strip_nul(p.content_md) for p in doc.pages[:10])
    doc.detected_stocks = extract_stocks_from_text(clean_filename, sample_text)

    # 统一出口清洗：确保任何解析器产出的 ParsedDocument 100% 不含 \x00、非法 surrogate 与 NaN/Inf
    doc.filename = _strip_nul(doc.filename)
    doc.summary_md = _strip_nul(doc.summary_md)
    doc.full_md = _strip_nul(doc.full_md)
    doc.toc = _strip_nul(doc.toc)
    doc.detected_stocks = _strip_nul(doc.detected_stocks)
    doc.extra_meta = _strip_nul(doc.extra_meta)
    for p in doc.pages:
        p.page_title = _strip_nul(p.page_title)
        p.content_md = _strip_nul(p.content_md)
        p.char_count = len(p.content_md)
    doc.total_chars = sum(p.char_count for p in doc.pages)
    return doc


__all__ = [
    "ALLOWED_EXTENSIONS",
    "ParsedDocument",
    "ParsedPage",
    "_strip_nul",
    "extract_stocks_from_text",
    "parse_docx_bytes",
    "parse_excel_bytes",
    "parse_md_bytes",
    "parse_pdf_bytes",
    "parse_uploaded_file",
]
