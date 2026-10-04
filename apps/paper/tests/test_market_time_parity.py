"""`market_time.py` 在 paper / fin-worker / api **必须逐字节相同**。

三个服务各有各的构建上下文（`apps/paper/` / `apps/fin-worker/` / 仓库根），
不能互相 import，只能各留一份同实现。**漂了就红** —— 时区口径两处不一致，正是 M7
「美股 event_time 时差 12 小时」那一类问题的温床（改一处忘另一处）。

`L02` 把 api 侧也纳进来（原来 api 里散着十几处手写 `timezone(timedelta(hours=8))`，
统一封装没覆盖它），所以这里从「两份」扩到「三份」。
"""

from __future__ import annotations

import hashlib
import os

_THIS = os.path.dirname(os.path.abspath(__file__))
_PAPER = os.path.join(_THIS, "..", "app", "market_time.py")
_FIN_WORKER = os.path.join(_THIS, "..", "..", "fin-worker", "app", "market_time.py")
_API = os.path.join(_THIS, "..", "..", "api", "app", "services", "market_time.py")


def _digest(path: str) -> str:
    with open(os.path.normpath(path), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def test_market_time_identical_in_both_services():
    assert os.path.exists(os.path.normpath(_FIN_WORKER)), "找不到 fin-worker 的同名文件"
    assert _digest(_PAPER) == _digest(_FIN_WORKER), (
        "apps/paper/app/market_time.py 与 apps/fin-worker/app/market_time.py 不一致："
        "改一处必须同步改另一处（两份必须逐字节相同）"
    )


def test_market_time_identical_in_api_too():
    """L02：api 侧是第三份，同样必须逐字节相同。"""
    assert os.path.exists(os.path.normpath(_API)), "找不到 api 的 market_time.py"
    assert _digest(_PAPER) == _digest(_API), (
        "apps/api/app/services/market_time.py 与 apps/paper/app/market_time.py 不一致："
        "api 侧那份是同一事实的镜像，改一处必须同步改三处（含 fin-worker）"
    )
