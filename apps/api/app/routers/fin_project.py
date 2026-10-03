"""智能炒股 · 项目与开户 API（一期 M1）。

    GET  /api/v1/fin/tiers              — 三档模板（向导第 2 步与右侧参数摘要）
    POST /api/v1/fin/projects           — 开户：只收 `tier`，本金与参数由服务端写死
    GET  /api/v1/fin/projects/current   — 当前进行中的项目 + 参数

鉴权复用现仓那套（`app/middleware/auth.py` 的 JWT 中间件）：`/api/v1/fin/` **不在**
`_PUBLIC_PREFIXES` 里，未带 token 直接 401，走到这里时 `request.state.user_id`
已经是 `users.id` 的 UUID 字符串。**不新造鉴权**。

参数一律服务端写死（见 `app/services/fin/tiers.py`）：请求体里除了 `tier` 之外的
任何数字都不被读取——界面传什么都不作数。这样「档位金额不可修改」（03 §11.1 拍板 1）
不是一句界面上的承诺，而是接口层根本没有那条路。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.services.fin import store, tiers

router = APIRouter(tags=["fin"])


class CreateProjectIn(BaseModel):
    """开户入参。档位由服务端写死；`markets` 只决定开哪几个市场的子账户。

    `markets` 缺省 `None` → 服务端按 `["CN_A"]`（**老客户端行为逐字节不变**）。
    """

    tier: str
    markets: Optional[list[str]] = None


class NewProjectIn(BaseModel):
    """开新项目（M-16）。`reason` 只用于关停留痕，不参与任何计算。"""

    tier: str
    reason: str = "user_opened_new"
    markets: Optional[list[str]] = None


class AddMarketsIn(BaseModel):
    """追加市场（P1）。`markets` 是**目标集合**：必须包含项目现有的每个市场。

    少传一个 = 请求隐含「移除某个市场」→ 服务端 400（只增不减）。
    """

    markets: list[str]


def _require_user(request: Request) -> str:
    uid = getattr(request.state, "user_id", None)
    if not uid:
        # 中间件已经把没 token 的挡在门外；这里只是兜底（也便于路由被单独挂测试 app）。
        raise HTTPException(status_code=401, detail={"message": "请先登录", "need_login": True})
    return uid


@router.get("/v1/fin/tiers")
async def list_tiers(request: Request):
    """三档模板。向导用它渲染档位卡与参数摘要；**服务端开户时仍按同一份重算**。

    P1 起每份模板多两个字段：`markets_supported`（本轮支持的市场，固定顺序）与
    `per_market`（每个市场的币种与板块能力）。**老字段一个都没改** —— `initial_capital`
    仍是 CNY 口径的档位金额（每市场各一份是「同一数字、各用本币」）。

    两个新字段只在 `tiers.build_template` 里定义一处，**不在响应里再抄一份** ——
    同一件事写在两处，改一处忘一处就是两套口径。
    """
    _require_user(request)
    return {"tiers": tiers.all_templates()}


@router.post("/v1/fin/projects")
async def open_project(body: CreateProjectIn, request: Request):
    """开户。

    幂等：同一用户已有进行中的项目时**返回现成的那个**（`created=false`），
    不新建第二个——「一个账户同时只有一个进行中项目」由库层的部分唯一索引保证。
    幂等口径**不因这次传的 `markets` 不同而改变**（要换市场走开新项目 / 追加市场）。
    """
    uid = _require_user(request)
    try:
        result = store.create_project(uid, body.tier, markets=body.markets)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **result}


@router.post("/v1/fin/projects/new")
async def open_new_project(body: NewProjectIn, request: Request):
    """**开新项目**（M-16）：先关闭当前项目，再开一个新的。

    与 `POST /v1/fin/projects` 的区别是**不幂等**：那个是「开户」（已有就返回现成的），
    这个是「换一段账本」——旧项目落 `status='closed'` + `closed_at` + `close_reason`，
    动作记进 `fin_param_change_log`；旧账本 / 旧成交 / 旧报告原样保留、可回看。

    同一用户任何时刻只有一个 `active` 项目（`fin_project_one_active` 部分唯一索引）。
    """
    uid = _require_user(request)
    if not (body.reason or "").strip():
        raise HTTPException(status_code=400, detail="close_reason 不能为空")
    try:
        result = store.open_new_project(uid, body.tier, reason=body.reason.strip(),
                                        markets=body.markets)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **result}


@router.post("/v1/fin/projects/{project_id}/markets")
async def add_project_markets(project_id: str, body: AddMarketsIn, request: Request):
    """**追加市场 · 只增不减**（P1）。

    入参 `markets` 是**目标集合**（不是「新增那几个」）—— 必须包含项目现有的每一个市场。
    少传一个 = 请求在隐含「移除某个市场」→ **400**（这一条服务端必须拦，界面不给移除
    控件只是双保险）。要减少市场只能「关停旧项目 + 开新项目」。

    幂等：已存在的市场跳过。⚠️ 新追加的市场**从追加时刻起开账**，**不回填历史**。
    """
    uid = _require_user(request)
    try:
        result = store.add_markets(uid, project_id, body.markets)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result:
        raise HTTPException(status_code=404, detail="项目不存在")
    return {"ok": True, **result}


@router.get("/v1/fin/projects")
async def list_projects(request: Request):
    """该用户**全部**项目（进行中 + 已关闭）。

    开新项目之后，旧的那一段从这里仍然看得到 —— 这是「旧账本可回看」的读入口。
    """
    uid = _require_user(request)
    return {"items": store.list_projects(uid)}


@router.get("/v1/fin/projects/current")
async def current_project(request: Request):
    """当前项目 + 参数；没有进行中的项目时 `project` 为 null（不是 404）。"""
    uid = _require_user(request)
    current = store.get_current(uid)
    if not current:
        return {"project": None, "param": None, "tier_template": None}
    return current


@router.get("/v1/fin/projects/{project_id}")
async def get_project(project_id: str, request: Request):
    """读一个项目（**含已关闭**）。不属于当前用户 → 404（不泄露存在性）。"""
    uid = _require_user(request)
    found = store.get_project(uid, project_id)
    if not found:
        raise HTTPException(status_code=404, detail="项目不存在")
    return found
