"""兼容导入路径：app.services.pdf_parser -> re-export doc_parser.pdf_parser。"""
from app.services.doc_parser.pdf_parser import (
    FULL_INJECT_MAX_CHARS,
    FULL_INJECT_MAX_PAGES,
    MAX_OCR_PAGES_PER_DOC,
    MAX_PDF_BYTES,
    MAX_PDF_PAGES,
    SCAN_PAGE_MIN_CHARS,
    ParsedDocument,
    ParsedPage,
    _clean_cell,
    _extract_page_l1,
    _normalize_fin_text,
    _ocr_page_l2,
    _table_to_markdown,
    parse_pdf_bytes,
)

__all__ = [
    "FULL_INJECT_MAX_CHARS",
    "FULL_INJECT_MAX_PAGES",
    "MAX_OCR_PAGES_PER_DOC",
    "MAX_PDF_BYTES",
    "MAX_PDF_PAGES",
    "SCAN_PAGE_MIN_CHARS",
    "ParsedDocument",
    "ParsedPage",
    "_clean_cell",
    "_extract_page_l1",
    "_normalize_fin_text",
    "_ocr_page_l2",
    "_table_to_markdown",
    "parse_pdf_bytes",
]
