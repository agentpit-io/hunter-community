"""智能炒股 · 受控自进化 · **提案层**（第四段 `R6` · `plan/R6.md` §一）。

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
from datetime import datetime, timezone
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
ALGO_VERSION = "evolution-algo-v1"

# `fin_evolution_event.kind` 闭集 —— 与 `0043` 的 CHECK **逐字一致**，改一处必须改另一处。
EVENT_KINDS = (
    "created", "validating", "passed", "rejected", "applied",
    "rolled_back", "inconclusive", "rejected_by_gate",
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
        "type": "ratio", "min": 0.005, "max": 0.5, "max_step": 0.05,
        "tighter_when": "smaller", "baseline_version": "strategy-fields-v1",
        "note": "止损幅度；越小 = 越早离场 = 越紧",
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


def read_config(cur, project_id: str) -> dict[str, Any]:
    """读项目的**可调配置**（白名单字段 → 数值）的规范快照。

    配置 = `fin_param` 里**白名单覆盖到的**那些字段：三个风控字段 + 顶层策略字段
    （`stop_loss_pct` / `take_profit_pct` / `hold_days_max`）+ 各 `strategies[].params` 里
    白名单命中的参数（键 `strategies.<key>.params.<name>`）。
    """
    cur.execute(
        """
        SELECT max_position_pct, daily_loss_halt_pct, account_drawdown_halt_pct,
               stop_loss_pct, take_profit_pct, hold_days_max, strategies
          FROM fin_param WHERE project_id = %s
        """,
        (project_id,),
    )
    row = cur.fetchone()
    if not row:
        raise LookupError("项目参数不存在（fin_param）")
    row = dict(row)
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
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
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
    if not switches.evolution_enabled():
        raise EvolutionDisabledError(
            "进化未启用（FIN_EVOLUTION_MODE=off）：本部署当前不接受提案")

    project_id = str(project_id or "").strip()
    if not project_id:
        raise EvolutionValidationError("project_id 不能为空")
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
        conn.rollback()      # 只读
        out = []
        for p in proposals:
            item = _jsonable(p)
            item["plan"] = _jsonable(plans.get(p["proposal_id"]))
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
        conn.rollback()
        item = _jsonable(dict(row))
        item["plan"] = _jsonable(dict(plan)) if plan else None
        return item
    finally:
        if own:
            conn.close()


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
