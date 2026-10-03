"""业务契约 · `StrategyDecision` → Paper Service 命令。

契约字段（任务书 §2 点名要的五个，一个不少）：

| 字段 | 是什么 | 为什么必须在 |
|---|---|---|
| `contract_version` | 契约本身的版本号 | 契约要带版本号；不带的契约过半年就没人敢动 |
| `strategy_version` | **策略版本** | 复盘时要能说清「这笔是哪个策略版本下的」 |
| `data_snapshot` | **数据快照** | 策略是基于哪一份数据做的判断（M3 的 `fin_snapshot` 编号） |
| `account_version` | **账户版本** | 与 M-13 的乐观锁一致：意图必须落在它看到的那一版上 |
| `intent` | **意图** | 买/卖、代码、数量、价型、限价 |
| `valid_until` | **有效期** | 过期不补单（`01方案 §11.2`「信号过期」） |

**契约版本与命令版本是两回事**：`contract_version` 说的是这份 JSON 的形状，
`strategy_version` 说的是策略逻辑。两者都进 `decision_ref`，落进 `fin_order`。

这是**不可信输入**（策略侧产出、策略侧可能读到第三方 SKILL 正文），所以
`from_dict` 做严格校验：缺字段、类型不对、数量非正一律拒 —— 宁可让工作流失败，
也不要把一份看不懂的意图「猜着」翻译成委托。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional

# 当前契约版本。加字段不改语义 → 次版本；改字段含义 → 主版本，且必须留兼容分支。
CONTRACT_VERSION = "1.0"

SIDES = ("buy", "sell")
PRICE_TYPES = ("limit", "market")


class ContractError(ValueError):
    """契约不合法。**拒绝**，不猜测、不补齐缺字段。"""


@dataclass(frozen=True)
class Intent:
    """一笔交易意图。对应 `Paper Service` 的 `OrderIn` 里「下单」那部分。"""

    code: str
    side: str
    qty: int
    price_type: str = "limit"
    limit_price: Optional[Decimal] = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Intent":
        if not isinstance(raw, dict):
            raise ContractError("intent 必须是对象")
        code = str(raw.get("code") or "").strip()
        if not code:
            raise ContractError("intent.code 必填")
        side = str(raw.get("side") or "").strip()
        if side not in SIDES:
            raise ContractError(f"intent.side 必须是 {SIDES} 之一，得到 {side!r}")
        try:
            qty = int(raw.get("qty"))
        except (TypeError, ValueError):
            raise ContractError("intent.qty 必须是整数")
        if qty <= 0:
            raise ContractError("intent.qty 必须为正（qty<=0 是格式错误，不是风控拒绝）")
        price_type = str(raw.get("price_type") or "limit").strip()
        if price_type not in PRICE_TYPES:
            raise ContractError(f"intent.price_type 必须是 {PRICE_TYPES} 之一")
        limit_price = raw.get("limit_price")
        if price_type == "limit":
            if limit_price is None:
                raise ContractError("限价意图必须带 limit_price")
            limit_price = Decimal(str(limit_price))
            if limit_price <= 0:
                raise ContractError("limit_price 必须为正")
        else:
            if limit_price is not None:
                raise ContractError("市价意图不接受 limit_price")
            limit_price = None
        return cls(code=code, side=side, qty=qty, price_type=price_type, limit_price=limit_price)


@dataclass(frozen=True)
class StrategyDecision:
    """策略意图。**唯一**允许进入 Runtime Bridge 的业务输入。"""

    decision_id: str
    strategy_key: str
    strategy_version: str
    account_version: int
    intent: Intent
    valid_until: str  # ISO8601（带时区）
    data_snapshot: dict[str, Any] = field(default_factory=dict)
    # R3 · 决策前冻结的那份经验集（`memory.query(freeze=True)` 的结果）。
    # 形状 `{"memory_snapshot_id": "msnap_...", "experience_ids": [...]}` ——
    # **id 与内容哈希都在快照里**（`fin_memory_snapshot.query_filter`），这里只带 id，
    # 好让 `fin_order.intent_ref` 记得住「这笔委托是站在哪一版经验上做的决定」。
    memory: dict[str, Any] = field(default_factory=dict)
    contract_version: str = CONTRACT_VERSION

    # ── 构造 ──────────────────────────────────────────────────────────────
    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "StrategyDecision":
        if not isinstance(raw, dict):
            raise ContractError("StrategyDecision 必须是对象")
        cv = str(raw.get("contract_version") or "").strip()
        if not cv:
            raise ContractError("契约缺 contract_version —— 没有版本号的契约不许进桥")
        if cv.split(".")[0] != CONTRACT_VERSION.split(".")[0]:
            # 主版本不同 → 形状可能已经变了，拒绝而不是硬解。
            raise ContractError(
                f"契约主版本不兼容：桥支持 {CONTRACT_VERSION}，收到 {cv}（升级 Runtime Bridge 再重放）"
            )
        decision_id = str(raw.get("decision_id") or "").strip()
        if not decision_id:
            raise ContractError("decision_id 必填（幂等键要用它）")
        strategy_key = str(raw.get("strategy_key") or "").strip()
        strategy_version = str(raw.get("strategy_version") or "").strip()
        if not strategy_key or not strategy_version:
            raise ContractError("strategy_key / strategy_version 必填（复盘要能追到版本）")
        try:
            account_version = int(raw.get("account_version"))
        except (TypeError, ValueError):
            raise ContractError("account_version 必须是整数")
        if account_version < 0:
            raise ContractError("account_version 不能为负")
        valid_until = str(raw.get("valid_until") or "").strip()
        if not valid_until:
            raise ContractError("valid_until 必填（过期不补单）")
        snapshot = raw.get("data_snapshot") or {}
        if not isinstance(snapshot, dict):
            raise ContractError("data_snapshot 必须是对象")
        memory = raw.get("memory") or {}
        if not isinstance(memory, dict):
            raise ContractError("memory 必须是对象（决策前冻结的经验集）")
        return cls(
            decision_id=decision_id,
            strategy_key=strategy_key,
            strategy_version=strategy_version,
            account_version=account_version,
            intent=Intent.from_dict(raw.get("intent")),
            valid_until=valid_until,
            data_snapshot=snapshot,
            memory=memory,
            contract_version=cv,
        )

    @property
    def strategy_ref(self) -> str:
        return f"{self.strategy_key}@{self.strategy_version}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "decision_id": self.decision_id,
            "strategy_key": self.strategy_key,
            "strategy_version": self.strategy_version,
            "account_version": self.account_version,
            "data_snapshot": self.data_snapshot,
            "memory": self.memory,
            "intent": {
                "code": self.intent.code,
                "side": self.intent.side,
                "qty": self.intent.qty,
                "price_type": self.intent.price_type,
                "limit_price": (
                    str(self.intent.limit_price) if self.intent.limit_price is not None else None
                ),
            },
            "valid_until": self.valid_until,
        }

    # ── 翻译成执行区命令 ──────────────────────────────────────────────────
    def to_paper_command(
        self,
        *,
        project_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """→ `POST /api/v1/orders` 的请求体。

        `expected_version` 直接取意图里的 `account_version` —— 账本版本对不上
        由 **paper** 拒绝并留痕（`01方案 §11.1`：并发的后到者被版本校验拦下）。
        桥这一层不做版本判断，它没有账本可看。
        """
        body: dict[str, Any] = {
            "project_id": project_id,
            "code": self.intent.code,
            "side": self.intent.side,
            "qty": self.intent.qty,
            "price_type": self.intent.price_type,
            "source": "ai",             # 一期恒 ai（`09 §4.5`）
            "actor": "fin-worker",
            "decision_ref": self.decision_id,
            "valid_until": self.valid_until,
            "idempotency_key": idempotency_key,
            "expected_version": self.account_version,
            "intent_ref": {
                # L3 留痕不可修改：策略版本、数据快照、契约版本都钉在这里。
                "strategy": self.strategy_ref,
                "strategy_key": self.strategy_key,
                "strategy_version": self.strategy_version,
                "contract_version": self.contract_version,
                "data_snapshot": self.data_snapshot,
                # R3 · 这笔委托站在哪一版经验上做的决定（没有经验集时为空对象）。
                "memory": self.memory,
            },
        }
        if self.intent.limit_price is not None:
            body["limit_price"] = str(self.intent.limit_price)
        return body
