"""风险档位（保守 / 稳健 / 激进）与「参数只能收紧」的单向棘轮（一期 M6）。

**口径来源**：`plan/ref/03-新用户向导与操作参数方案.md` §七（锁定与放宽规则）——
「风控参数的单向棘轮：收紧随时可以，放宽必须用户明确点头并留痕」。
原型页 `ref/原型/01-自动交易.html` 的「⑤ 设定风险上限」把它表达成三选一，
档位卡片上写的就是「单票≤X% / 亏损 Y% 停」。

**这个档位管什么**：只管道具里的**上限与熔断**三条，不管止损止盈
（那是向导第 5 步按档位给的策略参数，属于项目模板，不在这里改）：

| 字段 | 含义 | 越小越紧？ |
|---|---|---|
| `max_position_pct` | 单票最大占比 | 是（占比越低越保守） |
| `daily_loss_halt_pct` | 单日亏损熔断线（负数） | 是（越接近 0 越早停） |
| `account_drawdown_halt_pct` | 账户回撤熔断线（负数） | 是（越接近 0 越早停） |

**收紧 / 放宽怎么判**：逐字段比。目标档位的每个字段都不比现值更松 → 收紧，直接生效；
**只要有一个字段变松** → 必须带 `confirm=true`（前端二次确认框），否则接口拒绝（409）。
判据是逐字段而不是「档位序号」——因为项目模板给的三档默认值（`tiers.py`）与这里的
档位预设不是同一套数，只看序号会出现「序号在收紧、实际却把某条刹车松了」。

三条口径**不是**从数据里学出来的，是产品拍板值；改它们要连原型页一起改。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

# 档位顺序：① 保守 → ② 稳健 → ③ 激进（与原型页一致）。
TIER_ORDER = ("conservative", "steady", "aggressive")

TIER_LABEL = {
    "conservative": "保守",
    "steady": "稳健",
    "aggressive": "激进",
}

# 档位 → 它管的三个字段。数值全部是产品拍板值（原型页档位卡上的数字）。
PRESETS: dict[str, dict[str, float]] = {
    "conservative": {
        "max_position_pct": 0.10,
        "daily_loss_halt_pct": -0.02,
        "account_drawdown_halt_pct": -0.05,
    },
    "steady": {
        "max_position_pct": 0.20,
        "daily_loss_halt_pct": -0.03,
        "account_drawdown_halt_pct": -0.08,
    },
    "aggressive": {
        "max_position_pct": 0.35,
        "daily_loss_halt_pct": -0.05,
        "account_drawdown_halt_pct": -0.15,
    },
}

# 每个字段「越小越紧」还是「越大越紧」。
#
# ⚠️ 两条熔断线是**负数**，方向与单票占比**相反**：
#   · 单票占比 20% → 10%   = 收紧（买得更少）
#   · 日亏损线 -3% → -2%   = 收紧（亏 2% 就停，比亏 3% 才停更早刹车）
# 把两条熔断线也写成「越小越紧」会让 -3% → -2% 被判成「放宽」，
# 于是每次收紧都要求二次确认、而真的放宽却畅通无阻 —— 正好反了。
_TIGHTER_WHEN_SMALLER = {
    "max_position_pct": True,
    "daily_loss_halt_pct": False,          # 越接近 0 越早停 = 越紧
    "account_drawdown_halt_pct": False,    # 同上
}

# 三个字段的中文名（报错信息与前端提示共用一份，别各写一套）。
FIELD_LABEL = {
    "max_position_pct": "单票最高占比",
    "daily_loss_halt_pct": "单日亏损熔断线",
    "account_drawdown_halt_pct": "账户回撤熔断线",
}


def preset(tier: str) -> dict[str, float]:
    if tier not in PRESETS:
        raise ValueError(f"未知风险档位：{tier!r}（可选 {', '.join(TIER_ORDER)}）")
    return dict(PRESETS[tier])


def _num(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001 —— 库里读出来的是奇怪的串时按「读不到」处理
        return None


def compare(current: dict[str, Any], target: dict[str, float]) -> dict[str, Any]:
    """逐字段比 target 相对 current 是收紧还是放宽。

    返回 `{tightens: bool, changes: [...], loosened: [...]}`。
    `current` 里读不到的字段（None）**不算放宽** —— 无从比较时按最保守的方向处理：
    不拦（值缺失是数据问题，不该让用户永远改不动档位），但也不谎称它收紧了，
    在 `changes` 里标 `unknown_old`。
    """
    changes: list[dict[str, Any]] = []
    loosened: list[dict[str, Any]] = []
    for field, new_v in target.items():
        old = _num(current.get(field))
        new = _num(new_v)
        if new is None:
            continue
        if old is None:
            changes.append({"field": field, "label": FIELD_LABEL.get(field, field),
                            "old": None, "new": float(new), "direction": "unknown_old"})
            continue
        if new == old:
            direction = "same"
        elif (new < old) == _TIGHTER_WHEN_SMALLER.get(field, True):
            direction = "tighter"
        else:
            direction = "looser"
        item = {"field": field, "label": FIELD_LABEL.get(field, field),
                "old": float(old), "new": float(new), "direction": direction}
        changes.append(item)
        if direction == "looser":
            loosened.append(item)
    return {"tightens": not loosened, "changes": changes, "loosened": loosened}


def tier_of(current: dict[str, Any]) -> Optional[str]:
    """现有参数**恰好等于**某个档位预设时返回该档位，否则 None。

    显示用：project 的模板参数（`tiers.py`）与三档预设不是同一套数，
    所以初始项目多半是 None —— 那时前端显示「自定义（模板默认）」而不是硬贴一个档位名。
    """
    for tier, values in PRESETS.items():
        if all(_num(current.get(f)) == _num(v) for f, v in values.items()):
            return tier
    return None
