"""影子臂的**决策构造**（R7）—— 两臂跑同一条策略、不同的配置。

红线 11「两臂必须同条件」在**决策**这一侧的落点：两个臂调**同一个** `build_decision`
（`strategy/sample.py`），输入里只有两样不同 —— **该臂的配置**与**该臂自己的现金 / 持仓**。
别的都相同：同一个标的、同一个交易日、同一个时点、同一个行情快照（快照在 paper 侧取一次）。

候选配置是 **api 侧 `evolution.shadow_prepare` 算好的**（base + 服务端机器算的 `param_diff`），
这一层只**搬运**，不重算 diff、不猜口径（方向与取值都由服务端定，见 `services/fin/evolution.py`）。

**当前示例策略只读 `fin_param` 的留痕字段**（`max_position_pct` / `max_positions`），
所以两臂在现金相同、都没持仓时产出的决策是**同一份**（除定量随现金变）——
这是示例策略的性质，不是影子框架的缺陷；框架已把「候选配置」接进决策入参，
策略将来真读这些参数时无需改这里。见成果文档「交给 R8」一节。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from app.strategy.sample import SampleNoBudget, build_decision


def param_of(config: Optional[dict[str, Any]]) -> dict[str, Any]:
    """从影子配置里取出示例策略**会用到**的字段（供 `data_snapshot` 留痕）。

    **只取真值**：配置里没有的字段就是 `None`（不填一个「看起来正常」的默认）。
    """
    cfg = config or {}
    return {
        "max_position_pct": cfg.get("max_position_pct"),
        "max_positions": None,
    }


def arm_order(decision: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """`StrategyDecision` 字典 → 影子请求里的那一笔委托（只取 paper 需要的四个字段）。"""
    if not decision:
        return None
    intent = decision.get("intent") or {}
    return {
        "side": intent.get("side"),
        "qty": intent.get("qty"),
        "price_type": intent.get("price_type"),
        "limit_price": intent.get("limit_price"),
    }


def build_for_arm(*, project: dict[str, Any], config: Optional[dict[str, Any]],
                  trade_date: str, point: str, now: datetime, code: str,
                  strategy_key: str, strategy_version: str, ttl_seconds: int,
                  market: str, market_order_supported: bool,
                  reference_price: Any = None, lot_size: Any = None,
                  available_cash: Any = None) -> Optional[dict[str, Any]]:
    """为该臂产出一笔委托；**买不起 / 被闸门拦下 → `None`**（如实记「本时点无决策」）。

    不做经验闸门（`memory` 不传）—— 影子验证量的是**策略本身**的成绩，不叠加实盘的
    经验拦截；那会把「记忆有没有让系统更保守」和「候选参数好不好」两件事混在一起。
    """
    try:
        decision = build_decision(
            project=project, param=param_of(config), trade_date=trade_date, point=point,
            now=now, code=code, strategy_key=strategy_key, strategy_version=strategy_version,
            ttl_seconds=ttl_seconds, market=market,
            market_order_supported=market_order_supported,
            reference_price=reference_price, lot_size=lot_size,
            available_cash=available_cash, memory=None,
        )
    except SampleNoBudget:
        # 买不起 / 无预算 —— 本时点不出决策（与实盘同一种「如实的不作为」）。
        return None
    return arm_order(decision)
