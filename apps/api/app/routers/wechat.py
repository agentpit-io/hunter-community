"""微信服务号对外接口
老板的微信服务号服务器调用此处（Header: X-Wechat-Token）：

  POST /api/wechat/bind            — 绑定微信 openid 与 AgentPit 账号
  POST /api/wechat/watchlist       — 微信端新增自选股（同步到 hermes + finance-data）
  GET  /api/wechat/push-queue      — 读取待发送推送内容
  POST /api/wechat/push-queue/ack  — 标记已发送
"""
import os
from typing import Optional

import bcrypt
import httpx
import psycopg2
from fastapi import APIRouter, Header, HTTPException, Query
from loguru import logger
from pydantic import BaseModel

router = APIRouter()

FINANCE_DATA_URL = os.getenv("FINANCE_DATA_URL", "https://finance-data.agentpit.io")
FINANCE_DATA_TOKEN = os.getenv("FINANCE_DATA_TOKEN", "FinAPI@2026!")
WECHAT_API_TOKEN = os.getenv("WECHAT_API_TOKEN", "")
AGENTPIT_DATABASE_URL = os.getenv(
    "AGENTPIT_DATABASE_URL",
    ""
)

_FD_HEADERS = {"X-Finance-Token": FINANCE_DATA_TOKEN}


def _check_token(x_wechat_token: str):
    if WECHAT_API_TOKEN and x_wechat_token != WECHAT_API_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid wechat token")


class BindRequest(BaseModel):
    openid: str
    email: str
    password: str


class WatchlistRequest(BaseModel):
    openid: str
    code: str
    name: str
    market: str = "A"
    exchange: str = "SZ"
    asset_type: str = "stock"


class AckRequest(BaseModel):
    ids: list[int]


@router.post("/wechat/bind")
async def bind(req: BindRequest, x_wechat_token: str = Header(default="")):
    _check_token(x_wechat_token)

    # 验证 AgentPit 账号密码
    try:
        conn = psycopg2.connect(AGENTPIT_DATABASE_URL)
        cur = conn.cursor()
        cur.execute(
            'SELECT id, email, password FROM "apbase_User" WHERE email = %s',
            (req.email.lower().strip(),)
        )
        row = cur.fetchone()
        conn.close()
    except Exception as e:
        logger.error("wechat bind DB error: {}", e)
        raise HTTPException(500, "数据库错误")

    if not row:
        raise HTTPException(400, "账号不存在")
    user_id, email, pw_hash = row
    if not bcrypt.checkpw(req.password.encode(), pw_hash.encode()):
        raise HTTPException(400, "密码错误")

    # 写绑定到 finance-data
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(
            f"{FINANCE_DATA_URL}/api/v1/internal/wechat/bind",
            headers=_FD_HEADERS,
            json={"user_id": user_id, "email": email, "openid": req.openid},
        )
    if r.status_code != 200:
        logger.error("wechat bind finance-data error: {}", r.text)
        raise HTTPException(500, "绑定存储失败")

    return {"ok": True, "message": "绑定成功", "email": email}


@router.post("/wechat/watchlist")
async def add_watchlist(req: WatchlistRequest, x_wechat_token: str = Header(default="")):
    _check_token(x_wechat_token)

    # 查 openid → user_id
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(
            f"{FINANCE_DATA_URL}/api/v1/internal/wechat/bind/{req.openid}",
            headers=_FD_HEADERS,
        )
    data = r.json()
    if not data.get("bound"):
        raise HTTPException(400, "该微信用户尚未绑定 AgentPit 账号，请先绑定")
    user_id = data["user_id"]

    # 写入 hermes 自选股
    from app.services.database import add_stock_by_user
    try:
        add_stock_by_user(req.code, req.name, req.market, req.exchange, user_id, req.asset_type)
    except Exception as e:
        logger.error("wechat add watchlist error: {}", e)
        raise HTTPException(500, "添加自选股失败")

    # 同步到 finance-data 采集列表
    async with httpx.AsyncClient(timeout=10, verify=False) as c:
        await c.post(
            f"{FINANCE_DATA_URL}/api/v1/watchlist/subscribe",
            headers=_FD_HEADERS,
            json={
                "code": req.code, "name": req.name,
                "market": req.market, "exchange": req.exchange,
                "asset_type": req.asset_type,
            },
        )

    return {"ok": True, "message": f"{req.name}（{req.code}）已加入自选股"}


@router.get("/wechat/push-queue")
async def get_push_queue(
    openid: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    x_wechat_token: str = Header(default=""),
):
    _check_token(x_wechat_token)
    params: dict = {"limit": limit}
    if openid:
        params["openid"] = openid
    async with httpx.AsyncClient(timeout=10, verify=False) as c:
        r = await c.get(
            f"{FINANCE_DATA_URL}/api/v1/internal/wechat/push-queue",
            headers=_FD_HEADERS,
            params=params,
        )
    return r.json()


@router.post("/wechat/push-queue/ack")
async def ack_push(req: AckRequest, x_wechat_token: str = Header(default="")):
    _check_token(x_wechat_token)
    async with httpx.AsyncClient(timeout=10, verify=False) as c:
        r = await c.post(
            f"{FINANCE_DATA_URL}/api/v1/internal/wechat/push-queue/ack",
            headers=_FD_HEADERS,
            json={"ids": req.ids},
        )
    return r.json()
