"""固定示例策略 · 按档位参数出意图。

三条口径（都写下来，免得后来人以为是「分析」）：

1. **标的写死**：`FIN_SAMPLE_CODE`（默认 601398）。示例策略不选股。
2. **手数按档位**：`play=100` / `manage=1000` / `operate=10000` 股。
   取固定值而不是「按资金比例算」——因为那样要一个价格，而**桥这一层不许读行情**
   （会变成「桥在做决策」）。数量多少不改变管道是否跑通。
3. **市价单**：成交价由 `paper` 在执行时按快照的对手价 + 滑点决定
   （M3 口径），示例策略不猜价格。

`decision_id` 是**确定性**的（策略 + 日期 + 时点 + 代码，见 `bridge.idem`），
不是随机数 —— Worker 重启后重跑，拿到同一个 `decision_id`，幂等键就稳定。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any, Optional

from app.bridge.contracts import CONTRACT_VERSION
from app.bridge.idem import sample_decision_id

# 档位 → 示例手数。三档资金（1 万 / 10 万 / 100 万）下都能买得起示例标的。
SAMPLE_LOTS: dict[str, int] = {"play": 100, "manage": 1000, "operate": 10000}

# 档位不确定时的兜底 —— 是最小档，宁可买少不买多。
_DEFAULT_LOT = 100

SHANGHAI = ZoneInfo("Asia/Shanghai")   # A 股本地时区（IANA 名，非固定偏移）


def _lot_for(tier: str) -> int:
    return SAMPLE_LOTS.get((tier or "").strip(), _DEFAULT_LOT)


def build_decision(
    *,
    project: dict[str, Any],
    param: Optional[dict[str, Any]],
    trade_date: str,
    point: str,
    now: datetime,
    code: str,
    strategy_key: str,
    strategy_version: str,
    ttl_seconds: int,
) -> dict[str, Any]:
    """产出一份 `StrategyDecision`（dict 形状，便于跨 Temporal 边界传递）。

    `account_version` 取项目当前的 `version` —— 意图绑定在它看到的那一版账本上；
    账本在它提交前被别的命令改过，`paper` 会拒绝并留痕（M-13 乐观锁）。
    """
    tier = str(project.get("tier") or "")
    lot = _lot_for(tier)
    valid_until = (now + timedelta(seconds=ttl_seconds)).astimezone(SHANGHAI)

    decision: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "decision_id": sample_decision_id(strategy_key, trade_date, point, code),
        "strategy_key": strategy_key,
        "strategy_version": strategy_version,
        "account_version": int(project.get("version") or 0),
        "data_snapshot": {
            # 示例策略**不依赖行情快照**：成交价由 paper 在执行那一刻取快照决定。
            # 这里如实写清它用到的唯一输入（项目参数），不假装自己看过行情。
            "kind": "project_params",
            "project_id": project.get("project_id"),
            "tier": tier,
            "note": "固定示例策略：不读行情；成交价由 paper 按执行时刻快照决定",
        },
        "intent": {
            "code": code,
            "side": "buy",
            "qty": lot,
            "price_type": "market",
        },
        "valid_until": valid_until.isoformat(),
    }
    # param 目前只用来留痕（策略未来读档位参数时用它），这里声明引用关系，
    # 避免「策略版本 + 参数」两件事在复盘时对不上。
    if param is not None:
        decision["data_snapshot"]["max_position_pct"] = str(param.get("max_position_pct"))
        decision["data_snapshot"]["max_positions"] = param.get("max_positions")
    return decision
