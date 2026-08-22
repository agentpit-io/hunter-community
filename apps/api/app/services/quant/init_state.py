"""量化首次初始化的进度 —— 给前端看的。

## 为什么需要

定时任务只在每天 17:10 CST 触发,**启动时不跑**。一个用户上午从 GitHub
拉下来 `docker compose up`,打开策略工作台看到的是:股票池空的、点回测
提示"成分股还没有同步"。他要等到当天下午五点。

现在改成:启动时发现库是空的就立刻跑一遍。但这要十几分钟(实测 K 线
800 只 24 分钟),期间用户对着一个空界面完全不知道发生了什么 ——
所以要把进度说出来。

## 为什么放内存不放 Redis

这是**当前这次进程内**的初始化进度,重启就该重来。放 Redis 反而要处理
"上次没跑完的残留状态",而那个状态和实际数据是不是齐了没有关系 ——
真正的判据永远是查库(见 needs_init)。
"""
from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_state: dict = {
    "running": False,
    "done": False,
    "step": "",          # 当前在做什么
    "detail": "",        # 细到第几只
    "steps_total": 4,
    "steps_done": 0,
    "started_at": None,
    "finished_at": None,
    "error": "",
}


def snapshot() -> dict:
    with _lock:
        s = dict(_state)
    if s["started_at"] and not s["finished_at"]:
        s["elapsed_sec"] = int(time.time() - s["started_at"])
    elif s["started_at"] and s["finished_at"]:
        s["elapsed_sec"] = int(s["finished_at"] - s["started_at"])
    return s


def begin() -> bool:
    """标记开始。**已经在跑就返回 False** —— 两个初始化并发跑同一批数据
    只会互相拖慢,并且日志交织在一起没法看。"""
    with _lock:
        if _state["running"]:
            return False
        _state.update(running=True, done=False, step="准备中", detail="",
                      steps_done=0, started_at=time.time(),
                      finished_at=None, error="")
        return True


def step(name: str, done_count: int, detail: str = "") -> None:
    with _lock:
        _state.update(step=name, steps_done=done_count, detail=detail)


def detail(text: str) -> None:
    with _lock:
        _state["detail"] = text


def finish(error: str = "") -> None:
    with _lock:
        _state.update(running=False, done=not error, error=error,
                      finished_at=time.time(),
                      step="完成" if not error else "失败")


def needs_init() -> bool:
    """**查库,不查状态** —— 判据是"数据齐不齐",不是"跑没跑过"。

    跑过但失败了、跑了一半被杀了(实测发生过:重建容器把回填进程
    SIGKILL 了),这些情况下状态可能是"跑过",而数据依然是空的。
    """
    from app.services.database import get_conn
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("SELECT count(DISTINCT code) FROM factor_value")
        n_factor = cur.fetchone()[0] or 0
        cur.execute("SELECT count(DISTINCT code) FROM klines WHERE period='daily'")
        n_kline = cur.fetchone()[0] or 0
    except Exception:                                         # noqa: BLE001
        return False        # 表还没建好 · init_db 之后会再有机会
    finally:
        cur.close(); conn.close()
    # 少于 100 只就当作"没有数据" —— 不用 0 判,因为可能有零星几条
    # 手工插入的测试数据,而那不足以支撑选股
    return n_factor < 100 or n_kline < 100
