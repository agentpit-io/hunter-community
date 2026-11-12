"""风控引擎：把六条纯函数串成一次委托预检。

**按顺序跑完六条、收集全部不过的原因**（不是第一条不过就返回）——用户一次就能看到
「数量不整手 + 资金也不够」，而不是改一个报一个。`decline_reason` 是所有原因的拼接。

这里**没有任何按 `source` 分支的逻辑**（`09 §六-10`）：AI 的单和人下的单走同一套。
`source` 只是随委托落库、供二期统计归因，不参与任何判断。

**按市场取参数**（N2）：六条规则本身不变，参数从 `fin_market_rule` 的一行来
（`RiskInputs.market_rule`）。**取不到市场规则 → 整笔拒绝**，不退回 A 股口径
（拿 A 股规则顶替港美股就是编数据，`二期追加规则 §六-11`）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

from app.market_time import DEFAULT_MARKET
from app.risk import fee as fee_mod
from app.risk import funds as funds_mod
from app.risk import halt as halt_mod
from app.risk import limits as limits_mod
from app.risk import lot as lot_mod
from app.risk import price_limit as limit_mod
from app.risk import session as session_mod
from app.risk import t1 as t1_mod
from app.risk.fee import FeeBreakdown
from app.risk.result import RiskResult

NAME = "risk"


@dataclass
class RiskInputs:
    at: datetime                       # 委托到达时刻（带时区）
    side: str                          # 'buy' / 'sell'
    qty: int
    price: Decimal                     # 拟成交价（市价单 = 对手价 / 快照价）
    calendar: Optional[dict]           # fin_market_calendar 的一行（按 (market, 当地日期) 取）
    instrument: Optional[dict]         # fin_instrument 的一行
    fee_model: Optional[dict]          # fin_fee_model 的一行（该市场）
    prev_close: Optional[Decimal]      # 前收盘价（来自快照）
    available: Decimal                 # 该市场子账户当前可用资金（本币）
    position_qty: int = 0              # 当前总持仓
    sellable_qty: int = 0              # 当前可卖（按市场规则，已扣挂单占用）
    lot_size: int = lot_mod.DEFAULT_LOT   # 旧调用方兼容；market_rule 在场时以规则为准
    market: str = DEFAULT_MARKET       # CN_A / HK / US
    market_rule: Optional[dict] = None # fin_market_rule 的一行（六条规则的参数来源）
    tick: Optional[Decimal] = None     # 最小变动价位（app.tick 解析；None→按分，与改前一致）
    # 档位级两条金额约束（第 7、8 条）——**只对 A 股给值**，其余市场 `None` = 不适用。
    # `min_order_amount` 从 `fin_param` 来（开户时按档位写死）；`max_price` 是它的
    # **派生值**（÷ 每手股数，不落库），在 `limits.check_max_price` 里现算。
    min_order_amount: Optional[Decimal] = None   # 单笔最小金额（买入）


@dataclass
class RiskOutcome:
    passed: bool
    results: list[RiskResult] = field(default_factory=list)
    fee: Optional[FeeBreakdown] = None
    amount: Optional[Decimal] = None

    @property
    def decline_reason(self) -> Optional[str]:
        failed = [r.reason for r in self.results if not r.ok and r.reason]
        return "；".join(failed) if failed else None

    @property
    def failed_names(self) -> list[str]:
        return [r.name for r in self.results if not r.ok]

    def result_of(self, name: str) -> Optional[RiskResult]:
        for r in self.results:
            if r.name == name:
                return r
        return None

    @property
    def price_limit(self) -> Optional[RiskResult]:
        """第 4 条的结果（回执用它出 `price_limit_checked` 留痕）。"""
        return self.result_of("price_limit")


def evaluate(inp: RiskInputs) -> RiskOutcome:
    rule = inp.market_rule
    if not rule:
        # 没有该市场的规则行 → **不猜**，整笔拒绝并写明原因（宁可拒绝也不错用 A 股口径）。
        return RiskOutcome(
            passed=False,
            results=[RiskResult.reject(
                NAME,
                f"没有 {inp.market} 的市场规则（fin_market_rule 缺行），拒绝委托",
                market=inp.market,
            )],
        )

    lot_size = lot_mod.resolve_lot_size(
        rule.get("lot_rule"),
        rule.get("lot_fixed"),
        (inp.instrument or {}).get("lot_size"),
    )
    results: list[RiskResult] = [
        # 停牌**先读**（L05）：停牌即拒单，别的规则不必再跑出别的理由混淆视听。
        halt_mod.check_halt(inp.instrument),
        session_mod.check_session(
            inp.calendar, inp.at,
            market=inp.market,
            timezone_name=rule.get("timezone"),
            sessions=rule.get("sessions"),
        ),
        lot_mod.check_lot(inp.side, inp.qty, lot_size),
        t1_mod.check_t1(
            inp.side, inp.qty, inp.sellable_qty,
            rule=rule.get("sellable_rule") or "t_plus_n",
            days=rule.get("sellable_days") or 0,
            position_qty=inp.position_qty,
        ),
        limit_mod.check_price_limit(
            inp.instrument, inp.side, inp.price, inp.prev_close,
            mode=rule.get("price_limit_mode") or "pct",
            tick=inp.tick,
        ),
    ]

    fee_result = fee_mod.check_fee_model(inp.fee_model)
    results.append(fee_result)

    amount: Optional[Decimal] = None
    breakdown: Optional[FeeBreakdown] = None
    if fee_result.ok:
        amount = (Decimal(inp.qty) * Decimal(str(inp.price))).quantize(Decimal("0.0001"))
        breakdown = fee_mod.compute_fee(inp.side, amount, inp.fee_model)
        results.append(
            funds_mod.check_funds(
                inp.side, amount, breakdown.total, inp.available, inp.position_qty, inp.qty,
                currency=rule.get("currency"),
            )
        )

    # ── 第 8 条 · 档位级「单笔最小金额」（S13 起真的执行）──────────────────
    # 前面那七条**都是市场级规则**（`fin_market_rule`）；这一条是**档位级**
    # （`fin_param.min_order_amount`），S13 之前只落库、只上界面，
    # **没有任何一处拿它判过单**。`None` = 该市场不适用（只放行、不拒）—— 见 `risk/limits.py`。
    #
    # 为什么**没有**把「可买股价上限」也做成一条判据：它不落库（`fin_param` 没有那一列），
    # 而且源设计里它的三个值来路各不相同（① 30 = 下限÷100 股、② 200 = 下限÷100 股、
    # ③ 1500 = **单票预算** 100 万×15%÷100 股），文档 §二 2.2 与 §二 的「单只 ¥3,500–5,000」
    # 也互相打架（① 档按上限只能买 ≤30 元/股，一手就是 3,000 元，压根到不了 3,500）。
    # 硬执行它会把 ① 档能买的股票再砍一刀，且与本文档自身的口径矛盾 —— 属产品口径，
    # 不猜、不擅自收紧（见 `S13` 成果文档 §六）。
    results.append(
        limits_mod.check_min_order_amount(
            amount, inp.min_order_amount, side=inp.side, currency=rule.get("currency")))

    return RiskOutcome(
        passed=all(r.ok for r in results),
        results=results,
        fee=breakdown,
        amount=amount,
    )
