"""DryRunBroker · 默认实现 · 不真下单 · 打印日志 + 内存记录
(Phase E · 2026-08-18)

行为:
- submit_order · 立即返 filled(不真下单)· 用最新 klines close 作 avg_price
- query_order · 从内存查
- cancel_order · 已 filled 无法撤 · 返 False
- query_positions · 累积历次 filled 得当前持仓(减 sell)· 内存
- query_balance · mock 初始 100 万 · 每次 buy 扣

**只用于测试** · 生产接入券商时替换。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime

from . import Broker, Order, OrderStatus

log = logging.getLogger(__name__)


class DryRunBroker(Broker):
    name = "dryrun"

    def __init__(self):
        self._orders: dict[str, OrderStatus] = {}
        self._positions: dict[str, dict] = {}   # code → {qty, avg_cost}
        self._cash: float = 1_000_000.0
        self._start_cash: float = 1_000_000.0

    def _get_price(self, code: str, hint: float | None = None) -> float:
        if hint and hint > 0:
            return hint
        try:
            from app.services.database import get_conn
            conn = get_conn(); cur = conn.cursor()
            cur.execute(
                """SELECT close FROM klines
                   WHERE code=%s AND period='daily' AND close IS NOT NULL
                   ORDER BY ts DESC LIMIT 1""",
                (code,),
            )
            r = cur.fetchone()
            cur.close(); conn.close()
            return float(r[0]) if r else 0.0
        except Exception:
            return 0.0

    def submit_order(self, order: Order) -> OrderStatus:
        oid = f"dry_{uuid.uuid4().hex[:8]}"
        price = self._get_price(order.code, order.price)
        if price <= 0:
            status = OrderStatus(order_id=oid, status="rejected",
                                 message=f"无 {order.code} 最新 close")
        elif order.side not in ("buy", "sell"):
            status = OrderStatus(order_id=oid, status="rejected",
                                 message=f"未知 side: {order.side}")
        else:
            cost = price * order.qty
            if order.side == "buy":
                if cost > self._cash:
                    status = OrderStatus(order_id=oid, status="rejected",
                                         filled_qty=0,
                                         message=f"资金不足 · 需 {cost:.2f} · 剩 {self._cash:.2f}")
                else:
                    self._cash -= cost
                    p = self._positions.get(order.code, {"qty": 0, "avg_cost": 0.0})
                    new_qty = p["qty"] + order.qty
                    new_cost = (p["qty"] * p["avg_cost"] + cost) / new_qty
                    self._positions[order.code] = {"qty": new_qty, "avg_cost": new_cost}
                    status = OrderStatus(order_id=oid, status="filled",
                                         filled_qty=order.qty, avg_price=price,
                                         message="dry run · 已扣资金")
            else:  # sell
                held = self._positions.get(order.code, {"qty": 0})["qty"]
                if held < order.qty:
                    status = OrderStatus(order_id=oid, status="rejected",
                                         message=f"持仓不足 · 需 {order.qty} · 剩 {held}")
                else:
                    self._cash += cost
                    new_qty = held - order.qty
                    if new_qty == 0:
                        del self._positions[order.code]
                    else:
                        self._positions[order.code]["qty"] = new_qty
                    status = OrderStatus(order_id=oid, status="filled",
                                         filled_qty=order.qty, avg_price=price,
                                         message="dry run · 已加资金")
        self._orders[oid] = status
        log.info("[dry_broker] %s %s %s@%s · %s",
                 order.side, order.qty, order.code, order.price or "mkt", status.status)
        return status

    def query_order(self, order_id: str) -> OrderStatus:
        return self._orders.get(order_id,
                                OrderStatus(order_id=order_id, status="not_found"))

    def cancel_order(self, order_id: str) -> bool:
        st = self._orders.get(order_id)
        if not st or st.status == "filled":
            return False
        st.status = "cancelled"
        return True

    def query_positions(self) -> list[dict]:
        out = []
        for code, p in self._positions.items():
            price = self._get_price(code)
            out.append({
                "code": code,
                "qty": p["qty"],
                "avg_cost": round(p["avg_cost"], 2),
                "current_price": round(price, 2),
                "market_value": round(price * p["qty"], 2),
                "pnl": round((price - p["avg_cost"]) * p["qty"], 2),
            })
        return out

    def query_balance(self) -> dict:
        mv = sum(p["current_price"] * p["qty"] for p in self.query_positions())
        return {
            "cash": round(self._cash, 2),
            "market_value": round(mv, 2),
            "total": round(self._cash + mv, 2),
            "initial_cash": self._start_cash,
            "pnl_total": round(self._cash + mv - self._start_cash, 2),
        }
