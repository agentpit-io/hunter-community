"""用户鉴权：直接验证 AgentPit 用户表，生成本地 JWT。"""
import os
import time
import psycopg2
import bcrypt
import jwt
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()

# AgentPit 数据库连接（hermes 与 AgentPit 在同一 GCP 内网）
AGENTPIT_DB_URL = os.environ.get(
    "AGENTPIT_DATABASE_URL",
    ""
)

JWT_SECRET = os.environ.get("JWT_SECRET", "")
JWT_EXPIRE = 7 * 24 * 3600  # 7天


class LoginIn(BaseModel):
    email: str
    password: str


def _get_agentpit_user(email: str) -> dict | None:
    """从 AgentPit 数据库查询用户。"""
    try:
        conn = psycopg2.connect(AGENTPIT_DB_URL)
        cur = conn.cursor()
        cur.execute(
            'SELECT id, email, password, name, role FROM "apbase_User" WHERE email = %s',
            (email.lower().strip(),),
        )
        row = cur.fetchone()
        conn.close()
        if row:
            return {"id": row[0], "email": row[1], "password": row[2],
                    "name": row[3], "role": row[4]}
    except Exception:
        pass
    return None


def _make_token(user_id: str, email: str, role: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "iat": int(time.time()),
        "exp": int(time.time()) + JWT_EXPIRE,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def verify_jwt(token: str) -> dict | None:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except Exception:
        return None


@router.post("/auth/login")
async def login(body: LoginIn):
    """用 AgentPit 账号登录，返回本地 JWT token。"""
    user = _get_agentpit_user(body.email)
    if not user:
        return {"ok": False, "error": "邮箱或密码错误"}

    # 验证 bcrypt 密码
    try:
        pwd_match = bcrypt.checkpw(
            body.password.encode("utf-8"),
            user["password"].encode("utf-8"),
        )
    except Exception:
        pwd_match = False

    if not pwd_match:
        return {"ok": False, "error": "邮箱或密码错误"}

    token = _make_token(user["id"], user["email"], user["role"] or "USER")
    return {
        "ok": True,
        "token": token,
        "user": {"id": user["id"], "email": user["email"], "name": user["name"]},
    }


@router.get("/auth/agentpit-sso")
async def agentpit_sso(apt: str):
    """
    Verify an agentpit finance access token (apt) and return a hermes JWT.
    Called by the hermes /sso page after redirect from agentpit.io.
    """
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://www.agentpit.io/api/v1/finance/verify-token",
                headers={"Authorization": f"Bearer {apt}"},
            )
        data = r.json()
        if not data.get("ok"):
            return {"ok": False, "error": "invalid agentpit token"}
        user_id = data["userId"]
        email = data.get("email") or ""
        role = data.get("role") or "USER"
        token = _make_token(user_id, email, role)
        return {"ok": True, "token": token, "user": {"id": user_id, "email": email}}
    except Exception:
        return {"ok": False, "error": "sso verification failed"}


@router.get("/auth/me")
async def me(request: Request):
    return {"user_id": request.state.user_id, "authenticated": True}


@router.get("/auth/profile")
async def get_profile(request: Request):
    """读取用户展示名（存于 user_preference.extra.display_name）。"""
    from app.services.database import get_user_preference
    pref = get_user_preference(request.state.user_id)
    display_name = (pref.get("extra") or {}).get("display_name", "")
    return {"display_name": display_name}


class ProfileUpdateIn(BaseModel):
    display_name: str


@router.patch("/auth/profile")
async def update_profile(request: Request, body: ProfileUpdateIn):
    """更新用户展示名，写入 user_preference.extra.display_name。"""
    import json as _json
    from app.services.database import get_conn
    name = body.display_name.strip()[:30]
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO user_preference (user_id, extra, updated_at)
        VALUES (%s, %s::jsonb, NOW())
        ON CONFLICT (user_id) DO UPDATE
        SET extra      = user_preference.extra || %s::jsonb,
            updated_at = NOW()
        """,
        (request.state.user_id,
         _json.dumps({"display_name": name}),
         _json.dumps({"display_name": name})),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "display_name": name}


@router.get("/auth/status")
async def status():
    return {"ok": True}
