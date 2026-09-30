"""用户策略编辑路由：身份必需，模型和回测均有并发边界。"""
import asyncio
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from app.services.quant import agent_builder as builder

router = APIRouter(prefix="/agent/builder")
_ai_lock = asyncio.Lock()


def uid(request):
    value = getattr(request.state, "user_id", None)
    if not value: raise HTTPException(401, "请先登录")
    return str(value)


async def call(fn, *args):
    try: return await asyncio.to_thread(fn, *args)
    except ValueError as e: raise HTTPException(400, str(e))


async def body(request):
    try: value = await request.json()
    except Exception: raise HTTPException(400, "请求不是有效的 JSON")
    if not isinstance(value, dict): raise HTTPException(400, "请求体必须是对象")
    return value


@router.get("")
async def listing(request: Request):
    items = await call(builder.list_for, uid(request))
    return {"items": items, "schema": builder.catalog(), "execution": builder.EXECUTION, "data_range": await call(builder.data_range)}


@router.post("/recognize")
async def recognize(request: Request):
    user = uid(request)
    b = await body(request)
    language = await call(builder.language_of, b.get("language", "text"))
    if not isinstance(b.get("text"), str) or not b["text"].strip() or len(b["text"]) > 6000:
        raise HTTPException(400, "请输入策略描述，最多六千字")
    if _ai_lock.locked(): raise HTTPException(429, "AI 正在识别其他规则，请稍后重试")
    async with _ai_lock:
        from app.services.quant import screen_quota
        role = getattr(request.state, "user_role", None)
        try:
            await asyncio.to_thread(screen_quota.reserve, user, role, "ai")
        except screen_quota.QuotaExceeded:
            raise HTTPException(429, "今日 AI 识别额度已用完，请稍后再试")
        try:
            return await call(builder.recognize, b.get("text"), b.get("single") is True, language)
        except HTTPException as e:
            if str(e.detail).startswith(("尚未配置", "AI 识别失败")):
                await asyncio.to_thread(screen_quota.refund, user, role, "ai")
            raise


@router.post("")
async def save(request: Request):
    return await call(builder.save, uid(request), await body(request))


@router.get("/{ident}")
async def get(ident: str, request: Request):
    return await call(builder.read, uid(request), ident)


@router.delete("/research/{line}")
async def delete_research(line: str, request: Request):
    return await call(builder.delete_research, uid(request), line)


@router.delete("/{ident}")
async def delete(ident: str, request: Request):
    return await call(builder.delete, uid(request), ident)


@router.post("/{ident}/backtest")
async def backtest(ident: str, request: Request, bg: BackgroundTasks):
    conn, key, run = await call(builder.submit, uid(request), ident, await body(request))
    bg.add_task(builder.run_job, conn, key, run)
    return run
