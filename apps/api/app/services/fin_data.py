"""智能炒股 · 统一行情适配层（Data Service 的边界，`01方案 §6.1`）。

```text
原始行情来源（官方链路 / 免费通道）
    → 本模块：采集、清洗、来源记录、时间对齐
    → 统一金融数据结构（一个 dict，字段固定）
    → paper 的行情适配层（apps/paper/app/snapshot/source.py）
    → 不可变 DataSnapshot（fin_snapshot）
```

**为什么要有这一层。** 论文 §6.1 要求「QFinZero 作为可替换实现，不成为永久接口标准；
自有 Data Service 负责稳定业务含义、字段和版本」。落地的做法就是这里：
`paper`（执行区）只认这里返回的那**一个结构**，换数据源 = 换下面的 provider 链，
账本与撮合一行不动。

## 时间字段（`01方案 §6.2` 的子集）

| 返回字段 | 含义 |
|---|---|
| `event_time` | **数据源给的**行情时刻（市场本地时间，带 +08:00）—— 快照编号只能用它 |
| `ingested_at` | 我们拿到这条数据的时刻（UTC）—— 只用于判断新鲜度，**不许拿去当 event_time** |

数据源没给时刻 → `event_time = None`，上层必须按「不落快照 / 不成交」处理
（`09 §六-6`：代码里禁止用 `now()` 填 `fin_snapshot.snapshot_time`）。

## provider 链（顺序即优先级，逐级降级并如实记录 `source`）

1. `official` —— 现仓官方链路 `finance_data_client.get_quote`
   （用户源 → SaaS → `providers.data_source`）。没配 key 时 A 股会抛
   `HunterKeyRequired`，这里**捕获并降级**，不让它把整条链打断。
2. `tencent-qt` —— 腾讯 `qt.gtimg.cn` 免费通道（`market_source` 用的是同一个域名，
   港美股已经在用它）。**A 股也走这里**：本机实测官方链路无 key、akshare/yfinance
   在本机不可用（见 M7 报告），而这条通道 A 股给得出**真实 Level-1**
   （最新价 + 买一/卖一价量）。`market_source` 里 A 股被显式关掉（「别抢原路径」），
   所以本模块**另起一个只读解析器**，不去改 `market_source` 的行为。

`market_source.quote` 对 A 股返回 `None`（设计如此），港美股仍优先用它。

## 买一 / 卖一盘口

腾讯 A 股报文里 `f[9]/f[10]` 是买一价/量、`f[19]/f[20]` 是卖一价/量（本机实测
sh601398 → 8.27/9562、8.28/11922）。**港美股这条通道不给盘口**（实测 hk00700 /
usAAPL 的 f[9]..f[20] 是 0 或与最新价相同），那种情况返回 `None` ——
不给盘口就说没有，**不拿最新价冒充买一**。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from loguru import logger

CST = timezone(timedelta(hours=8))
UTC = timezone.utc

# 免费通道超时。执行区最不该久等：拿不到就是不成交，不是等下去。
QT_TIMEOUT_S = 5.0
_QT = "https://qt.gtimg.cn/q="
_UA = {"User-Agent": "Mozilla/5.0"}

# 板块 → 涨跌停幅度。口径写死在这里、不猜（`总控规则 §八`）。
#   · 沪深主板 ±10%、创业板/科创板 ±20%、北交所 ±30%。
#   · ST 自 2025-07-07 起沪深主板也是 ±10%（CLAUDE.md「涨停后强势整理」一节的实测），
#     所以 `is_st` 照实记下来供风控与展示用，**不再收窄幅度**。
BOARD_LIMIT = {"main": "0.10", "chinext": "0.20", "star": "0.20", "bse": "0.30"}
_BOARD_LOT = 100


# ── 市场 / 代码形态 ────────────────────────────────────────────────────────

def market_of(code: str) -> str:
    """`a` / `hk` / `us` —— 与 `market_source.market_of` 同口径（5 位 = 港股）。"""
    s = (code or "").strip().upper()
    if not s:
        return "a"
    if s.endswith(".HK"):
        return "hk"
    if s.endswith(".US"):
        return "us"
    bare = s.split(".")[0]
    if bare.isdigit():
        return "hk" if len(bare) == 5 else "a"
    return "us"


def _qt_symbol(code: str) -> Optional[str]:
    """腾讯报价用的代码：`sh601398` / `hk00700` / `usAAPL`。"""
    market = market_of(code)
    bare = code.split(".")[0].strip().upper()
    if market == "hk":
        return "hk" + bare.zfill(5)
    if market == "us":
        return "us" + bare
    # A 股：必须有 6 位；前缀按板块规则给。
    if not (len(bare) == 6 and bare.isdigit()):
        return None
    if bare[0] == "6":
        return "sh" + bare
    if bare[0] in ("0", "3"):
        return "sz" + bare
    if bare[0] in ("4", "8"):
        return "bj" + bare
    return None


def board_of(code: str) -> tuple[Optional[str], Optional[str]]:
    """由代码形态判 `(exchange, board)`。判不出 → `(None, None)`。

    **判不出就不判** —— 上层据此拒绝该标的，而不是猜一个板块再猜一个幅度。
    """
    bare = (code or "").split(".")[0].strip()
    if not (len(bare) == 6 and bare.isdigit()):
        return None, None
    if bare.startswith(("688", "689")):
        return "SH", "star"
    if bare.startswith("6"):
        return "SH", "main"
    if bare.startswith(("300", "301")):
        return "SZ", "chinext"
    if bare.startswith(("000", "001", "002", "003")):
        return "SZ", "main"
    if bare.startswith(("4", "8")):
        return "BJ", "bse"
    return None, None


# ── 时间对齐 ───────────────────────────────────────────────────────────────

_TS_FORMATS = ("%Y%m%d%H%M%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")


def parse_event_time(raw: Any, market: str = "a") -> Optional[datetime]:
    """把数据源给的时刻解析成**带时区**的 `datetime`。解析不出 → `None`。

    三种格式都见过（`market_source._iso_ts` 的注释里有实测）：
    A 股 `20260930161458`、港股 `2026/10/02 16:08:10`、美股 `2026-10-02 12:03:20`。

    **时区按市场给**：数据源给的是**该市场的本地时间**。A 股 / 港股是 +08:00，
    美股是纽约时间（EDT/EST）。第一版一律贴 +08:00，于是美股 12:04 ET 被记成
    12:04 CST —— 差 12 小时，`snapshot_id` 里的时刻和新鲜度判定全跟着错，
    而且**不报错**（时间戳看着完全正常）。`01方案 §6.2` 的「时间对齐」就是这件事。

    **解析不出来就返回 None** —— 不拿本机时间顶上（`09 §六-6`）。
    """
    text = str(raw or "").strip().replace("/", "-")
    if not text:
        return None
    tz = _market_tz(market)
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=tz)
        except ValueError:
            continue
    return None


def _market_tz(market: str):
    if market == "us":
        try:
            from zoneinfo import ZoneInfo

            return ZoneInfo("America/New_York")
        except Exception:  # noqa: BLE001 —— 缺 tzdata 时退回 EST（不静默当 +08:00）
            return timezone(timedelta(hours=-5))
    return CST


def _dec(value: Any) -> Optional[str]:
    """报价数字统一成**字符串**（下游用 Decimal 收，**不经浮点**）。

    金额与价格的搬运一律 `str → Decimal → str`：走一遍 `float` 会把
    `8.2800` 变成 `8.28` 甚至科学计数法，账本侧再 `Decimal(str(...))` 就拿到了
    一个不是数据源给的值（`09 §六-8`：金额不用浮点）。
    """
    if value in (None, "", "-"):
        return None
    try:
        dec = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None
    # 0 在盘口里表示「这一档没有」——不返回 "0"，返回 None。
    if dec == 0:
        return None
    return format(dec.normalize(), "f")


def _int(value: Any) -> Optional[int]:
    if value in (None, "", "-"):
        return None
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return None
    return n or None


# ── provider 1 · 官方链路 ─────────────────────────────────────────────────

def _official_quote(code: str) -> Optional[dict]:
    """现仓官方链路。**任何异常都降级**（含 `HunterKeyRequired`），不往上抛。"""
    try:
        from app.services import finance_data_client as fdc

        q = fdc.get_quote(code)
    except Exception as exc:  # noqa: BLE001 —— 降级路径必须吞掉一切
        logger.info("[fin_data] 官方链路不可用 {} · {}", code, exc.__class__.__name__)
        return None
    if not q or q.get("price") in (None, 0, "0"):
        return None
    return {
        "name": q.get("name"),
        "last_price": _dec(q.get("price")),
        "prev_close": _dec(q.get("prev_close")),
        "open": _dec(q.get("open")),
        "high": _dec(q.get("high")),
        "low": _dec(q.get("low")),
        "bid1_price": _dec(q.get("bid1")),
        "bid1_volume": _int(q.get("bid1v")),
        "ask1_price": _dec(q.get("ask1")),
        "ask1_volume": _int(q.get("ask1v")),
        "event_time": parse_event_time(q.get("ts"), market_of(code)),
        "source": "official",
    }


# ── provider 2 · 腾讯免费通道（含 A 股）────────────────────────────────────

def _tencent_quote(code: str) -> Optional[dict]:
    """腾讯 `qt.gtimg.cn`。拿不到 / 解析不出 → `None`（不返回 0 价）。"""
    symbol = _qt_symbol(code)
    if not symbol:
        return None
    try:
        import requests

        resp = requests.get(_QT + symbol, headers=_UA, timeout=QT_TIMEOUT_S)
        # 腾讯这个接口是 GBK（按 UTF-8 解会把中文名变成乱码且**不报错**）
        text = resp.content.decode("gbk", "ignore")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[fin_data] 腾讯免费通道失败 {} · {}", code, exc)
        return None

    if '"' not in text:
        return None
    fields = text.split('"')[1].split("~")
    if len(fields) < 6:
        return None

    def f(i: int) -> Optional[str]:
        return fields[i] if i < len(fields) else None

    last = _dec(f(3))
    if last is None:                      # 价格 0 或没有 → 这个代码拿不到
        return None

    market = market_of(code)
    # 盘口：只有 A 股这条通道给得出（实测港美股是 0）。给不出就 None，不冒充。
    if market == "a":
        bid1, bid1v = _dec(f(9)), _int(f(10))
        ask1, ask1v = _dec(f(19)), _int(f(20))
    else:
        bid1 = bid1v = ask1 = ask1v = None

    return {
        "name": f(1),
        "last_price": last,
        "prev_close": _dec(f(4)),
        "open": _dec(f(5)),
        "high": _dec(f(33)),
        "low": _dec(f(34)),
        "bid1_price": bid1,
        "bid1_volume": bid1v,
        "ask1_price": ask1,
        "ask1_volume": ask1v,
        "event_time": parse_event_time(f(30), market),
        "source": "tencent-qt",
    }


# ── 统一入口 ───────────────────────────────────────────────────────────────

def quote(code: str) -> Optional[dict]:
    """统一金融数据结构。**两个 provider 都拿不到 → `None`**（不返回零价）。

    返回体（字段固定，换数据源不改字段）：
    `code / name / market / last_price / prev_close / open / high / low /
     bid1_price / bid1_volume / ask1_price / ask1_volume /
     event_time / ingested_at / source / orderbook / gaps`
    """
    now = datetime.now(UTC)
    gaps: list[str] = []

    hit = _official_quote(code)
    if hit is None:
        gaps.append("official: 官方链路无数据（未配置 key / 上游不可用）")
        hit = _tencent_quote(code)
    if hit is None:
        return None

    if hit.get("event_time") is None:
        # 数据源没给时刻。**仍然返回**，让上层按「不落快照」处理并留下缺口记录 ——
        # 直接返回 None 会让「行情还在、只是没时间戳」和「完全没有行情」混成一种。
        gaps.append("event_time: 数据源没有给出行情时刻")

    if market_of(code) == "a" and hit.get("bid1_price") is None:
        gaps.append("orderbook: 该来源没有买一/卖一盘口")

    return {
        "code": code,
        "name": hit.get("name"),
        "market": market_of(code).upper(),
        "last_price": hit.get("last_price"),
        "prev_close": hit.get("prev_close"),
        "open": hit.get("open"),
        "high": hit.get("high"),
        "low": hit.get("low"),
        "bid1_price": hit.get("bid1_price"),
        "bid1_volume": hit.get("bid1_volume"),
        "ask1_price": hit.get("ask1_price"),
        "ask1_volume": hit.get("ask1_volume"),
        "event_time": hit["event_time"].isoformat() if hit.get("event_time") else None,
        "ingested_at": now.isoformat(),
        "source": hit.get("source"),
        "orderbook": hit.get("bid1_price") is not None and hit.get("ask1_price") is not None,
        "gaps": gaps,
    }


# ── 标的元数据（fin_instrument 的同步素材）────────────────────────────────

def instrument(code: str, *, name: Optional[str] = None,
               board: Optional[str] = None) -> dict:
    """把「某个 A 股代码的涨跌停 / ST 元数据」解析出来。

    `company_master` / `stock_universe` 是**跨库同步源**（`09 §八`）——它们提供
    名称与板块；板块也可以由代码形态推出来（公开规则，不是猜）。**两处都判不出
    → `available=False`**，调用方（fin-worker）据此**不写 `fin_instrument`**，
    于是风控第 4 条会拒绝这个标的 —— 绝不猜一个涨跌幅写进账本。
    """
    bare = (code or "").split(".")[0].strip()
    derived_exchange, derived_board = board_of(bare)

    resolved_board = (board or "").strip() or derived_board
    # 把 company_master 里的中文板块名归一到 fin_instrument 的枚举
    resolved_board = _normalize_board(resolved_board)

    if not derived_exchange or not resolved_board:
        return {
            "code": bare,
            "available": False,
            "reason": ("无法确定交易所或板块（company_master / stock_universe 无记录，"
                       "代码形态也判不出）——拒绝该标的，不猜涨跌停幅度"),
        }
    if resolved_board not in BOARD_LIMIT:
        return {
            "code": bare,
            "available": False,
            "reason": f"未知板块 {resolved_board!r}——拒绝该标的，不猜涨跌停幅度",
        }

    is_st = bool(re.search(r"ST|退", (name or ""), re.IGNORECASE))
    limit = BOARD_LIMIT[resolved_board]
    return {
        "code": bare,
        "available": True,
        "name": name or bare,
        "exchange": derived_exchange,
        "board": resolved_board,
        "is_st": is_st,
        "limit_up_pct": limit,
        "limit_down_pct": limit,
        "lot_size": _BOARD_LOT,
        "source": "company_master/stock_universe",
    }


_BOARD_ALIASES = {
    "沪主板": "main", "深主板": "main", "主板": "main", "main": "main",
    "创业板": "chinext", "chinext": "chinext",
    "科创板": "star", "star": "star",
    "北交所": "bse", "北京证券交易所": "bse", "bse": "bse",
}


def _normalize_board(board: Optional[str]) -> Optional[str]:
    text = (board or "").strip()
    if not text:
        return None
    if text in BOARD_LIMIT:
        return text
    for alias, canonical in _BOARD_ALIASES.items():
        if alias in text:
            return canonical
    return text
