"""用户策略编辑路由：身份必需，模型和回测均有并发边界。"""
import asyncio
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from app.services.quant import agent_builder as builder

router = APIRouter(prefix="/agent/builder")
_ai_jobs = {}  # 当前进程按账号登记可取消任务；本地部署为单API进程。
_AI_TIMEOUT = 90


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


@router.get("/recognize/status")
async def recognition_status(request: Request):
    task = _ai_jobs.get(uid(request))
    return {"running": bool(task and not task.done())}


@router.post("/recognize/cancel")
async def cancel_recognition(request: Request):
    user = uid(request)
    task = _ai_jobs.get(user)
    if task and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    if _ai_jobs.get(user) is task: _ai_jobs.pop(user, None)
    return {"stopped": True}


@router.post("/recognize")
async def recognize(request: Request):
    user = uid(request)
    b = await body(request)
    try: language = builder.language_of(b.get("language", "text"))
    except ValueError as e: raise HTTPException(400, str(e))
    if not isinstance(b.get("text"), str) or not b["text"].strip() or len(b["text"]) > 6000:
        raise HTTPException(400, "请输入策略描述，最多六千字")
    existing = _ai_jobs.get(user)
    if existing and not existing.done():
        raise HTTPException(429, "你有一次识别尚未结束，可点击停止识别后重试")
    if sum(not t.done() for t in _ai_jobs.values()) >= 4:
        raise HTTPException(429, "当前识别请求较多，请稍后重试")

    async def perform():
        from app.services.quant import screen_quota
        role = getattr(request.state, "user_role", None)
        try: await asyncio.to_thread(screen_quota.reserve, user, role, "ai")
        except screen_quota.QuotaExceeded: raise HTTPException(429, "今日 AI 识别额度已用完，请稍后再试")
        try:
            return await builder.recognize_async(b["text"], b.get("single") is True, language)
        except ValueError as e:
            if str(e).startswith(("尚未配置", "AI 识别失败")):
                await asyncio.to_thread(screen_quota.refund, user, role, "ai")
            raise HTTPException(400, str(e))

    task = asyncio.create_task(perform())
    _ai_jobs[user] = task
    try:
        return await asyncio.wait_for(task, timeout=_AI_TIMEOUT)
    except asyncio.TimeoutError:
        raise HTTPException(504, "AI 识别超时，已停止本次等待，请精简内容后重试。原规则未改变。")
    except asyncio.CancelledError:
        raise HTTPException(409, "已停止识别，原始输入和已有规则已保留")
    finally:
        if _ai_jobs.get(user) is task: _ai_jobs.pop(user, None)


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
