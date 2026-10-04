"""智能炒股 · 运行时开关（第四段 R4 · `03 §2`；`R21` 起可改）。

| 端点 | 干什么 | 写库吗 |
|---|---|---|
| `GET  /v1/fin/runtime` | **前端读开关的唯一取值来源**（只读） | **不写** |
| `POST /v1/fin/runtime/switch` | 改**这个项目**的经验库开关（`R21`） | 写现值 + 追加流水 |

前端**不许**自己读 env、**不许**自己判断模式：那等于把开关搬到客户端，
藏一个按钮不叫开关（红线 6「不许假入口」）。这里读的值来自服务端
`services/fin/switches.py`（全仓唯一的读点）。

返回体（`03 §2` 的形状 + `R21` 的三个新字段）：

```json
{
  "memory_enabled": true,
  "evolution_mode": "observe",
  "evolution_mode_requested": "paper",
  "degraded_reason": "进入 paper 模式的前置依赖未就绪 → 已降级 observe：模拟账本未就绪（…）",
  "auto_apply": false,
  "live_order_enabled": false,
  "project_id": "prj_…",
  "ceiling":  {"memory_enabled": true,  "evolution_mode": "observe"},
  "selected": {"memory_enabled": null,  "evolution_mode": null},
  "can_change": {"memory_enabled": true, "evolution_mode": true}
}
```

- `ceiling` = **部署者允许到哪**（只看环境变量）；`selected` = **界面上选了啥**（`null` = 没设过）；
- `can_change` = 这一项**能不能改**。天花板为关时它是 `false` —— 界面据此把开关画成灰的，
  并且**写接口也会 400**（两条都拦，光灰不算）。
- 不带 `project_id` 时，前六个老字段就是**天花板值**（老调用点行为逐字不变）。

**鉴权**：与其它 `/api/v1/fin/*` 一致，走默认硬鉴权（不在 middleware 的免登录前缀里）。
带 `project_id` 时**必须过项目归属校验**（跨用户 404，不区分「不存在」与「无权限」）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app.services.fin import store, switches

router = APIRouter(tags=["fin-runtime"])


def _uid(request: Request) -> str:
    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(status_code=401, detail={"message": "请先登录", "need_login": True})
    return str(uid)


def _assert_owned(conn, uid: str, project_id: str) -> None:
    """项目归属校验。**跨用户 / 不存在一律 404**（不区分两者，不泄露存在性）。"""
    with conn.cursor() as cur:
        cur.execute("SELECT user_id FROM fin_project WHERE project_id = %s", (project_id,))
        row = cur.fetchone()
    if not row or str(row[0]) != str(uid):
        raise HTTPException(status_code=404, detail="项目不存在")


@router.get("/v1/fin/runtime")
async def runtime(request: Request, project_id: Optional[str] = Query(None)) -> dict:
    """运行时开关快照。**硬开关违规 → 503**，把原因写在接口里（`03 §2`）。

    `project_id` 可选：给了就是**这个项目的生效值**（天花板 ∩ 天窗）+ 三个新字段；
    不给就是天花板值。两种都要登录。
    """
    try:
        switches.assert_hard_ok()
    except switches.SwitchConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    pid = (project_id or "").strip() or None
    if pid:
        uid = _uid(request)
        conn = store.get_conn()
        try:
            _assert_owned(conn, uid, pid)
            conn.rollback()          # 只读，显式回滚，不留 idle in transaction
        finally:
            conn.close()

    # 探针要打 HTTP（paper）与库（四个库探针），都是阻塞调用 —— 放进线程池，
    # 别把事件循环卡住（仓内铁律：同步 SDK 调用在 async 端点里必须 asyncio.to_thread）。
    return await asyncio.to_thread(lambda: switches.runtime_state(project_id=pid))


class SwitchIn(BaseModel):
    """改一个项目的经验库开关。

    `reason` **必填** —— 每次改动都要写清「为什么改」（流水账里那一列是它的落点）。
    """

    project_id: str
    switch_key: str
    value: Any
    reason: str = ""


@router.post("/v1/fin/runtime/switch")
async def set_switch(body: SwitchIn, request: Request) -> dict:
    """改**这个项目**的经验库开关（`R21` 的唯一写入口）。

    - 改完**立刻生效**（写入口清缓存，不等 5 秒）—— **不需要重建 / 重启任何服务**；
    - **超天花板 → 400**（报错里写清天花板是多少）；**硬开关 key → 400**；
    - 每次真改动**追加一行流水**（谁、何时、从什么到什么、为什么）。
    """
    uid = _uid(request)
    conn = store.get_conn()
    try:
        _assert_owned(conn, uid, body.project_id)
        try:
            out = switches.set_project_switch(
                conn, body.project_id,
                switch_key=body.switch_key, value=body.value, actor=uid,
                reason=body.reason,
            )
        except ValueError as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        conn.close()
    # 顺带回一份新快照，前端拿来直接刷新面板（不用再打一枪）。
    out["runtime"] = await asyncio.to_thread(
        lambda: switches.runtime_state(project_id=body.project_id))
    return out
