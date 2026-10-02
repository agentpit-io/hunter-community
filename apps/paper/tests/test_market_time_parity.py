"""`market_time.py` 在 paper 与 fin-worker **必须逐字节相同**。

两个服务各有各的构建上下文（`apps/paper/` / `apps/fin-worker/`），不能互相 import，
只能各留一份同实现。**漂了就红** —— 时区口径两处不一致，正是 M7「美股 event_time
时差 12 小时」那一类问题的温床（改一处忘另一处）。
"""

from __future__ import annotations

import hashlib
import os

_THIS = os.path.dirname(os.path.abspath(__file__))
_PAPER = os.path.join(_THIS, "..", "app", "market_time.py")
_FIN_WORKER = os.path.join(_THIS, "..", "..", "fin-worker", "app", "market_time.py")


def _digest(path: str) -> str:
    with open(os.path.normpath(path), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def test_market_time_identical_in_both_services():
    assert os.path.exists(os.path.normpath(_FIN_WORKER)), "找不到 fin-worker 的同名文件"
    assert _digest(_PAPER) == _digest(_FIN_WORKER), (
        "apps/paper/app/market_time.py 与 apps/fin-worker/app/market_time.py 不一致："
        "改一处必须同步改另一处（两份必须逐字节相同）"
    )
