"""Local email + password + JWT auth · Hunter Community P3.

Replaces the SaaS agentpit-DB-backed auth with a fully local system:
  - argon2id password hashing
  - JWT (HS256) access tokens · 1h TTL
  - Opaque refresh tokens (sha256-hashed at rest) · 30d TTL · rotated on use
  - First registered user auto-promoted to admin
  - Registration modes: open · invite · closed

Depends on tables in db/migrations/0001_local_users.sql. `_ensure_auth_tables()`
also runs the same DDL idempotently on first request, so upgrades from an
existing DB (P1/P2 volume) work without a wipe.
"""
import os
import time
import secrets
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHash
from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, EmailStr, Field

from app.services.database import get_conn

router = APIRouter()
ph = PasswordHasher()

# JWT signing · rotate JWT_SECRET on any deployment that has been exposed
JWT_SECRET = os.environ.get("JWT_SECRET", "")
JWT_ACCESS_TTL = int(os.getenv("JWT_ACCESS_TTL", "3600"))         # 1h
JWT_REFRESH_TTL = int(os.getenv("JWT_REFRESH_TTL", "2592000"))    # 30d

# Registration policy · open (default) · invite · closed
REGISTRATION_MODE = os.getenv("REGISTRATION_MODE", "open").lower()
if REGISTRATION_MODE not in ("open", "invite", "closed"):
    logger.warning("[auth] unknown REGISTRATION_MODE={} · falling back to 'closed'",
                   REGISTRATION_MODE)
    REGISTRATION_MODE = "closed"


# ─── Schema ────────────────────────────────────────────────

_DDL = """
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS users (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email          VARCHAR(255) UNIQUE NOT NULL,
  email_lower    VARCHAR(255) UNIQUE NOT NULL,
  pw_hash        TEXT NOT NULL,
  role           VARCHAR(20) NOT NULL DEFAULT 'user' CHECK (role IN ('user','admin')),
  display_name   VARCHAR(80),
  avatar_url     TEXT,
  status         VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
  email_verified BOOLEAN NOT NULL DEFAULT FALSE,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_login     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_users_email_lower ON users(email_lower);

CREATE TABLE IF NOT EXISTS user_sessions (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  refresh_hash TEXT NOT NULL,
  expires_at   TIMESTAMPTZ NOT NULL,
  user_agent   TEXT,
  ip           INET,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  revoked_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_user_sessions_user
    ON user_sessions(user_id) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_user_sessions_refresh_hash
    ON user_sessions(refresh_hash);

CREATE TABLE IF NOT EXISTS invite_codes (
  code       VARCHAR(64) PRIMARY KEY,
  created_by UUID REFERENCES users(id),
  used_by    UUID REFERENCES users(id),
  used_at    TIMESTAMPTZ,
  expires_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

_ddl_applied = False


def _ensure_auth_tables() -> None:
    """Run auth DDL once per process. Idempotent · safe on existing DB."""
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
        logger.error("[auth] ensure_auth_tables failed: {}", e)
        raise


# ─── Schemas ───────────────────────────────────────────────

class RegisterReq(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: Optional[str] = Field(default=None, max_length=80)
    invite_code: Optional[str] = None


class LoginReq(BaseModel):
    email: EmailStr
    password: str


class RefreshReq(BaseModel):
    refresh_token: str


class LogoutReq(BaseModel):
    refresh_token: Optional[str] = None


class UserOut(BaseModel):
    id: str
    email: str
    role: str
    display_name: Optional[str] = None


class TokenResp(BaseModel):
    ok: bool = True
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut
    # Legacy compat · older frontends read `token` field
    token: str


# ─── JWT ───────────────────────────────────────────────────

def _sign_access(user_id: str, role: str, email: str) -> str:
    if not JWT_SECRET:
        raise RuntimeError("JWT_SECRET is not configured")
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": user_id,
            "email": email,
            "role": role,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=JWT_ACCESS_TTL)).timestamp()),
            "type": "access",
        },
        JWT_SECRET,
        algorithm="HS256",
    )


def verify_jwt(token: str) -> Optional[dict]:
    """Called by middleware/auth.py and (in the future) hunter-opencode plugin."""
    if not JWT_SECRET:
        return None
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


# ─── Helpers ───────────────────────────────────────────────

def _issue_tokens(user_id: str, role: str, email: str,
                  display_name: Optional[str], request: Request,
                  cur, conn) -> TokenResp:
    access = _sign_access(user_id, role, email)
    refresh_raw = secrets.token_urlsafe(48)
    refresh_hash = hashlib.sha256(refresh_raw.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=JWT_REFRESH_TTL)
    cur.execute(
        """INSERT INTO user_sessions (user_id, refresh_hash, expires_at, user_agent, ip)
           VALUES (%s, %s, %s, %s, %s)""",
        (
            user_id,
            refresh_hash,
            expires_at,
            (request.headers.get("user-agent") or "")[:200],
            request.client.host if request.client else None,
        ),
    )
    conn.commit()
    conn.close()
    return TokenResp(
        access_token=access,
        token=access,
        refresh_token=refresh_raw,
        expires_in=JWT_ACCESS_TTL,
        user=UserOut(id=user_id, email=email, role=role, display_name=display_name),
    )


# ─── Endpoints ─────────────────────────────────────────────

@router.post("/auth/register", response_model=TokenResp)
async def register(body: RegisterReq, request: Request):
    if REGISTRATION_MODE == "closed":
        raise HTTPException(403, "该实例已关闭注册 · 联系管理员开通")

    _ensure_auth_tables()
    email_lower = body.email.lower().strip()
    conn = get_conn()
    cur = conn.cursor()

    # First user = admin. Fetch this before invite check so invite-mode still
    # allows the very first bootstrap user through even without a code.
    cur.execute("SELECT COUNT(*) FROM users")
    is_first = cur.fetchone()[0] == 0
    role = "admin" if is_first else "user"

    if REGISTRATION_MODE == "invite" and not is_first:
        if not body.invite_code:
            conn.close()
            raise HTTPException(400, "此实例需要邀请码")
        cur.execute(
            """SELECT code FROM invite_codes
               WHERE code=%s AND used_at IS NULL
                 AND (expires_at IS NULL OR expires_at > NOW())""",
            (body.invite_code,),
        )
        if not cur.fetchone():
            conn.close()
            raise HTTPException(400, "邀请码无效或已用")

    cur.execute("SELECT id FROM users WHERE email_lower=%s", (email_lower,))
    if cur.fetchone():
        conn.close()
        raise HTTPException(409, "该邮箱已注册")

    pw_hash = ph.hash(body.password)
    cur.execute(
        """INSERT INTO users (email, email_lower, pw_hash, role, display_name)
           VALUES (%s, %s, %s, %s, %s) RETURNING id""",
        (body.email, email_lower, pw_hash, role, body.display_name),
    )
    user_id = str(cur.fetchone()[0])

    if body.invite_code and not is_first:
        cur.execute(
            "UPDATE invite_codes SET used_by=%s, used_at=NOW() WHERE code=%s",
            (user_id, body.invite_code),
        )

    conn.commit()
    logger.info("[auth] registered user={} role={} first={}", email_lower, role, is_first)
    return _issue_tokens(user_id, role, body.email, body.display_name, request, cur, conn)


@router.post("/auth/login", response_model=TokenResp)
async def login(body: LoginReq, request: Request):
    _ensure_auth_tables()
    email_lower = body.email.lower().strip()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT id, pw_hash, role, display_name, status, email
           FROM users WHERE email_lower=%s""",
        (email_lower,),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(401, "邮箱或密码错误")

    user_id, pw_hash, role, display_name, status, email = row
    if status != "active":
        conn.close()
        raise HTTPException(403, "账户已停用")

    try:
        ph.verify(pw_hash, body.password)
    except (VerifyMismatchError, InvalidHash):
        conn.close()
        raise HTTPException(401, "邮箱或密码错误")

    if ph.check_needs_rehash(pw_hash):
        cur.execute("UPDATE users SET pw_hash=%s WHERE id=%s",
                    (ph.hash(body.password), user_id))
    cur.execute("UPDATE users SET last_login=NOW() WHERE id=%s", (user_id,))
    conn.commit()
    return _issue_tokens(str(user_id), role, email, display_name, request, cur, conn)


@router.post("/auth/refresh", response_model=TokenResp)
async def refresh(body: RefreshReq, request: Request):
    _ensure_auth_tables()
    if not body.refresh_token:
        raise HTTPException(400, "refresh_token missing")
    refresh_hash = hashlib.sha256(body.refresh_token.encode()).hexdigest()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT s.user_id, u.role, u.email, u.display_name
           FROM user_sessions s JOIN users u ON u.id = s.user_id
           WHERE s.refresh_hash=%s AND s.revoked_at IS NULL
             AND s.expires_at > NOW() AND u.status='active'""",
        (refresh_hash,),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(401, "refresh_token 无效或已过期")
    uid, role, email, display_name = row
    # rotate: revoke the old refresh
    cur.execute("UPDATE user_sessions SET revoked_at=NOW() WHERE refresh_hash=%s",
                (refresh_hash,))
    return _issue_tokens(str(uid), role, email, display_name, request, cur, conn)


@router.post("/auth/logout")
async def logout(body: LogoutReq):
    if not body.refresh_token:
        return {"ok": True}
    refresh_hash = hashlib.sha256(body.refresh_token.encode()).hexdigest()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE user_sessions SET revoked_at=NOW() WHERE refresh_hash=%s",
                (refresh_hash,))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.get("/auth/me")
async def me(request: Request):
    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(401, "需要登录")
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT id, email, role, display_name, avatar_url, created_at, last_login
           FROM users WHERE id=%s""",
        (uid,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "用户不存在")
    return {
        "id": str(row[0]),
        "email": row[1],
        "role": row[2],
        "display_name": row[3],
        "avatar_url": row[4],
        "created_at": row[5].isoformat() if row[5] else None,
        "last_login": row[6].isoformat() if row[6] else None,
    }


@router.get("/auth/status")
async def status():
    """Public probe. Frontend uses this to decide whether to route
    a first-time visitor to /register (SetupWizard) instead of /login."""
    _ensure_auth_tables()
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users WHERE role='admin'")
        admin_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM users")
        user_count = cur.fetchone()[0]
        conn.close()
        return {
            "ok": True,
            "admin_exists": admin_count > 0,
            "needs_setup": user_count == 0,
            "user_count": user_count,
            "registration_mode": REGISTRATION_MODE,
        }
    except Exception as e:
        logger.warning("[auth] status probe failed: {}", e)
        return {
            "ok": False,
            "admin_exists": None,
            "needs_setup": None,
            "registration_mode": REGISTRATION_MODE,
            "error": "db_unreachable",
        }


# ─── Legacy display_name endpoints (kept for existing frontend) ───

@router.get("/auth/profile")
async def get_profile(request: Request):
    """Legacy · reads display_name from user_preference for old callers."""
    try:
        from app.services.database import get_user_preference
        pref = get_user_preference(request.state.user_id)
        display_name = (pref.get("extra") or {}).get("display_name", "")
    except Exception:
        display_name = ""
    return {"display_name": display_name}


class ProfileUpdateIn(BaseModel):
    display_name: str


@router.patch("/auth/profile")
async def update_profile(request: Request, body: ProfileUpdateIn):
    """Legacy · mirrors display_name to user_preference.extra."""
    import json as _json
    from app.services.database import get_conn as _db
    name = body.display_name.strip()[:30]
    conn = _db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO user_preference (user_id, extra, updated_at)
        VALUES (%s, %s::jsonb, NOW())
        ON CONFLICT (user_id) DO UPDATE
        SET extra      = user_preference.extra || %s::jsonb,
            updated_at = NOW()
        """,
        (
            request.state.user_id,
            _json.dumps({"display_name": name}),
            _json.dumps({"display_name": name}),
        ),
    )
    conn.commit()
    conn.close()
    # Also propagate to users.display_name
    try:
        conn2 = _db()
        cur2 = conn2.cursor()
        cur2.execute("UPDATE users SET display_name=%s WHERE id=%s",
                     (name, request.state.user_id))
        conn2.commit()
        conn2.close()
    except Exception:
        pass
    return {"ok": True, "display_name": name}
