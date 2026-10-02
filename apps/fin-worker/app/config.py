"""fin-worker 配置。

**这里没有数据库连接串，而且不许加。** fin-worker 是「推理区 → 执行区」之间的
Runtime Bridge（`01方案 §5.1`）：它把策略意图翻译成 Paper Service 命令，自己
**不碰账本**。账本库（`hunter_fin` / 应用库里的 `fin_*` 表）只有一个写入口 ——
`paper` 的 HTTP 接口（`01方案 §7.4` / §11.3、`08 §二`）。

`tests/test_no_ledger_access.py` 有一条源码守卫盯着这件事：本模块与整个
`app/` 里不许出现 DB 驱动 import、不许出现 `DATABASE_URL` 之类的读取。
「不直连账本」不是一句注释，是一条能被 grep 出来的事实。
"""

from __future__ import annotations

import os


# ── Temporal ──────────────────────────────────────────────────────────────
def temporal_address() -> str:
    return (os.getenv("TEMPORAL_ADDRESS") or "temporal:7233").strip()


def temporal_namespace() -> str:
    return (os.getenv("TEMPORAL_NAMESPACE") or "default").strip()


def temporal_task_queue() -> str:
    return (os.getenv("TEMPORAL_TASK_QUEUE") or "fin-trading").strip()


# ── 执行区（只有 HTTP）────────────────────────────────────────────────────
def paper_base_url() -> str:
    """唯一能改账本的服务。fin-worker 的全部写操作都打到这里。"""
    return (os.getenv("PAPER_BASE_URL") or "http://paper:8000").rstrip("/")


def api_base_url() -> str:
    """hunter-community api。用于「谁决定什么时候拉数据」里的取数触发与交易日历。"""
    return (os.getenv("API_BASE_URL") or "http://api:8000").rstrip("/")


def internal_key() -> str:
    return (os.getenv("HUNTER_INTERNAL_KEY") or "").strip()


# ── 内部 HTTP 端点（给 api / 运维触发）────────────────────────────────────
def http_port() -> int:
    try:
        return int(os.getenv("FIN_WORKER_HTTP_PORT") or "8300")
    except ValueError:
        return 8300


# ── 一期固定示例策略（管道跑通优先，策略做强是后面的事，`08 §二`）────────
def sample_code() -> str:
    # 默认 601398（工商银行）：低价 + 高流动性，三个档位（1 万 / 10 万 / 100 万）
    # 的示例单都买得起，管道能在任何档位跑通。这是**固定示例**，不是选股结论。
    return (os.getenv("FIN_SAMPLE_CODE") or "601398").strip()


def sample_strategy_key() -> str:
    return (os.getenv("FIN_SAMPLE_STRATEGY_KEY") or "sample-fixed").strip()


def sample_strategy_version() -> str:
    return (os.getenv("FIN_SAMPLE_STRATEGY_VERSION") or "1.0").strip()


# ── 调度 ──────────────────────────────────────────────────────────────────
def schedules_enabled() -> bool:
    """默认开。关掉只用于「手工触发工作流」的排障场景，生产保持开。"""
    return (os.getenv("FIN_SCHEDULES_ENABLED") or "1").strip() not in ("0", "false", "False")


def schedule_timezone() -> str:
    return (os.getenv("FIN_SCHEDULE_TZ") or "Asia/Shanghai").strip()


def activity_workers() -> int:
    """同步 Activity 的线程池大小。并发时点很少（六个时点 × 活跃项目数），8 足够。"""
    try:
        return max(1, int(os.getenv("FIN_WORKER_ACTIVITY_WORKERS") or "8"))
    except ValueError:
        return 8


def contract_timeout_seconds() -> int:
    """策略意图有效期（秒）。过了这个点，意图不再补单（`01方案 §11.2` 信号过期）。"""
    try:
        return int(os.getenv("FIN_INTENT_TTL_SECONDS") or "1800")
    except ValueError:
        return 1800
