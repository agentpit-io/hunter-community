"""风控第 8 条 · 档位级「单笔最小金额」（S13 起**真的执行**）。

  · `min_order_amount` —— **单笔最小金额（买入）**：委托金额小于该档下限就拒。
    依据是**费率**：A 股佣金有最低 5 元，单笔金额太小时实际费率被它抬高 —— ¥3,000 的
    单往返成本 0.385%，是名义费率的 6.7 倍（`plan/ref/03-新用户向导与操作参数方案.md` §三）。
    文档里它的原位就是**买入规则**（同文档 §6.1「买入规则」里的「单笔金额」一行），
    所以**只判买入**，卖出不判（§6.2 卖出规则没有这条）。
**这个数从哪来**：开户时按档位写死进 `fin_param.min_order_amount`
（`api/app/services/fin/tiers.py`），本模块只负责判，不算数。

**`None` = 该市场不适用 → 不拦**（不是「不限」）：

  这个数是从 **A 股费率口径**推出来的。港美股现行费率模型
  （`fin_fee_model.commission_min`）**都是 0** —— 没有最低佣金这回事，小单不额外吃亏，
  「费率控制」的理由在港美股**不成立**。给港美股编一个数就是编数据（红线），所以显式 `None`。

  **不看市场名**：本模块只有「有值就判、没值就放行」一条判据。哪个市场给值由取数那一侧
  决定（`ledger.order_limits`），这样将来港美股真有了最低佣金，改一处即可生效。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from app.risk.funds import money_label
from app.risk.result import RiskResult

NAME = "min_order_amount"


def check_min_order_amount(
    amount: Optional[Decimal], floor: Any, *, side: str, currency=None
) -> RiskResult:
    """委托金额（`qty × 价`，本币）是否达到该档单笔下限。`floor=None` → 不适用，放行。"""
    if floor is None or side != "buy":
        return RiskResult.pass_(NAME, applicable=False)
    if amount is None:                      # 金额都没算出来（费用模型缺失）→ 本条不表态
        return RiskResult.pass_(NAME, applicable=False)
    unit = money_label(currency)
    if Decimal(str(amount)) < Decimal(str(floor)):
        return RiskResult.reject(
            NAME,
            f"单笔金额不足：该档单笔最小 {floor} {unit}（费率控制），本笔 {amount} {unit}",
            floor=str(floor), amount=str(amount), currency=str(currency or "CNY"),
        )
    return RiskResult.pass_(NAME, applicable=True, floor=str(floor),
                            amount=str(amount), currency=str(currency or "CNY"))
