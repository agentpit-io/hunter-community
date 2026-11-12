"""统一文档解析分发器 · 支持 .pdf / .xlsx / .xls / .csv / .docx / .md / .txt"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

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


def parse_uploaded_file(raw_bytes: bytes, filename: str) -> ParsedDocument:
    ext = Path(filename or "").suffix.lower().lstrip(".")
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"暂不支持 .{ext} 格式，请上传 PDF、Excel(.xlsx/.xls/.csv)、Word(.docx) 或 Markdown(.md/.txt) 文件"
        )

    parser_fn = _DISPATCH_MAP[ext]
    doc = parser_fn(raw_bytes, filename)

    # 统一跑一遍关联股票实体识别（扫描文件名 + 前 10 页/分片文本）
    sample_text = "\n".join(p.content_md for p in doc.pages[:10])
    doc.detected_stocks = extract_stocks_from_text(filename, sample_text)
    return doc


__all__ = [
    "ALLOWED_EXTENSIONS",
    "ParsedDocument",
    "ParsedPage",
    "extract_stocks_from_text",
    "parse_docx_bytes",
    "parse_excel_bytes",
    "parse_md_bytes",
    "parse_pdf_bytes",
    "parse_uploaded_file",
]
