"""SSO 桥：将 hermes JWT 转换为 finance access token，跳转到 sentinel 等子站。
避免在微信浏览器中经过 agentpit.io（该域名被微信安全拦截）。
"""
import os
import time
from fastapi import APIRouter
from fastapi.responses import RedirectResponse
import jwt as pyjwt

router = APIRouter()

_HERMES_SECRET   = os.getenv("JWT_SECRET", "")
_FINANCE_SECRET  = os.getenv("FINANCE_GATEWAY_SECRET", "")
_SENTINEL_URL    = os.getenv("SENTINEL_URL", "https://sentinel.agentpit.io")

_ALLOWED_SUBS = {"sentinel": _SENTINEL_URL}


@router.get("/sso/{sub}")
async def sso_bridge(sub: str, token: str = "", path: str = "/online-analysis"):
    """验证 hermes JWT → 签发 finance access token → 302 跳转子站。"""
    base_url = _ALLOWED_SUBS.get(sub)
    if not base_url:
        return RedirectResponse("/wx/home", status_code=302)

    if not token or not _FINANCE_SECRET:
        target = base_url + (path if path.startswith("/") else "/" + path)
        return RedirectResponse(target, status_code=302)

    try:
        payload = pyjwt.decode(token, _HERMES_SECRET, algorithms=["HS256"])
        user_id = payload.get("sub", "")
        email   = payload.get("email", "")
        role    = payload.get("role", "USER")
    except Exception:
        target = base_url + (path if path.startswith("/") else "/" + path)
        return RedirectResponse(target, status_code=302)

    now = int(time.time())
    finance_payload = {
        "sub":   user_id,
        "email": email,
        "role":  role,
        "subdomain": sub,
        "iss":       "agentpit-finance",
        "iat":   now,
        "exp":   now + 900,   # 15 分钟
    }
    apt = pyjwt.encode(finance_payload, _FINANCE_SECRET, algorithm="HS256")
    import urllib.parse
    target_path = path if path.startswith("/") else "/" + path
    redirect = f"{base_url}{target_path}?apt={urllib.parse.quote(apt)}"
    return RedirectResponse(redirect, status_code=302)
