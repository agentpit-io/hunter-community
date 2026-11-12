"""对话框多格式文件导入（PDF / Excel / Word / Markdown / TXT）路由与数据库持久化服务。

同时挂载：
- /api/chat/files/* （面向前端与 BFF 的标准接口）
- /api/v1/files/*   （兼容 v1 规范别名）
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from loguru import logger
from pydantic import BaseModel

from app.services.database import get_conn
from app.services.doc_parser import ALLOWED_EXTENSIONS, ParsedDocument, parse_uploaded_file

router = APIRouter(tags=["chat-files"])

CHAT_UPLOAD_DIR = os.getenv("CHAT_UPLOAD_DIR", "/tmp/hunter_chat_uploads")
MAX_PAGE_READ_SPAN = 8
MAX_READ_BYTES = 14_000  # 严格受控在 14KB 以内，避免触发 16KB 截断


def _uid(request: Request) -> str:
    """获取当前请求用户 ID（兼容 JWT user_id 及内部调用 Header）。"""
    uid = getattr(request.state, "user_id", None)
    if uid:
        return str(uid)
    hdr_uid = (
        request.headers.get("X-Hunter-User-Id", "").strip()
        or request.headers.get("X-User-Key", "").strip()
    )
    if hdr_uid:
        return hdr_uid
    raise HTTPException(401, "需要登录")


def _ensure_tables_once() -> None:
    """惰性幂等建表兜底（防止单测或未重启进程时表不存在）。"""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_uploaded_files (
                file_id           VARCHAR(64) PRIMARY KEY,
                user_id           TEXT NOT NULL DEFAULT '',
                user_key          TEXT NOT NULL DEFAULT '',
                session_id        VARCHAR(128) NOT NULL DEFAULT '',
                filename          VARCHAR(512) NOT NULL,
                file_ext          VARCHAR(16) NOT NULL,
                mime_type         VARCHAR(128) NOT NULL DEFAULT '',
                file_size_bytes   BIGINT NOT NULL DEFAULT 0,
                file_size         BIGINT NOT NULL DEFAULT 0,
                file_sha256       VARCHAR(64) NOT NULL DEFAULT '',
                storage_path      TEXT NOT NULL DEFAULT '',
                parse_status      VARCHAR(32) NOT NULL DEFAULT 'ready',
                parse_error       TEXT,
                error_message     TEXT,
                total_pages       INT NOT NULL DEFAULT 0,
                page_count        INT NOT NULL DEFAULT 0,
                total_chars       INT NOT NULL DEFAULT 0,
                char_count        INT NOT NULL DEFAULT 0,
                table_count       INT NOT NULL DEFAULT 0,
                ocr_page_count    INT NOT NULL DEFAULT 0,
                inject_mode       VARCHAR(32) NOT NULL DEFAULT 'full',
                toc_json          JSONB NOT NULL DEFAULT '[]'::jsonb,
                detected_stocks   JSONB NOT NULL DEFAULT '[]'::jsonb,
                related_stocks    JSONB NOT NULL DEFAULT '[]'::jsonb,
                full_markdown     TEXT NOT NULL DEFAULT '',
                summary_md        TEXT NOT NULL DEFAULT '',
                summary_markdown  TEXT NOT NULL DEFAULT '',
                extra_meta        JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                expires_at        TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '7 days')
            );
            CREATE TABLE IF NOT EXISTS chat_uploaded_file_pages (
                id                BIGSERIAL PRIMARY KEY,
                file_id           VARCHAR(64) NOT NULL REFERENCES chat_uploaded_files(file_id) ON DELETE CASCADE,
                page_no           INT NOT NULL,
                page_title        VARCHAR(256) NOT NULL DEFAULT '',
                content_md        TEXT NOT NULL DEFAULT '',
                page_markdown     TEXT NOT NULL DEFAULT '',
                char_count        INT NOT NULL DEFAULT 0,
                table_count       INT NOT NULL DEFAULT 0,
                has_table         BOOLEAN NOT NULL DEFAULT FALSE,
                used_ocr          BOOLEAN NOT NULL DEFAULT FALSE,
                is_ocr            BOOLEAN NOT NULL DEFAULT FALSE,
                UNIQUE(file_id, page_no)
            );
            """
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.debug("[chat_files] ensure_tables warning: {}", e)
    finally:
        conn.close()


def _save_raw_file(file_id: str, user_id: str, ext: str, raw_bytes: bytes) -> str:
    """将上传文件保存到本地临时存储目录，返回相对路径。"""
    day_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    safe_uid = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in str(user_id))[:64] or "anon"
    rel_dir = os.path.join(day_str, safe_uid)
    abs_dir = os.path.join(CHAT_UPLOAD_DIR, rel_dir)
    os.makedirs(abs_dir, exist_ok=True)
    rel_path = os.path.join(rel_dir, f"{file_id}.{ext}")
    abs_path = os.path.join(CHAT_UPLOAD_DIR, rel_path)
    try:
        with open(abs_path, "wb") as f:
            f.write(raw_bytes)
    except Exception as e:
        logger.warning("[chat_files] save raw file failed ({}): {}", abs_path, e)
        return ""
    return rel_path


def _format_file_record(row: tuple[Any, ...]) -> dict[str, Any]:
    (
        file_id,
        user_id,
        session_id,
        filename,
        file_ext,
        mime_type,
        file_size_bytes,
        parse_status,
        parse_error,
        total_pages,
        total_chars,
        table_count,
        ocr_page_count,
        inject_mode,
        toc_json,
        detected_stocks,
        summary_md,
        extra_meta,
        created_at,
    ) = row
    stocks = detected_stocks if isinstance(detected_stocks, list) else []
    toc = toc_json if isinstance(toc_json, list) else []
    meta = extra_meta if isinstance(extra_meta, dict) else {}
    return {
        "file_id": file_id,
        "user_id": user_id,
        "user_key": user_id,
        "session_id": session_id,
        "filename": filename,
        "file_ext": file_ext,
        "mime_type": mime_type,
        "file_size_bytes": int(file_size_bytes or 0),
        "file_size": int(file_size_bytes or 0),
        "parse_status": parse_status,
        "parse_error": parse_error,
        "error_message": parse_error,
        "total_pages": int(total_pages or 0),
        "page_count": int(total_pages or 0),
        "total_chars": int(total_chars or 0),
        "char_count": int(total_chars or 0),
        "table_count": int(table_count or 0),
        "ocr_page_count": int(ocr_page_count or 0),
        "inject_mode": inject_mode,
        "toc": toc,
        "toc_json": toc,
        "detected_stocks": stocks,
        "related_stocks": stocks,
        "summary_md": summary_md or "",
        "summary_markdown": summary_md or "",
        "extra_meta": meta,
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at or ""),
    }


def save_parsed_document_to_db(
    *,
    user_id: str,
    session_id: str,
    filename: str,
    mime_type: str,
    raw_bytes: bytes,
    parsed: ParsedDocument,
) -> dict[str, Any]:
    """将解析完成的 ParsedDocument 持久化到 chat_uploaded_files 与 chat_uploaded_file_pages。"""
    _ensure_tables_once()
    file_id = f"file_{secrets.token_hex(8)}"
    sha256_hex = hashlib.sha256(raw_bytes).hexdigest()
    storage_path = _save_raw_file(file_id, user_id, parsed.file_ext, raw_bytes)

    conn = get_conn()
    try:
        cur = conn.cursor()
        toc_json_str = json.dumps(parsed.toc or [], ensure_ascii=False)
        stocks_json_str = json.dumps(parsed.detected_stocks or [], ensure_ascii=False)
        meta_json_str = json.dumps(parsed.extra_meta or {}, ensure_ascii=False)

        cur.execute(
            """
            INSERT INTO chat_uploaded_files (
                file_id, user_id, user_key, session_id, filename, file_ext, mime_type,
                file_size_bytes, file_size, file_sha256, storage_path,
                parse_status, parse_error, error_message,
                total_pages, page_count, total_chars, char_count,
                table_count, ocr_page_count, inject_mode,
                toc_json, detected_stocks, related_stocks,
                full_markdown, summary_md, summary_markdown, extra_meta
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                'ready', NULL, NULL,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s::jsonb, %s::jsonb, %s::jsonb,
                %s, %s, %s, %s::jsonb
            )
            """,
            (
                file_id,
                user_id,
                user_id,
                session_id,
                filename,
                parsed.file_ext,
                mime_type or "",
                len(raw_bytes),
                len(raw_bytes),
                sha256_hex,
                storage_path,
                parsed.total_pages,
                parsed.total_pages,
                parsed.total_chars,
                parsed.total_chars,
                parsed.table_count,
                parsed.ocr_page_count,
                parsed.inject_mode,
                toc_json_str,
                stocks_json_str,
                stocks_json_str,
                parsed.full_md or parsed.summary_md,
                parsed.summary_md,
                parsed.summary_md,
                meta_json_str,
            ),
        )

        for p in parsed.pages:
            cur.execute(
                """
                INSERT INTO chat_uploaded_file_pages (
                    file_id, page_no, page_title, content_md, page_markdown,
                    char_count, table_count, has_table, used_ocr, is_ocr
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (file_id, page_no) DO UPDATE SET
                    page_title = EXCLUDED.page_title,
                    content_md = EXCLUDED.content_md,
                    page_markdown = EXCLUDED.page_markdown,
                    char_count = EXCLUDED.char_count,
                    table_count = EXCLUDED.table_count,
                    has_table = EXCLUDED.has_table,
                    used_ocr = EXCLUDED.used_ocr,
                    is_ocr = EXCLUDED.is_ocr
                """,
                (
                    file_id,
                    p.page_no,
                    p.page_title,
                    p.content_md,
                    p.content_md,
                    p.char_count,
                    p.table_count,
                    p.has_table,
                    p.used_ocr,
                    p.used_ocr,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "file_id": file_id,
        "user_id": user_id,
        "user_key": user_id,
        "session_id": session_id,
        "filename": filename,
        "file_ext": parsed.file_ext,
        "mime_type": mime_type or "",
        "file_size_bytes": len(raw_bytes),
        "file_size": len(raw_bytes),
        "parse_status": "ready",
        "total_pages": parsed.total_pages,
        "page_count": parsed.total_pages,
        "total_chars": parsed.total_chars,
        "char_count": parsed.total_chars,
        "table_count": parsed.table_count,
        "ocr_page_count": parsed.ocr_page_count,
        "inject_mode": parsed.inject_mode,
        "toc": parsed.toc,
        "detected_stocks": parsed.detected_stocks,
        "related_stocks": parsed.detected_stocks,
        "summary_preview": (parsed.summary_md or "")[:600],
        "extra_meta": parsed.extra_meta,
    }


async def _handle_upload(
    request: Request,
    file: UploadFile,
    session_id: str = "",
) -> dict[str, Any]:
    uid = _uid(request)
    filename = (file.filename or "uploaded.pdf").strip()
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            400,
            f"不支持的文件类型 .{ext}，目前支持上传 PDF、Excel(.xlsx/.xls/.csv)、Word(.docx) 或 Markdown(.md/.txt)",
        )

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(400, "上传的文件内容为空")

    try:
        parsed = parse_uploaded_file(raw_bytes, filename)
    except ValueError as ve:
        raise HTTPException(400, str(ve))
    except Exception as e:
        logger.exception("[chat_files] parse failed for {}: {}", filename, e)
        raise HTTPException(400, f"文件解析失败：{e}")

    record = save_parsed_document_to_db(
        user_id=uid,
        session_id=(session_id or "").strip(),
        filename=filename,
        mime_type=file.content_type or "",
        raw_bytes=raw_bytes,
        parsed=parsed,
    )
    return {"ok": True, "file": record, **record}


@router.post("/chat/files/upload")
async def upload_chat_file(
    request: Request,
    file: UploadFile = File(...),
    session_id: str = Form(""),
):
    """上传并实时解析单个文件（支持 .pdf/.xlsx/.xls/.csv/.docx/.md/.txt）。"""
    return await _handle_upload(request, file, session_id)


@router.post("/v1/files/upload")
async def upload_v1_file(
    request: Request,
    file: UploadFile = File(...),
    session_id: str = Form(""),
):
    """v1 兼容别名：POST /api/v1/files/upload"""
    return await _handle_upload(request, file, session_id)


@router.get("/chat/files")
async def list_chat_files(
    request: Request,
    session_id: str = Query("", description="会话 ID"),
):
    """查询某会话下当前用户已挂载的所有文件列表。"""
    uid = _uid(request)
    _ensure_tables_once()
    conn = get_conn()
    try:
        cur = conn.cursor()
        if session_id:
            cur.execute(
                """
                SELECT file_id, user_id, session_id, filename, file_ext, mime_type,
                       file_size_bytes, parse_status, parse_error, total_pages,
                       total_chars, table_count, ocr_page_count, inject_mode,
                       toc_json, detected_stocks, summary_md, extra_meta, created_at
                FROM chat_uploaded_files
                WHERE user_id = %s AND session_id = %s
                ORDER BY created_at ASC
                """,
                (uid, session_id),
            )
        else:
            cur.execute(
                """
                SELECT file_id, user_id, session_id, filename, file_ext, mime_type,
                       file_size_bytes, parse_status, parse_error, total_pages,
                       total_chars, table_count, ocr_page_count, inject_mode,
                       toc_json, detected_stocks, summary_md, extra_meta, created_at
                FROM chat_uploaded_files
                WHERE user_id = %s
                ORDER BY created_at DESC
                LIMIT 50
                """,
                (uid,),
            )
        rows = cur.fetchall()
        files = [_format_file_record(r) for r in rows]
        return {"ok": True, "files": files}
    finally:
        conn.close()


def _get_file_and_preview(
    uid: str,
    file_id: str,
    start_page: int = 1,
    end_page: int = 5,
    page_no: int | None = None,
) -> dict[str, Any]:
    _ensure_tables_once()
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT file_id, user_id, session_id, filename, file_ext, mime_type,
                   file_size_bytes, parse_status, parse_error, total_pages,
                   total_chars, table_count, ocr_page_count, inject_mode,
                   toc_json, detected_stocks, summary_md, extra_meta, created_at
            FROM chat_uploaded_files
            WHERE file_id = %s
            """,
            (file_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "文件不存在或已过期清理")
        owner_id = str(row[1] or "")
        if owner_id != str(uid):
            raise HTTPException(403, "无权访问该文件")

        file_info = _format_file_record(row)
        if page_no is not None:
            s_p = max(1, int(page_no))
            e_p = s_p
        else:
            s_p = max(1, int(start_page))
            e_p = max(s_p, min(s_p + 20, int(end_page)))

        cur.execute(
            """
            SELECT page_no, page_title, content_md, char_count, table_count, has_table, used_ocr
            FROM chat_uploaded_file_pages
            WHERE file_id = %s AND page_no >= %s AND page_no <= %s
            ORDER BY page_no ASC
            """,
            (file_id, s_p, e_p),
        )
        pages = [
            {
                "page_no": r[0],
                "page_title": r[1],
                "content_md": r[2],
                "page_markdown": r[2],
                "char_count": r[3],
                "table_count": r[4],
                "has_table": bool(r[5]),
                "used_ocr": bool(r[6]),
            }
            for r in cur.fetchall()
        ]
        return {
            "ok": True,
            "file": file_info,
            "toc": file_info["toc"],
            "pages": pages,
            **file_info,
        }
    finally:
        conn.close()


@router.get("/chat/files/{file_id}/preview")
async def preview_chat_file(
    file_id: str,
    request: Request,
    start_page: int = Query(1, ge=1),
    end_page: int = Query(5, ge=1),
    page_no: int | None = Query(None, ge=1),
):
    """获取文件详情、目录及指定页 Markdown 预览（供右侧 ArtifactPanel 渲染原文与表格核对）。"""
    uid = _uid(request)
    return _get_file_and_preview(uid, file_id, start_page=start_page, end_page=end_page, page_no=page_no)


@router.get("/chat/files/{file_id}")
async def get_chat_file_detail(
    file_id: str,
    request: Request,
    start_page: int = Query(1, ge=1),
    end_page: int = Query(5, ge=1),
    page_no: int | None = Query(None, ge=1),
):
    uid = _uid(request)
    return _get_file_and_preview(uid, file_id, start_page=start_page, end_page=end_page, page_no=page_no)


@router.get("/v1/files/{file_id}")
async def get_v1_file_detail(
    file_id: str,
    request: Request,
    start_page: int = Query(1, ge=1),
    end_page: int = Query(5, ge=1),
    page_no: int | None = Query(None, ge=1),
):
    uid = _uid(request)
    return _get_file_and_preview(uid, file_id, start_page=start_page, end_page=end_page, page_no=page_no)


def _delete_user_file(uid: str, file_id: str) -> dict[str, Any]:
    _ensure_tables_once()
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT user_id, storage_path FROM chat_uploaded_files WHERE file_id = %s",
            (file_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "文件不存在")
        if str(row[0] or "") != str(uid):
            raise HTTPException(403, "无权访问该文件")
        storage_path = row[1] or ""
        cur.execute("DELETE FROM chat_uploaded_files WHERE file_id = %s AND user_id = %s", (file_id, uid))
        conn.commit()
        if storage_path:
            abs_p = os.path.join(CHAT_UPLOAD_DIR, storage_path)
            try:
                if os.path.isfile(abs_p):
                    os.remove(abs_p)
            except Exception:
                pass
        return {"ok": True, "file_id": file_id}
    finally:
        conn.close()


@router.delete("/chat/files/{file_id}")
async def delete_chat_file(file_id: str, request: Request):
    uid = _uid(request)
    return _delete_user_file(uid, file_id)


@router.delete("/v1/files/{file_id}")
async def delete_v1_file(file_id: str, request: Request):
    uid = _uid(request)
    return _delete_user_file(uid, file_id)


class V1ContextIn(BaseModel):
    session_id: str = ""
    user_id: str | None = None
    user_key: str | None = None
    file_ids: list[str] = []


@router.post("/v1/internal/files/context")
async def v1_files_context(body: V1ContextIn, request: Request):
    uid = (body.user_id or body.user_key or "").strip()
    if not uid:
        uid = _uid(request)
    return build_files_context_bundle(uid, body.file_ids, body.session_id)


class V1SearchIn(BaseModel):
    file_id: str
    keyword: str
    limit: int = 5
    user_id: str | None = None


@router.post("/v1/internal/files/search")
async def v1_files_search(body: V1SearchIn, request: Request):
    uid = (body.user_id or "").strip() or _uid(request)
    return search_in_uploaded_file(uid, body.file_id, body.keyword, body.limit)


class V1ReadPagesIn(BaseModel):
    file_id: str
    start_page: int = 1
    end_page: int = 5
    user_id: str | None = None


@router.post("/v1/internal/files/read_pages")
async def v1_files_read_pages(body: V1ReadPagesIn, request: Request):
    uid = (body.user_id or "").strip() or _uid(request)
    return read_uploaded_file_pages(uid, body.file_id, body.start_page, body.end_page)


# ─────────────────────────────────────────────────────────────────────
# 内部上下文组装、关键词搜索、按页读取服务函数（供 internal_tools 与 BFF 调用）
# ─────────────────────────────────────────────────────────────────────

def build_files_context_bundle(
    user_id: str,
    file_ids: list[str],
    session_id: str = "",
) -> dict[str, Any]:
    """根据 file_ids 校验 user_id 归属，并组装 <uploaded-documents> 上下文与关联股票列表。"""
    _ensure_tables_once()
    clean_ids = [str(fid).strip() for fid in (file_ids or []) if str(fid).strip()]
    if not clean_ids:
        return {"ok": True, "context_md": "", "detected_stocks": [], "files": []}

    conn = get_conn()
    try:
        cur = conn.cursor()
        doc_blocks: list[str] = []
        all_stocks: list[dict[str, Any]] = []
        seen_stock_codes: set[str] = set()
        file_metas: list[dict[str, Any]] = []

        for fid in clean_ids[:10]:
            cur.execute(
                """
                SELECT file_id, user_id, session_id, filename, file_ext,
                       total_pages, total_chars, table_count, inject_mode,
                       detected_stocks, summary_md, extra_meta
                FROM chat_uploaded_files
                WHERE file_id = %s
                """,
                (fid,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, f"文件 {fid} 不存在或已过期")
            owner_id = str(row[1] or "")
            if user_id and owner_id != str(user_id):
                raise HTTPException(403, "无权访问该文件")

            (
                r_fid,
                _r_uid,
                _r_sid,
                r_fname,
                r_ext,
                r_pages,
                r_chars,
                r_tables,
                r_mode,
                r_stocks,
                r_summary,
                r_meta,
            ) = row

            stocks_list = r_stocks if isinstance(r_stocks, list) else []
            for s in stocks_list:
                if isinstance(s, dict) and s.get("code") and s["code"] not in seen_stock_codes:
                    seen_stock_codes.add(s["code"])
                    all_stocks.append(s)

            stocks_desc = (
                "、".join(f"{s.get('name', '')}({s.get('code', '')})" for s in stocks_list[:6])
                if stocks_list
                else "未检测到显式股票代码"
            )
            mode_desc = (
                "全文直通模式（已包含完整每页正文与表格）"
                if r_mode == "full"
                else "长文档目录+黄金摘要模式（如需查看未展开页码请调用 file_search_in_doc 或 file_read_pages 工具）"
            )
            header = (
                f"#### 📄 文档：《{r_fname}》（file_id: `{r_fid}` · 格式: `{r_ext}` · "
                f"共 {r_pages} 页 · {r_chars} 字 · {r_tables} 张表格 · 模式: `{r_mode}` {mode_desc}）\n"
                f"- **识别到的关联标的**：{stocks_desc}\n\n"
            )
            doc_blocks.append(header + (r_summary or ""))
            file_metas.append(
                {
                    "file_id": r_fid,
                    "filename": r_fname,
                    "file_ext": r_ext,
                    "total_pages": r_pages,
                    "total_chars": r_chars,
                    "table_count": r_tables,
                    "inject_mode": r_mode,
                    "detected_stocks": stocks_list,
                    "extra_meta": r_meta if isinstance(r_meta, dict) else {},
                }
            )

        context_md = "\n\n---\n\n".join(doc_blocks)
        return {
            "ok": True,
            "context_md": context_md,
            "context_prompt": context_md,
            "detected_stocks": all_stocks,
            "related_stocks": all_stocks,
            "files": file_metas,
        }
    finally:
        conn.close()


def search_in_uploaded_file(
    user_id: str,
    file_id: str,
    keyword: str,
    limit: int = 5,
) -> dict[str, Any]:
    """在上传文档的分页表中搜索关键词，优先返回包含表格的命中页及前后文。"""
    _ensure_tables_once()
    kw = (keyword or "").strip()
    if not kw:
        return {"ok": False, "error": "关键词不能为空", "matches": []}

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT user_id, filename, total_pages FROM chat_uploaded_files WHERE file_id = %s",
            (file_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "文件不存在")
        if user_id and str(row[0] or "") != str(user_id):
            raise HTTPException(403, "无权访问该文件")
        filename, total_pages = row[1], row[2]

        cur.execute(
            """
            SELECT page_no, page_title, content_md, has_table
            FROM chat_uploaded_file_pages
            WHERE file_id = %s AND content_md ILIKE %s
            ORDER BY has_table DESC, page_no ASC
            LIMIT %s
            """,
            (file_id, f"%{kw}%", max(1, min(10, int(limit or 5)))),
        )
        matches: list[dict[str, Any]] = []
        total_out_bytes = 0
        for p_no, p_title, c_md, has_tb in cur.fetchall():
            snippet = c_md or ""
            if len(snippet.encode("utf-8")) > 3500:
                idx = snippet.lower().find(kw.lower())
                if idx >= 0:
                    start = max(0, idx - 600)
                    end = min(len(snippet), idx + 1200)
                    snippet = (
                        f"=== [来源：{filename} · 第 {p_no} 页（节选）] ===\n"
                        + snippet[start:end]
                    )
                else:
                    snippet = snippet[:1800]
            b_len = len(snippet.encode("utf-8"))
            if total_out_bytes + b_len > MAX_READ_BYTES and matches:
                break
            total_out_bytes += b_len
            matches.append(
                {
                    "page_no": p_no,
                    "page_title": p_title,
                    "has_table": bool(has_tb),
                    "content_md": snippet,
                }
            )

        return {
            "ok": True,
            "file_id": file_id,
            "filename": filename,
            "total_pages": total_pages,
            "keyword": kw,
            "match_count": len(matches),
            "matches": matches,
        }
    finally:
        conn.close()


def read_uploaded_file_pages(
    user_id: str,
    file_id: str,
    start_page: int = 1,
    end_page: int = 5,
) -> dict[str, Any]:
    """按页码区间读取上传文档内容，单次最多跨 8 页，总输出严格限制在 14KB 以内。"""
    _ensure_tables_once()
    s_p = max(1, int(start_page or 1))
    e_p = max(s_p, min(s_p + MAX_PAGE_READ_SPAN - 1, int(end_page or s_p)))

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT user_id, filename, total_pages FROM chat_uploaded_files WHERE file_id = %s",
            (file_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "文件不存在")
        if user_id and str(row[0] or "") != str(user_id):
            raise HTTPException(403, "无权访问该文件")
        filename, total_pages = row[1], row[2]

        cur.execute(
            """
            SELECT page_no, page_title, content_md, has_table
            FROM chat_uploaded_file_pages
            WHERE file_id = %s AND page_no >= %s AND page_no <= %s
            ORDER BY page_no ASC
            """,
            (file_id, s_p, e_p),
        )
        pages_out: list[dict[str, Any]] = []
        combined_parts: list[str] = []
        used_bytes = 0
        truncated = False

        for p_no, p_title, c_md, has_tb in cur.fetchall():
            text_part = c_md or ""
            encoded = text_part.encode("utf-8")
            if used_bytes + len(encoded) > MAX_READ_BYTES:
                remain = max(0, MAX_READ_BYTES - used_bytes - 200)
                if remain > 200:
                    clipped = encoded[:remain].decode("utf-8", errors="ignore") + "\n...(本页剩余内容因单次 14KB 保护截断)"
                    pages_out.append(
                        {
                            "page_no": p_no,
                            "page_title": p_title,
                            "has_table": bool(has_tb),
                            "content_md": clipped,
                        }
                    )
                    combined_parts.append(clipped)
                truncated = True
                break
            used_bytes += len(encoded)
            pages_out.append(
                {
                    "page_no": p_no,
                    "page_title": p_title,
                    "has_table": bool(has_tb),
                    "content_md": text_part,
                }
            )
            combined_parts.append(text_part)

        content_md = "\n\n".join(combined_parts)
        return {
            "ok": True,
            "file_id": file_id,
            "filename": filename,
            "total_pages": total_pages,
            "start_page": s_p,
            "end_page": e_p,
            "truncated": truncated,
            "pages": pages_out,
            "content_md": content_md,
        }
    finally:
        conn.close()


def cleanup_expired_chat_files() -> dict[str, int]:
    """清理过期的数据库记录（>7天）与磁盘临时文件（>24小时）。"""
    _ensure_tables_once()
    deleted_db = 0
    deleted_disk = 0
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM chat_uploaded_files WHERE expires_at < NOW()")
        deleted_db = cur.rowcount or 0
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.warning("[chat_files] cleanup db failed: {}", e)
    finally:
        conn.close()

    try:
        if os.path.isdir(CHAT_UPLOAD_DIR):
            now_ts = datetime.now(timezone.utc).timestamp()
            for entry in os.listdir(CHAT_UPLOAD_DIR):
                full_p = os.path.join(CHAT_UPLOAD_DIR, entry)
                if os.path.isdir(full_p):
                    mtime = os.path.getmtime(full_p)
                    if now_ts - mtime > 86400 * 2:
                        shutil.rmtree(full_p, ignore_errors=True)
                        deleted_disk += 1
    except Exception as e:
        logger.warning("[chat_files] cleanup disk failed: {}", e)

    return {"deleted_db": deleted_db, "deleted_disk": deleted_disk}
