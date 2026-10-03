"""智能炒股 · 只读运行时开关（第四段 R4 · `03 §2`）。

`GET /v1/fin/runtime` —— **前端读开关的唯一取值来源**。

前端**不许**自己读 env、**不许**自己判断模式：那等于把开关搬到客户端，
藏一个按钮不叫开关（红线 6「不许假入口」）。这里读的值来自服务端
`services/fin/switches.py`（全仓唯一的读点）。

**只读、无副作用**：不打桩、不写库、不改任何状态。`paper` 的前置依赖探针也只是读。

返回体（`03 §2` 的形状）：

```json
{
  "memory_enabled": true,
  "evolution_mode": "observe",
  "evolution_mode_requested": "paper",
  "degraded_reason": "进入 paper 模式的前置依赖未就绪 → 已降级 observe：模拟账本未就绪（…）",
  "auto_apply": false,
  "live_order_enabled": false
}
```

**鉴权**：与其它 `/api/v1/fin/*` 一致，走默认硬鉴权（不在 middleware 的免登录前缀里）。
它不含任何用户数据，但也没有理由对匿名开放 —— 单个部署的开关状态不欠外人一个答案。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request

from app.services.fin import switches

router = APIRouter(tags=["fin-runtime"])


@router.get("/v1/fin/runtime")
async def runtime(request: Request) -> dict:
    """运行时开关快照。**硬开关违规 → 503**，把原因写在接口里（`03 §2`）。"""
    try:
        switches.assert_hard_ok()
    except switches.SwitchConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    # 探针要打 HTTP（paper）与库（四个库探针），都是阻塞调用 —— 放进线程池，
    # 别把事件循环卡住（仓内铁律：同步 SDK 调用在 async 端点里必须 asyncio.to_thread）。
    return await asyncio.to_thread(switches.runtime_state)
