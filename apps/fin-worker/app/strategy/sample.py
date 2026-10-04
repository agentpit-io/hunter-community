"""固定示例策略 · 按档位参数出意图。

**按市场参数化**（P2）。一期只做 A 股，所以这里当初写死了「市价单 + 固定手数」；
二期的港美股**只接限价单**（`拍板-2026-10-03-港美股必须可交易.md` §四：数据源没有
盘口，市价单按最新价撮合会失真），而且**手数按标的**（港股每手不同），固定手数
（100/1000/10000 股）在 `00700`（421 HKD）上任何档位都买不起 —— 那也是一处
「按 A 股写死」。现在两处都按市场走：

| 市场 | 价型 | 手数 |
|---|---|---|
| 支持市价单的市场（`fin_market_rule.market_order_supported=true`，现只有 `CN_A`） | **市价单**（沿用一期：成交价由 paper 按对手价 + 滑点定） | 档位固定手数（`SAMPLE_LOTS`，一期口径逐字不变） |
| 只接限价单的市场（`HK` / `US`） | **限价单**（限价单必须带价 → 用**真实报价**当参考价，`HunterApiClient.quote`） | **按 `max_position_pct` 定预算的整手数**（见下；买不起一手就不出委托） |

三条口径（都写下来，免得后来人以为是「分析」）：

1. **标的写死**：`config.sample_code(market)`（A 股 `601398` / 港股 `00700` / 美股 `AAPL`）。
   示例策略不选股。
2. **数量**（`R12` 起港美股**真读白名单参数** `max_position_pct`）：

   ```
   限价单市场：qty = min(档位手数, ⌊可用资金 × max_position_pct ÷ (参考价 × 每手股数)⌋ × 每手股数)
   ```

   **每手股数是真实数据**（`fin_instrument.lot_size`）、参考价是**真实报价**、
   可用资金是**真实余额**、占比是**项目参数**（`fin_param.max_position_pct`）——
   四个输入都是真的，没有一个是编的。量价三输入（可用资金 / 参考价 / 每手股数）
   **任一缺失 → 回落到档位固定手数**（拿不到可靠输入就不猜，见 `_position_qty`）。
3. **成交价**：始终由 `paper` 在执行那一刻按快照决定（限价单也要看快照价是否不劣于限价），
   策略只给**边界**（限价）或**价型**（市价），从不猜成交价。

### 为什么 A 股（市价单）路径**不**读 `max_position_pct`

市价单路径**根本不取价格与可用余额**（一期口径：成交价由 `paper` 按对手价 + 滑点定，
所以出意图时不需要报价），**拿不到量价输入就不做占比定量** —— 硬按占比算，
分母是编的。这不是「漏读参数」，是**如实的不作为**：有输入的路径才定量，
没有输入的路径沿用档位固定手数。所以 `R12` 的验收用**只选港股 / 只选美股**的项目
（对齐 `追加规则 §六.6` 与三期 `P4` 口径）。

### 策略真正读取的白名单字段（唯一口径）

`STRATEGY_READ_FIELDS` 是**示例策略真的会读**的白名单参数清单。影子臂的
`shadow.param_of()` 按这份清单搬参数 —— 两处共用一份，否则会「接线做完了但 `delta` 还是 0」。
`R12` 只接 `max_position_pct`；`stop_loss_pct` / `take_profit_pct` / `hold_days_max` 属于
**持仓管理层**，示例策略是**只买不卖**的策略，不在这里自造卖出规则（那才是编造）。

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

# 示例策略**真正读取**的白名单字段（`R12`）—— 影子臂的 `shadow.param_of()` 按这份清单搬参数。
# 只列策略**真的会读**的字段：多列不影响结果，少列一个会让影子臂读不到它、
# 两臂不分化（表现是「接线做完了但 delta 还是 0」）。改这里要同步 `shadow.param_of` 的用例。
STRATEGY_READ_FIELDS: tuple[str, ...] = ("max_position_pct",)

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


def _position_qty(*, available: Any, price: Any, lot: Any, cap: int, pct: Any) -> int:
    """限价单市场按 `max_position_pct` 定买入**整手**数量，封顶 `cap`（档位固定手数）。

    ```
    qty = min(cap, ⌊available × pct × 0.995 ÷ (price × lot)⌋ × lot)
    ```

    预算是「可用资金 × 占比」，再乘 `_CASH_CUSHION`（0.5% 买入手续费余量）。
    **量价三输入（`available` / `price` / `lot`）任一缺失 / 不可用 → 回落到档位固定手数 `cap`**
    —— 拿不到可靠输入就不猜（与旧的「缺任一 → 0」同一种「如实的不作为」精神，只是落点从
    「不出委托」换成「按档位固定手数出委托」，因为档位手数是配置里写死的数字、不是猜的）。
    **占比（`pct`）缺失 → 沿用旧口径**（不设占比上限，按可用资金定量）：量价输入都在，
    只是没配占比，仍算得出真实数量。资金 / 占比为 0 → 返回 0（调用方据此 `SampleNoBudget`）。

    全程 `Decimal`，不做浮点转换（`09 §六-8`）。
    """
    cap = int(cap)
    # 量价三输入任一缺失 → 回落档位固定手数（不猜）。
    if available is None or price is None or lot is None:
        return cap
    try:
        cash = Decimal(str(available))
        px = Decimal(str(price))
        lot_i = int(lot)
    except (ArithmeticError, ValueError, TypeError):
        return cap                      # 形状不对 = 拿不到可靠输入，不硬算
    if px <= 0 or lot_i <= 0:
        return cap                      # 价 / 每手无效 = 没有可靠量价输入
    if pct is None:
        ratio = Decimal(1)              # 占比未配置 → 旧口径（不设占比上限）
    else:
        try:
            ratio = Decimal(str(pct))
        except (ArithmeticError, ValueError, TypeError):
            return cap                  # 占比形状不对 = 拿不到可靠输入，不猜
    if cash <= 0 or ratio <= 0:
        return 0                        # 可用资金 / 占比为 0 → 买不起一手
    budget = cash * ratio * _CASH_CUSHION
    lots = int(budget // (px * lot_i))
    return min(lots * lot_i, cap)


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
    数量按 `max_position_pct` 定预算的整手数（`_position_qty`）；买不起一手抛
    `SampleNoBudget`（调用方如实记「没出单」）。

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
    # `R12`：策略真正读取的白名单参数（唯一口径见模块头）。`param` 在实盘路径是
    # `fin_param` 行、在影子路径是 `shadow.param_of(config)` —— 两边都带 `max_position_pct`。
    pct = (param or {}).get("max_position_pct") if param else None
    # 有效期：`now + ttl`，**保持 `now` 自带的时区**（调度路径下 `now` 是市场当地的
    # 带时区时刻）—— 不再 `.astimezone(上海)`，那对美股是拿 A 股时区表示一个绝对时刻
    # （instant 没错，但表示法会误导）。naive 时刻才用 A 股时区兜底。
    valid_until = now + timedelta(seconds=ttl_seconds)
    if valid_until.tzinfo is None:
        valid_until = valid_until.replace(tzinfo=SHANGHAI)

    if market_order_supported:
        # ── 市价单（A 股，一期口径逐字不变）──────────────────────────────
        # **不读 `max_position_pct`**：市价单路径不取价格与可用余额（成交价由 paper 按
        # 对手价 + 滑点定），拿不到量价输入就不做占比定量（见模块头「为什么 A 股路径不读」）。
        qty = cap
        price_type = "market"
        limit_price = None
        snapshot_note = "固定示例策略：不读行情；成交价由 paper 按执行时刻快照决定"
    else:
        # ── 限价单（港美股）—— 按 `max_position_pct` 定量（R12）────────────
        # 限价单必须带价：没有可靠参考价就没法出委托（如实的不作为，不猜价）。
        if reference_price is None:
            raise SampleNoBudget(
                f"示例策略：{market} 只接限价单，行情源没给到 {code} 的参考价，本时点不出委托")
        qty = _position_qty(available=available_cash, price=reference_price,
                            lot=lot_size, cap=cap, pct=pct)
        if qty <= 0:
            raise SampleNoBudget(
                f"示例策略按可用资金 / 占比买不起 {code} 一手"
                f"（每手 {lot_size} 股 × 参考价 {reference_price}，可用 {available_cash}，"
                f"占比 {pct}）"
            )
        price_type = "limit"
        limit_price = str(reference_price)
        snapshot_note = (
            f"固定示例策略：{market} 只接限价单，取当前报价 {reference_price} 作参考价"
            f"、按 max_position_pct={pct} 定预算（真实行情源 + 真实余额，非分析）；"
            "成交价由 paper 按执行时刻快照决定"
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
    # 留痕：把策略**真正读取**的白名单字段写进 `data_snapshot`（`R12` 起它参与定量，
    # 不再只是留痕）。`params_used` 与 `shadow.param_of` 的清单同一份（`STRATEGY_READ_FIELDS`），
    # 避免「策略版本 + 参数」在复盘时对不上。
    if param is not None:
        used = {name: param.get(name) for name in STRATEGY_READ_FIELDS}
        decision["data_snapshot"]["params_used"] = used
        # 兼容旧读取方：`max_position_pct` 仍在顶层（字符串化）。
        if "max_position_pct" in used:
            decision["data_snapshot"]["max_position_pct"] = str(used["max_position_pct"])
    return decision
