"""智能炒股 · 受控自进化 · **提案层 + 影子层**（第四段 `R6` / `R7`）。

模块头（一）讲提案层（`R6`），模块头（二）讲影子层（`R7`）—— 两段合起来才是本文件的全部职责。

## （二）影子层（`R7 · plan/R7.md` §一.2–4）

`fin_evolution_shadow_event` 是**事件溯源式的影子账本**：每一行 = 某臂在某个
`(validation_id, trade_date, point, symbol)` 上的一次决策，以及它的（模拟）成交 / 拒单 /
手续费 / 滑点 / **持仓快照 / 估值快照**。两条铁律：

1. **它和 `fin_trade` 毫无关系**（`03 §4-C`）—— `fin_trade` 只记真实 / 现行策略的成交，
   **推不出未执行过的候选策略的收益**。影子成交只能进这张表，**绝不进** `fin_trade` / `fin_order`
   （红线 12；由 `paper` 侧的 `DecisionRecorder` / `OrderExecutor` 接口隔离 + 集成测试保证）。
2. **验证口径写入即冻结**（红线 10）：判定**一律读 `fin_evolution_plan` 冻结下来的**通过线 /
   失败线 / 窗口 / 最小可比样本，**不许临时改**。样本不足 / 行情缺口 / 未完成持仓 → `inconclusive`
   （不结论也是结论）。窗口未结束前**任何**路径都不得写出 `passed`。

两臂**同条件**（红线 11）：同一行情快照、同初始现金 / 仓位、同费率 / 滑点 / 停牌 / 撮合假设
（撮合与费用**复用 `paper` 的实现**，不在别处重写一份）。样本以「**两臂均有可比机会**的
`(trade_date, point, symbol)`」计 —— **不许**把「候选有交易、现行没交易」的笔数直接相减。

**为什么影子事件由本模块写、而不是 `paper`**：`0043`（照 `0041`）**刻意不给**
`fin_paper_rw` 任何授权（见 `0043` 文件尾注释），运行期唯一有 `fin_*` 权限的正是那个角色 ——
所以 `paper` **写不了**这张表，它只负责**算**（`apps/paper/app/shadow.py`），
api 负责**记**。这正好把「撮合实现」与「影子账本」分成两件事，各自只有一份。

## （一）提案层（`R6 · plan/R6.md` §一`）。

**这一层做的事，一句话**：把「一批经验 → 一个参数改动」变成一个**可审计、可冻结、
且 AI 提不了越界的东西**的对象。它**不改任何生效配置**（生效在 `R8`）、**不做影子验证**
（在 `R7`）—— 只负责「提案 + 冻结计划 + 拒绝留痕」。

## 唯一入口与四条护栏

写路径只有一条：`propose(...)`。它把三件事**写在同一个事务里**（缺一即整体回滚）：

1. `fin_evolution_proposal`（提案，`status` 只是投影）；
2. `fin_evolution_plan`（**冻结计划**，写后不可改 —— 触发器 + `plan_hash`）；
3. 第一条 `fin_evolution_event(kind='created')`（状态事件，哈希链的起点）。

四条护栏（`plan/R6.md` §一.3）：

| 护栏 | 口径 | 越界 |
|---|---|---|
| **白名单** | 只许改白名单里的参数，每个带 类型 / 取值范围 / 单次最大变化 / 基线版本 | 拒绝 + `rejected_by_gate` |
| **证据** | `evidence_refs` 同项目 · 同时间边界 · 非 holdout · 来自**冻结快照**；失败经验（`refute`）同样可引用 | 拒绝 + `rejected_by_gate` |
| **`param_diff`** | **服务端机器算**（base 与 candidate 逐字段比），比对写入方传进来的 diff | 不一致 → 拒绝 + `rejected_by_gate` |
| **方向（红线 8）** | `risk` 走 `risk.compare()`；`strategy` 走白名单每参数的方向语义。**AI 无权放宽风控** | 放宽 → **400 + 不入库** + `rejected_by_gate` |

**红线 8 是这一层的承重墙**（`01 §3.3`）：自我强化回路闭合的地方，正是「记忆能影响仓位」
的那一点。把它焊死，记忆就只剩「收紧」和「换策略」两条腿，回撤放大器被拆掉。

## 为什么 `strategy` 方向不能借用 `risk.compare()`

`risk.compare()` 只认识它自己的三个字段（`_TIGHTER_WHEN_SMALLER` 里的），
拿它判策略参数（`vol_mult` / `confirm_days` …）会得出「没有变化」或方向反了的结论。
所以策略方向由**白名单里每个参数自带的 `tighter_when`** 决定（见 `_STRATEGY_FIELDS`）。

## 证据为什么不直接读经验表

经验的**唯一读入口**是 `services/fin/memory.py`（红线 2）。本模块**不碰经验三表**
（`tests/test_fin_memory_guard.py` 盯着这条），而是调 `memory.get_snapshot(id)` 拿那份**冻结集合**。
这样「拿的是哪一批经验」由快照固定，
不是「此刻查得到的那批」—— 防未来函数。

## `plan` 的默认值（红线 13：不预写阈值当既成事实）

`DEFAULT_PLAN` 里的窗口 / 最小样本 / 通过线 / 失败线 / 回滚线是**实现时默认值**，
**依据与验证样本公开在** `docs/开发文档/R6-提案层与不可变冻结计划.md`。
本轮**只保证它们能被写、不能被改**（触发器）—— `R7` 会拿真实影子数据校准它们；
在那之前，`pass_line` / `fail_line` 允许为空（=「未定」）。
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import psycopg2
import psycopg2.errors
import psycopg2.extras
from loguru import logger

from app.services.fin import memory as memory_svc
from app.services.fin import regime as regime_svc
from app.services.fin import risk as risk_svc
from app.services.fin import switches

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://hunter:hunter@localhost:5432/hunter")

# ── 版本键 ───────────────────────────────────────────────────────────────
# 提案算法版本：白名单（参数集 + 方向语义 + 取值范围 + 单次最大变化）的版本。
# **改白名单任何一项 = 换版本**（新增一个键，旧提案指着老版本，永不被新口径重新解释）。
#
# v2（2026-10-04 · R10 出口验收）：`stop_loss_pct` 的取值范围 / 方向语义**改正为负号口径**
#   （`[-0.5, -0.005]` · `tighter_when='larger'`），与 `fin_param` 的实际存量数据
#   （1294/1312 个项目的 `stop_loss_pct = -0.04 ~ -0.03`）和其它两条熔断线**同口径**。
#   v1 写的是**正号**范围 `[0.005, 0.5]`，与档位模板（`tiers.py`）和向导写进
#   `fin_param.stop_loss_pct` 的**负数**对不上 —— 于是**任何向导开出来的真实项目都提不出提案**
#   （候选配置里带着 `stop_loss_pct=-0.04`，一进门就被范围检查拒掉）。这是跨阶段缺陷，
#   在 R10 的演示站验收里实测复现并修掉。旧提案仍指 `evolution-algo-v1`，不被新口径重新解释。
ALGO_VERSION = "evolution-algo-v2"

# `fin_evolution_event.kind` 闭集 —— 与 `0043`（+`0044`/`0045`）的 CHECK **逐字一致**，
# 改一处必须改另一处。`failed` 是 `R7` 加的（迁移 `0044`）：验证**跑出来不达标**是独立语义，
# 与 `rejected`（提案被驳回）分开；`proposal.status` 仍投影到 `rejected`（状态列没有 failed）。
# `alert` 是 `R8` 加的（迁移 `0045`）：观察期「普通性能不达标」的**告警** —— 它既不驳回提案、
# 也不回滚，所以**不改变**投影状态（`STATUS_OF_KIND['alert'] = 'applied'`）。
EVENT_KINDS = (
    "created", "validating", "passed", "rejected", "applied",
    "rolled_back", "inconclusive", "rejected_by_gate", "failed", "alert",
)
TARGETS = ("strategy", "risk")
DIRECTIONS = ("tighten", "loosen", "mixed")

# 事件 kind → 提案 `status` 投影（`status` 是**可重建投影**，权威是事件表）。
# `rejected_by_gate` 的提案通常**根本没入库**（红线 8），所以这里给的是"若存在则该是什么"。
STATUS_OF_KIND = {
    "created": "draft",
    "validating": "validating",
    "passed": "passed",
    "rejected": "rejected",
    "applied": "applied",
    "rolled_back": "rolled_back",
    "inconclusive": "inconclusive",
    "rejected_by_gate": "rejected",
    "failed": "rejected",      # 验证不达标 → 提案被驳回（状态列无 failed）
    "alert": "applied",        # 观察期告警：不改变投影状态（仍处生效 / 观察态）
}


# ════════════════════════════════════════════════════════════════════════
# 一 · 白名单（**改为带版本号的表**，`ALGO_VERSION` 指回来）
# ════════════════════════════════════════════════════════════════════════
#
# 每个参数带：`type`（ratio / int）· `min` / `max`（取值范围）· `max_step`（单次最大变化）·
# `baseline_version`（该参数取值语义的基线版本键）· `tighter_when`（方向语义，strategy 判定用）。
#
# 三个风控字段的取值与方向**与 `risk.py` 同源**（`PRESETS` / `_TIGHTER_WHEN_SMALLER`）：
# `max_position_pct` 越小越紧；两条熔断线是**负数**，越接近 0 越早停 = 越紧。
# risk 的方向**不**看这里的 `tighter_when`（那只是文档），由 `risk.compare()` 判（见下）。

_RISK_FIELDS: dict[str, dict[str, Any]] = {
    "max_position_pct": {
        "type": "ratio", "min": 0.0, "max": 1.0, "max_step": 0.10,
        "tighter_when": "smaller", "baseline_version": "risk-fields-v1",
        "note": "单票最高占比；越小 = 买得越少 = 越紧（risk.py:PRESETS）",
    },
    "daily_loss_halt_pct": {
        "type": "ratio", "min": -0.5, "max": 0.0, "max_step": 0.05,
        "tighter_when": "larger", "baseline_version": "risk-fields-v1",
        "note": "单日亏损熔断线（负数）；越接近 0 = 越早停 = 越紧",
    },
    "account_drawdown_halt_pct": {
        "type": "ratio", "min": -1.0, "max": 0.0, "max_step": 0.10,
        "tighter_when": "larger", "baseline_version": "risk-fields-v1",
        "note": "账户回撤熔断线（负数）；越接近 0 = 越早停 = 越紧",
    },
}

# 策略参数：**每个自带方向语义**（`tighter_when`）。`stop_loss_pct` / `take_profit_pct` /
# `hold_days_max` 是 `fin_param` 的顶层列；`vol_mult` / `confirm_days` 出现在
# `strategies[].params` 里（外层字段键形如 `strategies.<key>.params.<name>`）。
_STRATEGY_FIELDS: dict[str, dict[str, Any]] = {
    "stop_loss_pct": {
        "type": "ratio", "min": -0.5, "max": -0.005, "max_step": 0.05,
        "tighter_when": "larger", "baseline_version": "strategy-fields-v1",
        "note": "止损线（**负数**，与 fin_param / 另两条熔断线同口径）；越接近 0 = 越早离场 = 越紧",
    },
    "take_profit_pct": {
        "type": "ratio", "min": 0.005, "max": 5.0, "max_step": 0.5,
        "tighter_when": "smaller", "baseline_version": "strategy-fields-v1",
        "note": "止盈幅度；越小 = 越早落袋 = 越保守",
    },
    "hold_days_max": {
        "type": "int", "min": 1, "max": 60, "max_step": 5,
        "tighter_when": "smaller", "baseline_version": "strategy-fields-v1",
        "note": "持有天数上限；越短 = 越保守",
    },
    "vol_mult": {
        "type": "ratio", "min": 0.1, "max": 10.0, "max_step": 1.0,
        "tighter_when": "larger", "baseline_version": "strategy-fields-v1",
        "note": "放量倍数门槛；越高 = 入场越严 = 越紧",
    },
    "confirm_days": {
        "type": "int", "min": 0, "max": 10, "max_step": 2,
        "tighter_when": "larger", "baseline_version": "strategy-fields-v1",
        "note": "确认天数；越多 = 入场越严 = 越紧",
    },
}

# 提交里的 `plan` 默认值（**实现时默认值**，非既成阈值 —— 见模块文档）。
DEFAULT_PLAN: dict[str, Any] = {
    "metric": "portfolio_net_return",     # 同资本基准的组合净收益（03 §4-C 的首选指标）
    "window_days": 20,                    # 一个交易月（默认值，R7 校准）
    "min_comparable_sample": 20,          # 最小**可比样本**（红线 11：两臂均有可比机会）
    "cost_model": "fee-model-v1",
    "slippage_model": "slippage-v1",
    "data_source_version": "data-v1",
    "pass_line": 0.01,                    # 通过线（默认 +1%，R7 校准）
    "fail_line": -0.01,                   # 失败线（默认 -1%）
    "early_stop_condition": {"max_drawdown_pct": -0.08},
    "rollback_line": {"max_drawdown_pct": -0.10},
}


def whitelist_for(field: str) -> Optional[tuple[str, str, dict[str, Any]]]:
    """`field` → `(target, param_name, 白名单条目)`；不在白名单 → `None`。

    `strategies.<key>.params.<name>` 取**最后一段**当参数名；顶层字段直接查两张表。
    """
    field = str(field or "").strip()
    if not field:
        return None
    if field.startswith("strategies."):
        name = field.rsplit(".", 1)[-1]
        entry = _STRATEGY_FIELDS.get(name)
        return ("strategy", name, entry) if entry else None
    if field in _RISK_FIELDS:
        return ("risk", field, _RISK_FIELDS[field])
    if field in _STRATEGY_FIELDS:
        return ("strategy", field, _STRATEGY_FIELDS[field])
    return None


def public_whitelist() -> dict[str, Any]:
    """白名单的可序列化视图（成果文档 / 测试 / 未来前端展示共用一份）。"""
    return {
        "algo_version": ALGO_VERSION,
        "risk": _RISK_FIELDS,
        "strategy": _STRATEGY_FIELDS,
    }


# ════════════════════════════════════════════════════════════════════════
# 二 · 异常（路由据此翻 HTTP 码）
# ════════════════════════════════════════════════════════════════════════

class EvolutionError(Exception):
    """本模块所有服务端拒绝的基类。"""


class EvolutionDisabledError(EvolutionError):
    """`FIN_EVOLUTION_MODE=off` —— 能力没开，不是请求写错了（路由 → **503**）。"""


class EvolutionGateError(EvolutionError):
    """闸门拒绝（白名单 / 证据 / `param_diff` 篡改 / 红线 8 / regime 不可用）。

    服务端在抛它之前会**先追加一条 `rejected_by_gate` 事件**（谁试过必须留痕），
    路由据此回 **400**。
    """


class EvolutionConflictError(EvolutionError):
    """同一项目同一 base 已有一个待验证提案（唯一索引）—— 路由 → **409**。"""


class EvolutionValidationError(EvolutionError):
    """请求结构本身不合法（缺字段 / 类型不对）—— 路由 → **400**，不写事件。"""


# ════════════════════════════════════════════════════════════════════════
# 三 · 纯函数（不碰库，可在 tests/test_fin_evolution.py 里直接测）
# ════════════════════════════════════════════════════════════════════════

def _canon_num(value: Any) -> str:
    """数字 → 规范字符串（去尾零、不用科学计数法）。`0.10` / `0.1` / `Decimal('0.100')` 同形。"""
    if isinstance(value, bool):
        raise ValueError("布尔值不是数字")
    d = Decimal(str(value))
    s = format(d, "f")                    # 定点，避免 1E+1 这种
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def _json_num(value: Any) -> Any:
    """数字 → JSON 可序列化（整数给 int，否则 float）。"""
    if value is None:
        return None
    d = Decimal(str(value))
    if d == d.to_integral_value():
        return int(d)
    return float(d)


def _canonical(value: Any) -> Any:
    """递归规范化：数字 → 规范字符串；dict / list 递归。用于所有哈希。"""
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float, Decimal)):
        return _canon_num(value)
    return str(value)


def _hash_json(obj: Any) -> str:
    """对象的规范哈希：sha256(json(递归规范化, 键排序, 紧凑, 不转义非 ASCII))。"""
    raw = json.dumps(_canonical(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def hash_config(config: dict[str, Any]) -> str:
    """一份配置的规范哈希。配置是「字段 → 数值」的扁平 dict。"""
    return _hash_json(config)


def config_diff(base: dict[str, Any], candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """逐字段比 base 与 candidate，返回 `[{field, old, new}]`（**只列变化字段**，按字段排序）。

    这是「`param_diff` 由服务端机器计算」的兑现：写入方传进来的 diff **不采信**，
    只拿它跟这里算出来的比。
    """
    out: list[dict[str, Any]] = []
    for field in sorted(set(base) | set(candidate)):
        b = base.get(field)
        c = candidate.get(field)
        if b is None and c is None:
            continue
        if _canon_num(b) != _canon_num(c):
            out.append({"field": field, "old": _json_num(b), "new": _json_num(c)})
    return out


def _check_value(field: str, entry: dict[str, Any], value: Any) -> float:
    """类型 + 取值范围校验（不含 `max_step` —— 那要看 old，在 `_check_step` 里）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise EvolutionGateError(f"{field} 应是数字，收到 {type(value).__name__}")
    num = float(value)
    if num != num or num in (float("inf"), float("-inf")):
        raise EvolutionGateError(f"{field} 应是有限数字")
    if entry["type"] == "int" and num != int(num):
        raise EvolutionGateError(f"{field} 应是整数，收到 {num}")
    if not (entry["min"] <= num <= entry["max"]):
        raise EvolutionGateError(
            f"{field} 超出白名单取值范围 [{entry['min']}, {entry['max']}]，收到 {num}")
    return num


def _check_step(field: str, entry: dict[str, Any], old: Any, new: Any) -> None:
    """单次最大变化：`|new - old| <= max_step`。"""
    delta = abs(float(new) - float(old))
    if delta > entry["max_step"] + 1e-12:
        raise EvolutionGateError(
            f"{field} 单次变化 {delta:g} 超过白名单上限 {entry['max_step']:g}"
            f"（{entry.get('note', '')}）")


def normalize_candidate(candidate: Any) -> dict[str, float]:
    """候选配置 → 规范化 dict；任一字段不在白名单 / 类型或范围不对 → `EvolutionGateError`。"""
    if not isinstance(candidate, dict) or not candidate:
        raise EvolutionGateError("candidate_config 必须是一个非空对象（字段 → 数值）")
    out: dict[str, float] = {}
    for field, value in candidate.items():
        key = str(field).strip()
        wl = whitelist_for(key)
        if wl is None:
            allowed = sorted(list(_RISK_FIELDS) + list(_STRATEGY_FIELDS))
            raise EvolutionGateError(
                f"参数 {key!r} 不在白名单里（只许提白名单参数：{', '.join(allowed)}）")
        _target, _name, entry = wl
        out[key] = _check_value(key, entry, value)
    return out


def _norm_diff_map(diff: Any) -> dict[str, tuple[str, str]]:
    """写入方传进来的 `param_diff` → `{field: (canon_old, canon_new)}`（去重、校验形状）。"""
    if not isinstance(diff, list):
        raise EvolutionGateError("param_diff 应是数组 [{field, old, new}]")
    out: dict[str, tuple[str, str]] = {}
    for i, entry in enumerate(diff):
        if not isinstance(entry, dict):
            raise EvolutionGateError(f"param_diff[{i}] 应是对象")
        field = str(entry.get("field") or "").strip()
        if not field:
            raise EvolutionGateError(f"param_diff[{i}].field 不能为空")
        if "old" not in entry or "new" not in entry:
            raise EvolutionGateError(f"param_diff[{i}] 必须同时给 old 与 new")
        if field in out:
            raise EvolutionGateError(f"param_diff 里字段重复：{field}")
        try:
            out[field] = (_canon_num(entry["old"]), _canon_num(entry["new"]))
        except Exception as exc:  # noqa: BLE001
            raise EvolutionGateError(f"param_diff[{i}] 的 old/new 不是数字：{exc}") from exc
    return out


def compute_direction(target: str, base: dict[str, Any], candidate: dict[str, Any],
                      changed: list[str]) -> str:
    """服务端判定方向（**不采信写入方**）。

    - `target='risk'`：走 `risk.compare()`（它已处理「负数是更紧」`_TIGHTER_WHEN_SMALLER`）。
    - `target='strategy'`：走白名单每参数自带的 `tighter_when` 语义。

    返回 `tighten` / `loosen` / `mixed`。**没有变化**由调用方先行拒绝，这里不处理空集。
    """
    if target == "risk":
        current = {f: base[f] for f in changed}
        wanted = {f: candidate[f] for f in changed}
        cmp = risk_svc.compare(current, wanted)
        return "tighten" if cmp["tightens"] else "loosen"
    dirs = set()
    for field in changed:
        wl = whitelist_for(field)
        assert wl is not None, field
        entry = wl[2]
        old = float(base[field])
        new = float(candidate[field])
        if new == old:
            continue
        tighter = (new < old) if entry["tighter_when"] == "smaller" else (new > old)
        dirs.add("tighten" if tighter else "loosen")
    if dirs == {"tighten"}:
        return "tighten"
    if dirs == {"loosen"}:
        return "loosen"
    return "mixed"


def derive_target(changed: list[str]) -> str:
    """改动字段全属同一 target 才允许提案；混类 → 拒绝（一次只改一类，diff 才可读）。"""
    targets = set()
    for field in changed:
        wl = whitelist_for(field)
        if wl is None:
            raise EvolutionGateError(f"参数 {field!r} 不在白名单里")
        targets.add(wl[0])
    if len(targets) != 1:
        raise EvolutionGateError(
            f"一次提案只能改一类参数（策略或风控），收到 {' 与 '.join(sorted(targets))} —— "
            "请拆成两条提案")
    return targets.pop()


def _hash_dt(dt: datetime) -> str:
    """事件哈希里用的时间规范形：**一律 UTC ISO**。

    为什么不用本地时区：timestamptz 读回来时的 `tzinfo` 依赖会话时区，用本地时区格式化
    会让「写入时算的哈希」与「读回时重算的哈希」在跨会话时区时对不上 —— 那是假警报。
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def event_hash(prev_hash: str, proposal_id: str, kind: str, payload: Any,
               created_at: datetime) -> str:
    """`event_hash = sha256(prev_hash | proposal_id | kind | payload 规范序 | created_at)`。"""
    raw = "\x1f".join([
        prev_hash or "",
        proposal_id,
        kind,
        json.dumps(_canonical(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        _hash_dt(created_at),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_regime_tags(regime_tags: Any) -> list[str]:
    """提案的 `regime_tags` → 去重排序列表；空 → 抛（提案必须声明依据的市场状态）。"""
    if regime_tags is None:
        raise EvolutionGateError("必须提供 regime_tags（提案依据的市场状态）")
    if isinstance(regime_tags, str):
        regime_tags = [regime_tags]
    if not isinstance(regime_tags, (list, tuple)):
        raise EvolutionGateError("regime_tags 应是字符串数组")
    out = sorted({str(t).strip() for t in regime_tags if str(t).strip()})
    if not out:
        raise EvolutionGateError("regime_tags 不能为空（提案必须声明依据的市场状态）")
    return out


def check_regime(prop_tags: list[str], evidence_tags: set[str]) -> None:
    """regime 混组规则（`R5` 交给 `R6` 的约定）+ 可用性（`is_available`）。

    - `unknown` **不得**与明确 regime 混组（提案侧、证据侧各查一次）；
    - 提案的 regime 必须**可用**（`regime.is_available`）—— 不可用 = 没有可靠数据源
      ⇒ **停止策略提案**（`R5.md` §一.2 规则 4）；
    - 证据的明确 regime 必须落在提案声明的范围内（不然证据支持不了这条提案）。
    """
    # 提案侧：每个标签都要 is_available（= 明确 regime）；unknown 一进来就不可用 → 拒。
    bad = [t for t in prop_tags if not regime_svc.is_available({"label": t})]
    if bad:
        raise EvolutionGateError(
            f"regime_tags 含不可用的市场状态 {bad}（unknown / 无可靠数据源）→ "
            "按设计停止策略提案；unknown 也不得与明确 regime 混成同组")
    # 证据侧：unknown 与明确 regime 不得混。
    definite = {t for t in evidence_tags if regime_svc.is_available({"label": t})}
    unknown = evidence_tags - definite
    if unknown and definite:
        raise EvolutionGateError(
            f"证据的市场状态把 unknown {sorted(unknown)} 与明确 regime {sorted(definite)} "
            "混在一起 → 不得同组聚合（R5 的交接口径）")
    if definite - set(prop_tags):
        raise EvolutionGateError(
            f"证据的市场状态 {sorted(definite - set(prop_tags))} 超出提案声明的 "
            f"regime_tags {prop_tags}")
    # 证据只有 unknown（definite 为空）是允许的（旧经验没有 regime 标签）——不强迫证据带 regime。


# ════════════════════════════════════════════════════════════════════════
# 四 · 计划冻结（默认值 + 规范哈希）
# ════════════════════════════════════════════════════════════════════════

def build_plan(overrides: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """`DEFAULT_PLAN`（可选按市场覆盖字段）→ 冻结计划 dict。

    `overrides` 只许覆盖 `DEFAULT_PLAN` 里已有的键（防止塞进任意列）；`plan_hash` 由
    `plan_hash()` 另算，**不接受调用方给**（写后不变的那个值必须是服务端算的）。
    """
    plan = dict(DEFAULT_PLAN)
    if overrides:
        unknown = sorted(set(overrides) - set(DEFAULT_PLAN))
        if unknown:
            raise EvolutionValidationError(
                f"plan 覆盖里有未知字段：{unknown}（只许覆盖 {sorted(DEFAULT_PLAN)}）")
        plan.update(overrides)
    return plan


def plan_hash(plan: dict[str, Any]) -> str:
    """计划的规范哈希。**写后不变** —— 改了字段，哈希就变，审计一眼看出。"""
    return _hash_json(plan)


# ════════════════════════════════════════════════════════════════════════
# 五 · 库访问
# ════════════════════════════════════════════════════════════════════════

def _new_id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:24]


def get_conn():
    """与 `memory.py` / `store.py` 同口径：psycopg2 直连 `DATABASE_URL`，无连接池。"""
    return psycopg2.connect(DATABASE_URL)


def _owned_project(cur, project_id: str, user_id: Optional[str]) -> dict:
    """取项目并校验归属（口径同 `memory._owned_project`）。跨用户 / 不存在一律 `LookupError`。"""
    cur.execute("SELECT project_id, user_id FROM fin_project WHERE project_id = %s", (project_id,))
    row = cur.fetchone()
    if not row:
        raise LookupError("项目不存在")
    if user_id is not None and str(row["user_id"]) != str(user_id):
        raise LookupError("项目不存在")
    return dict(row)


# `fin_param` 行里**构成本配置**的那些列（供 `config_from_row` / `read_config` /
# `control.activate_candidate` 共用一份列清单 —— 三处各写一遍必然漂）。
CONFIG_COLUMNS = (
    "max_position_pct", "daily_loss_halt_pct", "account_drawdown_halt_pct",
    "stop_loss_pct", "take_profit_pct", "hold_days_max", "strategies",
)


def config_from_row(row: Any) -> dict[str, Any]:
    """**纯函数**：`fin_param` 行（dict）→ 可调配置（白名单字段 → 数值）。

    配置 = `fin_param` 里**白名单覆盖到的**那些字段：三个风控字段 + 顶层策略字段
    （`stop_loss_pct` / `take_profit_pct` / `hold_days_max`）+ 各 `strategies[].params` 里
    白名单命中的参数（键 `strategies.<key>.params.<name>`）。

    ⚠️ **只有这一份实现**。`read_config` 与 `control.activate_candidate`（CAS 要同一口径的
    哈希）都调它 —— 两处各写一遍会让「提案算的哈希」与「生效时算的哈希」在字段口径上悄悄
    分叉，表现是**永远 CAS 失败**且不报原因。
    """
    row = dict(row or {})
    cfg: dict[str, Any] = {}
    for field in (*_RISK_FIELDS, *_STRATEGY_FIELDS):
        value = row.get(field)
        if value is not None:
            cfg[field] = _json_num(value)
    for strat in (row.get("strategies") or []):
        if not isinstance(strat, dict):
            continue
        key = str(strat.get("key") or "").strip()
        params = strat.get("params") or {}
        if not key or not isinstance(params, dict):
            continue
        for name, value in params.items():
            if name in _STRATEGY_FIELDS and value is not None:
                cfg[f"strategies.{key}.params.{name}"] = _json_num(value)
    return cfg


def read_config(cur, project_id: str) -> dict[str, Any]:
    """读项目的**可调配置**（白名单字段 → 数值）的规范快照。见 `config_from_row`。"""
    cur.execute(
        "SELECT " + ", ".join(CONFIG_COLUMNS) + " FROM fin_param WHERE project_id = %s",
        (project_id,),
    )
    row = cur.fetchone()
    if not row:
        raise LookupError("项目参数不存在（fin_param）")
    return config_from_row(row)


def _load_evidence(project_id: str, evidence_refs: list[str],
                   evidence_snapshot_id: Optional[str]) -> set[str]:
    """校验证据（`plan/R6.md` §一.3）→ 返回证据里出现过的 regime 标签集合。

    四条硬性：≥1 条 / 来自**冻结快照** / 同项目 / 非 holdout / 时间边界。
    **失败经验（`polarity='refute'`）同样能作为证据** —— 这里不按 polarity 区分，
    只按「在不在那份冻结集合里」判（refute 条目本来就在集合里）。
    """
    if not isinstance(evidence_refs, (list, tuple)) or not evidence_refs:
        raise EvolutionGateError("evidence_refs 至少一条 —— 提案不许无据")
    refs = [str(r).strip() for r in evidence_refs if str(r).strip()]
    if len(refs) != len(set(refs)):
        raise EvolutionGateError("evidence_refs 有重复条目")
    if not evidence_snapshot_id:
        raise EvolutionGateError(
            "必须提供 evidence_snapshot_id —— 证据必须来自**冻结快照**，"
            "不能是「此刻查得到的那批」（防未来函数）")

    try:
        snap = memory_svc.get_snapshot(memory_snapshot_id=str(evidence_snapshot_id))
    except LookupError as exc:
        raise EvolutionGateError(f"证据快照不存在：{evidence_snapshot_id}") from exc

    if str(snap.get("project_id")) != str(project_id):
        raise EvolutionGateError("证据快照不属于本项目（同项目边界）")
    if str(snap.get("purpose")) == "holdout":
        raise EvolutionGateError("holdout 快照不能作为提案证据（防评估泄露）")

    frozen = set(snap.get("experience_ids") or [])
    missing = [r for r in refs if r not in frozen]
    if missing:
        raise EvolutionGateError(
            f"这些证据不在冻结快照 {evidence_snapshot_id} 里：{missing}"
            "（证据只能来自同一份冻结集合）")

    items = {it["experience_id"]: it for it in (snap.get("items") or [])}
    basis = (snap.get("query_filter") or {}).get("as_of_basis")
    tags: set[str] = set()
    for ref in refs:
        item = items.get(ref)
        if item is None:
            raise EvolutionGateError(f"证据 {ref} 在快照里查不到内容，无法校验（同时间边界）")
        as_of = item.get("as_of")
        if basis and as_of and str(as_of) > str(basis):
            raise EvolutionGateError(
                f"证据 {ref} 的形成时间 {as_of} 晚于快照基准 {basis}（同时间边界）")
        for tag in (item.get("regime_tags") or []):
            if str(tag).strip():
                tags.add(str(tag).strip())
    return tags


def _append_event(cur, proposal_id: str, kind: str, payload: Any, *, actor: str,
                  update_status: bool = True) -> dict:
    """追加一条状态事件（哈希链），并把 `proposal.status` 更新成它的投影。

    `prev_hash` = 同一提案上一条事件的 `event_hash`（首条为 `''`）。
    """
    if kind not in EVENT_KINDS:
        raise EvolutionValidationError(f"未知事件类型：{kind!r}")
    cur.execute(
        "SELECT event_hash FROM fin_evolution_event WHERE proposal_id = %s "
        "ORDER BY created_at DESC, event_id DESC LIMIT 1",
        (proposal_id,),
    )
    row = cur.fetchone()
    prev = str(row["event_hash"]) if row else ""
    created_at = datetime.now(timezone.utc)
    eh = event_hash(prev, proposal_id, kind, payload, created_at)
    event_id = _new_id("eve_")
    cur.execute(
        """
        INSERT INTO fin_evolution_event
          (event_id, proposal_id, kind, payload, prev_hash, event_hash, actor, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (event_id, proposal_id, kind, psycopg2.extras.Json(_jsonable(payload)),
         prev, eh, actor, created_at),
    )
    if update_status and kind in STATUS_OF_KIND:
        cur.execute("UPDATE fin_evolution_proposal SET status = %s WHERE proposal_id = %s",
                    (STATUS_OF_KIND[kind], proposal_id))
    return {"event_id": event_id, "kind": kind, "prev_hash": prev, "event_hash": eh,
            "created_at": created_at.isoformat()}


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    return value


def _shadow_json(value: Any) -> Any:
    """影子快照 / 信号的 JSON 编码：**金额留下字符串**（不转 float）。

    与 `_jsonable` 的差别就在这里 —— `_jsonable` 把 `Decimal` 转成 `float`（供事件 payload
    这类只需人读的地方）；影子快照要**逐位可复算**（验收项「估值可复算」），
    转 float 会引入二进制舍入，所以金额一律 `str(Decimal)` 原样存。
    """
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return [_shadow_json(v) for v in value]
    if isinstance(value, dict):
        return {k: _shadow_json(v) for k, v in value.items()}
    return value


# ════════════════════════════════════════════════════════════════════════
# 六 · 唯一写入口：propose（提案 + 冻结计划 + 首条事件，**同一事务**）
# ════════════════════════════════════════════════════════════════════════

def _propose_inner(cur, *, pid: str, project_id: str, evidence_refs: list[str],
                   evidence_snapshot_id: Optional[str], candidate_config: Any,
                   param_diff: Any, target: Optional[str], regime_tags: Any,
                   rationale: Any, created_by: str, plan_overrides: Optional[dict],
                   user_id: Optional[str]) -> dict:
    """校验 + 落库（在调用方的事务里）。任何闸门拒绝抛 `EvolutionGateError`。"""
    _owned_project(cur, project_id, user_id)

    rationale = str(rationale or "").strip()
    if not rationale:
        raise EvolutionGateError("rationale 不能为空（人可读理由）")

    # ① 证据（经 memory 的冻结快照；本模块不碰经验表）
    evidence_tags = _load_evidence(project_id, evidence_refs, evidence_snapshot_id)
    prop_tags = normalize_regime_tags(regime_tags)
    check_regime(prop_tags, evidence_tags)

    # ② 基线配置（服务端读）
    base = read_config(cur, project_id)
    if not base:
        raise EvolutionGateError("该项目没有可调配置（fin_param 里白名单字段全空）")
    base_hash = hash_config(base)

    # ③ 候选配置（白名单 / 类型 / 范围 / 字段集）
    cand = normalize_candidate(candidate_config)
    extra = sorted(set(cand) - set(base))
    missing = sorted(set(base) - set(cand))
    if extra or missing:
        raise EvolutionGateError(
            "candidate_config 必须是**完整配置**（字段集与基线一致）："
            f"多出 {extra or '无'}、缺少 {missing or '无'}")

    # ④ 单次最大变化 + 服务端机器算 diff
    changed = [f for f in sorted(base) if _canon_num(base[f]) != _canon_num(cand[f])]
    if not changed:
        raise EvolutionGateError("提案没有任何参数变化（diff 为空）")
    for field in changed:
        wl = whitelist_for(field)
        assert wl is not None, field
        _check_step(field, wl[2], base[field], cand[field])

    computed = config_diff(base, cand)
    computed_map = {e["field"]: (_canon_num(e["old"]), _canon_num(e["new"])) for e in computed}
    claimed_map = _norm_diff_map(param_diff)
    if computed_map != claimed_map:
        raise EvolutionGateError(
            "param_diff 与服务端机器计算结果不一致（疑似篡改）："
            f"服务端算出 {json.dumps(computed, ensure_ascii=False)}，"
            f"写入方给了 {json.dumps(_jsonable(param_diff), ensure_ascii=False)}")

    # ⑤ target（服务端算，与写入方声明比对）+ 方向（服务端算）
    derived_target = derive_target(changed)
    if target is not None and str(target).strip() and str(target).strip() != derived_target:
        raise EvolutionGateError(
            f"target 声明为 {target!r}，但改动字段属于 {derived_target!r}（服务端判定）")
    direction = compute_direction(derived_target, base, cand, changed)

    # ⑥ 红线 8：AI 无权放宽风控。服务端 + 数据库 CHECK 双保险，这里先拦并留痕。
    if derived_target == "risk" and direction == "loosen":
        raise EvolutionGateError(
            "target='risk' 的提案方向是**放宽**（loosen）—— AI 无权放宽风控，"
            "一律拒绝且**不入库**；放宽风控只能由真人走 apply_risk_tier(confirm=True)")

    candidate_hash = hash_config(cand)

    # ⑦ 落库：提案 + 冻结计划 + 首条事件
    cur.execute(
        """
        INSERT INTO fin_evolution_proposal
          (proposal_id, project_id, evidence_refs, evidence_snapshot_id,
           base_config_hash, candidate_config_hash, param_diff,
           regime_tags, rule_version, proposal_algo_version,
           target, direction, status, rationale, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'draft', %s, %s)
        """,
        (pid, project_id, list(evidence_refs), evidence_snapshot_id,
         base_hash, candidate_hash, psycopg2.extras.Json(computed),
         prop_tags, regime_svc.RULE_VERSION, ALGO_VERSION,
         derived_target, direction, rationale, created_by),
    )

    plan = build_plan(plan_overrides)
    p_hash = plan_hash(plan)
    plan_id = _new_id("evpl_")
    cur.execute(
        """
        INSERT INTO fin_evolution_plan
          (plan_id, proposal_id, metric, window_days, min_comparable_sample,
           cost_model, slippage_model, data_source_version,
           pass_line, fail_line, early_stop_condition, rollback_line, plan_hash)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (plan_id, pid, plan["metric"], plan["window_days"], plan["min_comparable_sample"],
         plan["cost_model"], plan["slippage_model"], plan["data_source_version"],
         plan["pass_line"], plan["fail_line"],
         psycopg2.extras.Json(_jsonable(plan["early_stop_condition"])),
         psycopg2.extras.Json(_jsonable(plan["rollback_line"])),
         p_hash),
    )
    _append_event(
        cur, pid, "created",
        {"target": derived_target, "direction": direction,
         "base_config_hash": base_hash, "candidate_config_hash": candidate_hash,
         "param_diff": computed, "evidence_refs": list(evidence_refs),
         "evidence_snapshot_id": evidence_snapshot_id,
         "regime_tags": prop_tags, "plan_hash": p_hash},
        actor=created_by,
    )
    return {"proposal_id": pid, "plan_id": plan_id, "plan_hash": p_hash,
            "base_config_hash": base_hash, "candidate_config_hash": candidate_hash,
            "target": derived_target, "direction": direction, "param_diff": computed}


def propose(*, project_id: str, evidence_refs: Any, evidence_snapshot_id: Any,
            candidate_config: Any, param_diff: Any, target: Any, regime_tags: Any,
            rationale: Any, created_by: str = "fin-worker",
            proposal_id: Optional[str] = None, plan_overrides: Optional[dict] = None,
            user_id: Optional[str] = None, conn=None) -> dict:
    """**唯一写入口**：提交一条提案（提案 + 冻结计划 + 首条事件，**同一事务**）。

    - 闸门拒绝（白名单 / 证据 / 篡改 / 红线 8 / regime）→ 先追加 `rejected_by_gate` 事件
      再抛 `EvolutionGateError`（路由 → 400）。**拒绝的事件独立提交**，因为它记的是
      「谁试过」——那份提案本身不入库（红线 8），外键在这里必须让路（见 `0043` 表四注释）。
    - 同一项目同一 base 已有待验证提案 → `EvolutionConflictError`（路由 → 409）。
    - `plan_overrides`：按市场 / 需求覆盖 `DEFAULT_PLAN` 的**已有字段**（本轮默认不覆盖）。
    """
    # 先归一化 `project_id`，**再**判闸门 —— 否则带前后空白的 project_id 会以原样当缓存键
    # 与查询键，读不到覆盖行 → 悄悄退回「只看天花板」，这个项目在界面上关掉了也照样提案。
    project_id = str(project_id or "").strip()
    if not project_id:
        raise EvolutionValidationError("project_id 不能为空")
    # R21：判据按**项目**取（天花板 ∩ 天窗）。关掉一个项目的学习强度 = 不再为它提案；
    # 其余项目照常。生效 / 回滚（下面两个函数）仍只看**天花板** —— 那是「人工确认」的动作，
    # 一条已经在验证中的提案不该因为事后把学习强度调低而被作废。
    if not switches.evolution_enabled(project_id):
        raise EvolutionDisabledError(
            "进化未启用（本部署的 FIN_EVOLUTION_MODE=off，或这个项目的学习强度为「关闭」）："
            "本部署当前不接受这个项目的提案")

    created_by = str(created_by or "").strip() or "fin-worker"
    pid = str(proposal_id or "").strip() or _new_id("evp_")

    own = conn is None
    conn = conn or get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            try:
                result = _propose_inner(
                    cur, pid=pid, project_id=project_id, evidence_refs=evidence_refs,
                    evidence_snapshot_id=evidence_snapshot_id, candidate_config=candidate_config,
                    param_diff=param_diff, target=target, regime_tags=regime_tags,
                    rationale=rationale, created_by=created_by,
                    plan_overrides=plan_overrides, user_id=user_id)
            except EvolutionGateError as exc:
                # 闸门拒绝：**独立提交**一条 rejected_by_gate 事件（谁试过必须留痕），
                # 再往上抛 400。提案本身不入库。
                _append_event(cur, pid, "rejected_by_gate",
                              {"reason": str(exc), "project_id": project_id,
                               "target": str(target or ""), "stage": "gate"},
                              actor=created_by, update_status=True)
                conn.commit()
                logger.info("[fin.evolution] 闸门拒绝 pid={} project={} reason={}",
                            pid, project_id, str(exc))
                raise
        conn.commit()
    except psycopg2.errors.UniqueViolation as exc:
        conn.rollback()
        raise EvolutionConflictError(
            "同一项目同一基线下已有一个待验证提案（唯一索引 "
            "fin_evolution_proposal_one_pending）—— 先处理完它或换一个 base") from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        if own:
            conn.close()

    logger.info("[fin.evolution] 提案 {} 落库 project={} target={} direction={}",
                pid, project_id, result["target"], result["direction"])
    return result


# ════════════════════════════════════════════════════════════════════════
# 七 · 读路径（R9 界面用）+ 审计工具
# ════════════════════════════════════════════════════════════════════════

def list_proposals(*, project_id: str, user_id: Optional[str] = None,
                   limit: int = 50, conn=None) -> list[dict]:
    """列出一个项目下的提案（含冻结计划）。JWT 通道按项目归属过滤（跨用户 → `LookupError`）。"""
    own = conn is None
    conn = conn or get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            _owned_project(cur, project_id, user_id)
            cur.execute(
                "SELECT * FROM fin_evolution_proposal WHERE project_id = %s "
                "ORDER BY created_at DESC, proposal_id DESC LIMIT %s",
                (project_id, max(1, int(limit))),
            )
            proposals = [dict(r) for r in cur.fetchall()]
            ids = [p["proposal_id"] for p in proposals]
            plans: dict[str, dict] = {}
            if ids:
                cur.execute(
                    "SELECT * FROM fin_evolution_plan WHERE proposal_id = ANY(%s)", (ids,))
                plans = {r["proposal_id"]: dict(r) for r in cur.fetchall()}
            # R8 · 「回灌待完成」状态位（R9 界面要显示它）—— 有 pending 回灌任务即为真。
            pending_reinject: set[str] = set()
            if ids:
                cur.execute(
                    "SELECT DISTINCT proposal_id FROM fin_evolution_reinject_task "
                    " WHERE proposal_id = ANY(%s) AND status = 'pending'", (ids,))
                pending_reinject = {r["proposal_id"] for r in cur.fetchall()}
        conn.rollback()      # 只读
        out = []
        for p in proposals:
            item = _jsonable(p)
            item["plan"] = _jsonable(plans.get(p["proposal_id"]))
            item["reinject_pending"] = p["proposal_id"] in pending_reinject
            out.append(item)
        return out
    finally:
        if own:
            conn.close()


def get_proposal(*, proposal_id: str, conn=None) -> dict:
    """按 id 取一条提案（含计划）。不存在 → `LookupError`。"""
    own = conn is None
    conn = conn or get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM fin_evolution_proposal WHERE proposal_id = %s",
                        (proposal_id,))
            row = cur.fetchone()
            if not row:
                raise LookupError("提案不存在")
            cur.execute("SELECT * FROM fin_evolution_plan WHERE proposal_id = %s", (proposal_id,))
            plan = cur.fetchone()
            cur.execute("SELECT count(*) AS n FROM fin_evolution_reinject_task "
                        " WHERE proposal_id = %s AND status = 'pending'", (proposal_id,))
            pending = int((cur.fetchone() or {}).get("n") or 0)
        conn.rollback()
        item = _jsonable(dict(row))
        item["plan"] = _jsonable(dict(plan)) if plan else None
        item["reinject_pending"] = pending > 0
        return item
    finally:
        if own:
            conn.close()


def list_events(*, project_id: str, user_id: Optional[str] = None, limit: int = 200,
                conn=None) -> list[dict]:
    """按时间**倒序**列出某项目的状态事件链（R9 界面用）。**只读，不改任何状态。**

    归属有两种来源，缺一不可：

    1. `proposal_id` 指向本项目的提案（LEFT JOIN `fin_evolution_proposal`）；
    2. **闸门拒绝的事件**（`rejected_by_gate`）—— 红线 8 / 白名单 / 证据不合规拒绝的提案
       **从来没入库**（`fin_evolution_event.proposal_id` 故意不加外键，就是为了能记下
       「从未存在的提案」）。这些事件的 `payload.project_id` 是唯一的归属线索。

    所以过滤条件是这两者取或 —— 只按 JOIN 查会**漏掉所有被闸门拦下的拒绝事件**，
    而那正是「被拒的提案为什么被拒」要回答的东西。
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            _owned_project(cur, project_id, user_id)
            cur.execute(
                """
                SELECT e.*
                  FROM fin_evolution_event e
                  LEFT JOIN fin_evolution_proposal p ON p.proposal_id = e.proposal_id
                 WHERE p.project_id = %s OR (e.payload->>'project_id') = %s
                 ORDER BY e.created_at DESC, e.event_id DESC
                 LIMIT %s
                """,
                (project_id, project_id, max(1, min(int(limit), 1000))),
            )
            rows = [dict(r) for r in cur.fetchall()]
        conn.rollback()      # 只读
    finally:
        if own:
            conn.close()
    return [_jsonable(r) for r in rows]


def verify_chain(proposal_id: str, *, conn=None) -> dict:
    """逐条重算事件哈希，验证哈希链完整。返回 `{ok, count, broken}`。

    被判为断链的两种情况：`prev_hash` 接不上上一条、`event_hash` 与内容（含 `created_at`）
    对不上。**篡改一条事件的 `payload`（哪怕绕过触发器）都会让这里报错** —— 这是触发器
    之外的第二层审计（因为库属主能 `DISABLE TRIGGER`，见 `0043` 的能力边界注释）。
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM fin_evolution_event WHERE proposal_id = %s "
                "ORDER BY created_at ASC, event_id ASC", (proposal_id,))
            rows = [dict(r) for r in cur.fetchall()]
        conn.rollback()
    finally:
        if own:
            conn.close()

    prev = ""
    broken: list[dict] = []
    for r in rows:
        expected = event_hash(r["prev_hash"], r["proposal_id"], r["kind"],
                              r["payload"], r["created_at"])
        if str(r["prev_hash"]) != prev:
            broken.append({"event_id": r["event_id"], "why": "prev_hash 接不上上一条"})
        if str(r["event_hash"]) != expected:
            broken.append({"event_id": r["event_id"], "why": "event_hash 与内容不符（内容被改过）"})
        prev = str(r["event_hash"])
    return {"proposal_id": proposal_id, "count": len(rows), "ok": not broken, "broken": broken}


def projection_mismatches(*, conn=None) -> list[dict]:
    """`proposal.status` 与「最后一条事件的投影」不一致的行。

    验收项：**应为 0 行**。事件表是审计权威，`status` 只是可重建投影 ——
    一旦两处打架，说明有人绕过了「改 status 必须同时追加事件」这条约定。
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT p.proposal_id, p.status,
                       (SELECT e.kind FROM fin_evolution_event e
                         WHERE e.proposal_id = p.proposal_id
                         ORDER BY e.created_at DESC, e.event_id DESC LIMIT 1) AS last_kind
                  FROM fin_evolution_proposal p
                """,
            )
            rows = [dict(r) for r in cur.fetchall()]
        conn.rollback()
    finally:
        if own:
            conn.close()

    out: list[dict] = []
    for r in rows:
        kind = r["last_kind"]
        if kind is None:
            out.append({"proposal_id": r["proposal_id"], "status": r["status"],
                        "last_kind": None, "reason": "没有任何事件（审计缺失）"})
            continue
        expected = STATUS_OF_KIND.get(kind)
        if expected != r["status"]:
            out.append({"proposal_id": r["proposal_id"], "status": r["status"],
                        "last_kind": kind, "expected": expected, "reason": "投影与事件不一致"})
    return out


def append_event(*, proposal_id: str, kind: str, payload: Any = None,
                 actor: str = "system", conn=None) -> dict:
    """追加一条状态事件（R7 / R8 的 validating / passed / applied / rolled_back 走这里）。

    同一事务里同时更新 `proposal.status` 投影（改 status 必须同时追加事件）。
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT 1 FROM fin_evolution_proposal WHERE proposal_id = %s",
                        (proposal_id,))
            if cur.fetchone() is None:
                raise LookupError("提案不存在")
            row = _append_event(cur, proposal_id, kind, payload or {}, actor=actor)
        conn.commit()
        return row
    except Exception:
        conn.rollback()
        raise
    finally:
        if own:
            conn.close()


# ════════════════════════════════════════════════════════════════════════
# 八 · 影子层（R7）：候选臂独立模拟记账 + 两臂同条件验证
# ════════════════════════════════════════════════════════════════════════
#
# 口径见模块头（二）。本段的函数只碰 `fin_evolution_shadow_event` 与 `fin_evolution_event`
# 两张表（`test_fin_evolution_guard.py` 盯着），**绝不碰** `fin_trade` / `fin_order` / `fin_param`。
#
# `validation_id` = `proposal_id`：一条提案恰好一次验证。R6 建阴影表时**不设外键**
# （见 0043 表三注释），本段沿用 —— 验证是围绕一条提案的，不是一条独立实体。

SHADOW_ARMS = ("incumbent", "candidate")

_SIGNAL_KEYS = ("side", "qty", "price_type", "limit_price", "fill", "realized_pnl", "note")


# ── 影子状态（持仓 / 估值快照）─────────────────────────────────────────────
#
# **不新建账本表**：一个臂的「现金 + 持仓」就存在它最近一条影子事件的
# `position_ref`（jsonb）里；下一个时点从这个快照接着算。这就是「由事件逐笔重建」
# 的落点 —— 每一行本来就带着**它那一步之后的完整快照**，所以重建 = 取最近一条 ≤ 当日的行。
# 这与「从头逐笔重放」逐位等价，而且**可复算**（估值口径见 `valuation_ref`）。

def _shadow_initial_state(initial_capital: Any) -> dict[str, Any]:
    """两臂**共同的**初始状态（红线 11：同初始现金 / 仓位）。

    初始仓位 = 空（`positions: []`），初始可用现金 = 项目本金。
    """
    cap = _json_num(initial_capital)
    return {"cash_available": cap, "cash_frozen": 0, "positions": []}


def _shadow_apply_diff(base: dict[str, Any], param_diff: Any) -> dict[str, Any]:
    """把**服务端算出的** `param_diff` 应用到 base 配置 → 候选配置（完整）。

    `param_diff` 曾是提案表里落库的 `[{field, old, new}]`（服务端机器算，见 `config_diff`）。
    这里只把 `new` 覆盖回去 —— 候选配置必须与 base **字段集一致**（提案已保证）。
    """
    cand = dict(base)
    for entry in (param_diff or []):
        field = str(entry.get("field") or "")
        if field in cand:
            cand[field] = entry.get("new")
    return cand


def _shadow_base_config(cur, project_id: str) -> dict[str, Any]:
    return read_config(cur, project_id)


def shadow_prepare(*, proposal_id: str, market: Optional[str] = None,
                   conn=None) -> dict[str, Any]:
    """影子一步的**准备数据**（只读）：提案 / 计划 / 两臂配置 / 两臂当前状态 / 初始资金 / 市场。

    - `base_config` / `candidate_config`：两臂各自的**完整配置**（candidate = base + diff）；
    - `states`：两臂各自的当前持仓 / 现金（从**最近一条影子事件**重建）；
      **同初始现金**由 `initial_capital` 保证（t=0 时两臂状态相同 → 满足红线 11）；
    - `initial_capital` / `market` / `currency`。
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            prop = get_proposal_inner(cur, proposal_id)
            project_id = prop["project_id"]
            base = _shadow_base_config(cur, project_id)
            cand = _shadow_apply_diff(base, prop.get("param_diff"))
            cur.execute(
                "SELECT initial_capital, currency, market_scope FROM fin_project "
                " WHERE project_id = %s", (project_id,))
            prow = cur.fetchone() or {}
            market_v = market or prow.get("market_scope") or "CN_A"
            cur.execute("SELECT market, currency, initial_capital FROM fin_project_market "
                        " WHERE project_id = %s AND market = %s", (project_id, market_v))
            mrow = cur.fetchone() or {}
            initial_capital = mrow.get("initial_capital") or prow.get("initial_capital")
            currency = mrow.get("currency") or prow.get("currency")
            states = {}
            for arm in SHADOW_ARMS:
                states[arm] = _shadow_latest_state(cur, proposal_id, arm, initial_capital)
        conn.rollback()
    finally:
        if own:
            conn.close()
    return {
        "validation_id": proposal_id,
        "proposal_id": proposal_id,
        "project_id": project_id,
        "market": market_v,
        "currency": currency,
        "initial_capital": _json_num(initial_capital),
        "target": prop.get("target"),
        "direction": prop.get("direction"),
        "status": prop.get("status"),
        "base_config": base,
        "candidate_config": cand,
        "param_diff": _jsonable(prop.get("param_diff")),
        "plan": _jsonable(prop.get("plan")),
        "states": states,
    }


def get_proposal_inner(cur, proposal_id: str) -> dict[str, Any]:
    """在**已有游标**上取提案 + 计划（`shadow_prepare` / `evaluate_validation` 共用）。

    与 `get_proposal()` 同口径，只是不自己开连接（那两个函数本来就在事务里）。
    """
    cur.execute("SELECT * FROM fin_evolution_proposal WHERE proposal_id = %s", (proposal_id,))
    row = cur.fetchone()
    if not row:
        raise LookupError("提案不存在")
    item = dict(row)
    cur.execute("SELECT * FROM fin_evolution_plan WHERE proposal_id = %s", (proposal_id,))
    plan = cur.fetchone()
    item["plan"] = dict(plan) if plan else None
    return item


def _shadow_latest_state(cur, validation_id: str, arm: str, initial_capital) -> dict[str, Any]:
    """该臂**最近一条**影子事件的持仓快照（≤ 当前），没有则返回初始状态。"""
    cur.execute(
        "SELECT position_ref FROM fin_evolution_shadow_event "
        " WHERE validation_id = %s AND arm = %s "
        " ORDER BY trade_date DESC, point DESC, created_at DESC LIMIT 1",
        (validation_id, arm),
    )
    row = cur.fetchone()
    ref = (row or {}).get("position_ref")
    if ref:
        return dict(ref)
    return _shadow_initial_state(initial_capital)


# ── 影子事件：写入（幂等）/ 读取 ───────────────────────────────────────────

def record_shadow_events(*, records: list[dict[str, Any]], conn=None) -> dict[str, Any]:
    """把两臂的影子里程碑**追加**进 `fin_evolution_shadow_event`。**幂等**。

    每个 record 带业务身份 `(validation_id, arm, trade_date, point, symbol)` —— 唯一键撞上
    就 `ON CONFLICT DO NOTHING`，所以**重试 / 重启 / 补跑不会重复记账**（`03 §6` 必测场景）。

    返回 `{written, skipped}`（本次真正插入的条数与撞键跳过的条数）。
    """
    if not records:
        return {"written": 0, "skipped": 0}
    written = 0
    own = conn is None
    conn = conn or get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            for r in records:
                arm = str(r.get("arm") or "")
                if arm not in SHADOW_ARMS:
                    raise EvolutionValidationError(f"未知的臂：{arm!r}（只认 {SHADOW_ARMS}）")
                sig = r.get("signal")
                signal_text = (json.dumps(_shadow_json(sig), ensure_ascii=False)
                               if sig is not None else None)
                cur.execute(
                    """
                    INSERT INTO fin_evolution_shadow_event
                      (shadow_event_id, validation_id, arm, trade_date, point, symbol,
                       quote_as_of, signal, filled, reject_reason, fee, slippage,
                       position_ref, valuation_ref)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (validation_id, arm, trade_date, point, symbol) DO NOTHING
                    """,
                    (
                        _new_id("evsh_"), r["validation_id"], arm, r["trade_date"], r["point"],
                        r["symbol"], r.get("quote_as_of"), signal_text,
                        None if r.get("filled") is None else bool(r.get("filled")),
                        r.get("reject_reason"),
                        None if r.get("fee") is None else _money4(r.get("fee")),
                        None if r.get("slippage") is None else _money4(r.get("slippage")),
                        psycopg2.extras.Json(_shadow_json(r.get("position"))),
                        psycopg2.extras.Json(_shadow_json(r.get("valuation"))),
                    ),
                )
                written += 1 if cur.rowcount == 1 else 0
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if own:
            conn.close()
    return {"written": written, "skipped": len(records) - written}


def _money4(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.0001"))


def shadow_events(*, validation_id: str, arm: Optional[str] = None,
                  limit: int = 2000, conn=None) -> list[dict[str, Any]]:
    """读某次验证的影子事件（可按臂过滤），按时间顺序。R9 界面 / 指标都用它。"""
    clause, params = "", []
    if arm:
        clause, params = " AND arm = %s", [arm]
    own = conn is None
    conn = conn or get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM fin_evolution_shadow_event WHERE validation_id = %s" + clause +
                " ORDER BY trade_date ASC, point ASC, arm ASC LIMIT %s",
                (validation_id, *params, max(1, int(limit))),
            )
            rows = [dict(r) for r in cur.fetchall()]
        conn.rollback()
    finally:
        if own:
            conn.close()
    return [_jsonable(r) for r in rows]


def count_shadow_events(*, validation_id: Optional[str] = None, conn=None) -> int:
    """影子事件行数（验收用：幂等重复跑 → 行数不变）。"""
    own = conn is None
    conn = conn or get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if validation_id:
                cur.execute("SELECT count(*) AS n FROM fin_evolution_shadow_event "
                            " WHERE validation_id = %s", (validation_id,))
            else:
                cur.execute("SELECT count(*) AS n FROM fin_evolution_shadow_event")
            n = int(cur.fetchone()["n"])
        conn.rollback()
    finally:
        if own:
            conn.close()
    return n


# ── 指标（组合口径 + 辅助诊断）─────────────────────────────────────────────
#
# `03 §4-C`：**优先用同资本基准的组合口径** —— 组合净收益 / 最大回撤 / 换手 / 交易成本。
# `realized_net_pnl_per_trade` **只作辅助诊断**（持仓周期与频率会让它失真），
# 结果里带 `auxiliary: true` 明示。

def _arm_equity(rows: list[dict]) -> list[tuple[str, str, Decimal]]:
    """一臂的净值曲线 `[(trade_date, point, total_assets)]`（有估值的行才算）。"""
    pts: list[tuple[str, str, Decimal]] = []
    for r in rows:
        v = r.get("valuation_ref") or {}
        if v.get("total_assets") is None:
            continue
        pts.append((str(r["trade_date"]), str(r["point"]), Decimal(str(v["total_assets"]))))
    return pts


def _max_drawdown(pts: list[tuple[str, str, Decimal]]) -> Optional[Decimal]:
    """最大回撤（负比例，例如 `-0.05`）。没有两个点 → `None`（算不出就不编）。"""
    if len(pts) < 2:
        return None
    peak = pts[0][2]
    worst = Decimal("0")
    for _d, _p, total in pts:
        if total > peak:
            peak = total
        if peak > 0:
            dd = (total - peak) / peak
            if dd < worst:
                worst = dd
    return worst


def _arm_metrics(rows: list[dict], initial_capital: Decimal) -> dict[str, Any]:
    """一臂的组合口径指标 + 辅助诊断。"""
    pts = _arm_equity(rows)
    fills = [r for r in rows if r.get("filled")]
    turnover = Decimal("0")
    cost = Decimal("0")
    realized = Decimal("0")
    n_realized = 0
    for r in fills:
        sig = _parse_signal(r.get("signal"))
        fill = (sig or {}).get("fill") or {}
        amount = fill.get("amount")
        if amount is not None:
            turnover += Decimal(str(amount))
        if r.get("fee") is not None:
            cost += Decimal(str(r["fee"]))
        rp = (sig or {}).get("realized_pnl")
        if rp is not None:
            realized += Decimal(str(rp))
            n_realized += 1

    last_total = pts[-1][2] if pts else initial_capital
    net_return = ((last_total - initial_capital) / initial_capital
                  if initial_capital and initial_capital > 0 else None)
    return {
        "points": len(pts),
        "trades": len(fills),
        "final_total_assets": _json_num(last_total),
        "portfolio_net_return": None if net_return is None else float(net_return),
        "max_drawdown": (lambda dd: None if dd is None else float(dd))(_max_drawdown(pts)),
        "turnover": None if initial_capital <= 0 else float(turnover / initial_capital),
        "transaction_cost": float(cost),
        "realized_net_pnl_per_trade": {
            "value": None if n_realized == 0 else float(realized / n_realized),
            "n": n_realized,
            "auxiliary": True,
            "why": "持仓周期与交易频率会让单笔口径失真，只作辅助诊断（03 §4-C）；无已实现卖出时为 null",
        },
    }


def _parse_signal(signal: Any) -> Optional[dict]:
    if signal is None:
        return None
    if isinstance(signal, dict):
        return signal
    try:
        return json.loads(str(signal))
    except (ValueError, TypeError):
        return None


def _comparable_sample_keys(rows: list[dict]) -> set[tuple[str, str, str]]:
    """**可比样本**的键集合：`(trade_date, point, symbol)`，要求**两臂都出了决策**。

    红线 11：样本以「两臂均有可比机会的交易日 / 事件」计。判据是**两臂各自的数据行都存在、
    且都带非空 `signal`**（signal = 该臂在该点真的出了决策）。只有一臂出决策的那些点
    **不计入样本** —— 所以「候选有交易、现行没交易」的笔数不会被当成样本直接相减。
    """
    saw: dict[tuple[str, str, str], set[str]] = {}
    for r in rows:
        if _parse_signal(r.get("signal")) is None:
            continue
        key = (str(r["trade_date"]), str(r["point"]), str(r["symbol"]))
        saw.setdefault(key, set()).add(str(r["arm"]))
    return {k for k, arms in saw.items() if arms >= set(SHADOW_ARMS)}


def _loss_tail(pts: list[tuple[str, str, Decimal]], n: int = 5) -> list[dict[str, Any]]:
    """亏损尾部：单步净值变化最差的若干步（真算出来的数，不编）。"""
    deltas: list[tuple[str, str, Decimal]] = []
    for i in range(1, len(pts)):
        deltas.append((pts[i][0], pts[i][1], pts[i][2] - pts[i - 1][2]))
    deltas.sort(key=lambda t: t[2])
    return [{"trade_date": d, "point": p, "delta": float(v)} for d, p, v in deltas[:n]]


def shadow_metrics(*, validation_id: str, market: Optional[str] = None,
                   initial_capital: Optional[Any] = None, conn=None) -> dict[str, Any]:
    """两臂的组合口径指标 + 可比样本 + 亏损尾部 + 分 regime。

    `initial_capital` 不给就现查 `fin_project`。
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if initial_capital is None:
                cur.execute(
                    "SELECT p.initial_capital FROM fin_evolution_proposal e"
                    " JOIN fin_project p ON p.project_id = e.project_id"
                    " WHERE e.proposal_id = %s", (validation_id,))
                row = cur.fetchone()
                initial_capital = (row or {}).get("initial_capital") or 0
            cur.execute(
                "SELECT * FROM fin_evolution_shadow_event WHERE validation_id = %s"
                " ORDER BY trade_date ASC, point ASC, arm ASC", (validation_id,))
            rows = [dict(r) for r in cur.fetchall()]
        conn.rollback()
    finally:
        if own:
            conn.close()

    cap = Decimal(str(initial_capital or 0))
    by_arm = {arm: [r for r in rows if r["arm"] == arm] for arm in SHADOW_ARMS}
    metrics = {arm: _arm_metrics(by_arm[arm], cap) for arm in SHADOW_ARMS}
    cand_ret = metrics["candidate"]["portfolio_net_return"]
    inc_ret = metrics["incumbent"]["portfolio_net_return"]
    delta = None if (cand_ret is None or inc_ret is None) else float(cand_ret - inc_ret)
    samples = _comparable_sample_keys(rows)

    # 极端 regime 全部落在 `unknown` 一组 —— 本部署**没有基准行情源**（R5 的判定器
    # `observe()` 默认 None → 恒 unknown）。**不把 unknown 混进别的组**，也不假装有别的组。
    by_regime = {
        "unknown": {
            "comparable_samples": len(samples),
            "candidate_net_return": cand_ret,
            "incumbent_net_return": inc_ret,
            "note": "本部署无基准行情源（R5：regime 判定器恒 unknown）—— 全部样本归此组，"
                    "不与其他 regime 混组（红线：unknown 不得与明确 regime 同组聚合）",
        }
    }
    return {
        "validation_id": validation_id,
        "market": market,
        "initial_capital": _json_num(cap),
        "arms": metrics,
        "delta_candidate_minus_incumbent": delta,
        "comparable_samples": len(samples),
        "sample_keys": sorted(f"{d}|{p}|{s}" for d, p, s in samples)[:50],
        "loss_tail": {
            "candidate": _loss_tail(_arm_equity(by_arm["candidate"])),
            "incumbent": _loss_tail(_arm_equity(by_arm["incumbent"])),
            "note": "单步净值变化最差的若干步（真算），不是单笔损益",
        },
        "by_regime": by_regime,
    }


# ── 判定（读冻结计划，不临时改口径）────────────────────────────────────────

def _trading_days_between(cur, market: str, start: str, end: str) -> Optional[int]:
    """`(start, end]` 区间内该市场的**交易日数**（含 end 不含 start）。日历缺 → 抛。"""
    cur.execute(
        "SELECT count(*) AS n FROM fin_market_calendar"
        " WHERE market = %s AND is_trading AND trade_date > %s AND trade_date <= %s",
        (market, start, end))
    row = cur.fetchone()
    return None if row is None else int(row["n"])


def _validation_start(cur, proposal_id: str) -> Optional[str]:
    """第一次 `validating` 事件里记的 `window_start`（验证窗口的起点）。没有 → None。"""
    cur.execute(
        "SELECT payload FROM fin_evolution_event WHERE proposal_id = %s AND kind = 'validating'"
        " ORDER BY created_at ASC, event_id ASC LIMIT 1", (proposal_id,))
    row = cur.fetchone()
    if not row:
        return None
    return ((row.get("payload") or {}).get("window_start"))


def start_validation(*, proposal_id: str, market: str, trade_date: str,
                     actor: str = "fin-worker", conn=None) -> dict[str, Any]:
    """把提案推进到 `validating` 并**记下窗口起点**（幂等：已有 validating 事件则跳过）。"""
    own = conn is None
    conn = conn or get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if _validation_start(cur, proposal_id) is not None:
                conn.rollback()
                return {"started": False, "proposal_id": proposal_id, "reason": "already_validating"}
        conn.rollback()
    finally:
        if own:
            conn.close()
    row = append_event(proposal_id=proposal_id, kind="validating",
                       payload={"window_start": trade_date, "market": market,
                                "started_by": actor},
                       actor=actor)
    return {"started": True, "proposal_id": proposal_id, **row}


def _decide_verdict(metrics: dict[str, Any], plan: dict[str, Any],
                    window: dict[str, Any]) -> tuple[Optional[str], str]:
    """按**冻结计划**判 verdict。返回 `(verdict_or_None, 理由)`。

    顺序与红线：
    1. 窗口未结束 → **绝不** `passed`；若已触及失败线 / 提前停止线 → `failed`（提前止损是允许的）。
    2. 窗口结束：样本 < 最小可比样本 → `inconclusive`（不结论是合法终局）。
    3. `delta >= pass_line` → `passed`；`delta <= fail_line` 或回撤 ≤ early_stop → `failed`；
       其余 → `inconclusive`。
    """
    delta = metrics.get("delta_candidate_minus_incumbent")
    cand_dd = metrics["arms"]["candidate"]["max_drawdown"]
    fail_line = _as_float(plan.get("fail_line"))
    pass_line = _as_float(plan.get("pass_line"))
    early = (plan.get("early_stop_condition") or {})
    stop_dd = _as_float(early.get("max_drawdown_pct"))

    # 提前失败（与窗口无关）：触及失败线或提前停止线。
    if delta is not None and fail_line is not None and delta <= fail_line:
        return "failed", f"候选相对现行 {delta:+.4%} ≤ 失败线 {fail_line:+.4%}"
    if cand_dd is not None and stop_dd is not None and Decimal(str(cand_dd)) <= Decimal(str(stop_dd)):
        return "failed", f"候选最大回撤 {cand_dd:.4%} ≤ 提前停止线 {stop_dd:.4%}"

    if not window.get("ended"):
        return None, (f"验证窗口未结束（{window.get('elapsed_trading_days')}/"
                      f"{window.get('window_days')} 交易日）—— 窗口结束前不许通过")

    min_sample = int(plan.get("min_comparable_sample") or 0)
    if metrics.get("comparable_samples", 0) < min_sample:
        return "inconclusive", (f"可比样本 {metrics.get('comparable_samples')} < 最小可比样本 "
                                f"{min_sample}（两臂均有可比机会的交易日才算样本）")

    if delta is None:
        return "inconclusive", "算不出候选与现行的组合净收益差（缺估值）"
    if pass_line is not None and delta >= pass_line:
        return "passed", f"候选相对现行 {delta:+.4%} ≥ 通过线 {pass_line:+.4%}，样本 {metrics.get('comparable_samples')}"
    return "inconclusive", (f"窗口已结束但差异 {delta:+.4%} 落在 [{fail_line}, {pass_line}] 之间，"
                            "或样本质量不足以支撑结论")


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(Decimal(str(value)))
    except Exception:  # noqa: BLE001
        return None


def evaluate_validation(*, proposal_id: str, market: str, trade_date: str,
                        actor: str = "fin-worker", conn=None) -> dict[str, Any]:
    """跑一次判定：算指标 → 若**终局**（窗口结束或提前失败）则追加状态事件。

    **终局才写事件**：中途每次跑都写一个 `inconclusive` 会把提案的投影状态压成 inconclusive、
    让它退出 validating，后续就再也跑不到 —— 所以中间结果只算不写。终局三种：
    `passed` / `failed` / `inconclusive`（样本不足 / 行情缺口 / 未完成持仓）。

    `passed` 只在**窗口结束且样本足够且达标**时产生 —— 这是「窗口未到不得通过」的可执行定义。
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            prop = get_proposal_inner(cur, proposal_id)
            plan = prop.get("plan") or dict(DEFAULT_PLAN)
            start = _validation_start(cur, proposal_id)
            elapsed = None
            if start:
                elapsed = _trading_days_between(cur, market, str(start), str(trade_date))
            window_days = int(plan.get("window_days") or 0)
            window = {
                "start": start,
                "window_days": window_days,
                "elapsed_trading_days": elapsed,
                "ended": bool(start and elapsed is not None and elapsed >= window_days),
                "market": market,
                "as_of": trade_date,
            }
            # 指标用**同一个连接**（会话时区一致），在关连接之前算完。
            metrics = shadow_metrics(validation_id=proposal_id, market=market, conn=conn)
        conn.rollback()
    finally:
        if own:
            conn.close()
    verdict, reason = _decide_verdict(metrics, plan, window)
    result = {"proposal_id": proposal_id, "market": market, "trade_date": trade_date,
              "window": window, "metrics": metrics, "verdict": verdict, "reason": reason,
              "appended": False}
    if verdict is None:
        return result
    # 终局：追加事件（同时把 proposal.status 更新成它的投影）。
    ev = append_event(proposal_id=proposal_id, kind=verdict,
                      payload={"reason": reason, "window": window,
                               "metrics": {k: metrics[k] for k in
                                           ("arms", "delta_candidate_minus_incumbent",
                                            "comparable_samples", "loss_tail", "by_regime")}},
                      actor=actor)
    result["appended"] = True
    result["event"] = ev
    return result


def shadow_summary(*, proposal_id: str, conn=None) -> dict[str, Any]:
    """给界面 / 文档用的一次性汇总：提案 + 计划 + 事件数 + 两臂指标 + 判定（只读，不写）。"""
    own = conn is None
    conn = conn or get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            prop = get_proposal_inner(cur, proposal_id)
            metrics = shadow_metrics(validation_id=proposal_id, conn=conn)
        conn.rollback()
    finally:
        if own:
            conn.close()
    return {"proposal": _jsonable(prop), "metrics": metrics}


# ════════════════════════════════════════════════════════════════════════
# 九 · 生效 / 观察 / 回滚 / 回灌（R8 · `plan/R8.md` §一.2–4）
# ════════════════════════════════════════════════════════════════════════
#
# 这是**全系统唯一会自主改 `fin_param` 的地方**（`记忆系统追加规则.md` §五 第 7、9 条）。
# 三条纪律，改这一段之前先读：
#
#   1. **生效只人工确认**（`03 §7.5`）：不做自动生效（`FIN_AUTO_APPLY` 恒 0）；接口必须带
#      `confirm` 显式标志；放行依据**不止**一个收益阈值（passed 事件 / 计划未过期 /
#      样本足够 / 不触及风控上限 / 成本滑点模型一致）。
#   2. **参数只能经唯一写入口**：真正改 `fin_param` 的是 `control.activate_candidate` /
#      `control.restore_base_config`（四步同事务）。本模块**一行都不直改** `fin_param`
#      （`test_fin_evolution_apply.py` 的 grep 守护盯着）。
#   3. **回滚只回到「上一个已验证版本」**（红线 9）：目标 key 从
#      `fin_evolution_event(kind='applied')` 的 `from_key` 取，**再校验**它在
#      `fin_param.strategies` 里真的存在、且当前 active 就是那次生效的 `to_key`；
#      核验不过 → **拒绝回滚并告警**（不许只信 `base_strategy_key` 字符串，不许猜）。

# 影子期用的成本 / 滑点模型（与 `apps/paper` 的实现一致）。生效前复核「成本/滑点模型与影子期
# 一致」靠这张白名单 —— 若代码里的模型版本变了而 plan 仍冻结在老版本，影子结果就不再可比。
SUPPORTED_COST_MODELS = ("fee-model-v1",)
SUPPORTED_SLIPPAGE_MODELS = ("slippage-v1",)

ALERT_KIND = "fin.evolution.alert"          # 告警种类（写进 `fin_alert_log.kind`，宿主投递）


class EvolutionRollbackRefused(EvolutionError):
    """回滚目标核验不过（版本链对不上 / 目标已不在 `strategies[]`）—— 路由 → **409**。

    与闸门拒绝区分：这不是「提案不合规」，是「回滚这一动作本身不敢做」。仍会**告警**。
    """


def _control():
    """晚导入 `control`（唯一参数写入口）。避开 `control → evolution`（哈希）的模块级循环。"""
    from app.services.fin import control as control_svc
    return control_svc


def _latest_event(cur, proposal_id: str, kind: Optional[str] = None) -> Optional[dict]:
    """某提案最后一条事件（可按 kind 过滤）。没有 → `None`。"""
    if kind:
        cur.execute(
            "SELECT * FROM fin_evolution_event WHERE proposal_id = %s AND kind = %s "
            "ORDER BY created_at DESC, event_id DESC LIMIT 1", (proposal_id, kind))
    else:
        cur.execute(
            "SELECT * FROM fin_evolution_event WHERE proposal_id = %s "
            "ORDER BY created_at DESC, event_id DESC LIMIT 1", (proposal_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def _applied_payload(cur, proposal_id: str) -> Optional[dict]:
    """最近一次 `applied` 事件里的 payload（回滚的锚点：from_key / base_config / 哈希）。"""
    ev = _latest_event(cur, proposal_id, "applied")
    return dict(ev.get("payload") or {}) if ev else None


def _comparable_sample_count(cur, validation_id: str) -> int:
    """影子可比样本数（两臂均出决策的 `(trade_date, point, symbol)`）—— 与 `shadow_metrics` 同口径。"""
    cur.execute("SELECT arm, trade_date, point, symbol, signal FROM fin_evolution_shadow_event "
                " WHERE validation_id = %s", (validation_id,))
    return len(_comparable_sample_keys([dict(r) for r in cur.fetchall()]))


def _candidate_key(base_key: str, proposal_id: str) -> str:
    """候选版本的**新 key**（`<基线 key>#<提案号尾>`）—— 一眼看出它从哪来、属于哪条提案。"""
    tail = str(proposal_id or "").replace("evp_", "")[:10] or "cand"
    return f"{base_key or 'strategy'}#{tail}"


def _write_alert(cur, *, project_id: str, kind: str, subject: str, body: str) -> bool:
    """写一条告警到 `fin_alert_log`（宿主的 `scripts/fin_recon_alert.sh` 读 `sent_at IS NULL` 投递）。

    **不直接调 notify-qq**：投递是宿主的事（与 `paper/recon.py` 同一条链路）；服务端只落记录，
    这样测试不会真发邮件、离线环境也不会因为发不出去而失败。表不存在 / 写失败只记日志，
    **不让它把回滚本身带崩**。
    """
    try:
        cur.execute(
            """
            INSERT INTO fin_alert_log (kind, ref_id, channel, project_id, subject, body)
            SELECT %s, NULL, 'notify-qq', %s, %s, %s
             WHERE NOT EXISTS (
               SELECT 1 FROM fin_alert_log
                WHERE kind = %s AND project_id = %s AND sent_at IS NULL AND subject = %s)
            """,
            (kind, project_id, subject, body, kind, project_id, subject))
        return cur.rowcount == 1
    except Exception as exc:                                    # noqa: BLE001
        logger.warning("[fin.evolution] 写告警失败（不影响主流程）：{}", exc)
        return False


# ── 放行闸门（`plan/R8.md` §一.2）──────────────────────────────────────────

def _apply_gate_reason(cur, prop: dict, plan: dict, proposal_id: str,
                       expected_base_config_hash: str) -> Optional[str]:
    """生效前**必须**复核的五条（任一不满足 → 返回原因字符串，调用方拒绝并记 `rejected_by_gate`）。

    1. 该提案有 `passed` 事件（**只有 passed 才算验证通过** —— `inconclusive` 不算，红线 10）；
    2. 计划未过期（= 基线没被改动：当前配置哈希仍等于提案的 `base_config_hash`；
       CAS 会在 `activate_candidate` 里以 `FOR UPDATE` 再核一次）；
    3. 影子可比样本 ≥ 冻结计划的 `min_comparable_sample`（红线 11）；
    4. **不触及任何风控上限** —— 本路径只生效 `target='strategy'` 的提案；风控改动一律走
       `apply_risk_tier` 人工棘轮（红线 8/9：策略切换 ≠ 可放宽风控）；
    5. 成本 / 滑点模型与影子期一致（都在白名单里）。
    """
    if not _latest_event(cur, proposal_id, "passed"):
        return ("提案没有 passed 事件 —— 只有验证通过的提案才能生效；"
                "inconclusive（不结论）与 failed（不达标）都不是通过（红线 10）")
    if str(expected_base_config_hash or "") != str(prop.get("base_config_hash") or ""):
        return (f"expected_base_config_hash {expected_base_config_hash!r} 与提案记录的 "
                f"base_config_hash {prop.get('base_config_hash')!r} 不一致")
    if str(prop.get("target")) != "strategy":
        return ("本生效路径不触及任何风控上限：target='risk' 的提案不走这里"
                "（风控只经 apply_risk_tier 人工棘轮，策略切换 ≠ 可放宽风控）")
    current = read_config(cur, prop["project_id"])
    if hash_config(current) != str(prop.get("base_config_hash") or ""):
        return ("计划已过期：基线配置已被改动（当前配置哈希与提案的 base_config_hash 不符）"
                "—— 旧提案不得生效")
    min_sample = int(plan.get("min_comparable_sample") or 0)
    n = _comparable_sample_count(cur, proposal_id)
    if n < min_sample:
        return f"影子可比样本 {n} < 冻结计划的最小可比样本 {min_sample}（红线 11）"
    if (str(plan.get("cost_model")) not in SUPPORTED_COST_MODELS
            or str(plan.get("slippage_model")) not in SUPPORTED_SLIPPAGE_MODELS):
        return (f"成本/滑点模型与影子期不一致：plan 记的是 "
                f"cost={plan.get('cost_model')!r} slippage={plan.get('slippage_model')!r}，"
                f"当前支持 cost∈{SUPPORTED_COST_MODELS} slippage∈{SUPPORTED_SLIPPAGE_MODELS}")
    return None


def _gate_reject(cur, proposal_id: str, project_id: str, reason: str, actor: str,
                 stage: str) -> None:
    """追加一条 `rejected_by_gate` 事件（放行闸门拒绝时谁试过都要留痕）。"""
    _append_event(cur, proposal_id, "rejected_by_gate",
                  {"reason": reason, "project_id": project_id, "stage": stage},
                  actor=actor)


# ── 生效（人工确认）────────────────────────────────────────────────────────

def apply_proposal(*, proposal_id: str, expected_base_config_hash: Any, actor: Any,
                   confirm: bool = False, market: Any = None,
                   conn=None) -> dict[str, Any]:
    """**人工确认后把提案应用到模拟盘**（`03 §4-D` / §7.5）。

    不做自动生效：`FIN_AUTO_APPLY` 必须为 0，`confirm` 必须显式为真。放行五条见
    `_apply_gate_reason`；任一不过 → 追加 `rejected_by_gate` 并抛 `EvolutionGateError`（→400）。

    通过与生效**写在同一笔事务里**：`control.activate_candidate` 做完
    「CAS → 注册候选版本 → 写 change_log → 切 active」四步后，`within_txn` 回调在同一事务内
    追加 `kind='applied'` 事件（payload 记 `from_key`/`to_key`/两个哈希/`plan_hash`/两侧完整配置）
    —— 这是回滚要找的锚点。本模块**不碰** `fin_param`（唯一写入口在 `control`）。
    """
    if not switches.evolution_enabled():
        raise EvolutionDisabledError("进化未启用（FIN_EVOLUTION_MODE=off）：本部署当前不接受生效")
    switches.assert_hard_ok()                                    # FIN_AUTO_APPLY 必须为 0
    if not confirm:
        raise EvolutionValidationError(
            "必须显式人工确认（confirm=true）才生效 —— 本方案不做自动生效（FIN_AUTO_APPLY 恒 0）")

    proposal_id = str(proposal_id or "").strip()
    if not proposal_id:
        raise EvolutionValidationError("proposal_id 不能为空")
    actor = str(actor or "").strip() or "human"
    market = (str(market).strip() or None) if market else None

    own = conn is None
    conn = conn or get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            try:
                prop = get_proposal_inner(cur, proposal_id)
            except LookupError:
                conn.rollback()
                raise
            plan = dict(prop.get("plan") or {})
            project_id = prop["project_id"]

            reason = _apply_gate_reason(cur, prop, plan, proposal_id,
                                        str(expected_base_config_hash or ""))
            if reason:
                _gate_reject(cur, proposal_id, project_id, reason, actor, "apply")
                conn.commit()
                logger.info("[fin.evolution] 生效遭闸门拒绝 pid={} reason={}", proposal_id, reason)
                raise EvolutionGateError(reason)

            base_config = read_config(cur, project_id)
            candidate_config = _shadow_apply_diff(base_config, prop.get("param_diff"))
            base_hash = str(prop["base_config_hash"])
            cand_hash = str(prop["candidate_config_hash"])
            plan_hash = plan.get("plan_hash")
            cur.execute("SELECT strategies FROM fin_param WHERE project_id = %s", (project_id,))
            active = _control().active_strategy(
                {"strategies": (cur.fetchone() or {}).get("strategies") or []}) or {}
            base_key = str(active.get("key") or "").strip()
            new_key = _candidate_key(base_key, proposal_id)
        conn.rollback()                                          # 结束只读事务

        def _emit(cur, receipt):
            _append_event(cur, proposal_id, "applied", {
                "from_key": receipt["from_key"], "to_key": receipt["to_key"],
                "base_config_hash": base_hash, "candidate_config_hash": cand_hash,
                "plan_hash": plan_hash,
                # 回滚要用的锚点：两侧完整配置 + 生效后行哈希（CAS 用）
                "base_config": base_config, "candidate_config": candidate_config,
                "active_config_hash": receipt["active_config_hash"],
                "market": market, "applied_by": actor,
            }, actor=actor)

        receipt = _control().activate_candidate(
            conn, project_id, candidate_config=candidate_config,
            expected_base_config_hash=base_hash, new_key=new_key, actor=actor,
            within_txn=_emit)
        logger.info("[fin.evolution] 生效 pid={} {} → {} (by {})",
                    proposal_id, receipt["from_key"], receipt["to_key"], actor)
        return {"ok": True, "proposal_id": proposal_id, "project_id": project_id,
                **{k: receipt[k] for k in ("from_key", "to_key", "base_config_hash",
                                           "candidate_config_hash", "active_config_hash")},
                "changed_fields": receipt["changed_fields"], "market": market}
    finally:
        if own:
            conn.close()


# ── 观察（按原 plan 计算）──────────────────────────────────────────────────

def _live_equity(cur, project_id: str, market: str, since) -> list[tuple[Any, float]]:
    """生效之后的**实盘（模拟盘）净值序列** `[(as_of, nav)]`（读 `fin_valuation`）。

    这是「观察期继续按原 plan 计算」的数据来源：生效后候选臂就是现行臂，影子已结束，
    只能看**真实账本的净值**。缺失（还没估值）→ 空列表（不编）。
    """
    cur.execute(
        "SELECT as_of, nav FROM fin_valuation WHERE project_id = %s AND market = %s "
        " AND as_of >= %s ORDER BY as_of", (project_id, market, since))
    out = []
    for r in cur.fetchall():
        nav = r["nav"]
        if nav is None:
            continue
        out.append((r["as_of"], float(Decimal(str(nav)))))
    return out


def _drawdown_and_return(points: list[tuple[Any, float]]) -> dict[str, Any]:
    """净值序列 → `{max_drawdown, net_return, points}`（点不足两个 → 都 `None`，不编）。"""
    if len(points) < 2:
        return {"max_drawdown": None, "net_return": None, "points": len(points)}
    peak = points[0][1]
    worst = 0.0
    for _t, nav in points:
        peak = max(peak, nav)
        if peak > 0:
            worst = min(worst, nav / peak - 1.0)
    first = points[0][1]
    net = (points[-1][1] / first - 1.0) if first else None
    return {"max_drawdown": worst, "net_return": net, "points": len(points)}


def observe_applied(*, proposal_id: str, as_of: Optional[str] = None, market: Any = None,
                    actor: str = "fin-worker", conn=None) -> dict[str, Any]:
    """观察一次已生效的提案：按**原冻结计划**算实盘净值表现。

    - **普通性能不达标 → 告警**（写 `fin_alert_log` + 追加 `alert` 事件），**配置一动不动**，
      等人确认回退；
    - **唯一自动回滚的条件**：实盘最大回撤 **≤ 冻结的 `rollback_line`**（风险紧急线）。

    返回 `{observed, action, window, live, rollback_line, fail_line}`（只读字段，供 R9 展示）。
    """
    own = conn is None
    conn = conn or get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            prop = get_proposal_inner(cur, proposal_id)
            last = _latest_event(cur, proposal_id)
            last_kind = str((last or {}).get("kind") or "")
            if last_kind not in ("applied", "alert"):
                conn.rollback()
                return {"observed": False, "proposal_id": proposal_id,
                        "reason": f"提案当前不是已生效态（最后事件 {last_kind or '无'}）"}
            ap = _latest_event(cur, proposal_id, "applied")
            payload = dict(ap.get("payload") or {})
            plan = dict(prop.get("plan") or {})
            project_id = prop["project_id"]
            market_v = (str(market).strip() if market else "") \
                or str(payload.get("market") or "") or None
            if not market_v:
                cur.execute("SELECT market_scope FROM fin_project WHERE project_id = %s",
                            (project_id,))
                market_v = (cur.fetchone() or {}).get("market_scope") or "CN_A"
            window_start = payload.get("applied_at") or ap.get("created_at")
            window_days = int(plan.get("window_days") or 0)
            elapsed = _trading_days_between(cur, market_v, str(window_start), str(as_of)) \
                if as_of else None
            live = _drawdown_and_return(_live_equity(cur, project_id, market_v, window_start))
            rollback_dd = _as_float((plan.get("rollback_line") or {}).get("max_drawdown_pct"))
            early_dd = _as_float((plan.get("early_stop_condition") or {}).get("max_drawdown_pct"))
            fail_line = _as_float(plan.get("fail_line"))
            window = {"start": str(window_start), "window_days": window_days,
                      "elapsed_trading_days": elapsed,
                      "ended": bool(as_of and elapsed is not None and elapsed >= window_days),
                      "market": market_v, "as_of": as_of}
        conn.rollback()

        dd = live["max_drawdown"]
        # ① 唯一的自动回滚条件：触发冻结的 rollback_line（风险紧急线）
        if dd is not None and rollback_dd is not None and dd <= rollback_dd:
            rb = rollback_proposal(proposal_id=proposal_id,
                                   reason=f"触发风险紧急线：实盘最大回撤 {dd:.4%} ≤ 冻结的 "
                                          f"rollback_line {rollback_dd:.4%}",
                                   actor=actor, conn=None)
            return {"observed": True, "action": "rolled_back", "proposal_id": proposal_id,
                    "window": window, "live": live, "rollback_line": rollback_dd,
                    "fail_line": fail_line, "rollback": rb}
        # ② 普通不达标 / 触及提前停止线 → 只告警，不自动回退
        alert = None
        if dd is not None and early_dd is not None and dd <= early_dd:
            alert = (f"实盘最大回撤 {dd:.4%} 已触及冻结的提前停止线 {early_dd:.4%}")
        elif window["ended"] and live["net_return"] is not None and fail_line is not None \
                and live["net_return"] <= fail_line:
            alert = (f"观察期结束但实盘净收益 {live['net_return']:+.4%} ≤ 冻结的失败线 "
                     f"{fail_line:+.4%}")
        if alert:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                _append_event(cur, proposal_id, "alert",
                              {"reason": alert, "project_id": project_id, "window": window,
                               "live": live, "rollback_line": rollback_dd, "fail_line": fail_line},
                              actor=actor)
                _write_alert(cur, project_id=project_id, kind=ALERT_KIND,
                             subject="[智能炒股·进化] 生效策略观察期不达标（未自动回退）",
                             body=f"提案 {proposal_id}：{alert}<br>配置**未变**，等待人工确认是否回退。")
            conn.commit()
            logger.info("[fin.evolution] 观察告警 pid={} {}", proposal_id, alert)
            return {"observed": True, "action": "alert", "proposal_id": proposal_id,
                    "reason": alert, "window": window, "live": live,
                    "rollback_line": rollback_dd, "fail_line": fail_line}
        return {"observed": True, "action": "none", "proposal_id": proposal_id,
                "window": window, "live": live, "rollback_line": rollback_dd,
                "fail_line": fail_line}
    finally:
        if own:
            conn.close()


# ── 紧急回滚（系统唯一自主改 fin_param 的场合）──────────────────────────────

def rollback_proposal(*, proposal_id: str, reason: Any, actor: Any,
                      expected_current_config_hash: Any = None,
                      conn=None) -> dict[str, Any]:
    """**回到上一个已验证版本**（红线 9）。触发时**立即停止该项目的模拟下单**。

    回滚目标取自**版本链**：`fin_evolution_event(kind='applied')` 的 `from_key`；
    再校验它在 `fin_param.strategies` 里真的存在、且当前 active 正是那次生效的 `to_key`；
    核验不过 → 追加 `alert` 事件 + 告警 + 抛 `EvolutionRollbackRefused`（→409），**不猜**。

    回滚一并写入三样（`plan/R8.md` §一.4）：
      1. `kind='rolled_back'` 事件（与配置改动同事务）；
      2. `fin_param_change_log` 行（由 `control.restore_base_config` 写，同事务）；
      3. **一条有证据的失败经验**（`polarity='refute'`，经 `memory.append_evidence`）。
         **写入失败不许吞** —— 落进 `fin_evolution_reinject_task`（`pending`），
         只读接口据此报「回灌待完成」。
    """
    if not switches.evolution_enabled():
        raise EvolutionDisabledError("进化未启用（FIN_EVOLUTION_MODE=off）：本部署当前不接受回滚")
    proposal_id = str(proposal_id or "").strip()
    if not proposal_id:
        raise EvolutionValidationError("proposal_id 不能为空")
    actor = str(actor or "").strip() or "system"
    reason = str(reason or "").strip() or "manual"

    own = conn is None
    conn = conn or get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            prop = get_proposal_inner(cur, proposal_id)
            project_id = prop["project_id"]
            payload = _applied_payload(cur, proposal_id)
            if not payload:
                conn.rollback()
                raise EvolutionGateError("该提案没有 applied 事件 —— 没有可回滚的生效版本")
            from_key = str(payload.get("from_key") or "").strip()
            to_key = str(payload.get("to_key") or "").strip()
            base_config = dict(payload.get("base_config") or {})
            target_hash = str(expected_current_config_hash
                              or payload.get("active_config_hash") or "")
            cur.execute("SELECT strategies FROM fin_param WHERE project_id = %s", (project_id,))
            items = [i for i in ((cur.fetchone() or {}).get("strategies") or [])
                     if isinstance(i, dict)]
            keys = [str(i.get("key") or "").strip() for i in items]
            cur_key = str((_control().active_strategy({"strategies": items}) or {}).get("key") or "")

            problem = None
            if not from_key:
                problem = "applied 事件没记 from_key —— 版本链不完整，拒绝回滚"
            elif from_key not in keys:
                problem = (f"回滚目标 {from_key!r} 已不在 fin_param.strategies 里"
                           f"（现有 {', '.join(k or '?' for k in keys)}）—— 拒绝回滚并告警")
            elif to_key and cur_key != to_key:
                problem = (f"当前 active 是 {cur_key!r}，不是那次生效的 to_key {to_key!r}"
                           "（版本链对不上）—— 拒绝回滚并告警")
            if problem:
                _append_event(cur, proposal_id, "alert",
                              {"reason": f"回滚核验失败：{problem}", "project_id": project_id,
                               "stage": "rollback_verify"},
                              actor=actor)
                _write_alert(cur, project_id=project_id, kind=ALERT_KIND,
                             subject="[智能炒股·进化] 回滚核验失败（拒绝回滚）",
                             body=f"提案 {proposal_id}：{problem}")
                conn.commit()
                logger.warning("[fin.evolution] 回滚核验失败 pid={} {}", proposal_id, problem)
                raise EvolutionRollbackRefused(problem)
        conn.rollback()

        def _emit(cur, receipt):
            _append_event(cur, proposal_id, "rolled_back", {
                "from_key": receipt["from_key"], "to_key": receipt["to_key"],
                "reason": reason, "restored_fields": receipt["restored_fields"],
                "halted": receipt["halted"], "base_config_hash": payload.get("base_config_hash"),
                "plan_hash": payload.get("plan_hash"), "rolled_back_by": actor,
            }, actor=actor)

        receipt = _control().restore_base_config(
            conn, project_id, target_key=from_key, base_config=base_config,
            expected_current_config_hash=target_hash, actor=actor, reason=reason,
            halt=True, within_txn=_emit)
        conn.rollback()
    finally:
        if own:
            conn.close()

    # ③ 失败经验回灌（配置事务已提交，单独一笔）
    reinject = queue_rollback_experience(
        proposal_id=proposal_id, project_id=project_id,
        reason=f"{reason}（回滚 {receipt['from_key']} → {receipt['to_key']}）",
        plan_hash=payload.get("plan_hash"))
    logger.warning("[fin.evolution] 已回滚 pid={} {} → {}（回灌 {}）",
                   proposal_id, receipt["from_key"], receipt["to_key"],
                   reinject.get("status"))
    return {"ok": True, "proposal_id": proposal_id, "project_id": project_id,
            "from_key": receipt["from_key"], "to_key": receipt["to_key"],
            "reason": reason, "restored_fields": receipt["restored_fields"],
            "halted": receipt["halted"], "active_config_hash": receipt["active_config_hash"],
            "reinject": reinject}


# ── 回灌（失败经验）与重试队列 ──────────────────────────────────────────────

def _rollback_experience(*, cur, project_id: str, reason: str, proposal_id: str,
                         plan_hash: Any) -> dict[str, Any]:
    """构造回滚失败经验的 `append_evidence` 入参（**数字只进证据，不进 statement**）。

    证据 = 本项目**账本真值**（`fin_trade` 行，最多 5 条）。没有成交 → 证据为空，
    `append_evidence` 会拒（「经验必须带证据」）→ 任务留在队列里（「回灌待完成」是真话）。
    """
    cur.execute("SELECT trade_id FROM fin_trade WHERE project_id = %s "
                " ORDER BY traded_at DESC LIMIT 5", (project_id,))
    evidence = [{"evidence_kind": "trade", "ref_id": r["trade_id"]} for r in cur.fetchall()]
    n_trades = len(evidence)
    return {
        "project_id": project_id,
        "kind": "verified",
        "statement": "被自动回滚的策略变更在观察期触及了风险紧急线，该变更方向不再采用",
        "method": "影子验证与观察期净值",
        "sample_size": max(n_trades, 1),
        "polarity": "refute",
        "importance": 0.9,        # 0~1（memory 校验）；回滚教训置前展示
        "evidence": evidence,
        "applicability": f"提案 {proposal_id} · plan {plan_hash or '无'} · 触发原因：{reason}",
        "invalidation_condition": "风险紧急线口径或影子 / 账本口径变更时需重新评估",
    }


def queue_rollback_experience(*, proposal_id: str, project_id: str, reason: str,
                              plan_hash: Any = None, conn=None) -> dict[str, Any]:
    """把「回滚要写的失败经验」落进重试队列并**立即尝试一次**。

    返回 `{task_id, status, error, attempts}`。`done` = 已真的写进经验库；
    `pending` = 回灌待完成（经验没写成，任务还在队列里）。
    """
    own = conn is None
    conn = conn or get_conn()
    task_id = _new_id("evri_")
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            payload = _rollback_experience(cur=cur, project_id=project_id, reason=reason,
                                           proposal_id=proposal_id, plan_hash=plan_hash)
            cur.execute(
                "INSERT INTO fin_evolution_reinject_task "
                " (task_id, proposal_id, project_id, reason, payload, status) "
                " VALUES (%s, %s, %s, %s, %s, 'pending')",
                (task_id, proposal_id, project_id, reason,
                 psycopg2.extras.Json(_jsonable(payload))))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if own:
            conn.close()
    # 立即尝试一次（失败 → 保持 pending，由 retry 入口继续）
    outcome = _reinject_one(task_id=task_id)
    return {"task_id": task_id, **outcome}


def _reinject_one(*, task_id: str, conn=None) -> dict[str, Any]:
    """尝试把一条回灌任务写进经验库（唯一写入口 `memory.append_evidence`）。"""
    own = conn is None
    read_conn = conn or get_conn()
    try:
        with read_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM fin_evolution_reinject_task WHERE task_id = %s", (task_id,))
            row = cur.fetchone()
            if not row:
                raise LookupError(f"回灌任务不存在：{task_id}")
            task = dict(row)
            if task["status"] == "done":
                read_conn.rollback()
                return {"status": "done", "attempts": task["attempts"], "error": None}
            payload = dict(task["payload"] or {})
        read_conn.rollback()
    finally:
        if own:
            read_conn.close()

    error = None
    try:
        memory_svc.append_evidence(
            caller="internal",
            project_id=payload["project_id"],
            kind=payload["kind"],
            statement=payload["statement"],
            evidence=payload["evidence"],
            method=payload.get("method"),
            sample_size=payload.get("sample_size"),
            polarity=payload.get("polarity"),
            importance=payload.get("importance"),
            applicability=payload.get("applicability"),
            invalidation_condition=payload.get("invalidation_condition"),
        )
    except Exception as exc:                                    # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"

    write_conn = conn or get_conn()
    try:
        with write_conn.cursor() as cur:
            if error is None:
                cur.execute("UPDATE fin_evolution_reinject_task "
                            "SET status='done', attempts=attempts+1, last_error=NULL, "
                            "    updated_at=now() WHERE task_id=%s", (task_id,))
            else:
                cur.execute("UPDATE fin_evolution_reinject_task "
                            "SET attempts=attempts+1, last_error=%s, updated_at=now() "
                            "WHERE task_id=%s", (error, task_id))
        write_conn.commit()
    except Exception:
        write_conn.rollback()
        raise
    finally:
        if own:
            write_conn.close()
    if error:
        logger.warning("[fin.evolution] 回灌失败（保持待完成）task={} {}", task_id, error)
        return {"status": "pending", "attempts": None, "error": error}
    return {"status": "done", "attempts": None, "error": None}


def reinject_pending(*, proposal_id: Optional[str] = None, conn=None) -> list[dict[str, Any]]:
    """还没回灌成功的任务（只读接口据此报「回灌待完成」）。"""
    clause, params = "", []
    if proposal_id:
        clause, params = " AND proposal_id = %s", [proposal_id]
    own = conn is None
    conn = conn or get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT task_id, proposal_id, project_id, reason, status, attempts, "
                        "last_error, created_at FROM fin_evolution_reinject_task "
                        "WHERE status = 'pending'" + clause + " ORDER BY created_at", params)
            rows = [dict(r) for r in cur.fetchall()]
        conn.rollback()
    finally:
        if own:
            conn.close()
    return [_jsonable(r) for r in rows]


def retry_reinject(*, proposal_id: Optional[str] = None, conn=None) -> dict[str, Any]:
    """重试回灌队列里 `pending` 的任务。返回 `{tried, done, pending}`（幂等：`done` 不再动）。"""
    own = conn is None
    conn = conn or get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            clause, params = "", []
            if proposal_id:
                clause, params = " AND proposal_id = %s", [proposal_id]
            cur.execute("SELECT task_id FROM fin_evolution_reinject_task "
                        "WHERE status = 'pending'" + clause + " ORDER BY created_at", params)
            ids = [r["task_id"] for r in cur.fetchall()]
        conn.rollback()
    finally:
        if own:
            conn.close()
    done = 0
    for tid in ids:
        if _reinject_one(task_id=tid)["status"] == "done":
            done += 1
    return {"tried": len(ids), "done": done, "pending": len(ids) - done}
