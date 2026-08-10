"""User settings · saas-config CRUD + connection test.

All routes are auth-gated (middleware injects request.state.user_id).
Secrets are encrypted at rest via app.utils.crypto (AES-256-GCM).
"""
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

from app.services.database import get_conn
from app.utils.crypto import decrypt, encrypt, mask

router = APIRouter()


# Idempotent DDL · matches db/migrations/0002_user_saas_config.sql
_DDL = """
CREATE TABLE IF NOT EXISTS user_saas_config (
  user_id        UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  data_url       TEXT,
  data_key_enc   TEXT,
  llm_url        TEXT,
  llm_key_enc    TEXT,
  llm_model      VARCHAR(120),
  kronos_url     TEXT,
  kronos_key_enc TEXT,
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

_ddl_applied = False


def _ensure_table() -> None:
    global _ddl_applied
    if _ddl_applied:
        return
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(_DDL)
        conn.commit()
        conn.close()
        _ddl_applied = True
    except Exception as e:
        logger.error("[settings] ensure_table failed: {}", e)
        raise


# ─── Schemas ──────────────────────────────────────────────

class SaasConfigOut(BaseModel):
    data_url: Optional[str] = None
    data_key_masked: Optional[str] = None
    llm_url: Optional[str] = None
    llm_key_masked: Optional[str] = None
    llm_model: Optional[str] = None
    kronos_url: Optional[str] = None
    kronos_key_masked: Optional[str] = None


class SaasConfigPatch(BaseModel):
    """Any field left `None` is left untouched. Empty string clears it."""
    data_url: Optional[str] = None
    data_key: Optional[str] = None
    llm_url: Optional[str] = None
    llm_key: Optional[str] = None
    llm_model: Optional[str] = None
    kronos_url: Optional[str] = None
    kronos_key: Optional[str] = None


class TestReq(BaseModel):
    service: str  # data | llm | kronos
    url: Optional[str] = None    # if omitted, use stored config
    key: Optional[str] = None


# ─── Endpoints ────────────────────────────────────────────

@router.get("/users/me/saas-config", response_model=SaasConfigOut)
async def get_saas_config(request: Request):
    _ensure_table()
    uid = _uid(request)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT data_url, data_key_enc, llm_url, llm_key_enc, llm_model,
                  kronos_url, kronos_key_enc
             FROM user_saas_config WHERE user_id=%s""",
        (uid,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return SaasConfigOut()

    def unmask(enc: str | None) -> str | None:
        if not enc:
            return None
        try:
            return mask(decrypt(enc))
        except Exception:
            return "****"

    return SaasConfigOut(
        data_url=row[0],
        data_key_masked=unmask(row[1]),
        llm_url=row[2],
        llm_key_masked=unmask(row[3]),
        llm_model=row[4],
        kronos_url=row[5],
        kronos_key_masked=unmask(row[6]),
    )


@router.patch("/users/me/saas-config", response_model=SaasConfigOut)
async def patch_saas_config(request: Request, body: SaasConfigPatch):
    _ensure_table()
    uid = _uid(request)

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO user_saas_config (user_id) VALUES (%s)
           ON CONFLICT (user_id) DO NOTHING""",
        (uid,),
    )

    updates: list[tuple[str, object]] = []

    def upd_url(col: str, val: str | None):
        if val is not None:
            updates.append((col, val.strip() or None))

    def upd_key(col: str, val: str | None):
        if val is None:
            return
        updates.append((col, encrypt(val) if val else None))

    upd_url("data_url", body.data_url)
    upd_key("data_key_enc", body.data_key)
    upd_url("llm_url", body.llm_url)
    upd_key("llm_key_enc", body.llm_key)
    upd_url("llm_model", body.llm_model)
    upd_url("kronos_url", body.kronos_url)
    upd_key("kronos_key_enc", body.kronos_key)

    if updates:
        set_clause = ", ".join(f"{col}=%s" for col, _ in updates) + ", updated_at=NOW()"
        params = [val for _, val in updates] + [uid]
        cur.execute(
            f"UPDATE user_saas_config SET {set_clause} WHERE user_id=%s", params
        )
        conn.commit()
    conn.close()
    return await get_saas_config(request)


@router.post("/users/me/saas-config/test")
async def test_saas_config(request: Request, body: TestReq):
    """Probe a URL + key combo. Returns {ok, latency_ms, status, error?}."""
    _ensure_table()
    uid = _uid(request)

    url = (body.url or "").strip()
    key = body.key
    if not url or key is None:
        # Fall back to stored values
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            """SELECT data_url, data_key_enc, llm_url, llm_key_enc,
                      kronos_url, kronos_key_enc
                 FROM user_saas_config WHERE user_id=%s""",
            (uid,),
        )
        row = cur.fetchone()
        conn.close()
        if not row:
            raise HTTPException(400, "no stored config to test · provide url+key")
        colmap = {
            "data": (row[0], row[1]),
            "llm": (row[2], row[3]),
            "kronos": (row[4], row[5]),
        }
        stored_url, enc = colmap.get(body.service, (None, None))
        if not stored_url:
            raise HTTPException(400, f"no {body.service} url configured")
        url = url or stored_url
        if key is None:
            key = decrypt(enc) if enc else ""

    probe = _probe_url_for_service(body.service, url)
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    import time
    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(probe, headers=headers)
        latency_ms = int((time.time() - t0) * 1000)
        return {
            "ok": r.status_code < 400,
            "status": r.status_code,
            "latency_ms": latency_ms,
            "probed": probe,
        }
    except Exception as e:
        return {
            "ok": False,
            "error": str(e)[:200],
            "latency_ms": int((time.time() - t0) * 1000),
            "probed": probe,
        }


def _probe_url_for_service(service: str, url: str) -> str:
    """Guess a cheap GET endpoint per service · keep it read-only."""
    url = url.rstrip("/")
    if service == "data":
        return f"{url}/health"
    if service == "llm":
        # OpenAI-compat convention · /models is cheap and covered by API key
        return f"{url}/models"
    if service == "kronos":
        return f"{url}/health"
    return f"{url}/"


def _uid(request: Request) -> str:
    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(401, "需要登录")
    return uid
