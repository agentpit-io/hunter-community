"""平台自有能力的 MCP 桥接 · /api/internal/cap/*

`_12` §7 Step 3 的后端部分。

**要解决的问题**:K线预测 / 多空辩论 / 在线分析 / 情报 这四个是我们最强的能力,
但它们只有 HTTP 接口、**不是 MCP 工具** —— 模型手上够不着。表现是:
用户点侧栏卡片能出富卡片,在对话里说"帮我预测茅台走势"却经常降级成"我无法获取"。

本文件把它们暴露成 `/api/internal/cap/*`,再由
`scripts/opencode-mcp/hunter_capability_mcp.py` 包成 MCP 工具。
与 internal_tools.py / internal_uzi.py 同一套路:共享 secret 鉴权,不走 JWT。

**为什么不让 MCP 直接打公开接口**:
  · `/api/kpred/*` 虽在白名单里(无需 JWT),但没有用户身份,后续要按 key 计量就没抓手
  · `/api/truesource/*` 和 debate 需要 JWT,MCP 侧没有用户 token
  · 统一走 /api/internal 这一层,身份由 hunter-mcp-context plugin 注入的
    X-Hunter-User-Id 带进来,与已有的 watchlist/portfolio/uzi 保持一致
"""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

router = APIRouter(prefix="/internal/cap", tags=["mcp-bridge-capability"])

_INTERNAL_KEY = os.getenv("HUNTER_INTERNAL_KEY", "")


def _auth(request: Request) -> str:
    """校验共享 secret · 返回 X-Hunter-User-Id(可空,由各能力自己决定是否必需)。"""
    if request.headers.get("X-Hunter-Internal-Key", "") != _INTERNAL_KEY:
        raise HTTPException(401, "internal auth failed")
    uid = request.headers.get("X-Hunter-User-Id", "").strip()
    logger.info("[internal.cap] path={} user_id={}", request.url.path, uid or "(none)")
    return uid


# ─────────────────────────────────────────────────────────────
# ① K 线预测(Kronos)· 感知最强的一个
# ─────────────────────────────────────────────────────────────

class KpredIn(BaseModel):
    code: str
    days: int = 10


@router.post("/kpred")
async def cap_kpred(body: KpredIn, request: Request):
    _auth(request)
    days = max(1, min(body.days, 30))
    # 直接复用公开路由的实现,不做 HTTP 自调 —— 少一跳、少一处超时配置
    from app.routers.kpred import get_kpred
    try:
        return await get_kpred(body.code.strip(), days=days, request=request)
    except HTTPException as e:
        # 把 HTTP 错误转成结构化 body · MCP 会把它交给模型,模型好转述给用户
        return {"error": "kpred_failed", "status": e.status_code, "message": str(e.detail)[:300]}
    except Exception as e:
        logger.exception("[internal.cap] kpred failed code={}", body.code)
        return {"error": "kpred_failed", "message": f"{type(e).__name__}: {e}"[:300]}


# ─────────────────────────────────────────────────────────────
# ② 情报 / 发现(TrueSource)
# ─────────────────────────────────────────────────────────────

class BriefIn(BaseModel):
    symbols: str          # 逗号分隔


@router.post("/truesource_brief")
async def cap_truesource_brief(body: BriefIn, request: Request):
    _auth(request)
    from app.routers.truesource import _proxy
    try:
        return await _proxy("/api/hunter/daily-brief", {"symbols": body.symbols.strip()})
    except HTTPException as e:
        return {"error": "truesource_failed", "status": e.status_code,
                "message": str(e.detail)[:300]}
    except Exception as e:
        logger.exception("[internal.cap] truesource brief failed")
        return {"error": "truesource_failed", "message": f"{type(e).__name__}: {e}"[:300]}


class ScoutIn(BaseModel):
    symbol: str
    name: str | None = None


@router.post("/truesource_scout")
async def cap_truesource_scout(body: ScoutIn, request: Request):
    """主动全量采集 · 30-60s,MCP 侧超时要给够(见 opencode.jsonc timeout)。"""
    _auth(request)
    from app.routers.truesource import _proxy_post
    try:
        return await _proxy_post(f"/api/hunter/scout/{body.symbol.strip()}",
                                 {"name": body.name} if body.name else None)
    except HTTPException as e:
        return {"error": "scout_failed", "status": e.status_code, "message": str(e.detail)[:300]}
    except Exception as e:
        logger.exception("[internal.cap] scout failed symbol={}", body.symbol)
        return {"error": "scout_failed", "message": f"{type(e).__name__}: {e}"[:300]}
