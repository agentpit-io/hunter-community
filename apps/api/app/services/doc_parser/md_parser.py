"""Markdown / TXT (.md / .txt) 解析器：
1. 自动识别 UTF-8 / GB18030 编码；
2. 按一级/二级标题切分章节页；
3. 自动检测是否包含量化选股/小鹿策略规则草稿（供前端一键送入策略中心回测）。
"""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from app.services.doc_parser.base import ParsedDocument, ParsedPage

MAX_MD_BYTES = 10 * 1024 * 1024  # 10MB
_STRATEGY_KEYWORDS_RE = re.compile(
    r"(买入条件|卖出条件|止盈|止损|回测|均线|MA\d+|RSI|MACD|KDJ|布林带|突破|选股规则|screen_dsl)",
    re.IGNORECASE,
)


def parse_md_bytes(raw_bytes: bytes, filename: str) -> ParsedDocument:
    if len(raw_bytes) > MAX_MD_BYTES:
        raise ValueError("Markdown/文本文件超过 10MB 上限")

    text = ""
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = raw_bytes.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    text = text.replace("\x00", "").encode("utf-8", "replace").decode("utf-8")
    if not text.strip():
        raise ValueError("Markdown/文本文件内容为空或编码无法识别")

    ext = Path(filename).suffix.lower().lstrip(".") or "md"
    if ext not in ("md", "txt"):
        ext = "md"

    # 按二级以上标题或 3000 字切分章节
    lines = text.splitlines()
    pages: list[ParsedPage] = []
    toc: list[dict[str, Any]] = []
    cur_buf: list[str] = []
    cur_title = "正文第 1 节"
    cur_chars = 0
    table_count = 0

    def _flush():
        nonlocal cur_buf, cur_chars, table_count
        if not cur_buf:
            return
        pno = len(pages) + 1
        body = "\n".join(cur_buf).strip()
        has_tb = bool(re.search(r"^\|.+\|\s*\n\|[\s:\-\|]+\|", body, re.MULTILINE))
        tb_cnt = 1 if has_tb else 0
        if has_tb:
            table_count += 1
        pages.append(
            ParsedPage(
                page_no=pno,
                page_title=cur_title,
                content_md=(
                    f"---\n"
                    f"### 📄 [来源：{filename} · 第 {pno} 页]\n"
                    f"=== [来源：{filename} · 第 {pno} 节（{cur_title}）] ===\n"
                    f"{body}"
                ),
                char_count=len(body),
                table_count=tb_cnt,
                has_table=has_tb,
            )
        )
        cur_buf = []
        cur_chars = 0

    for ln in lines:
        m = re.match(r"^(#{1,2})\s+(.+)$", ln.strip())
        if m:
            if cur_chars >= 1200:
                _flush()
            cur_title = m.group(2).strip()[:60]
            toc.append({"level": len(m.group(1)), "title": cur_title, "page": len(pages) + 1})
        cur_buf.append(ln)
        cur_chars += len(ln)
        if cur_chars >= 3500:
            _flush()

    _flush()
    total_chars = sum(p.char_count for p in pages)
    inject_mode = "full" if total_chars <= 30_000 else "paged"
    full_md = "\n\n".join(p.content_md for p in pages)
    summary_md = "\n\n".join(p.content_md for p in (pages if inject_mode == "full" else pages[:6]))[:30_000]

    # 检测是否包含量化策略草稿特征
    strategy_hits = len(_STRATEGY_KEYWORDS_RE.findall(text))
    has_strategy_draft = strategy_hits >= 3

    return ParsedDocument(
        filename=filename,
        file_ext=ext,
        total_pages=len(pages),
        total_chars=total_chars,
        table_count=table_count,
        ocr_page_count=0,
        inject_mode=inject_mode,
        toc=toc,
        summary_md=summary_md,
        full_md=full_md,
        pages=pages,
        extra_meta={
            "has_strategy_draft": has_strategy_draft,
            "strategy_keyword_hits": strategy_hits,
        },
    )
