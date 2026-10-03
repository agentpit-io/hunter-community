"""固定示例策略 · 按档位参数出意图。

**按市场参数化**（P2）。一期只做 A 股，所以这里当初写死了「市价单 + 固定手数」；
二期的港美股**只接限价单**（`拍板-2026-10-03-港美股必须可交易.md` §四：数据源没有
盘口，市价单按最新价撮合会失真），而且**手数按标的**（港股每手不同），固定手数
（100/1000/10000 股）在 `00700`（421 HKD）上任何档位都买不起 —— 那也是一处
「按 A 股写死」。现在两处都按市场走：

| 市场 | 价型 | 手数 |
|---|---|---|
| 支持市价单的市场（`fin_market_rule.market_order_supported=true`，现只有 `CN_A`） | **市价单**（沿用一期：成交价由 paper 按对手价 + 滑点定） | 档位固定手数（`SAMPLE_LOTS`，一期口径逐字不变） |
| 只接限价单的市场（`HK` / `US`） | **限价单**（限价单必须带价 → 用**真实报价**当参考价，`HunterApiClient.quote`） | **按可用资金买得起的整手数**（不猜价、不编数字；买不起一手就不出委托） |

三条口径（都写下来，免得后来人以为是「分析」）：

1. **标的写死**：`config.sample_code(market)`（A 股 `601398` / 港股 `00700` / 美股 `AAPL`）。
   示例策略不选股。
2. **数量**：A 股 = 档位固定手数；港美股 = 可用资金 ÷（参考价 × 每手股数），
   向下取整手、封顶在档位固定手数。**每手股数是真实数据**（`fin_instrument.lot_size`），
   参考价是**真实报价**，可用资金是**真实余额** —— 三个输入都是真的，没有一个是编的。
3. **成交价**：始终由 `paper` 在执行那一刻按快照决定（限价单也要看快照价是否不劣于限价），
   策略只给**边界**（限价）或**价型**（市价），从不猜成交价。

`decision_id` 是**确定性**的（策略 + 日期 + 时点 + 代码，见 `bridge.idem`），
不是随机数 —— Worker 重启后重跑，拿到同一个 `decision_id`，幂等键就稳定。
（`decision_id` **不含价格与数量**：它们由真实行情 / 余额决定，但幂等键要跨重放稳定，
所以只绑业务身份。重放时 Activity 结果从 Temporal 历史里读，不会重算。）
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Optional
from zoneinfo import ZoneInfo

from app.bridge.contracts import CONTRACT_VERSION
from app.bridge.idem import sample_decision_id
from app.strategy import memory_gate

# 档位 → 示例手数。三档资金（1 万 / 10 万 / 100 万）下都能买得起示例标的（A 股口径）。
SAMPLE_LOTS: dict[str, int] = {"play": 100, "manage": 1000, "operate": 10000}

# 档位不确定时的兜底 —— 是最小档，宁可买少不买多。
_DEFAULT_LOT = 100

# 现金安全垫（只用于「只接限价单」市场的定量）：留出 0.5% 覆盖买入手续费。
# 这是**策略的定量参数**（同 `SAMPLE_LOTS`），不是市场数据 ——
# 手续费口径的真值在 `fin_fee_model`（港股买方约 0.136%、美股买方约 0.01%），
# 0.5% 是最贵那个市场的几倍余量，宁可买少一手也不让 funds 风控拒单。
_CASH_CUSHION = Decimal("0.995")

SHANGHAI = ZoneInfo("Asia/Shanghai")   # 仅在调用方给了 naive 时刻时兜底（A 股本地时区）


class SampleNoBudget(RuntimeError):
    """按可用资金买不起该市场**一手** —— 本时点不出委托。

    不是错误（同「总开关关闭」那样是一种如实的不作为）：A 股小额档买不起一手
    `00700`（每手 100 股 × 421 HKD ≈ 42,120 HKD）是真实约束，不该编一个数量硬下单。
    """


class SampleMemoryBlocked(SampleNoBudget):
    """冻结经验集里有一条**命中本标的**的已验证结论 —— 本时点不出委托。

    R3 · `plan/R3.md` §一.4：这是本轮经验对决策的**唯一**作用，且**只收紧不放松** ——
    它只可能让系统更少下单。判据（规范化标的精确匹配）见 `app/strategy/memory_gate.py`。

    与 `SampleNoBudget` 同级：同一种「如实记不下单」的形态（工作流走 halted 分支）。
    多带两样东西供 checkpoint 留痕：依据的 `experience_id` 与 `memory_snapshot_id`。
    """

    def __init__(self, message: str, *, memory_snapshot_id: Optional[str] = None,
                 blocked: Optional[dict] = None):
        super().__init__(message)
        self.memory_snapshot_id = memory_snapshot_id
        self.blocked = blocked or {}


def _lot_for(tier: str) -> int:
    return SAMPLE_LOTS.get((tier or "").strip(), _DEFAULT_LOT)


def _affordable_qty(available: Any, price: Any, lot: int, cap: int) -> int:
    """可用资金买得起的**整手**数量，封顶 `cap`（档位固定手数）。

    三个输入缺任一 → 0（调用方据此判「买不起 / 算不出」）。价格与资金用 `Decimal`
    全程精确，不做浮点转换（`09 §六-8`）。
    """
    if available is None or price is None:
        return 0
    try:
        cash = Decimal(str(available)) * _CASH_CUSHION
        px = Decimal(str(price))
        lot_i = int(lot or 0)
    except Exception:  # noqa: BLE001 —— 形状不对就当算不出，不硬算
        return 0
    if px <= 0 or lot_i <= 0:
        return 0
    per_lot = px * lot_i
    if per_lot <= 0:
        return 0
    lots = int(cash // per_lot)
    return min(lots * lot_i, int(cap))


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
    market: str = "CN_A",
    market_order_supported: bool = True,
    reference_price: Any = None,
    lot_size: Any = None,
    available_cash: Any = None,
    memory: Any = None,
) -> dict[str, Any]:
    """产出一份 `StrategyDecision`（dict 形状，便于跨 Temporal 边界传递）。

    `account_version` 取项目当前的 `version` —— 意图绑定在它看到的那一版账本上；
    账本在它提交前被别的命令改过，`paper` 会拒绝并留痕（M-13 乐观锁）。

    价型与数量**按市场**（见模块头）：`market_order_supported=False` 的市场出限价单，
    数量按可用资金买得起的整手数定；买不起一手抛 `SampleNoBudget`（调用方如实记「没出单」）。

    `memory` 是**决策前冻结的那份经验集**（`{memory_snapshot_id, items}`）—— 见下面那道闸门。
    """
    # ── 经验闸门（R3 · §4.3）：**只收紧、不放松** ─────────────────────────
    # 冻结经验集里若含「精确命中本标的」的已验证结论 → 本时点**不出委托**。
    # 排在最前：这是安全方向，宁可因为一条经验不下单，也不要在下了单之后才发现。
    # 判据是**规范化标的的精确匹配**，不是文本包含（`memory_gate` 有真值表与理由）。
    blocked = memory_gate.blocking_experience(
        (memory or {}).get("items"), market=market, code=code)
    if blocked is not None:
        raise SampleMemoryBlocked(
            f"冻结经验集里有一条命中 {memory_gate.normalize_symbol(market, code)} 的已验证结论"
            f"（{blocked.get('experience_id')}）：本时点不产生委托",
            memory_snapshot_id=(memory or {}).get("memory_snapshot_id"),
            blocked=blocked,
        )

    tier = str(project.get("tier") or "")
    cap = _lot_for(tier)
    # 有效期：`now + ttl`，**保持 `now` 自带的时区**（调度路径下 `now` 是市场当地的
    # 带时区时刻）—— 不再 `.astimezone(上海)`，那对美股是拿 A 股时区表示一个绝对时刻
    # （instant 没错，但表示法会误导）。naive 时刻才用 A 股时区兜底。
    valid_until = now + timedelta(seconds=ttl_seconds)
    if valid_until.tzinfo is None:
        valid_until = valid_until.replace(tzinfo=SHANGHAI)

    if market_order_supported:
        # ── 市价单（A 股，一期口径逐字不变）──────────────────────────────
        qty = cap
        price_type = "market"
        limit_price = None
        snapshot_note = "固定示例策略：不读行情；成交价由 paper 按执行时刻快照决定"
    else:
        # ── 限价单（港美股）──────────────────────────────────────────────
        qty = _affordable_qty(available_cash, reference_price, lot_size, cap)
        if qty <= 0:
            raise SampleNoBudget(
                f"示例策略按可用资金买不起 {code} 一手"
                f"（每手 {lot_size} 股 × 参考价 {reference_price}，可用 {available_cash}）"
            )
        price_type = "limit"
        limit_price = str(reference_price)
        snapshot_note = (
            f"固定示例策略：{market} 只接限价单，取当前报价 {reference_price} 作参考价"
            "（真实行情源，非分析）；成交价由 paper 按执行时刻快照决定"
        )

    decision: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "decision_id": sample_decision_id(strategy_key, trade_date, point, code),
        "strategy_key": strategy_key,
        "strategy_version": strategy_version,
        "account_version": int(project.get("version") or 0),
        "data_snapshot": {
            # 示例策略**不做分析**：A 股不读行情（成交价由 paper 定）；港美股只取一个
            # 真实报价当限价单的参考价（限价单必须带价）。这里如实写清它用到的输入。
            "kind": "project_params",
            "project_id": project.get("project_id"),
            "market": market,
            "tier": tier,
            "note": snapshot_note,
        },
        "intent": {
            "code": code,
            "side": "buy",
            "qty": qty,
            "price_type": price_type,
        },
        "valid_until": valid_until.isoformat(),
        # R3 · 决策上下文里的经验集（冻结时那一版）。**只带 id 与命中的条目**，
        # 内容与内容哈希的真值在 `fin_memory_snapshot`（服务端），不在这里抄一份。
        "memory": {
            "memory_snapshot_id": (memory or {}).get("memory_snapshot_id"),
            "experience_ids": [i.get("experience_id") for i in (memory or {}).get("items") or []],
        },
    }
    if limit_price is not None:
        decision["intent"]["limit_price"] = limit_price
        decision["data_snapshot"]["reference_price"] = str(reference_price)
    # param 目前只用来留痕（策略未来读档位参数时用它），这里声明引用关系，
    # 避免「策略版本 + 参数」两件事在复盘时对不上。
    if param is not None:
        decision["data_snapshot"]["max_position_pct"] = str(param.get("max_position_pct"))
        decision["data_snapshot"]["max_positions"] = param.get("max_positions")
    return decision
