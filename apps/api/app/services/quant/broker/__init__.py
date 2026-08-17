"""E-6 · 券商接入统一 interface
(Phase E · 2026-08-18)

设计:
- abstract Broker · 定义 submit/query/cancel/positions/balance 5 方法
- DryRunBroker · 默认实现 · 不真下单 · 用户测试 · 记录 orders 表
- 具体券商(XTP · 广发 GFTrade · 富途)Phase F 按 interface 实现

**当前只支持 dry run** · 真实下单需:
- 用户签署"授权协议"
- 券商 API 商务合作
- 合规审查(见 02 §5.2)
- 每笔弹窗二次确认
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Order:
    code: str
    side: str                     # 'buy' / 'sell'
    qty: int
    price: Optional[float] = None # None = market
    price_type: str = "market"    # 'market' / 'limit'


@dataclass
class OrderStatus:
    order_id: str
    status: str                   # 'pending' / 'partial' / 'filled' / 'cancelled' / 'rejected'
    filled_qty: int = 0
    avg_price: Optional[float] = None
    message: str = ""


class Broker(ABC):
    """统一券商接口 · 具体券商实现 · 未来接 XTP/GFTrade 时按此实现"""

    name: str = "abstract"

    @abstractmethod
    def submit_order(self, order: Order) -> OrderStatus:
        """提交订单 · 返 pending / filled(市价立即成交)/ rejected"""

    @abstractmethod
    def query_order(self, order_id: str) -> OrderStatus:
        """查订单状态"""

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """撤单 · True = 成功"""

    @abstractmethod
    def query_positions(self) -> list[dict]:
        """当前持仓 · [{code, qty, avg_cost, market_value}]"""

    @abstractmethod
    def query_balance(self) -> dict:
        """账户余额 · {cash, market_value, total}"""


def get_broker(name: str = "dryrun") -> Broker:
    """factory · 目前只支持 dryrun · Phase F 加实际券商"""
    if name == "dryrun":
        from .dryrun import DryRunBroker
        return DryRunBroker()
    raise NotImplementedError(f"broker {name} 未接入 · 见 doc/broker-integration-guide.md")
