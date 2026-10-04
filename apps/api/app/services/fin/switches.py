"""智能炒股 · 运行开关与安全边界（第四段 R4 · `03 §2`）。

**全仓只在这里读那四个 `FIN_*` 环境变量。** 别处散着写 `os.environ.get("FIN_...")`
一律不许 —— 那是「同一件事写在多处、只改一处」的翻版：开关会在两个地方给出两个答案，
而且不会有任何报错（仓内铁律的原文见 `CLAUDE.md`）。

| 开关 | 取值 | 代码默认 | 语义 |
|---|---|---|---|
| `FIN_MEMORY_ENABLED` | `0` / `1` | **`0`** | `0` ⇒ `memory.query` 返回空集、`memory.append_evidence` 拒绝 |
| `FIN_EVOLUTION_MODE` | `off` / `observe` / `paper` | **`off`** | `paper` 的前置依赖缺一 ⇒ 降级 `observe` + 原因 |
| `FIN_AUTO_APPLY` | **只允许 `0`** | **`0`** | 非 `0` ⇒ 请求被拒（本方案恒为 0，自动生效未交付） |
| `FIN_LIVE_ORDER_ENABLED` | **只允许 `0`** | **`0`** | 非 `0` ⇒ 请求被拒（本项目**没有**实盘订单出口） |

**三个口径，先说清楚：**

1. **代码默认一律取安全值**（`0` / `off` / `0` / `0`）。环境变量缺省、留空、或值非法，
   一律**回落到安全值**并 `logger.warning` 留痕 —— **不静默、不猜别的数**。
   与 `config.review_delay_minutes()`（R3）同一个口径：配置写错不该让保护悄悄变了。
2. **`FIN_AUTO_APPLY` / `FIN_LIVE_ORDER_ENABLED` 是硬开关**：只要不是 `0`，
   `hard_config_errors()` 就报违规，`assert_hard_ok()` 抛 `SwitchConfigError`。
   服务端在**接口层拒绝请求**（`GET /v1/fin/runtime` → 503），并在**启动时打 ERROR 日志**。
   读法见 §「硬开关为什么拒绝请求而不是拒绝启动」。
3. **开关每次调用现读环境变量**，不在 import 期缓存。理由：`docker compose up -d`
   之后进程会重建、环境本来就会变；而测试要在同一个进程里切 env 验两条路径。
   缓存一份只会让「改完 env 却不生效」看起来像代码没部署。
4. **环境变量是天花板，`fin_memory_switch` 是天窗（R21）。**
   `FIN_MEMORY_ENABLED` / `FIN_EVOLUTION_MODE` 是**部署者的意志**（这台机器最多允许多少）；
   数据库里那两列是**使用者的日常选择**（在额度内）。**最终生效值 = 两者的交集**，
   取**更保守**的一个（`0 < 1`、`off < observe < paper`）。
   环境变量设成关时，界面上的开关是灰的、写入口直接 400 ——
   **界面永远开不出部署者不允许的东西**。
   **硬开关（`FIN_AUTO_APPLY` / `FIN_LIVE_ORDER_ENABLED`）不参与覆盖**，永远只认环境变量。
   **覆盖层只活在这个文件里**：别处不许读 `fin_memory_switch`（读点仍是一处）。
   候选值落库后靠一个 **5 秒 TTL 的模块级缓存**挡住热路径上的库往返；写入口主动清缓存，
   所以**改完立刻生效**（多进程最多差 5 秒，写进文档说明过）。

**硬开关为什么拒绝请求而不是拒绝启动**

`03 §2` 允许「拒绝启动**或**拒绝请求」。这里选**拒绝请求**，因为要求里还有半句
「并在**日志与接口**里写明」——只有进程活着，接口才说得清「哪个开关被拒、为什么」。
拒绝启动会让这台机器只剩一条日志，运维得 `docker compose logs` 才知道发生了什么，
而接口返回的 503 里那句话，前端（R9）可以直接展示给用户。
启动时同样打一条 ERROR 日志（见 `main.py` 的 lifespan），两者都留痕。
"""

from __future__ import annotations

import os
import time
from typing import Any, Optional

import httpx
import psycopg2
import psycopg2.extras
from loguru import logger

# 探针要读库。默认值与 `memory.py` / `control.py` 同源（同一个部署、同一个库）。
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://hunter:hunter@localhost:5432/hunter")

MEMORY_ENABLED_ENV = "FIN_MEMORY_ENABLED"
EVOLUTION_MODE_ENV = "FIN_EVOLUTION_MODE"
AUTO_APPLY_ENV = "FIN_AUTO_APPLY"
LIVE_ORDER_ENV = "FIN_LIVE_ORDER_ENABLED"

# 代码默认值 —— 全部取安全的一侧（fail-safe）。
MEMORY_ENABLED_DEFAULT = "0"
EVOLUTION_MODE_DEFAULT = "off"
AUTO_APPLY_DEFAULT = "0"
LIVE_ORDER_DEFAULT = "0"

EVOLUTION_MODES = ("off", "observe", "paper")

# 保守序：值越小越保守。取交集 = 取更小的那个（`off < observe < paper`）。
MODE_ORDER: dict[str, int] = {mode: i for i, mode in enumerate(EVOLUTION_MODES)}

# ── R21 · 天窗（按项目的覆盖层）────────────────────────────────────────────
# 可改的两项；**硬开关刻意不在这里** —— 它们连界面入口都没有（写入口直接 400）。
SWITCH_MEMORY = "memory_enabled"
SWITCH_MODE = "evolution_mode"
SWITCH_KEYS = (SWITCH_MEMORY, SWITCH_MODE)
# 请求里带了这两个 key → 400（报错里写清「本方案规定不许开」）。
HARD_SWITCH_KEYS = ("auto_apply", "live_order_enabled")

# 覆盖层的短 TTL 缓存：改完由写入口主动清，5 秒只是多副本之间的兜底上限。
SWITCH_CACHE_TTL_S = 5.0

# 硬开关被拒时用户看到的那句话（`03 §2` 原文）。日志与接口共用同一份，不许两处各写一句。
AUTO_APPLY_MESSAGE = f"{AUTO_APPLY_ENV} 必须为 0：本方案恒为 0，自动生效未交付"
LIVE_ORDER_MESSAGE = f"{LIVE_ORDER_ENV} 必须为 0：本项目没有实盘订单出口"


class SwitchConfigError(RuntimeError):
    """硬开关被设成非法值 —— 服务端据此拒绝请求（接口层 503）。"""


# ════════════════════════════════════════════════════════════════════════
# 一 · 读环境变量（唯一读点）
# ════════════════════════════════════════════════════════════════════════

def _raw(name: str, default: str) -> str:
    """读一个开关。

    `None`（未设置）与空串（compose 的 `${X:-}` 会把「没设」注成空串）**一视同仁**
    回落到默认值 —— 与 `boot.sh` 判 JWT_SECRET 的「非空才算配了」同一个道理。
    """
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def ceiling_memory_enabled() -> bool:
    """**天花板**：这台机器允不允许有经验库（只看环境变量）。默认 **关**（`0`）。"""
    raw = _raw(MEMORY_ENABLED_ENV, MEMORY_ENABLED_DEFAULT)
    if raw == "1":
        return True
    if raw == "0":
        return False
    logger.warning("[fin.switches] {}={!r} 不是 0/1，按 fail-safe 取 0（经验库关闭）",
                   MEMORY_ENABLED_ENV, raw)
    return False


def ceiling_evolution_mode() -> str:
    """**天花板**：这台机器最多允许多强的学习（只看环境变量）。默认 `off`。"""
    raw = _raw(EVOLUTION_MODE_ENV, EVOLUTION_MODE_DEFAULT).lower()
    if raw in EVOLUTION_MODES:
        return raw
    logger.warning("[fin.switches] {}={!r} 不是 off/observe/paper，按 fail-safe 取 off",
                   EVOLUTION_MODE_ENV, raw)
    return "off"


def memory_enabled(project_id: Optional[str] = None) -> bool:
    """经验库总开关（**生效值**）。

    - 不带 `project_id`（老调用点 / 老接口）：就是**天花板**，行为逐字不变。
    - 带 `project_id`（`R21`）：**天花板 ∩ 天窗** —— 天花板关 ⇒ 一律关（界面开不出来）；
      天花板开 ⇒ 看这个项目自己的选择，没设过就跟随天花板。
    """
    if not ceiling_memory_enabled():
        return False
    if not project_id:
        return True
    override = _read_override(project_id)
    if override and override.get(SWITCH_MEMORY) is not None:
        return bool(override[SWITCH_MEMORY])
    return True


def evolution_mode_requested(project_id: Optional[str] = None) -> str:
    """**请求**的进化模式（未做依赖校验）。默认 `off`。非法值 → `off` + 留痕。

    带 `project_id` 时取**天花板 ∩ 天窗**：两项谁更保守用谁
    （`off < observe < paper`，见 `MODE_ORDER`）。
    """
    ceiling = ceiling_evolution_mode()
    if not project_id:
        return ceiling
    override = _read_override(project_id)
    selected = override.get(SWITCH_MODE) if override else None
    if selected is None:
        return ceiling
    return selected if MODE_ORDER[selected] <= MODE_ORDER[ceiling] else ceiling


def evolution_enabled(project_id: Optional[str] = None) -> bool:
    """进化能力是否开启（生效模式 != `off`）。

    `R6` 的提案层用这一个判据（**别在 `evolution.py` 里另写 `mode != 'off'`** ——
    开关只在这里读，见模块顶部的铁律）。`observe` / `paper` 都允许提案；
    只不过 `paper` 的依赖缺一会先被降级成 `observe`（`runtime_state`）。
    """
    return evolution_mode_requested(project_id) != "off"


def auto_apply() -> bool:
    """是否允许自动生效。本方案恒为 `False`（`FIN_AUTO_APPLY` 只允许 `0`）。

    非 `0` 时这里返回 `True`，但**同一个值会让 `hard_config_errors()` 报违规**，
    于是 `GET /v1/fin/runtime` 在返回它之前就 503 了 —— 正常响应里它永远是 `False`。
    """
    return _raw(AUTO_APPLY_ENV, AUTO_APPLY_DEFAULT) != "0"


def live_order_enabled() -> bool:
    """是否放开实盘订单出口。本方案恒为 `False`（没有实盘出口）。同上，非 `0` 会被拒。"""
    return _raw(LIVE_ORDER_ENV, LIVE_ORDER_DEFAULT) != "0"


def hard_config_errors() -> list[str]:
    """两个硬开关的违规清单（空 = 合规）。"""
    errors: list[str] = []
    if auto_apply():
        errors.append(f"{AUTO_APPLY_ENV}={_raw(AUTO_APPLY_ENV, AUTO_APPLY_DEFAULT)!r} 被拒绝："
                      f"{AUTO_APPLY_MESSAGE}")
    if live_order_enabled():
        errors.append(f"{LIVE_ORDER_ENV}={_raw(LIVE_ORDER_ENV, LIVE_ORDER_DEFAULT)!r} 被拒绝："
                      f"{LIVE_ORDER_MESSAGE}")
    return errors


def assert_hard_ok() -> None:
    """硬开关合规检查。违规 → `SwitchConfigError`（路由翻成 503）。"""
    errors = hard_config_errors()
    if errors:
        message = "；".join(errors)
        logger.error("[fin.switches] 硬开关违规，拒绝本次请求：{}", message)
        raise SwitchConfigError(message)


# ════════════════════════════════════════════════════════════════════════
# 一之二 · R21 · 天窗：按项目的覆盖层（**只活在这个文件里**）
# ════════════════════════════════════════════════════════════════════════
#
# 现值表 `fin_memory_switch` 与流水账 `fin_memory_switch_log`（迁移 `0046`）。
# 这里只做三件事：**读覆盖（带 5 秒缓存）· 写覆盖（同事务写流水）· 清缓存**。
#
# ⚠️ 失败一律**回落环境变量**，绝不「猜一个」：库不通 / 表还没迁移 / 行读坏了
#    ⇒ 当作「没设过」，于是生效值 == 天花板 == 部署者的意志（fail-safe）。

_override_cache: dict[str, tuple[float, Optional[dict]]] = {}
# 「读不到覆盖层」的告警**每个进程只打一次**：热路径上每次决策都打一遍会把日志淹掉，
# 但第一次必须看得见（否则「表没迁移」会静默地退化成「开关点了没用」）。
_override_warned = False


def invalidate_switch_cache(project_id: Optional[str] = None) -> None:
    """清缓存。`project_id=None` 清全部（写入口与测试用）。"""
    if project_id is None:
        _override_cache.clear()
    else:
        _override_cache.pop(str(project_id), None)


def _read_override(project_id: str) -> Optional[dict]:
    """读这个项目的覆盖行（5 秒缓存）。**任何异常 → `None` + 留痕**（回落天花板）。

    返回 `{memory_enabled: bool|None, evolution_mode: str|None}`；没设过 → `None`。
    """
    key = str(project_id)
    now = time.monotonic()
    hit = _override_cache.get(key)
    if hit is not None and hit[0] > now:
        return hit[1]

    row: Optional[dict] = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT memory_enabled, evolution_mode FROM fin_memory_switch "
                    "WHERE project_id = %s", (key,))
                got = cur.fetchone()
            conn.rollback()          # 只读，显式回滚，不留 idle in transaction
        finally:
            conn.close()
        if got is not None:
            row = {SWITCH_MEMORY: got["memory_enabled"],
                   SWITCH_MODE: got["evolution_mode"]}
    except Exception as exc:                                     # noqa: BLE001
        # 表还没迁移 / 库不通 / 权限不足 —— 都落到「按没设过处理」。
        # 这里**不抛**：热路径（每次决策）不能因为一个附加开关读不到就整个 500。
        global _override_warned
        if not _override_warned:
            _override_warned = True
            logger.warning("[fin.switches] 读 fin_memory_switch 失败（{}），回落环境变量；"
                           "本进程后续不再重复这条告警：{}", type(exc).__name__, exc)
        else:
            logger.debug("[fin.switches] 读 fin_memory_switch 失败（{}），回落环境变量",
                         type(exc).__name__)

    _override_cache[key] = (now + SWITCH_CACHE_TTL_S, row)
    return row


def _jsonable_switch(item: dict) -> dict:
    """覆盖行 → 可 JSON 序列化的展示值（`None` 原样保留 = 未设置）。"""
    return {SWITCH_MEMORY: item.get(SWITCH_MEMORY), SWITCH_MODE: item.get(SWITCH_MODE)}


def selected_switch(project_id: Optional[str]) -> dict:
    """界面上**选的**那两项（未设置 = `None`）。给只读接口用。"""
    if not project_id:
        return {SWITCH_MEMORY: None, SWITCH_MODE: None}
    override = _read_override(project_id)
    return _jsonable_switch(override) if override else {SWITCH_MEMORY: None, SWITCH_MODE: None}


def can_change() -> dict:
    """这两项**能不能从界面改**（天花板说了算）。硬开关没有这一项 —— 它们不可改。"""
    return {
        SWITCH_MEMORY: ceiling_memory_enabled(),
        SWITCH_MODE: ceiling_evolution_mode() != "off",
    }


def set_project_switch(conn, project_id: str, *, switch_key: str, value: Any,
                       actor: Optional[str], reason: str) -> dict[str, Any]:
    """**R21 唯一写入口**：改一个项目的经验库开关。**同事务**写现值 + 追加流水。

    四条硬规矩（**写在这里，不靠界面自觉**）：

    1. **硬开关 key → `ValueError`**（路由翻 400）。它们连可改项都不在。
    2. **超天花板 → `ValueError`**，报错里写清天花板是多少、为什么。
    3. **`reason` 必填**；每次**真的改动**都追加一行流水（谁、何时、从什么到什么、为什么）。
    4. **没变就不写**（`changed=false`）—— 流水账记的是「改动」，不是「点了一下」。

    调用方（路由）负责：项目归属校验、把 `ValueError` 翻成 400。本函数**不查归属**。
    """
    import uuid as _uuid

    project_id = str(project_id or "").strip()
    if not project_id:
        raise ValueError("project_id 不能为空")
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError("reason 必填：每次改动都要写清「为什么改」")

    if switch_key in HARD_SWITCH_KEYS:
        hint = (AUTO_APPLY_MESSAGE if switch_key == "auto_apply" else LIVE_ORDER_MESSAGE)
        raise ValueError(f"{switch_key} 是硬开关，界面上没有入口：{hint}")
    if switch_key not in SWITCH_KEYS:
        raise ValueError(f"未知开关 {switch_key!r}（可改的只有 {'、'.join(SWITCH_KEYS)}）")

    if switch_key == SWITCH_MEMORY:
        if not isinstance(value, bool):
            raise ValueError("memory_enabled 必须是 true / false")
        if value and not ceiling_memory_enabled():
            raise ValueError(
                f"部署侧已锁死（{MEMORY_ENABLED_ENV}=0）：界面开不出经验库。"
                f"要开，得先让部署侧把 {MEMORY_ENABLED_ENV} 改成 1 并重建 api。")
        new_value: Any = value
    else:
        new_mode = str(value or "").strip().lower()
        if new_mode not in EVOLUTION_MODES:
            raise ValueError(f"evolution_mode 必须是 {' / '.join(EVOLUTION_MODES)}")
        ceiling = ceiling_evolution_mode()
        if MODE_ORDER[new_mode] > MODE_ORDER[ceiling]:
            raise ValueError(
                f"超过部署侧允许的上限（{EVOLUTION_MODE_ENV}={ceiling}）：界面最多选到 {ceiling}。")
        new_value = new_mode

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT memory_enabled, evolution_mode FROM fin_memory_switch "
                    "WHERE project_id = %s", (project_id,))
        row = cur.fetchone()
        cur_mem = row["memory_enabled"] if row else None
        cur_mode = row["evolution_mode"] if row else None
        old_value = cur_mem if switch_key == SWITCH_MEMORY else cur_mode
        changed = old_value != new_value

        if changed:
            new_mem = new_value if switch_key == SWITCH_MEMORY else cur_mem
            new_mode_db = new_value if switch_key == SWITCH_MODE else cur_mode
            cur.execute(
                """
                INSERT INTO fin_memory_switch
                    (project_id, memory_enabled, evolution_mode, updated_by, updated_at)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (project_id) DO UPDATE SET
                    memory_enabled = EXCLUDED.memory_enabled,
                    evolution_mode = EXCLUDED.evolution_mode,
                    updated_by     = EXCLUDED.updated_by,
                    updated_at     = now()
                """,
                (project_id, new_mem, new_mode_db, actor),
            )
            cur.execute(
                """
                INSERT INTO fin_memory_switch_log
                    (log_id, project_id, switch_key, old_value, new_value, actor, reason)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                ("fmsl_" + _uuid.uuid4().hex[:24], project_id, switch_key,
                 psycopg2.extras.Json(old_value) if old_value is not None else None,
                 psycopg2.extras.Json(new_value), actor, reason),
            )
    conn.commit()

    # 改完**立刻生效**：清掉缓存，下一次读就是新值（不等 5 秒）。
    invalidate_switch_cache(project_id)
    return {
        "project_id": project_id,
        "switch_key": switch_key,
        "old_value": old_value,
        "new_value": new_value,
        "changed": changed,
        "memory_enabled": memory_enabled(project_id),
        "evolution_mode": evolution_mode_requested(project_id),
        "effective": "立刻生效（写入口已清缓存）",
    }


# ════════════════════════════════════════════════════════════════════════
# 二 · `paper` 模式的前置依赖探针
# ════════════════════════════════════════════════════════════════════════
#
# `03 §2`：进入 paper 需要「模拟账本、市场日历、行情快照、策略决策器、验证调度」全健康。
# 每个依赖一个**真实读取**的探针（不打桩、不读缓存键）。任一不过 ⇒ 降级 observe。
#
# ⚠️ R4 只做「这些部件在不在、通不通」这一层；能力是否**足够**（比如影子臂算不算得出来）
#    是 R6–R8 的事。探针的语义逐条写在各自的 docstring 里，别把它当成更强的承诺。

# (key, 中文名) —— 顺序即降级原因里的出现顺序。
PAPER_DEPENDENCIES: tuple[tuple[str, str], ...] = (
    ("paper_ledger", "模拟账本"),
    ("market_calendar", "市场日历"),
    ("quote_snapshot", "行情快照"),
    ("strategy_decider", "策略决策器"),
    ("validation_schedule", "验证调度"),
)
_DEP_NAME = dict(PAPER_DEPENDENCIES)

_PROBE_TIMEOUT_S = 3.0
_SNAPSHOT_FRESH_DAYS = 90


def _get_conn():
    """探针专用的库连接。**自动提交**：一个探针失败不该毒化后面的探针。"""
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    return conn


def _probe_paper_ledger() -> tuple[bool, str]:
    """模拟账本：`paper` 的 `/healthz` 真的活着，且模式是 PAPER、账本库连通。

    地址取 `PAPER_BASE_URL`（与 compose 的 paper 段同源，默认 `http://paper:8000`）。
    **这不是新增基础设施** —— 只是让 api 能敲到本来就在跑的 paper。
    """
    base = (os.getenv("PAPER_BASE_URL") or "http://paper:8000").rstrip("/")
    try:
        resp = httpx.get(f"{base}/healthz", timeout=_PROBE_TIMEOUT_S)
        data = resp.json()
    except Exception as exc:                                    # noqa: BLE001
        return False, f"{base}/healthz 不可达（{type(exc).__name__}）"
    if not data.get("ok"):
        return False, f"{base}/healthz 报不健康（ledger_db={data.get('ledger_db')}）"
    if not data.get("ledger_db"):
        return False, "账本库不可用"
    if data.get("mode") != "PAPER":
        return False, f"模式不是 PAPER（{data.get('mode')!r}）"
    return True, ""


def _probe_market_calendar(cur) -> tuple[bool, str]:
    """市场日历：今天起**还有**已加载的交易日。

    日历空 → 验证窗口按哪个日历推进都推不动（`03 §4-C`「按对应市场交易日历推进」）。
    只要库里一条未来的交易日都没有，就判未就绪。
    """
    cur.execute(
        "SELECT count(*) AS n FROM fin_market_calendar "
        "WHERE trade_date >= CURRENT_DATE AND is_trading")
    n = int(cur.fetchone()["n"])
    if n <= 0:
        return False, "今天起没有任何已加载的交易日"
    return True, ""


def _probe_quote_snapshot(cur) -> tuple[bool, str]:
    """行情快照：近 90 天有落过 `fin_snapshot`。

    R4 只判「快照链路还在产出」。真正的两臂同快照口径在 R7。
    """
    cur.execute(
        "SELECT count(*) AS n FROM fin_snapshot "
        "WHERE created_at > now() - make_interval(days => %s)",
        (_SNAPSHOT_FRESH_DAYS,))
    n = int(cur.fetchone()["n"])
    if n <= 0:
        return False, f"近 {_SNAPSHOT_FRESH_DAYS} 天没有任何行情快照"
    return True, ""


def _probe_strategy_decider(cur) -> tuple[bool, str]:
    """策略决策器：至少有项目配了策略（`fin_param.strategies` 非空）。

    决策器本身在 `fin-worker`（api 侧读不到它的进程）；这里判的是**它的输入在不在** ——
    参数表一条策略都没有时，进 paper 也没有可跑的东西。
    """
    cur.execute(
        "SELECT count(*) AS n FROM fin_param WHERE jsonb_array_length(strategies) > 0")
    n = int(cur.fetchone()["n"])
    if n <= 0:
        return False, "没有任何项目配置了策略"
    return True, ""


def _probe_validation_schedule(cur) -> tuple[bool, str]:
    """验证调度：`fin_job` 里有时点 / 策略跑的记录（调度层真的在动）。

    取 `strategy_run` / `trading_point` 两类；一条都没有 ⇒ 调度没跑过。
    """
    cur.execute(
        "SELECT count(*) AS n FROM fin_job WHERE type IN ('strategy_run','trading_point')")
    n = int(cur.fetchone()["n"])
    if n <= 0:
        return False, "验证 / 时点调度没有任何执行记录"
    return True, ""


_DB_PROBES = (
    ("market_calendar", _probe_market_calendar),
    ("quote_snapshot", _probe_quote_snapshot),
    ("strategy_decider", _probe_strategy_decider),
    ("validation_schedule", _probe_validation_schedule),
)


def paper_dependency_checks(*, conn=None) -> list[dict[str, Any]]:
    """逐个跑探针，返回 `[{key, name, ok, detail}]`（顺序同 `PAPER_DEPENDENCIES`）。

    库整个连不上 ⇒ 四个库探针一律记未就绪并把原因写成「数据库不可用」，
    **不抛异常** —— 一个连不上库的环境本来就该降级 observe，而不是让接口 500。
    """
    results: dict[str, tuple[bool, str]] = {"paper_ledger": _probe_paper_ledger()}

    own = conn is None
    try:
        conn = conn or _get_conn()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            for key, probe in _DB_PROBES:
                try:
                    results[key] = probe(cur)
                except Exception as exc:                        # noqa: BLE001
                    results[key] = (False, f"读取失败（{type(exc).__name__}）")
    except Exception as exc:                                    # noqa: BLE001
        for key, _probe in _DB_PROBES:
            results.setdefault(key, (False, f"数据库不可用（{type(exc).__name__}）"))
    finally:
        if own and conn is not None:
            try:
                conn.close()
            except Exception:                                   # noqa: BLE001
                pass

    return [
        {
            "key": key,
            "name": _DEP_NAME[key],
            "ok": results.get(key, (False, "未评估"))[0],
            "detail": results.get(key, (False, "未评估"))[1],
        }
        for key, _name in PAPER_DEPENDENCIES
    ]


def paper_dependency_failures(*, conn=None) -> list[dict[str, Any]]:
    """未就绪的依赖（`ok=False` 的那些）。"""
    return [c for c in paper_dependency_checks(conn=conn) if not c["ok"]]


# ════════════════════════════════════════════════════════════════════════
# 三 · 运行时状态（只读接口的唯一取值来源）
# ════════════════════════════════════════════════════════════════════════

def runtime_state(*, project_id: Optional[str] = None, conn=None) -> dict[str, Any]:
    """`GET /v1/fin/runtime` 的返回体。**只读、无副作用。**

    `paper` 请求 + 依赖缺一 ⇒ `evolution_mode` 报 `observe`、`degraded_reason` 非空。
    请求的不是 `paper` 时**不跑探针**（前端每次轮询都打 5 个探针既慢又吵）。

    **`R21` 起多三个字段**（`ceiling` / `selected` / `can_change`）与 `project_id`：

    - **老字段一个没改**（前端已经在读；不带 `project_id` 时它们就是天花板值）；
    - 带了 `project_id` 时 `memory_enabled` / `evolution_mode*` 是**这个项目的生效值**
      （天花板 ∩ 天窗），于是「网页上改一下 → 这里立刻跟着变」。
    """
    requested = evolution_mode_requested(project_id)
    effective = requested
    reason: Optional[str] = None

    if requested == "paper":
        failures = paper_dependency_failures(conn=conn)
        if failures:
            effective = "observe"
            reason = "进入 paper 模式的前置依赖未就绪 → 已降级 observe：" + "；".join(
                f"{f['name']}未就绪（{f['detail']}）" for f in failures)

    return {
        "memory_enabled": memory_enabled(project_id),
        "evolution_mode": effective,
        "evolution_mode_requested": requested,
        "degraded_reason": reason,
        "auto_apply": auto_apply(),
        "live_order_enabled": live_order_enabled(),
        # ── R21 · 天花板 / 天窗 / 能不能改 ──
        "project_id": project_id,
        "ceiling": {
            SWITCH_MEMORY: ceiling_memory_enabled(),
            SWITCH_MODE: ceiling_evolution_mode(),
        },
        "selected": selected_switch(project_id),
        "can_change": can_change(),
    }
