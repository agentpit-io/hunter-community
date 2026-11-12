# -*- coding: utf-8 -*-
"""S13 · 风控第 8 条（档位级「单笔最小金额」）的判据与「不适用」口径。

纯函数，不连库：判据本身在 `app/risk/limits.py`。「值从 `fin_param` 来」那一段
（含「只对 A 股给值」）由 `test_risk_engine.py` 与 `test_risk_markets.py` 覆盖。
"""

from __future__ import annotations

from decimal import Decimal

from app.risk import limits


def test_单笔下限_不够就拒_够就过():
    assert limits.check_min_order_amount(Decimal("3000"), Decimal("3000"), side="buy").ok
    bad = limits.check_min_order_amount(Decimal("700"), Decimal("3000"), side="buy")
    assert not bad.ok, bad
    assert "单笔金额不足" in bad.reason
    assert "费率控制" in bad.reason


def test_单笔下限_只判买入():
    # 文档里这条挂在「§6.1 买入规则」下；卖出规则（§6.2）没有它 → 不判卖出
    assert limits.check_min_order_amount(Decimal("1"), Decimal("3000"), side="sell").ok


def test_单笔下限_没值就是不适用_放行():
    r = limits.check_min_order_amount(Decimal("1"), None, side="buy")
    assert r.ok and r.detail.get("applicable") is False


def test_金额算不出来时不表态():
    # 费用模型缺失时 amount 是 None —— 本条不表态（由「费用」那条去拒）
    r = limits.check_min_order_amount(None, Decimal("3000"), side="buy")
    assert r.ok and r.detail.get("applicable") is False
