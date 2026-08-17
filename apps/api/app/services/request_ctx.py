"""请求上下文 —— 当前用户 + 本次请求的数据来源记录。

**为什么需要它**:`finance_data_client` 里那十几个取数函数
(`get_quote` / `get_kline` / `get_news` …)都是**模块级同步函数,没有 user_id**。
而「优先用用户自己的数据源」这件事必须知道是谁在问。

两条路:
  ① 给十几个函数都加一个 `user_id` 参数,再改遍所有调用方
  ② 用 contextvar 把 user_id 挂在请求上,取数时就地读

选 ②。不是因为省事,是因为 ① 会**漏**:调用方散在工具层、SKILL 层、
深度分析、定时任务里,漏掉一处的表现是"这个功能不认用户的数据源",
而且不报错 —— 又是一次静默失败。contextvar 是一处设置、全链路可见。

contextvar 的代价是它**不跨线程自动传播**。`finance_data_client` 里有
`asyncio.run_coroutine_threadsafe` 那样的跳线程调用,那些地方要显式带过去。
`copy_ctx()` 就是给它们用的。
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass, field, asdict
from typing import Optional

# 当前请求的用户 —— 由 AuthMiddleware 设置。匿名/后台任务时为 None
_user_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "hunter_user_id", default=None
)

# 本次请求用了哪些数据源 —— 降级标注(`_21` §6.3)的数据来源
_provenance: contextvars.ContextVar[Optional[list]] = contextvars.ContextVar(
    "hunter_provenance", default=None
)


@dataclass
class SourceUse:
    """一次取数的出处记录。

    `tried` 里放的是**试过但没成功的**候选,附带原因。
    只记录最终用了谁是不够的 —— 用户最想知道的恰恰是
    "我配的那个为什么没用上"。
    """
    market: str
    kind: str
    used: str                    # "user:12" / "official" / "none"
    used_label: str              # "我的 Tushare" / "官方源"
    ok: bool
    tried: list = field(default_factory=list)   # [{"label":…, "reason":…}]
    ms: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def set_user(uid: Optional[str]) -> None:
    _user_id.set(uid)


def user_id() -> Optional[str]:
    return _user_id.get()


def begin_provenance() -> None:
    """开一个新的记录桶。每个 HTTP 请求一个。"""
    _provenance.set([])


def record(use: SourceUse) -> None:
    """记一笔。**桶不存在就丢弃,不自动创建** ——
    自动创建会让后台任务默默攒一个永远不被读取的列表。"""
    bucket = _provenance.get()
    if bucket is not None:
        bucket.append(use)


def drain() -> list[dict]:
    """取出本次请求的全部记录。"""
    bucket = _provenance.get()
    return [u.to_dict() for u in (bucket or [])]


def has_fallback() -> bool:
    """本次请求里有没有发生过「本想用用户的、结果走了官方的」。

    这是徽章该不该显示的判据 —— 全程走官方源(用户压根没配)
    不该弹徽章,那是正常状态,不是降级。
    """
    for u in (_provenance.get() or []):
        if u.used.startswith("official") and u.tried:
            return True
    return False
