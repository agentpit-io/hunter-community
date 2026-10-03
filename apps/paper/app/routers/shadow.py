"""影子臂模拟撮合端点（R7）—— **只读参考数据 + 写一张共用快照 + 返回事件，不下单**。

`POST /api/v1/shadow/simulate`：把一个行情快照上两臂各自的决策跑一遍
（`app/shadow.py:simulate_arms`），返回两臂的影子里程碑。**这条路径永远不下单** ——
它只依赖 `DecisionRecorder`（接口隔离见 `app/shadow.py` 模块头），
`OrderExecutor`（真实下单那条路）从头到尾没出现。

本端点**不落任何账本表**：撮合结果原样返回，由调用方（`fin.shadow` 工作流 → api 的
`/shadow/record`）写进影子事件表。理由见 `app/shadow.py` 模块头「为什么落库不在这里」。

返回体里的金额一律**字符串**（`LedgerJSONResponse` + `Decimal` 原样），不落浮点。
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import db
from app import shadow as shadow_mod

router = APIRouter(tags=["shadow"])


class SimulateIn(BaseModel):
    """一次影子模拟请求。`arms` 里每个臂自带**重建后的当前状态**（现金 / 持仓）。"""

    project_id: str
    market: str
    symbol: str
    trade_date: str
    point: str
    initial_capital: Decimal          # api 的 prepare 返回数字；Decimal 全程精确
    arms: list[dict]


@router.post("/api/v1/shadow/simulate")
def simulate(body: SimulateIn) -> dict:
    if not body.arms:
        raise HTTPException(400, "arms 不能为空（至少要有一个臂）")
    recorder = shadow_mod.CollectingRecorder()
    # 只读事务：simulate 只写 fin_snapshot（capture 内部），不改任何账本表。
    with db.cursor(commit=True) as cur:
        try:
            out = shadow_mod.simulate_arms(cur, body.model_dump(), recorder=recorder)
        except KeyError as exc:
            raise HTTPException(400, f"请求缺字段：{exc}") from exc
    return out
