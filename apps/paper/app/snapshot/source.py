"""行情来源：把「数据源」抽象成一个只回答「这只票此刻的报价是什么」的对象。

**复用，不重写**（任务书 §1）：`HttpQuoteSource` 打的是 `apps/api` 的
`GET /api/quote/{code}` —— 那条链路背后就是现仓的 `providers.data_source`
（`hunter` / `saas` / `akshare` / `yfinance`，见 `08 §五` 的「数据源抽象」一行）。
Paper Service 是**执行区**，不自己接行情源：接源要处理限速、WAF、多源回退，
那是 `apps/api` 已经做完的事（`CLAUDE.md` 里腾讯 WAF 那次事故就是教训）。
两个服务各自一个镜像，paper 的构建上下文是 `apps/paper/`，import 不到 `apps/api`，
所以这里走 HTTP 复用而不是 import 复用。

三条不能破的：

1. **`Quote.quote_time` 是数据源给的时刻**（`ts` 字段）。数据源没给 → `None`，
   上层不许拿本机时间补（`09 §六-6`）。
2. **拿不到就返回 `None`**，不返回"零价"或"上一次的价"。零价和真价长得一样，
   用户看不出来（`market_source.quote` 的头注也是这条）。
3. **默认是 `NullQuoteSource`**：`PAPER_QUOTE_URL` 没配就当行情没接通 ——
   断流走「不成交」分支，这是安全的一侧。配了才真去拉。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Protocol

from loguru import logger

CST = timezone(timedelta(hours=8))

# 拉一次行情的超时。论文里这是执行区里最不该久等的一步：超时 = 断流 = 不成交。
DEFAULT_TIMEOUT_S = 3.0


def _dec(value: Any) -> Optional[Decimal]:
    """把上游给的数（浮点 / 字符串 / None）转成 `Decimal`，转不了就 `None`。

    先 `str()` 再进 `Decimal`，**不经过浮点转换** —— 金额全程精确（`09 §六-8`；
    `tests/test_append_only.py` 在源码上扫这个转换函数名，注释里也别写它）。
    """
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError):
        return None


@dataclass(frozen=True)
class Quote:
    """一次报价。`quote_time is None` = **数据源没给时刻**（不是"我们没记"）。"""

    code: str
    source: str
    quote_time: Optional[datetime]
    last_price: Optional[Decimal] = None
    prev_close: Optional[Decimal] = None
    bid1_price: Optional[Decimal] = None
    bid1_volume: Optional[int] = None
    ask1_price: Optional[Decimal] = None
    ask1_volume: Optional[int] = None
    raw: dict = field(default_factory=dict)


class QuoteSource(Protocol):
    def fetch(self, code: str) -> Optional[Quote]:
        """拿不到（断流 / 超时 / 没有这只票）返回 `None`。**不抛异常给上层**。"""
        ...


class NullQuoteSource:
    """行情未接通的占位实现：永远拿不到。

    这是**默认**实现 —— `PAPER_QUOTE_URL` 没配时用它。行为是「一律断流」，
    于是撮合一律走「不成交」分支（挂单）。宁可挂单，也不拿过期价成交。
    """

    name = "null"

    def fetch(self, code: str) -> Optional[Quote]:  # noqa: ARG002
        return None


def _parse_quote_time(raw: str) -> Optional[datetime]:
    """把数据源的 `ts` 解析成带时区的 `datetime`。

    腾讯给 A 股的是 `20260827155755`（纯数字），给港美股的是
    `2026/08/27 16:03:00` 或 `2026-08-26 16:00:01`；`apps/api` 的
    `finance_data_client` 已经统一过一次，这里再兜一层（口径见
    `market_source._iso_ts`）。**解析不出来就返回 None** —— 不猜一个时刻。
    """
    text = (raw or "").strip().replace("/", "-")
    if not text:
        return None
    # 依次试：纯数字 → 带秒 → 到分 → 只有日期。全部失败就返回 None（不猜）。
    for fmt in ("%Y%m%d%H%M%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            naive = datetime.strptime(text, fmt)
        except ValueError:
            continue
        # 数据源给的是**市场本地时间**（A 股 = 上海时间，无夏令时）。
        return naive.replace(tzinfo=CST)
    return None


class HttpQuoteSource:
    """打 `apps/api` 的 `GET {base}/api/quote/{code}`。

    那个路径在 `app/middleware/auth.py` 的 `_PUBLIC_PREFIXES` 里（`/api/quote/`），
    免登录可访问 —— paper 不需要用户 token 就能取快照。
    """

    name = "http"

    def __init__(self, base_url: str, timeout: float = DEFAULT_TIMEOUT_S):
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    def fetch(self, code: str) -> Optional[Quote]:
        url = f"{self._base}/api/quote/{code}"
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
            logger.warning("[paper.snapshot] 行情请求失败 {} · {}", code, e)
            return None
        except (ValueError, json.JSONDecodeError) as e:
            logger.warning("[paper.snapshot] 行情响应无法解析 {} · {}", code, e)
            return None

        if not isinstance(payload, dict):
            return None
        # `apps/api` 取不到时也可能回一个空体 / 只有 error 的对象。
        if payload.get("price") is None and payload.get("last_price") is None:
            logger.info("[paper.snapshot] 行情无价 {} · {}", code, payload.get("error"))
            return None

        return Quote(
            code=payload.get("code") or code,
            source=(payload.get("source") or self.name),
            quote_time=_parse_quote_time(payload.get("ts") or payload.get("updated_at") or ""),
            last_price=_dec(payload.get("price", payload.get("last_price"))),
            prev_close=_dec(payload.get("prev_close", payload.get("pre_close"))),
            bid1_price=_dec(payload.get("bid1", payload.get("bid1_price"))),
            bid1_volume=_int(payload.get("bid1v", payload.get("bid1_volume"))),
            ask1_price=_dec(payload.get("ask1", payload.get("ask1_price"))),
            ask1_volume=_int(payload.get("ask1v", payload.get("ask1_volume"))),
            raw=payload,
        )


# ── 进程级单例（env 决定；测试可以 set_source 换掉）─────────────────────

_source: Optional[QuoteSource] = None


def _build_default() -> QuoteSource:
    url = (os.getenv("PAPER_QUOTE_URL") or "").strip()
    if not url:
        logger.info("[paper.snapshot] 未配置 PAPER_QUOTE_URL，行情按「未接通」处理（一律不成交）")
        return NullQuoteSource()
    logger.info("[paper.snapshot] 行情来源 {}", url)
    return HttpQuoteSource(url)


def get_source() -> QuoteSource:
    global _source
    if _source is None:
        _source = _build_default()
    return _source


def set_source(src: Optional[QuoteSource]) -> None:
    """替换行情来源。测试用；也可以用来在运行期把某个市场切成别的实现。"""
    global _source
    _source = src
