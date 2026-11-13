"""Excel (.xlsx / .xls / .csv) 解析器：
1. 支持多 Sheet 工作表读取，每个 Sheet 映射为一个 ParsedPage；
2. 自动用 pandas 计算数值列汇总统计（行数、均值、最大、最小、总和），防 LLM 心算出错；
3. 自动检测是否为「持仓明细 / 券商交割单」，提取结构化持仓数组供一键导入组合诊断。
"""
from __future__ import annotations

import io
from pathlib import Path
import re
from typing import Any

import pandas as pd

from app.services.doc_parser.base import ParsedDocument, ParsedPage

MAX_EXCEL_BYTES = 30 * 1024 * 1024  # 30MB
PREVIEW_MAX_ROWS_IN_FULL = 80       # 单表直注最多展示前 80 行（超过则走分页/查询工具）


def _read_sheets(raw_bytes: bytes, ext: str) -> dict[str, pd.DataFrame]:
    bio = io.BytesIO(raw_bytes)
    if ext == "csv":
        # 依次尝试 UTF-8-SIG、GB18030（国内券商导出交割单常用 GBK/GB18030）
        for enc in ("utf-8-sig", "gb18030", "utf-8"):
            try:
                bio.seek(0)
                df = pd.read_csv(bio, encoding=enc)
                return {"Sheet1": df}
            except Exception:
                continue
        raise ValueError("CSV 文件编码无法识别，请保存为 UTF-8 或标准 Excel 格式")
    elif ext == "xls":
        return pd.read_excel(bio, sheet_name=None, engine="xlrd")
    else:
        return pd.read_excel(bio, sheet_name=None, engine="openpyxl")


def _detect_portfolio_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    """检测表格是否为持仓单/交割单：识别【证券代码/名称、持仓数量/股数、成本价/买入均价】列"""
    cols = [str(c).strip() for c in df.columns]
    code_col = next((c for c in cols if re.search(r"代码|code|symbol|ticker", c, re.I)), None)
    name_col = next((c for c in cols if re.search(r"名称|简称|股票|标的|name", c, re.I)), None)
    shares_col = next((c for c in cols if re.search(r"数量|股数|持仓|余额|可用|shares|qty", c, re.I)), None)
    cost_col = next((c for c in cols if re.search(r"成本|均价|买入价|成交价|cost|price", c, re.I)), None)

    if not (code_col or name_col) or not (shares_col or cost_col):
        return []

    extracted: list[dict[str, Any]] = []
    for _, row in df.head(50).iterrows():
        code_val = str(row[code_col]).strip() if code_col and pd.notna(row[code_col]) else ""
        # 补齐被 Excel 吃掉前导 0 的 6 位 A 股代码（如 2594 -> 002594）
        if code_val.endswith(".0"):
            code_val = code_val[:-2]
        if code_val.isdigit() and len(code_val) < 6:
            code_val = code_val.zfill(6)
        name_val = str(row[name_col]).strip() if name_col and pd.notna(row[name_col]) else ""
        if not code_val and not name_val:
            continue
        item: dict[str, Any] = {"code": code_val, "name": name_val}
        if shares_col and pd.notna(row[shares_col]):
            try:
                item["shares"] = int(float(str(row[shares_col]).replace(",", "")))
            except ValueError:
                pass
        if cost_col and pd.notna(row[cost_col]):
            try:
                item["cost_price"] = round(float(str(row[cost_col]).replace(",", "")), 4)
            except ValueError:
                pass
        extracted.append(item)
    return extracted


def _df_to_md_table(df: pd.DataFrame, max_rows: int = 80) -> str:
    """将 DataFrame 前 max_rows 行转为标准 Markdown 表格（严防 NaN 与 NUL 露出，NaN 统一转 '—'）"""
    sub = df.head(max_rows).copy()
    sub = sub.fillna("—")
    headers = [str(c).replace("\x00", "").replace("|", "｜").replace("\n", " ").strip() for c in sub.columns]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for _, row in sub.iterrows():
        vals = [str(v).replace("\x00", "").replace("|", "｜").replace("\n", " ").strip() for v in row.values]
        lines.append("| " + " | ".join(vals) + " |")
    if len(df) > max_rows:
        lines.append(
            f"\n> *（注：本工作表共 {len(df)} 行，上方仅展示前 {max_rows} 行；如需查询后续行请调用 `file_search_in_doc` 或 `file_read_pages`）*"
        )
    return "\n".join(lines)


def _numeric_stats_md(df: pd.DataFrame) -> str:
    """用 pandas 精确计算数值列统计量，供大模型直接引用，严防模型心算错误"""
    num_df = df.select_dtypes(include=["number"])
    if num_df.empty:
        return ""
    lines = [
        "| 数值列名 | 有效行数 | 合计 (Sum) | 均值 (Mean) | 最小值 (Min) | 最大值 (Max) |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for col in list(num_df.columns)[:15]:
        s = num_df[col].dropna()
        if s.empty:
            continue
        lines.append(
            f"| {col} | {len(s)} | {s.sum():,.2f} | {s.mean():,.2f} | {s.min():,.2f} | {s.max():,.2f} |"
        )
    return "\n".join(lines)


def parse_excel_bytes(raw_bytes: bytes, filename: str) -> ParsedDocument:
    if len(raw_bytes) > MAX_EXCEL_BYTES:
        raise ValueError("Excel/CSV 文件超过 30MB 上限")
    ext = Path(filename).suffix.lower().lstrip(".")
    sheets = _read_sheets(raw_bytes, ext)
    if not sheets:
        raise ValueError("Excel 文件中未找到有效的工作表")

    pages: list[ParsedPage] = []
    toc: list[dict[str, Any]] = []
    portfolio_items: list[dict[str, Any]] = []
    sheet_meta: list[dict[str, Any]] = []

    page_no = 0
    for sheet_name, df in sheets.items():
        # 丢弃全空行与全空列
        df = df.dropna(how="all").dropna(axis=1, how="all")
        if df.empty:
            continue
        page_no += 1
        row_cnt, col_cnt = df.shape
        col_names = [str(c) for c in df.columns]

        # 检测是否为持仓/交割单
        pf = _detect_portfolio_rows(df)
        if pf and not portfolio_items:
            portfolio_items = pf

        stats_md = _numeric_stats_md(df)
        table_md = _df_to_md_table(df, max_rows=PREVIEW_MAX_ROWS_IN_FULL)

        sec_md = (
            f"---\n"
            f"### 📄 [来源：{filename} · 第 {page_no} 页]\n"
            f"=== [来源：{filename} · 工作表 {page_no}：《{sheet_name}》（共 {row_cnt} 行 × {col_cnt} 列）] ===\n"
            f"- **字段列表**：{'、'.join(col_names[:30])}\n\n"
        )
        if stats_md:
            sec_md += f"#### 📊 Python 精确计算的数值列统计摘要\n{stats_md}\n\n"
        sec_md += f"#### 📋 数据明细表\n{table_md}"

        pages.append(
            ParsedPage(
                page_no=page_no,
                page_title=f"工作表：{sheet_name}",
                content_md=sec_md,
                char_count=len(sec_md),
                table_count=1,
                has_table=True,
                used_ocr=False,
            )
        )
        toc.append({"level": 1, "title": f"工作表《{sheet_name}》({row_cnt}行×{col_cnt}列)", "page": page_no})
        sheet_meta.append({"sheet": sheet_name, "rows": row_cnt, "cols": col_cnt, "columns": col_names[:30]})

    if not pages:
        raise ValueError("Excel 文件所有工作表均为空")

    total_chars = sum(p.char_count for p in pages)
    inject_mode = "full" if total_chars <= 30_000 else "paged"
    full_md = "\n\n".join(p.content_md for p in pages)
    summary_md = "\n\n".join(p.content_md for p in (pages if inject_mode == "full" else pages[:3]))[:30_000]

    return ParsedDocument(
        filename=filename,
        file_ext=ext,
        total_pages=len(pages),
        total_chars=total_chars,
        table_count=len(pages),
        ocr_page_count=0,
        inject_mode=inject_mode,
        toc=toc,
        summary_md=summary_md,
        full_md=full_md,
        pages=pages,
        extra_meta={
            "sheets": sheet_meta,
            "is_portfolio_sheet": len(portfolio_items) > 0,
            "portfolio_rows": portfolio_items[:50],
        },
    )
