"""行情来源：把「数据源」抽象成一个只回答「这只票此刻的报价是什么」的对象。

这是 `01方案 §6.1` 说的那条**接口边界**：`paper`（执行区）取行情**只经这一个适配层**，
将来换 QFinZero 是**替换实现**，不是改账本 —— 账本、撮合、风控一行不动。

**复用，不重写**（任务书 §1）：`HttpQuoteSource` 打的是 `apps/api` 的内部行情端点
`GET /api/internal/fin/quote/{code}`（`app/services/fin_data.py`）—— 那条链路背后是
现仓的 `providers.data_source` / 免费通道（`08 §五` 的「数据源抽象」一行）。
Paper Service 是**执行区**，不自己接行情源：接源要处理限速、WAF、多源回退，
那是 `apps/api` 已经做完的事（`CLAUDE.md` 里腾讯 WAF 那次事故就是教训）。
两个服务各自一个镜像，paper 的构建上下文是 `apps/paper/`，import 不到 `apps/api`，
所以这里走 HTTP 复用而不是 import 复用。

兼容：老的 `/api/quote/{code}` 仍然被尝试（`PAPER_QUOTE_LEGACY=1`，默认开）。
内部端点不可用（404 / 401）时自动退到它 —— M4 的假行情服务就是那个形状。

三条不能破的：

1. **`Quote.quote_time` 是数据源给的时刻**（`event_time` / `ts`）。数据源没给 → `None`，
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
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Protocol
from zoneinfo import ZoneInfo

from loguru import logger

from app.market_time import UnknownMarket, canonical_market, market_of

# 裸格式（无偏移）时间戳的兜底时区 = A 股 / 港股共同的 +08:00（两地均无夏令时）。
# **美股不走这里**：统一结构给美股的 `event_time` 已带 `-04:00` / `-05:00` 偏移。
# 用 IANA 时区名而不是固定偏移（N2）：镜像必须装 tzdata（见 Dockerfile）。
CST = ZoneInfo("Asia/Shanghai")

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
    """一次报价。`quote_time is None` = **数据源没给时刻**（不是"我们没记"）。

    `market` / `quote_quality` 由 `apps/api` 的统一行情结构带上来（N3）——
    `market` 是本仓统一三值（`CN_A` / `HK` / `US`），**快照归属哪个市场全靠它**，
    以前它在这个边界被丢掉了。`quote_quality` 见设计文档附 B
    （`full` 有盘口 / `last_only` 只有最新价 / `no_book` 无盘口按最新价撮合）。
    两个都可为 `None`：老端点（M4 的假行情服务）不给，`capture` 会按代码形态补市场。
    """

    code: str
    source: str
    quote_time: Optional[datetime]
    market: Optional[str] = None
    quote_quality: Optional[str] = None
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
    """把数据源的时刻解析成带时区的 `datetime`。

    统一结构里 `event_time` 已经是**带偏移的 ISO 8601**（`apps/api` 按市场贴的时区，
    见 `fin_data._market_tz`）—— 直接 `fromisoformat` 就拿到正确的时区，
    **不能再一律贴 +08:00**：美股 12:04 ET 贴成 12:04 CST 差 12 小时且不报错。

    老的 `/api/quote/{code}`（兼容路径）给的是不加偏移的字符串（腾讯 A 股
    `20260830161458`、港美股 `2026/10/02 16:08:10`），那种按 CST 兜一层
    （口径同 `market_source._iso_ts`）。**解析不出来就返回 None** —— 不猜一个时刻。
    """
    text = (raw or "").strip()
    if not text:
        return None
    # 先试带偏移的 ISO（统一结构的形状）
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=CST)
    except ValueError:
        pass
    # 再试数据源的裸格式（A 股纯数字 / 港美股横杠或斜杠）
    text = text.replace("/", "-")
    for fmt in ("%Y%m%d%H%M%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            naive = datetime.strptime(text, fmt)
        except ValueError:
            continue
        # 裸格式只出现在 A 股 / 港股（都是 +08:00）；美股那条路统一结构里已带偏移。
        return naive.replace(tzinfo=CST)
    return None


# 内部行情端点的路径（`apps/api/app/routers/fin_data.py`）。可由 env 覆盖，
# 便于对着别的实现（将来换 QFinZero）做灰度。
DEFAULT_QUOTE_PATH = "/api/internal/fin/quote/{code}"
# 兼容路径：M4 的假行情服务与老的公开端点走这个形状。
LEGACY_QUOTE_PATH = "/api/quote/{code}"


class HttpQuoteSource:
    """打 `apps/api` 的行情端点。

    顺序：**内部端点**（`/api/internal/fin/quote/{code}`，带内网口令，回答任意代码）
    → **兼容端点**（`/api/quote/{code}`，免登录，M4 的假行情服务用这个形状）。

    为什么不是只用公开那条：`/api/quote/{code}` 先查用户的股票表，代码不在表里就
    404 —— 它是给「用户看自己的自选」用的，记账服务要的是任意合法代码的报价。
    """

    name = "http"

    def __init__(self, base_url: str, timeout: float = DEFAULT_TIMEOUT_S,
                 internal_key: Optional[str] = None, quote_path: Optional[str] = None):
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._key = internal_key if internal_key is not None else _internal_key()
        self._path = (quote_path or os.getenv("PAPER_QUOTE_PATH") or DEFAULT_QUOTE_PATH).strip()

    def _get(self, path: str) -> Optional[dict]:
        url = f"{self._base}{path}"
        headers = {"Accept": "application/json"}
        if self._key:
            headers["X-Hunter-Internal-Key"] = self._key
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # 404 = 这只票没有报价（正常的「没有」）；401 = 内部端点没认我们 → 走兼容路径。
            logger.info("[paper.snapshot] 行情端点 {} 返回 HTTP {} · {}", path, e.code, code_hint(path))
            return None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            logger.warning("[paper.snapshot] 行情请求失败 {} · {}", path, e)
            return None
        except (ValueError, json.JSONDecodeError) as e:
            logger.warning("[paper.snapshot] 行情响应无法解析 {} · {}", path, e)
            return None

    def fetch(self, code: str) -> Optional[Quote]:
        payload = self._get(self._path.format(code=code))
        if not _has_price(payload) and self._path != LEGACY_QUOTE_PATH:
            payload = self._get(LEGACY_QUOTE_PATH.format(code=code))
        if not _has_price(payload):
            logger.info("[paper.snapshot] 行情无价 {} · {}", code,
                        (payload or {}).get("detail") or (payload or {}).get("error"))
            return None

        bid1 = _dec(payload.get("bid1_price", payload.get("bid1")))
        ask1 = _dec(payload.get("ask1_price", payload.get("ask1")))
        return Quote(
            code=payload.get("code") or code,
            source=(payload.get("source") or self.name),
            quote_time=_parse_quote_time(
                payload.get("event_time") or payload.get("ts") or payload.get("updated_at") or ""
            ),
            # market / quote_quality 逐段透传（N3）：api 的统一结构给了就用它，
            # 老端点（假行情服务）没给 → 按代码形态判市场、按盘口判质量。
            market=_market_of_payload(payload, code),
            quote_quality=_quality_of_payload(payload, bid1, ask1),
            last_price=_dec(payload.get("last_price", payload.get("price"))),
            prev_close=_dec(payload.get("prev_close", payload.get("pre_close"))),
            bid1_price=bid1,
            bid1_volume=_int(payload.get("bid1_volume", payload.get("bid1v"))),
            ask1_price=ask1,
            ask1_volume=_int(payload.get("ask1_volume", payload.get("ask1v"))),
            raw=payload,
        )


def _market_of_payload(payload: dict, code: str) -> Optional[str]:
    """行情返回体里的市场 → 本仓统一三值。**透传优先，判不出按代码形态**。"""
    raw = (payload.get("market") or "").strip()
    if raw:
        try:
            return canonical_market(raw)      # CN_A / HK / US（含 a / cn / A 等写法）
        except UnknownMarket:
            logger.warning("[paper.snapshot] 行情给了不认识的市场 {!r}，按代码形态判", raw)
    return market_of(code)


# 与 `apps/api.fin_data.QUOTE_QUALITIES` 同一组值（两边各自留一份常量，
# 口径来自设计文档附 B，别各改各的）。
_QUOTE_QUALITIES = ("full", "last_only", "no_book")


def _quality_of_payload(payload: dict, bid1, ask1) -> str:
    """快照质量。**以上游给的 `quote_quality` 为准**，没给就按盘口推。"""
    raw = (payload.get("quote_quality") or "").strip()
    if raw in _QUOTE_QUALITIES:
        return raw
    return "full" if (bid1 is not None and ask1 is not None) else "last_only"


def _has_price(payload: Optional[dict]) -> bool:
    if not isinstance(payload, dict):
        return False
    for key in ("last_price", "price"):
        if payload.get(key) is not None:
            return True
    return False


def code_hint(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _internal_key() -> str:
    """内网口令。**只读 env**，不 import `app.config`（避免把配置模块拖进纯函数测试）。"""
    return (os.getenv("HUNTER_INTERNAL_KEY") or "").strip()


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
