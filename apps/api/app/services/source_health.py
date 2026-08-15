"""数据源健康观测 —— 以**被动**为主、主动探测为辅。

`_14` §4.4 / §6 Step B 第 2 项。

**为什么以被动为主**:主动探测(定时打一遍所有源)有三个问题 ——
  · 烧配额:每个源每分钟探一次,用户什么都没干就在消耗上游额度
  · 探到的不是用户关心的路径:探 `/quote/000001` 通,不代表用户查的那只票能出数
  · 探测本身要选参数,选错就误报
被动观测反过来:**每一次真实取数就是一次探测**,零额外开销,而且天然反映
用户实际用到的路径。代价是"从没调用过的源"没有数据 —— 那就如实标 `unknown`,
不假装健康,主动探测只用来补这个空(设置页的「测试全部」)。

**存哪**:滚动窗口在进程内存(`deque`),真实来源就是它;数据库表只存**快照**,
作用是容器重启后不至于全部回到 `unknown`。所以快照写入是节流的(默认 60 秒一次
一个源),不会因为观测把数据库打爆 —— 观测数据丢一点无所谓,它不是账单。

进程内存这个选择的前提是 api 单 worker(compose 里就是)。哪天上多 worker,
各 worker 的窗口会各算各的,那时再换共享存储(Redis)—— 现在上来就用 Redis
是给不存在的问题付成本。
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Any

from loguru import logger

# 每个源保留最近多少次调用 —— 50 次足够算出稳定的成功率,又不至于让
# 一次早已恢复的故障永远拉低分数。
WINDOW = 50

# 快照落库节流(秒)。观测不是账单,丢几条无所谓,别为它写数据库。
_SNAPSHOT_INTERVAL = 60.0

_lock = threading.Lock()
_ring: dict[str, deque] = defaultdict(lambda: deque(maxlen=WINDOW))
_last_snapshot: dict[str, float] = {}
_loaded = False

_DDL = """
CREATE TABLE IF NOT EXISTS source_health (
  source_key   VARCHAR(64) PRIMARY KEY,
  ok_count     INTEGER     NOT NULL DEFAULT 0,
  fail_count   INTEGER     NOT NULL DEFAULT 0,
  success_rate REAL,
  avg_ms       INTEGER,
  last_ok_at   TIMESTAMPTZ,
  last_fail_at TIMESTAMPTZ,
  last_error   TEXT        NOT NULL DEFAULT '',
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

_ddl_applied = False


def _ensure_table() -> bool:
    """建表。失败返回 False —— 健康观测坏掉**绝不能**影响取数主干。"""
    global _ddl_applied
    if _ddl_applied:
        return True
    try:
        from app.services.database import get_conn
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(_DDL)
        conn.commit()
        conn.close()
        _ddl_applied = True
        return True
    except Exception as e:
        logger.warning("[source_health] 建表失败(不影响取数): {}", e)
        return False


# ── 记录 ──────────────────────────────────────────────────────

def record(source_key: str, ok: bool, ms: float = 0.0, error: str = "") -> None:
    """记一次真实调用。**任何情况下都不许抛异常** —— 调用方在取数主干上。"""
    try:
        with _lock:
            _ring[source_key].append((bool(ok), float(ms), time.time(), error[:200]))
            due = time.time() - _last_snapshot.get(source_key, 0.0) >= _SNAPSHOT_INTERVAL
            if due:
                _last_snapshot[source_key] = time.time()
        if due:
            _write_snapshot(source_key)
    except Exception as e:      # pragma: no cover — 兜底,观测不该拖垮业务
        logger.debug("[source_health] record 失败 {}: {}", source_key, e)


class observe:
    """上下文管理器版本:`with observe("a.quote"): ...`

    正常退出记成功,抛异常记失败并**把异常继续抛出去**(不吞) —— 观测只观测,
    不改变调用方的错误处理。
    """

    __slots__ = ("key", "_t0")

    def __init__(self, source_key: str):
        self.key = source_key

    def __enter__(self):
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        ms = (time.perf_counter() - self._t0) * 1000
        if exc_type is None:
            record(self.key, True, ms)
        else:
            record(self.key, False, ms, f"{exc_type.__name__}: {exc}")
        return False        # 不吞异常


# ── 读取 ──────────────────────────────────────────────────────

def stats(source_key: str) -> dict[str, Any] | None:
    """滚动窗口统计。从没调用过返回 None(调用方据此显示 unknown,而不是 0%)。"""
    _load_snapshots_once()
    with _lock:
        buf = list(_ring.get(source_key) or ())
    if not buf:
        return None
    ok_n = sum(1 for b in buf if b[0])
    oks = [b for b in buf if b[0]]
    last_ok = max((b[2] for b in oks), default=None)
    fails = [b for b in buf if not b[0]]
    last_fail = max((b[2] for b in fails), default=None)
    return {
        "samples": len(buf),
        "ok_count": ok_n,
        "fail_count": len(buf) - ok_n,
        "success_rate": round(ok_n / len(buf), 3),
        "avg_ms": int(sum(b[1] for b in oks) / len(oks)) if oks else None,
        "last_ok_at": last_ok,
        "last_fail_at": last_fail,
        "last_error": (max(fails, key=lambda b: b[2])[3] if fails else ""),
    }


def health_of(source_key: str) -> str:
    """ok / degraded / down / unknown。

    阈值是拍的,但**分三档而不是两档**是有意的:数据源很少是非黑即白,
    偶尔超时一次不该标红让用户以为坏了,持续 70% 以下也不该标绿。
    """
    s = stats(source_key)
    if not s:
        return "unknown"
    r = s["success_rate"]
    if r >= 0.9:
        return "ok"
    if r >= 0.5:
        return "degraded"
    return "down"


def all_stats() -> dict[str, dict]:
    _load_snapshots_once()
    with _lock:
        keys = list(_ring.keys())
    return {k: s for k in keys if (s := stats(k))}


# ── 快照持久化 ────────────────────────────────────────────────

def _write_snapshot(source_key: str) -> None:
    s = stats(source_key)
    if not s or not _ensure_table():
        return
    try:
        from app.services.database import get_conn
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO source_health (source_key, ok_count, fail_count, success_rate,
                                       avg_ms, last_ok_at, last_fail_at, last_error, updated_at)
            VALUES (%s,%s,%s,%s,%s, to_timestamp(%s), to_timestamp(%s), %s, NOW())
            ON CONFLICT (source_key) DO UPDATE SET
              ok_count=EXCLUDED.ok_count, fail_count=EXCLUDED.fail_count,
              success_rate=EXCLUDED.success_rate, avg_ms=EXCLUDED.avg_ms,
              last_ok_at=EXCLUDED.last_ok_at, last_fail_at=EXCLUDED.last_fail_at,
              last_error=EXCLUDED.last_error, updated_at=NOW()
            """,
            (source_key, s["ok_count"], s["fail_count"], s["success_rate"], s["avg_ms"],
             s["last_ok_at"], s["last_fail_at"], s["last_error"]),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug("[source_health] 快照写入失败 {}: {}", source_key, e)


def _load_snapshots_once() -> None:
    """启动后第一次读取时,把上次的快照当作一个样本灌进窗口。

    这样重启后不会所有源都变 `unknown` —— 但**只灌一条**,新的真实调用会很快
    把它挤出去。不还原完整历史是故意的:陈旧的健康数据比没有更误导。
    """
    global _loaded
    if _loaded:
        return
    _loaded = True
    if not _ensure_table():
        return
    try:
        from app.services.database import get_conn
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT source_key, success_rate, avg_ms, last_error FROM source_health")
        rows = cur.fetchall()
        conn.close()
        now = time.time()
        with _lock:
            for key, rate, avg_ms, err in rows:
                if key in _ring and _ring[key]:
                    continue        # 进程里已有真实观测,不用旧快照覆盖
                _ring[key].append((bool(rate and rate >= 0.9), float(avg_ms or 0), now, err or ""))
        logger.info("[source_health] 载入 {} 条历史快照", len(rows))
    except Exception as e:
        logger.debug("[source_health] 载入快照失败: {}", e)
