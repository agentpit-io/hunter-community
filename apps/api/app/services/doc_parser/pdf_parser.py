"""PDF 三级解析流水线（L1 PyMuPDF 原生文本+表格还原 → L2 扫描页 OCR/视觉兜底 → 目录与黄金摘要生成）。"""
from __future__ import annotations

from collections import Counter
import io
import os
import re
from typing import Any

from loguru import logger
import pymupdf  # PyMuPDF >= 1.24 (宿主机已装 1.28.2，支持 page.find_tables())

from app.services.doc_parser.base import ParsedDocument, ParsedPage


# ── 阈值常量 ─────────────────────────────────────────────────────────
MAX_PDF_BYTES = int(os.getenv("CHAT_PDF_MAX_BYTES", str(50 * 1024 * 1024)))  # 50MB
MAX_PDF_PAGES = int(os.getenv("CHAT_PDF_MAX_PAGES", "500"))                   # 最大500页
FULL_INJECT_MAX_PAGES = 30
FULL_INJECT_MAX_CHARS = 30_000
SCAN_PAGE_MIN_CHARS = 30          # 单页原生提取少于 30 字且含图 → 判定为扫描/图片页
MAX_OCR_PAGES_PER_DOC = 15        # 单次上传实时 OCR 最大页数，避免 200 页纯扫描件阻塞请求


def _strip_nul_and_surrogates(text: str) -> str:
    """剥离 PostgreSQL/JSONB 禁止的 NUL (\\x00) 空字节及非法 UTF-8 surrogate 字符。"""
    if not text:
        return ""
    if "\x00" in text:
        text = text.replace("\x00", "")
    try:
        text.encode("utf-8")
        return text
    except UnicodeEncodeError:
        return text.encode("utf-8", "replace").decode("utf-8")


def _normalize_fin_text(text: str) -> str:
    """金融文本清洗（源自 supplement_cn_annual.py 实战经验）：
    1. 剥离 PDF 子集字体/CMap 可能混入的 NUL (\\x00) 空字节与非法 UTF-8 surrogate；
    2. 统一特殊负号（−、－、﹣、—）为标准 ASCII '-'，防负利润变正数；
    3. 消除中文字符间被 PDF 排版拆出的多余空格（如 '贵 州 茅 台' → '贵州茅台'）；
    4. 折叠连续空行。
    """
    if not text:
        return ""
    text = _strip_nul_and_surrogates(str(text))
    for minus in ("−", "－", "﹣", "—"):
        # 仅当后面紧跟数字时把破折号/特殊减号归一化为负号
        text = re.sub(rf"{re.escape(minus)}(?=\d)", "-", text)
    # 合并汉字之间的单个或连续空格（保留英文单词间空格和数字间空格）
    # 循环两次以处理 '贵 州 茅 台' 这种连续单字空格
    for _ in range(2):
        text = re.sub(r"(?<=[一-鿿])[ \t]+(?=[一-鿿])", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _clean_cell(val: Any) -> str:
    """清洗 PDF 表格单元格：去掉内部换行与多余空格，修复 CJK 字体下被拆乱的小数点/负号，转义管道符 |"""
    if val is None:
        return ""
    s = _strip_nul_and_surrogates(str(val)).replace("\r", " ").replace("\n", " ").strip()
    s = re.sub(r"\s+", " ", s)
    # 修复部分 PDF 引擎（如 pymupdf_layout + CJK 混排字体）将单元格内负号/小数点拆到末尾的乱序现象：
    # 例如 "1245% − ." -> "-12.45%"、"83451 ." -> "834.51"、"+1756% ." -> "+17.56%"
    m_neg_pct = re.fullmatch(r"(\d{3,})%\s*[−－﹣—-]\s*\.", s)
    if m_neg_pct:
        digits = m_neg_pct.group(1)
        s = f"-{digits[:-2]}.{digits[-2:]}%"
    else:
        m_pos_pct = re.fullmatch(r"([+-]?\d{3,})%\s*\.", s)
        if m_pos_pct:
            digits = m_pos_pct.group(1)
            s = f"{digits[:-2]}.{digits[-2:]}%"
        else:
            m_dec = re.fullmatch(r"([+-]?\d{3,})\s*\.", s)
            if m_dec:
                digits = m_dec.group(1)
                s = f"{digits[:-2]}.{digits[-2:]}"
    s = _normalize_fin_text(s)
    return s.replace("|", "｜")


def _table_to_markdown(rows: list[list[Any]]) -> str:
    """将 PyMuPDF extract() 得到的二维数组转为标准 Markdown 管道表格"""
    if not rows:
        return ""
    cleaned_rows = [[_clean_cell(c) for c in row] for row in rows]
    # 过滤全空行
    cleaned_rows = [r for r in cleaned_rows if any(c != "" for c in r)]
    if not cleaned_rows:
        return ""
    col_count = max(len(r) for r in cleaned_rows)
    if col_count == 0:
        return ""
    # 补齐列数
    norm_rows = [r + [""] * (col_count - len(r)) for r in cleaned_rows]
    header = norm_rows[0]
    # 若首行全空，用列序号占位
    if not any(header):
        header = [f"列{i+1}" for i in range(col_count)]
    sep = ["---"] * col_count
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(sep) + " |",
    ]
    for r in norm_rows[1:]:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def _extract_page_l1(page: pymupdf.Page) -> tuple[str, int, bool]:
    """L1 原生提取：将页面上的表格转为 Markdown 表格，并与非表格区域的文本按垂直坐标 Y 顺序拼接。
    返回: (page_markdown, table_count_on_page, is_scan_candidate)
    """
    blocks_with_y: list[tuple[float, str]] = []
    table_rects: list[pymupdf.Rect] = []
    table_count = 0

    # 1. 检测并还原表格
    try:
        finder = page.find_tables()
        tables = getattr(finder, "tables", []) or []
        for tb in tables:
            extracted = tb.extract()
            md_tb = _table_to_markdown(extracted)
            if md_tb and len(extracted) >= 2:
                bbox = pymupdf.Rect(tb.bbox)
                table_rects.append(bbox)
                blocks_with_y.append((bbox.y0, f"\n{md_tb}\n"))
                table_count += 1
    except Exception as e:
        logger.debug("[pdf_parser] find_tables fallback on page {}: {}", page.number + 1, e)

    # 2. 提取非表格区域的文本块（避免表格文字重复出现两遍）
    raw_blocks = page.get_text("blocks") or []
    for b in raw_blocks:
        # b = (x0, y0, x1, y1, text, block_no, block_type)
        if len(b) < 7 or b[6] != 0:  # block_type == 0 为文本
            continue
        b_rect = pymupdf.Rect(b[0], b[1], b[2], b[3])
        # 若该文本块中心落在某个已提取的表格框内，或与表格框高度重叠，跳过
        center = pymupdf.Point((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
        if any(tr.contains(center) or tr. intersects(b_rect) and (tr & b_rect).get_area() > 0.5 * max(1.0, b_rect.get_area()) for tr in table_rects):
            continue
        txt = _normalize_fin_text(b[4])
        if txt:
            blocks_with_y.append((b[1], txt))

    blocks_with_y.sort(key=lambda item: item[0])
    combined = "\n\n".join(item[1] for item in blocks_with_y).strip()

    # 3. 判断是否为扫描页/纯图页（文字少于 30 字且页面内有图片）
    is_scan = False
    if len(combined) < SCAN_PAGE_MIN_CHARS:
        try:
            images = page.get_images(full=False)
            if images:
                is_scan = True
        except Exception:
            pass

    return combined, table_count, is_scan


def _ocr_page_l2(page: pymupdf.Page) -> str:
    """L2 扫描页兜底：将页面渲染为 180 DPI 灰度图，优先走本地 pytesseract 快速识别"""
    try:
        from PIL import Image
        import pytesseract

        pix = page.get_pixmap(dpi=180, colorspace=pymupdf.csGRAY)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        try:
            text = pytesseract.image_to_string(img, lang="chi_sim+eng", config="--psm 6")
        except Exception:
            text = pytesseract.image_to_string(img, config="--psm 6")
        return _normalize_fin_text(text)
    except Exception as e:
        logger.warning("[pdf_parser] L2 OCR failed on page {}: {}", page.number + 1, e)
        return ""


_HEADING_RE = re.compile(
    r"^(?:第[一二三四五六七八九十百零\d]+[节章篇部分]|"
    r"[一二三四五六七八九十]+、|"
    r"（[一二三四五六七八九十]+）|"
    r"\d+\.\d*\s+[一-鿿A-Za-z])"
)


def parse_pdf_bytes(raw_bytes: bytes, filename: str) -> ParsedDocument:
    """核心入口：解析 PDF 二进制字节流，返回标准的 ParsedDocument"""
    if not raw_bytes or not raw_bytes.lstrip().startswith(b"%PDF"):
        raise ValueError("不是合法的 PDF 文件（文件头校验失败）")
    if len(raw_bytes) > MAX_PDF_BYTES:
        raise ValueError(f"PDF 文件大小超过上限（最大支持 {MAX_PDF_BYTES // 1024 // 1024}MB）")

    try:
        doc = pymupdf.open(stream=raw_bytes, filetype="pdf")
    except Exception as e:
        raise ValueError(f"不是合法的 PDF 文件或文件已损坏：{e}")

    if doc.is_encrypted:
        # 尝试空密码自动解密（部分研报设置了权限密码但打开密码为空）
        if not doc.authenticate(""):
            doc.close()
            raise ValueError("该 PDF 文件受密码保护，请先解除密码后再上传")

    total_pages = len(doc)
    if total_pages == 0:
        doc.close()
        raise ValueError("PDF 文件为空（0 页）")
    if total_pages > MAX_PDF_PAGES:
        doc.close()
        raise ValueError(f"PDF 页数过多（共 {total_pages} 页，当前上限 {MAX_PDF_PAGES} 页）")

    # 1. 提取原生 PDF 书签目录（若有）
    toc_list: list[dict[str, Any]] = []
    try:
        raw_toc = doc.get_toc()  # [[level, title, page], ...]
        for item in raw_toc[:80]:
            if len(item) >= 3:
                toc_list.append(
                    {
                        "level": int(item[0]),
                        "title": _strip_nul_and_surrogates(str(item[1])).strip(),
                        "page": int(item[2]),
                    }
                )
    except Exception:
        pass

    pages: list[ParsedPage] = []
    total_tables = 0
    ocr_count = 0

    # 统计页眉页脚高频重复短行（超过阈值页出现完全相同的首行/末行则视为页眉页脚噪音）
    first_lines: Counter[str] = Counter()
    last_lines: Counter[str] = Counter()

    raw_page_results: list[tuple[int, str, int, bool]] = []
    for idx in range(total_pages):
        page = doc[idx]
        md_text, tb_cnt, is_scan = _extract_page_l1(page)
        raw_page_results.append((idx + 1, md_text, tb_cnt, is_scan))
        lines = [ln.strip() for ln in md_text.splitlines() if ln.strip()]
        if lines and len(lines[0]) < 80 and not lines[0].startswith("|"):
            first_lines[lines[0]] += 1
        if len(lines) > 1 and len(lines[-1]) < 80 and not lines[-1].startswith("|"):
            last_lines[lines[-1]] += 1

    noise_threshold = max(4, int(total_pages * 0.3))
    header_noise = {k for k, v in first_lines.items() if v >= noise_threshold}
    footer_noise = {k for k, v in last_lines.items() if v >= noise_threshold}

    for page_no, md_text, tb_cnt, is_scan in raw_page_results:
        used_ocr = False
        if is_scan and ocr_count < MAX_OCR_PAGES_PER_DOC:
            ocr_txt = _ocr_page_l2(doc[page_no - 1])
            if len(ocr_txt) > len(md_text):
                md_text = f"> *[第 {page_no} 页为扫描图片页，以下为自动识别内容]*\n\n{ocr_txt}"
                used_ocr = True
                ocr_count += 1
            elif not md_text:
                md_text = f"> *[第 {page_no} 页为纯图片/图表页，未提取到内嵌文字]*"

        # 剥离重复页眉页脚
        lines = md_text.splitlines()
        if lines and lines[0].strip() in header_noise:
            lines = lines[1:]
        if lines and lines[-1].strip() in footer_noise:
            lines = lines[:-1]
        cleaned_md = "\n".join(lines).strip()

        # 若 PDF 无内置书签，从页面正文自动探测章节标题补充目录
        page_title = f"第 {page_no} 页"
        for ln in lines[:12]:
            s = ln.strip().lstrip("#").strip()
            if 3 <= len(s) <= 50 and _HEADING_RE.match(s):
                page_title = s
                if len(toc_list) < 80:
                    toc_list.append({"level": 1, "title": s, "page": page_no})
                break

        with_anchor = (
            f"---\n"
            f"### 📄 [来源：{filename} · 第 {page_no}/{total_pages} 页]\n"
            f"=== [来源：{filename} · 第 {page_no} 页] ===\n"
            f"{cleaned_md}"
        )
        pages.append(
            ParsedPage(
                page_no=page_no,
                page_title=page_title,
                content_md=with_anchor,
                char_count=len(cleaned_md),
                table_count=tb_cnt,
                has_table=(tb_cnt > 0),
                used_ocr=used_ocr,
            )
        )
        total_tables += tb_cnt

    doc.close()

    total_chars = sum(p.char_count for p in pages)
    inject_mode = (
        "full"
        if (total_pages <= FULL_INJECT_MAX_PAGES and total_chars <= FULL_INJECT_MAX_CHARS)
        else "paged"
    )

    full_md = "\n\n".join(p.content_md for p in pages)

    # 构建 summary_md：
    # - full 模式下直接存带页码锚点的全文（上限 3 万字）
    # - paged 模式下构建「黄金摘要包」：前 5 页全文 + 自动定位包含「主要会计数据 / 合并资产负债表 / 合并利润表 / 现金流量表」的关键财务表页（总长控制在 <= 15,000 字）
    if inject_mode == "full":
        summary_md = full_md
    else:
        key_pages: list[ParsedPage] = list(pages[:5])
        fin_kw = re.compile(
            r"主要会计数据|主要财务指标|合并利润表|合并资产负债表|合并现金流量表|利润表|资产负债表|现金流量表|营业收入|归属于上市公司股东"
        )
        for p in pages[5:]:
            if (p.has_table or fin_kw.search(p.content_md)) and fin_kw.search(p.content_md) and len(key_pages) < 10:
                key_pages.append(p)
        if not toc_list:
            # 兜底把前几个页面的标题放入 toc_list
            for p in pages[:min(10, len(pages))]:
                toc_list.append({"level": 1, "title": p.page_title, "page": p.page_no})
        toc_md = (
            "\n".join(f"- 第 {t['page']} 页：{t['title']}" for t in toc_list[:40])
            or "（未检测到显式章节书签，可按关键词搜索或按页码翻阅）"
        )
        excerpts_md = "\n\n".join(p.content_md for p in key_pages)[:12_000]
        summary_md = (
            f"### 📑 文档目录大纲（共 {total_pages} 页，总字数 {total_chars} 字，含 {total_tables} 张表格）\n"
            f"{toc_md}\n\n"
            f"### 📊 前言与核心财务表摘录（其余页面请调用 `file_search_in_doc` 或 `file_read_pages` 按需翻阅）\n"
            f"{excerpts_md}"
        )[:14_500]

    return ParsedDocument(
        filename=filename,
        file_ext="pdf",
        total_pages=total_pages,
        total_chars=total_chars,
        table_count=total_tables,
        ocr_page_count=ocr_count,
        inject_mode=inject_mode,
        toc=toc_list,
        summary_md=summary_md,
        full_md=full_md,
        pages=pages,
    )
