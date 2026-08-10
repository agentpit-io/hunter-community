"""Artifact 发布 · 公开链接系统

用户在 chat 里点"Publish artifact" → 生成短 URL · 匿名可访问
遵 Claude 规则:unpublish 后不可 republish · 强制新 artifact (防归属替换)

Endpoints:
  POST /api/artifacts/publish              发布 (需登录)
  POST /api/artifacts/{short_id}/unpublish  撤销 (仅所有者)
  GET  /api/artifacts/status?message_id=x   查发布状态 (需登录)
  GET  /api/public/artifacts/{short_id}     匿名访问 · view_count 递增

⚠️ 公开访问必须无 auth · 单独走 /api/public 前缀
"""
import logging
import secrets
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.services.database import get_conn

log = logging.getLogger(__name__)
router = APIRouter()


def _uid(request: Request) -> str:
    """当前登录用户。middleware 已验签 JWT 并注入 request.state.user_id。"""
    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(401, "需要登录")
    return str(uid)


def _gen_short_id() -> str:
    """生成短 URL id · e.g. 'aa42f292-bce1' · 与 Claude 格式一致
    8 hex + 4 hex · 12 位 + 1 分隔符 = 128 位随机 · 不可爆破
    """
    return f"{secrets.token_hex(4)}-{secrets.token_hex(2)}"


def _iso(ts) -> str | None:
    if ts is None:
        return None
    if isinstance(ts, str):
        return ts
    return ts.isoformat()


# ─── 请求/响应模型 ────────────────────────────────────────

class PublishReq(BaseModel):
    session_id: str | None = None
    message_id: str = Field(..., min_length=1, max_length=64)
    title: str = Field(..., min_length=1, max_length=200)
    # Sprint E · 支持 markdown 与 html 两种类型
    artifact_type: str = Field("markdown", pattern="^(markdown|html)$")
    content_md: str | None = None       # markdown 时必填
    content_html: str | None = None     # html 时必填
    metadata: dict | None = None


class PublishResp(BaseModel):
    short_id: str
    public_url: str
    published_at: str
    title: str
    artifact_type: str = "markdown"


class StatusResp(BaseModel):
    published: bool
    short_id: str | None = None
    public_url: str | None = None
    published_at: str | None = None
    unpublished_at: str | None = None
    republish_blocked: bool = False
    artifact_type: str | None = None


# ─── 端点 ────────────────────────────────────────────────

@router.post("/artifacts/publish", response_model=PublishResp)
def publish_artifact(req: PublishReq, request: Request):
    """发布 artifact · 生成公开短链

    支持两种类型:
    · markdown - 传 content_md
    · html - 传 content_html · Sprint E 新增
    规则:每个 (owner, message_id) 组合只允许发布一次 · unpublish 后不可 republish
    """
    uid = _uid(request)

    # 参数校验 · 类型与内容必须匹配
    if req.artifact_type == "markdown":
        if not req.content_md or not req.content_md.strip():
            raise HTTPException(400, "markdown 类型必须提供 content_md")
        content_md = req.content_md
        content_html = None
    elif req.artifact_type == "html":
        if not req.content_html or not req.content_html.strip():
            raise HTTPException(400, "html 类型必须提供 content_html")
        content_md = None
        content_html = req.content_html
    else:
        raise HTTPException(400, f"不支持的 artifact_type: {req.artifact_type}")

    c = get_conn(); cur = c.cursor()
    try:
        # 先查是否已有记录 (含已 unpublished 的)
        cur.execute("""
            SELECT short_id, is_published, artifact_type
            FROM hunter_artifacts.published_artifact
            WHERE owner_user_id = %s AND source_message_id = %s
        """, (uid, req.message_id))
        row = cur.fetchone()
        if row:
            short_id, is_pub, existing_type = row
            if is_pub:
                # 幂等返回
                return PublishResp(
                    short_id=short_id,
                    public_url=_public_url(short_id, request),
                    published_at=datetime.now().isoformat(),
                    title=req.title,
                    artifact_type=existing_type or "markdown",
                )
            else:
                raise HTTPException(
                    409,
                    "此内容已发布过并撤销 · 不可 republish · 请修改内容或新建对话后重试"
                )

        # 生成新 short_id · 若冲突重试
        short_id = _gen_short_id()
        for _ in range(5):
            cur.execute("SELECT 1 FROM hunter_artifacts.published_artifact WHERE short_id = %s", (short_id,))
            if cur.fetchone() is None:
                break
            short_id = _gen_short_id()

        cur.execute("""
            INSERT INTO hunter_artifacts.published_artifact
              (short_id, owner_user_id, session_id, source_message_id, title,
               artifact_type, content_md, content_html, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            RETURNING published_at
        """, (short_id, uid, req.session_id, req.message_id, req.title,
              req.artifact_type, content_md, content_html,
              __import__('json').dumps(req.metadata or {})))
        published_at = cur.fetchone()[0]
        c.commit()

        log.info(f"[artifact] published · owner={uid} · short={short_id} · type={req.artifact_type} · msg={req.message_id}")

        return PublishResp(
            short_id=short_id,
            public_url=_public_url(short_id, request),
            published_at=_iso(published_at),
            title=req.title,
            artifact_type=req.artifact_type,
        )
    finally:
        c.close()


@router.post("/artifacts/{short_id}/unpublish")
def unpublish_artifact(short_id: str, request: Request):
    """撤销发布 · 仅所有者

    unpublish 后:
    - is_published = false · 匿名访问 404
    - 记 unpublished_at
    - 不删数据 · 保留审计
    - 不可 republish (业务规则)
    """
    uid = _uid(request)
    c = get_conn(); cur = c.cursor()
    try:
        cur.execute("""
            SELECT owner_user_id, is_published
            FROM hunter_artifacts.published_artifact
            WHERE short_id = %s
        """, (short_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "artifact 不存在")
        owner, is_pub = row
        if owner != uid:
            raise HTTPException(403, "只能撤销自己发布的 artifact")
        if not is_pub:
            return {"ok": True, "message": "已是撤销状态", "unpublished_at": None}
        cur.execute("""
            UPDATE hunter_artifacts.published_artifact
            SET is_published = false, unpublished_at = NOW()
            WHERE short_id = %s
            RETURNING unpublished_at
        """, (short_id,))
        unpub_at = cur.fetchone()[0]
        c.commit()
        log.info(f"[artifact] unpublished · owner={uid} · short={short_id}")
        return {"ok": True, "unpublished_at": _iso(unpub_at)}
    finally:
        c.close()


@router.get("/artifacts/status", response_model=StatusResp)
def artifact_status(message_id: str, request: Request):
    """查某 message 的发布状态 · 前端每次打开 artifact 都会调

    Returns:
    - published=true · 显示 URL + Copy link + Unpublish
    - published=false · republish_blocked=true · 显示"曾发布 · 不可 republish"
    - published=false · republish_blocked=false · 显示 Publish 按钮
    """
    uid = _uid(request)
    c = get_conn(); cur = c.cursor()
    try:
        cur.execute("""
            SELECT short_id, is_published, published_at, unpublished_at, artifact_type
            FROM hunter_artifacts.published_artifact
            WHERE owner_user_id = %s AND source_message_id = %s
        """, (uid, message_id))
        row = cur.fetchone()
        if not row:
            return StatusResp(published=False)
        short_id, is_pub, pub_at, unpub_at, atype = row
        if is_pub:
            return StatusResp(
                published=True,
                short_id=short_id,
                public_url=_public_url(short_id, request),
                published_at=_iso(pub_at),
                artifact_type=atype or "markdown",
            )
        else:
            return StatusResp(
                published=False,
                short_id=short_id,
                published_at=_iso(pub_at),
                unpublished_at=_iso(unpub_at),
                republish_blocked=True,
                artifact_type=atype or "markdown",
            )
    finally:
        c.close()


@router.get("/public/artifacts/{short_id}")
def get_public_artifact(short_id: str, request: Request):
    """匿名访问 · 无 auth

    ⚠️ 此接口不需 JWT · main.py 的中间件应 whitelist /api/public/
    """
    c = get_conn(); cur = c.cursor()
    try:
        cur.execute("""
            SELECT title, artifact_type, content_md, content_html, published_at, view_count
            FROM hunter_artifacts.published_artifact
            WHERE short_id = %s AND is_published = true
        """, (short_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "artifact 不存在或已撤销发布")
        title, atype, content_md, content_html, pub_at, view_count = row
        cur.execute("""
            UPDATE hunter_artifacts.published_artifact
            SET view_count = view_count + 1
            WHERE short_id = %s AND is_published = true
        """, (short_id,))
        c.commit()
        return {
            "short_id": short_id,
            "title": title,
            "artifact_type": atype or "markdown",
            "content_md": content_md,
            "content_html": content_html,
            "published_at": _iso(pub_at),
            "view_count": view_count + 1,
        }
    finally:
        c.close()


def _public_url(short_id: str, request: Request) -> str:
    """构造 public URL · 从请求 host 推 · 便本地/生产都能用"""
    host = request.headers.get("host", "hunter.agentpit.io")
    # 生产走 https · 本地可能 http · 用请求 scheme
    scheme = request.headers.get("x-forwarded-proto") or ("https" if "agentpit" in host else "http")
    return f"{scheme}://{host}/public/artifacts/{short_id}"
