"""Paper Service · FastAPI 应用。

三道全局防线，按请求进入的顺序：

1. `LiveFieldGuard`（中间件）—— 实盘字段出现即 400；
2. `require_internal_key`（依赖）—— 无内部口令即 401（`/healthz` 除外）；
3. `LedgerJSONResponse` —— 金额 `Decimal` 原样写成字符串，不落浮点。

**没有第四道**：账本表在库层就只有 `INSERT`/`SELECT`（启动自检强制校验），
所以就算有一条路由想改历史，库也不让——这是「唯一账本」的最终保证。
"""

from __future__ import annotations

from fastapi import Depends, FastAPI

from app.jsonresp import LedgerJSONResponse
from app.routers import health, jobs, orders, projects, reference, snapshots
from app.security import LiveFieldGuard, require_internal_key

app = FastAPI(
    title="Hunter Community · Paper Service",
    description="唯一模拟账本 + 确定性风控（固定 PAPER 模式，不接实盘）",
    default_response_class=LedgerJSONResponse,
    dependencies=[Depends(require_internal_key)],
    # FastAPI 自带的 /docs 与 /openapi.json **不经过 app 级依赖**（实测无密钥也能 200），
    # 对一个「未带正确密钥的请求一律 401」的服务是例外口子。全关掉 —— 内部服务的
    # 接口形状不需要对公网暴露；要看契约读 `apps/paper/README.md`。
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.add_middleware(LiveFieldGuard)

app.include_router(health.router)
app.include_router(orders.router)
app.include_router(projects.router)
app.include_router(reference.router)
app.include_router(snapshots.router)
app.include_router(jobs.router)
