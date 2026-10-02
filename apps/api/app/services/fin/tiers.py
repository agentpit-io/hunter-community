"""档位模板 —— 三档资金与它们的整套 `fin_param` 默认值。

**逐字段来源**：`plan/ref/03-新用户向导与操作参数方案.md`
  · §二 2.1 定位与 2.2 参数总表（三档差异化）
  · §四 4.1 板块能力边界（科创板只对 100 万档开放）
  · §四 4.3 流动性门槛
  · §五 5.2 策略开放档位、5.3 每个策略的默认参数
  · §六 6.3 账户级风控

**这里就是权威**：路由不接受请求体里的任何数字，只接受 `tier` 三选一。
客户端（向导第 3~5 步）展示的是本模板的副本，改动只会落在前端展示，
服务端开户时**一律按本表重算一遍**——界面传什么数字都不作数。

金额单位一律 CNY；比例是小数（`-0.04` = −4%），与 `09 §二`「比例 NUMERIC(9,6)」一致。
"""

from __future__ import annotations

from typing import Any

# 三档顺序固定：① 个人玩玩 → ② 个人资产管理 → ③ 资产运营
TIER_ORDER = ("play", "manage", "operate")

TIER_LABEL = {
    "play": "个人玩玩",
    "manage": "个人资产管理",
    "operate": "资产运营",
}

# 策略清单（03 §5.2）。key 是稳定标识；params 是 03 §5.3 的默认参数。
_STRATEGIES: dict[str, dict[str, Any]] = {
    "ma_momentum": {
        "name": "均线动量",
        "params": {"fast": 5, "slow": 10, "vol_mult": 1.5},
    },
    "oversold_rebound": {
        "name": "超跌反弹",
        "params": {"ma": 20, "deviation": -0.08, "confirm_days": 1},
    },
    "volume_breakout": {
        "name": "量价突破",
        "params": {"window": 20, "vol_mult": 2.0},
    },
    "ma_pullback": {
        "name": "均线多头回踩",
        "params": {"fast": 5, "mid": 10, "slow": 20, "tolerance": 0.01},
    },
    "fund_flow": {
        "name": "资金流跟随",
        "params": {"min_days": 3},
    },
}

# 三档开放哪些策略（03 §5.2 末行）。
# ① 2 个 / ② 4 个 / ③ 5 个（全部）。**小资金只给 2 个是有意为之**。
_TIER_STRATEGIES = {
    "play": ["ma_momentum", "oversold_rebound"],
    "manage": ["ma_momentum", "oversold_rebound", "volume_breakout", "ma_pullback"],
    "operate": ["ma_momentum", "oversold_rebound", "volume_breakout", "ma_pullback", "fund_flow"],
}

# 最小日均成交额：≥ 1 亿元，三档相同（03 §4.3 前三条三档通用）。
_LIQUIDITY_MIN_AMOUNT = 100_000_000

# 档位模板的原始数字。顺序与 03 §二 2.2 总表一致，便于逐行核对。
_TIERS: dict[str, dict[str, Any]] = {
    "play": {
        "initial_capital": 10_000,
        "max_positions": 2,          # 默认最大持仓只数
        "max_positions_hard": 3,     # 持仓只数上限（界面用，不落库）
        "max_position_pct": 0.35,    # 单只最大仓位
        "min_order_amount": 3_000,   # 单笔最小金额
        "max_price": 30,             # 由此推出的可买股价上限 = 3000 / 100
        "stop_loss_pct": -0.04,
        "take_profit_pct": 0.06,
        "hold_days_max": 3,
        "daily_max_new": 1,
        "daily_max_orders": 2,
        "account_drawdown_halt_pct": -0.08,
        "daily_loss_halt_pct": -0.02,
        "liquidity_max_participation": None,   # ① 关（单笔太小，不构成冲击）
        "star_allowed": False,                 # 科创板只对 ③ 开放
    },
    "manage": {
        "initial_capital": 100_000,
        "max_positions": 4,
        "max_positions_hard": 6,
        "max_position_pct": 0.25,
        "min_order_amount": 20_000,
        "max_price": 200,
        "stop_loss_pct": -0.04,
        "take_profit_pct": 0.05,
        "hold_days_max": 3,
        "daily_max_new": 2,
        "daily_max_orders": 4,
        "account_drawdown_halt_pct": -0.08,
        "daily_loss_halt_pct": -0.025,
        "liquidity_max_participation": 0.01,   # 单只 ≤ 昨日成交额 1%
        "star_allowed": False,
    },
    "operate": {
        "initial_capital": 1_000_000,
        "max_positions": 8,
        "max_positions_hard": 12,
        "max_position_pct": 0.15,
        "min_order_amount": 50_000,
        "max_price": 1500,
        "stop_loss_pct": -0.03,
        "take_profit_pct": 0.04,
        "hold_days_max": 3,
        "daily_max_new": 3,
        "daily_max_orders": 8,
        "account_drawdown_halt_pct": -0.06,
        "daily_loss_halt_pct": -0.03,
        "liquidity_max_participation": 0.005,  # 单只 ≤ 昨日成交额 0.5%
        "star_allowed": True,
    },
}


def _board_flags(tier: str) -> dict[str, bool]:
    """板块能力边界（03 §4.1 第一层）。默认按真实投资者适当性设置。"""
    t = _TIERS[tier]
    return {
        "main": True,        # 沪市主板 60xxxx
        "chinext": True,     # 创业板 300xxx —— 三档均开（真实门槛 10 万，① 档属模拟宽容）
        "star": t["star_allowed"],  # 科创板 688xxx —— 仅 ③；①② 锁定关闭
        "bse": False,        # 北交所 —— 流动性差，不开放
        "st": False,         # ST / *ST —— 短线禁碰
        "sub_new": False,    # 次新股（上市 < 60 个交易日）
    }


def _strategies(tier: str) -> list[dict[str, Any]]:
    """该档开放的策略，带 03 §5.3 的默认参数与版本号。"""
    out = []
    for key in _TIER_STRATEGIES[tier]:
        meta = _STRATEGIES[key]
        out.append({"key": key, "name": meta["name"], "params": dict(meta["params"]), "version": "v1"})
    return out


def build_template(tier: str) -> dict[str, Any]:
    """把档位展开成一份**完整**的开户模板（不含 project_id 等运行时字段）。

    返回结构同时供两处使用：
      · `GET /api/v1/fin/tiers` —— 向导第 2 步展示、右侧参数摘要；
      · `store.create_project()` —— 写 `fin_project` / `fin_param` 的唯一数据源。
    """
    if tier not in _TIERS:
        raise ValueError(f"未知档位：{tier!r}（可选 {', '.join(TIER_ORDER)}）")
    t = _TIERS[tier]
    return {
        "tier": tier,
        "label": TIER_LABEL[tier],
        "initial_capital": t["initial_capital"],
        "max_positions": t["max_positions"],
        "max_positions_hard": t["max_positions_hard"],
        "max_position_pct": t["max_position_pct"],
        "min_order_amount": t["min_order_amount"],
        "max_price": t["max_price"],
        "stop_loss_pct": t["stop_loss_pct"],
        "take_profit_pct": t["take_profit_pct"],
        "hold_days_max": t["hold_days_max"],
        "daily_max_new": t["daily_max_new"],
        "daily_max_orders": t["daily_max_orders"],
        "account_drawdown_halt_pct": t["account_drawdown_halt_pct"],
        "daily_loss_halt_pct": t["daily_loss_halt_pct"],
        "liquidity_min_amount": _LIQUIDITY_MIN_AMOUNT,
        "liquidity_max_participation": t["liquidity_max_participation"],
        "board_flags": _board_flags(tier),
        "sector_prefs": [],                 # 默认不限行业（03 §4.2）
        "strategies": _strategies(tier),
    }


def all_templates() -> list[dict[str, Any]]:
    """三档模板，按 ①②③ 顺序。"""
    return [build_template(t) for t in TIER_ORDER]
