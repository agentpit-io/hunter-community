"""个人实验 API。每次读取、启动、停止均从登录身份确定所有者。"""
import asyncio
from fastapi import APIRouter, BackgroundTasks, Request, HTTPException
from app.services.quant import agent_experiments as experiments

router = APIRouter(prefix="/agent/experiments")


def uid(request):
    value = getattr(request.state, "user_id", None)
    if not value:
        raise HTTPException(401, "请先登录")
    return str(value)


async def call(fn, *args):
    try:
        return await asyncio.to_thread(fn, *args)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("")
async def listing(request: Request):
    return await call(experiments.listing, uid(request))


@router.post("")
async def create(request: Request, bg: BackgroundTasks):
    user = uid(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "请求不是有效 JSON")
    run = await call(experiments.create, user, body)
    bg.add_task(experiments.launch, user, run["id"], "plan")
    return {"id": run["id"], "status": run["status"]}


@router.get("/{ident}")
async def read(ident: str, request: Request):
    return await call(experiments.read, uid(request), ident)


@router.post("/{ident}/run")
async def start(ident: str, request: Request, bg: BackgroundTasks):
    user = uid(request)
    run = await call(experiments.start, user, ident)
    bg.add_task(experiments.launch, user, run["id"], "run")
    return {"id": run["id"], "status": run["status"]}


@router.post("/{ident}/cancel")
async def cancel(ident: str, request: Request):
    return await call(experiments.cancel, uid(request), ident)
