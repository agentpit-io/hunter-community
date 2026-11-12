"""兼容导入路径：app.services.stock_extractor -> re-export doc_parser.stock_extractor。"""
from app.services.doc_parser.stock_extractor import (
    _load_stock_dict,
    extract_stocks_from_text,
)

__all__ = [
    "_load_stock_dict",
    "extract_stocks_from_text",
]
