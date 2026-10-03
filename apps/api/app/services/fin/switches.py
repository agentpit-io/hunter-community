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

**硬开关为什么拒绝请求而不是拒绝启动**

`03 §2` 允许「拒绝启动**或**拒绝请求」。这里选**拒绝请求**，因为要求里还有半句
「并在**日志与接口**里写明」——只有进程活着，接口才说得清「哪个开关被拒、为什么」。
拒绝启动会让这台机器只剩一条日志，运维得 `docker compose logs` 才知道发生了什么，
而接口返回的 503 里那句话，前端（R9）可以直接展示给用户。
启动时同样打一条 ERROR 日志（见 `main.py` 的 lifespan），两者都留痕。
"""

from __future__ import annotations

import os
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


def memory_enabled() -> bool:
    """经验库总开关。默认 **关**（`0`）。非法值 → 关 + 留痕。"""
    raw = _raw(MEMORY_ENABLED_ENV, MEMORY_ENABLED_DEFAULT)
    if raw == "1":
        return True
    if raw == "0":
        return False
    logger.warning("[fin.switches] {}={!r} 不是 0/1，按 fail-safe 取 0（经验库关闭）",
                   MEMORY_ENABLED_ENV, raw)
    return False


def evolution_mode_requested() -> str:
    """**请求**的进化模式（未做依赖校验）。默认 `off`。非法值 → `off` + 留痕。"""
    raw = _raw(EVOLUTION_MODE_ENV, EVOLUTION_MODE_DEFAULT).lower()
    if raw in EVOLUTION_MODES:
        return raw
    logger.warning("[fin.switches] {}={!r} 不是 off/observe/paper，按 fail-safe 取 off",
                   EVOLUTION_MODE_ENV, raw)
    return "off"


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

def runtime_state(*, conn=None) -> dict[str, Any]:
    """`GET /v1/fin/runtime` 的返回体。**只读、无副作用。**

    `paper` 请求 + 依赖缺一 ⇒ `evolution_mode` 报 `observe`、`degraded_reason` 非空。
    请求的不是 `paper` 时**不跑探针**（前端每次轮询都打 5 个探针既慢又吵）。
    """
    requested = evolution_mode_requested()
    effective = requested
    reason: Optional[str] = None

    if requested == "paper":
        failures = paper_dependency_failures(conn=conn)
        if failures:
            effective = "observe"
            reason = "进入 paper 模式的前置依赖未就绪 → 已降级 observe：" + "；".join(
                f"{f['name']}未就绪（{f['detail']}）" for f in failures)

    return {
        "memory_enabled": memory_enabled(),
        "evolution_mode": effective,
        "evolution_mode_requested": requested,
        "degraded_reason": reason,
        "auto_apply": auto_apply(),
        "live_order_enabled": live_order_enabled(),
    }
