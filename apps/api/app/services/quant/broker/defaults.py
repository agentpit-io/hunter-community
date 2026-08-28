"""三市场 broker 默认参数(复赛 §3.B.2 · 2026-08-29)

设计:
- 保持 backtest_engine 单参 cost_bps(总累积 bps)接口不变
- 每档 preset 把手续费/税/滑点折算成 "双边平均 bps" · 塞给引擎
- 返回时保留 breakdown · 让前端能展示各项占多少 bps

真实参数依据(2026 现行):
- A 股 · 万分之 2.5 佣金 + 千一印花税(卖出) + 沪市万十过户
- 港股 · 万分之 3 佣金 + 千一印花税(双) + 三项证监费(约万分之 0.6)
- 美股 · 每股 $0.005 · 折算约 1 bps · SEC 微收(卖出)

滑点默认参数是"静态 bps"型 · 后续可扩 sqrt_impact.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Literal


@dataclass(frozen=True)
class CostBreakdown:
    """单向(即一笔换手)平均成本分解 · 单位 bps
    双边总成本 = 卖出 + 买入 · 简单情况下 ≈ 2 × 单向
    """
    commission: float          # 券商佣金
    stamp_tax: float           # 印花税
    slippage: float            # 滑点(默认静态 bps · 大单需 sqrt_impact)
    other: float = 0.0         # 过户/交易费/证监会规费/FINRA 等杂项

    @property
    def total_one_side(self) -> float:
        return round(self.commission + self.stamp_tax + self.slippage + self.other, 4)


@dataclass(frozen=True)
class BrokerPreset:
    key: str
    label: str
    market: Literal["cn", "hk", "us"]
    lot_size: int              # 一手
    tick_size: float           # 最小价位
    buy: CostBreakdown         # 买入侧
    sell: CostBreakdown        # 卖出侧
    notes: str                 # 面向 UI 的简短说明

    @property
    def total_bps_per_side(self) -> float:
        """一次单向换手平均 bps · 平均买卖两侧
        (换手率是双边,所以在引擎里传的是"平均单向 bps",引擎里换手 × 2 × 单向)
        """
        return round((self.buy.total_one_side + self.sell.total_one_side) / 2, 4)

    @property
    def total_bps_round_trip(self) -> float:
        """一次完整往返(买+卖)成本 · 用于展示"每笔交易大约扣多少" """
        return round(self.buy.total_one_side + self.sell.total_one_side, 4)

    def breakdown_avg(self) -> dict:
        """双边平均分解 · 用于前端"成本明细"折叠面板"""
        return {
            "commission": round((self.buy.commission + self.sell.commission) / 2, 4),
            "stamp_tax":  round((self.buy.stamp_tax  + self.sell.stamp_tax)  / 2, 4),
            "slippage":   round((self.buy.slippage   + self.sell.slippage)   / 2, 4),
            "other":      round((self.buy.other      + self.sell.other)      / 2, 4),
        }

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "market": self.market,
            "lot_size": self.lot_size,
            "tick_size": self.tick_size,
            "notes": self.notes,
            "total_bps_per_side": self.total_bps_per_side,
            "total_bps_round_trip": self.total_bps_round_trip,
            "breakdown": self.breakdown_avg(),
            "buy":  asdict(self.buy),
            "sell": asdict(self.sell),
        }


# ─── A 股(沪深)· 万分之 2.5 佣金 + 千一印花税(卖出) + 沪市万十过户 ────
CN_DEFAULT = BrokerPreset(
    key="cn_default",
    label="A 股 · 默认(标准零售)",
    market="cn",
    lot_size=100,
    tick_size=0.01,
    buy=CostBreakdown(
        commission=2.5,       # 佣金 万分之 2.5 = 2.5 bps
        stamp_tax=0.0,        # 买入无印花税
        slippage=3.0,         # 3 bps 静态滑点
        other=0.1,            # 沪市过户 0.001% ≈ 0.1 bps · 平均到全部 A 股
    ),
    sell=CostBreakdown(
        commission=2.5,
        stamp_tax=10.0,       # 卖出千一印花税 = 10 bps
        slippage=3.0,
        other=0.1,
    ),
    notes="佣金 万2.5 · 印花税 千1(卖出) · 过户 万0.1(沪市) · 滑点 3bps",
)

# ─── 港股 · 万分之 3 佣金 + 千一印花税(双向) + 三项证监费(约万分之 0.6) ─
HK_DEFAULT = BrokerPreset(
    key="hk_default",
    label="港股 · 默认(标准零售)",
    market="hk",
    lot_size=100,             # 港股 lot_size 依股 · 100 是简化
    tick_size=0.001,
    buy=CostBreakdown(
        commission=3.0,       # 万3
        stamp_tax=10.0,       # 千一(双向)
        slippage=5.0,
        other=0.6,            # 证监会 + 结算 + 交易 三项约 0.6 bps
    ),
    sell=CostBreakdown(
        commission=3.0,
        stamp_tax=10.0,
        slippage=5.0,
        other=0.6,
    ),
    notes="佣金 万3 · 印花税 千1(双向) · 证监会+结算+交易 万0.6 · 滑点 5bps",
)

# ─── 美股 · $0.005/股(折算约 1 bps · 估价 $50) + SEC 微收 + 滑点 4 bps ─
US_DEFAULT = BrokerPreset(
    key="us_default",
    label="美股 · 默认(标准零售)",
    market="us",
    lot_size=1,
    tick_size=0.01,
    buy=CostBreakdown(
        commission=1.0,       # $0.005/股 · 按均价 $50 折算约 1 bps
        stamp_tax=0.0,
        slippage=4.0,
        other=0.0,
    ),
    sell=CostBreakdown(
        commission=1.0,
        stamp_tax=0.0,
        slippage=4.0,
        other=0.2,            # SEC + FINRA + TAF 卖出规费约 0.2 bps
    ),
    notes="佣金 ≈1bps($0.005/股) · SEC/FINRA 卖出 万0.02 · 滑点 4bps",
)

# ─── 零成本 · 用于评委对比"如果不算成本会怎样" ─────────────
ZERO_COST = BrokerPreset(
    key="zero",
    label="零成本(仅对比用)",
    market="cn",
    lot_size=100,
    tick_size=0.01,
    buy=CostBreakdown(0, 0, 0, 0),
    sell=CostBreakdown(0, 0, 0, 0),
    notes="仅用于回测报告里对比 · 生产环境交易永远有成本",
)


BROKER_PRESETS: dict[str, BrokerPreset] = {
    "cn_default": CN_DEFAULT,
    "hk_default": HK_DEFAULT,
    "us_default": US_DEFAULT,
    "zero":       ZERO_COST,
}

DEFAULT_PRESET_KEY = "cn_default"


def resolve(preset_key: str | None) -> BrokerPreset:
    """把 preset 名解析到 BrokerPreset · 兜底 CN_DEFAULT · 不返 None"""
    if not preset_key:
        return CN_DEFAULT
    return BROKER_PRESETS.get(preset_key.strip().lower(), CN_DEFAULT)


def list_presets() -> list[dict]:
    """所有 preset 的 UI 描述 · 供 API /quant/broker/presets 列出"""
    return [p.to_dict() for p in BROKER_PRESETS.values()]
