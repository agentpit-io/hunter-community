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

## ST 判定走哪条通道（拍板 2026-10-02 §七 · 与行情主数据**分开**）

行情与涨跌停主数据走 hunter 网关（`official`），**但 ST 不行**：该网关 `quote`
返回的 `name` **就是代码本身**（实测 `600519` → `"name":"600519"`），既无中文
简称也无 ST 标记。`company_master` 也不能当权威 —— 演示库里只有 300 行种子，
判全市场会大面积漏判。所以 ST 一律看**腾讯通道的 `f(1)`**（真实中文简称，
免 key、全市场覆盖，见下面的 `tencent_names()`）；判不出 ST 的标的
`available=false`，风控第 4 条直接拒绝它。**绝不猜。**

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

from app.services.market_time import market_tz

CST = market_tz("CN_A")
UTC = timezone.utc

# 免费通道超时。执行区最不该久等：拿不到就是不成交，不是等下去。
QT_TIMEOUT_S = 5.0
_QT = "https://qt.gtimg.cn/q="
_UA = {"User-Agent": "Mozilla/5.0"}

# ── 汇率（只在「跨市场合计」这一处用；账本内永不折算）──────────────────────
# 来源：新浪 `hq.sinajs.cn/list=fx_shkdcny,fx_susdcnh` —— 返回**带日期 + 时分秒**的报价
# （`N0 §二 S5` 实测）。仓内 `mcp/market_tools._FX_APPROX` 那两个常量（0.91 / 7.20）
# 是**编出来的**，`N0 §五` 明令禁止用于合计 —— 这里只走新浪通道，取不到就返回空 dict。
_SINA_FX = "https://hq.sinajs.cn/list="
_SINA_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}
FX_TIMEOUT_S = 5.0
# 货币对 → 新浪代码 + 报价里「本币兑人民币」第几个字段（`N0` 贴的原始返回逐字对照）。
FX_PAIRS = {
    "HKDCNY": "fx_shkdcny",
    "USDCNY": "fx_susdcnh",   # 离岸人民币，与在岸 `fx_susdcny` 同量级、更新更勤
}


def fetch_fx(pairs: tuple[str, ...] = ("HKDCNY", "USDCNY")) -> dict[str, dict]:
    """现取带时刻的汇率：`{pair: {"rate": float, "at": iso, "source": str}}`。

    **拿不到就不返回该键**（调用方据此把合计置 `—`），绝不退回常量、绝不编数。
    新浪返回形如 `var hq_str_fx_shkdcny="06:59:36,0.85448...,...,2026-10-03";`
    —— 第 0 段是时刻、第 1 段是买入价（本币兑人民币）、最后一段是日期。
    """
    codes = [(p, FX_PAIRS[p]) for p in pairs if p in FX_PAIRS]
    if not codes:
        return {}
    try:
        import requests
        resp = requests.get(_SINA_FX + ",".join(c for _, c in codes),
                            headers=_SINA_HEADERS, timeout=FX_TIMEOUT_S)
        resp.encoding = "gbk"          # 新浪返回 GBK
        text = resp.text
    except Exception as exc:  # noqa: BLE001 —— 拿不到就是不折算，不抛给上层
        logger.warning("[fx] 汇率拉取失败：{}", exc)
        return {}

    out: dict[str, dict] = {}
    for pair, code in codes:
        m = re.search(rf'hq_str_{code}="([^"]*)"', text)
        if not m or not m.group(1):
            continue
        parts = m.group(1).split(",")
        if len(parts) < 2:
            continue
        hhmmss, rate_s = parts[0].strip(), parts[1].strip()
        day = parts[-1].strip() if parts[-1].strip() else ""
        try:
            rate = float(rate_s)
        except (TypeError, ValueError):
            continue
        if rate <= 0:
            continue
        at = f"{day}T{hhmmss}" if day and hhmmss else (day or hhmmss or "unknown")
        out[pair] = {"rate": rate, "at": at, "source": f"sina:{code}"}
    return out

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


# 本仓统一的市场三值（设计文档附 B：`CN_A` / `HK` / `US`）。
# 行情层内部沿用 `a` / `hk` / `us`（与 `market_source.market_of` 同口径，不破坏老的
# 调用点），**边界输出**（`quote()` 的返回体、`fin_snapshot.market`）统一走这三值。
_MARKET_LABEL = {"a": "CN_A", "hk": "HK", "us": "US"}


def market_label(market: str) -> str:
    """`a` / `hk` / `us` → 本仓统一的 `CN_A` / `HK` / `US`。"""
    return _MARKET_LABEL.get(market, market)


# `quote_quality`（设计文档附 B）：`full` 有盘口 / `last_only` 只有最新价 /
# `no_book` 无盘口按最新价撮合。本期港美股只接限价单，所以落库的值是 `full`
# （A 股，有买一卖一）或 `last_only`（港美股，实测无盘口）；`no_book` 留给
# 本期不做的市价单撮合路径（见 `paper` 撮合引擎对港美股市价单的拒绝）。
QUOTE_QUALITIES = ("full", "last_only", "no_book")


def quote_quality_of(orderbook: bool) -> str:
    """由「有没有买一/卖一盘口」判快照质量。"""
    return "full" if orderbook else "last_only"


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
    # L02：统一走 `market_time`（IANA 名，夏令时自动跟随）。旧实现在缺 tzdata 时
    # 静默退回固定 EST(-5) —— 那个兜底本身在夏天就是错的（应为 -4）；现在缺 tzdata
    # 由 `market_time.market_tz` **明确报错**，不再静默给一个错的时区。
    return market_tz("US") if market == "us" else market_tz("CN_A")


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


# ── ST 判定专用 · 腾讯通道的证券简称（拍板 2026-10-02 §七）──────────────────
#
# **为什么 ST 不能走 hunter 通道**：hunter 网关 `quote` 返回的 `name` 字段
# **就是代码本身**（实测 `600519` → `"name":"600519"`），既无中文简称也无 ST
# 标记，判不出来。腾讯 `qt.gtimg.cn` 的 `f(1)` 是真实中文简称
# （`贵州茅台` / `*ST 某某` / `退市某某`），免 key、覆盖全市场 A 股。
#
# **为什么不能拿 `company_master` 当权威**：演示库里它只有 300 行（沪深 300
# 种子），用它判全市场 ST 会大面积漏判 —— 漏判的后果是把一只 ST 股当成 ±10%
# 主板股撮合。所以 master 的名称只作**展示**用，ST 一律看这一条通道。
#
# 批量：一张 URL 能带多个代码（`q=sh600519,sz000001`），返回多行
# `v_sh600519="..."`，实测 60 个一次约 0.3 秒。
_NAME_BATCH = 50
# 批间隔。腾讯的 WAF 有前科（`CLAUDE.md`「全市场日线管线」一节：13 次/秒把
# fin-r1 打进过黑名单），这里主动限速到约 1 批/秒；见 501（WAF 拦截信号）
# **立即中止整批**，不硬闯。
_NAME_GAP_S = 1.0


class TencentWafBlocked(RuntimeError):
    """腾讯通道返回 501 —— WAF 把本机拦了。调用方据此中止整批，别硬闯。"""


def tencent_names(codes: list[str]) -> dict[str, str]:
    """批量取腾讯通道的**证券简称**，只用于 ST 判定（见本节头部）。

    返回 `{六位代码: 简称}`；某只取不到就**不进字典**，调用方按「ST 判不出」
    处理（拍板 §七：零容忍，绝不猜）。网络异常不抛给调用方（同步任务不该因为
    一次抖动整批失败），但 **501 会抛 `TencentWafBlocked`** —— 那时再打下去
    只会把整机 IP 送进黑名单。
    """
    import time

    symbols: dict[str, str] = {}
    for code in codes:
        sym = _qt_symbol(code)
        if sym:
            symbols[sym] = code.split(".")[0].strip().upper()
    out: dict[str, str] = {}
    syms = list(symbols)
    for i in range(0, len(syms), _NAME_BATCH):
        chunk = syms[i:i + _NAME_BATCH]
        if i:
            time.sleep(_NAME_GAP_S)
        try:
            import requests

            resp = requests.get(_QT + ",".join(chunk), headers=_UA, timeout=QT_TIMEOUT_S)
            if resp.status_code == 501:
                raise TencentWafBlocked(
                    f"腾讯通道返回 501（WAF 拦截），已取 {len(out)}/{len(syms)} 只，中止")
            text = resp.content.decode("gbk", "ignore")
        except TencentWafBlocked:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("[fin_data] 腾讯名称通道失败({} 个) · {}", len(chunk), exc)
            continue
        for m in re.finditer(r'v_([a-zA-Z]{2}\d{0,6}[A-Za-z.]*)="([^"]*)"', text):
            fields = m.group(2).split("~")
            if len(fields) < 3:
                continue
            name = fields[1].strip()
            code = symbols.get(m.group(1))
            if code and name:
                out[code] = name
    return out


# ── 统一入口 ───────────────────────────────────────────────────────────────

def _is_date_only(dt: Optional[datetime]) -> bool:
    """时刻只有日期、没有盘中时间（00:00:00.000）。

    hunter 网关的 A 股报价就是这种形状（`ts = "2026-09-30"`）。A 股不存在
    真正在零点成交的行情，所以「零点」在这里只有一个含义：**数据源没给时刻**。
    """
    return (dt is not None and dt.hour == 0 and dt.minute == 0
            and dt.second == 0 and dt.microsecond == 0)


# provider 顺序（N0 报告 §一 / §九 的结论）。用**函数名字符串**而不是直接存函数对象：
# 单测用 `monkeypatch.setattr(fin_data, "_tencent_quote", ...)` 换实现，按名字在调用时刻
# 取才能换得动（存对象会在 import 时定死）。
_CHAIN_CN_A = ("_official_quote", "_tencent_quote")     # A 股：hunter 优先（一期口径，不改）
_CHAIN_INTL = ("_tencent_quote", "_official_quote")     # 港美股：腾讯优先（见下）

_NO_DATA_GAP = {
    "_official_quote": "official: 官方链路无数据（未配置 key / 上游不可用）",
    "_tencent_quote": "tencent-qt: 腾讯免费通道无数据",
}


def _chain_for(market: str) -> list[tuple[str, object]]:
    """按市场给出 provider 顺序。

    · **A 股**：`official`（hunter 网关）优先 —— 一期口径，逐字节不变；
    · **港美股**：**腾讯优先**。依据 N0 实测：hunter 网关的港美股 `updated_at`
      **只到日期、且落后 1~2 个交易日** → 只能用于展示、**不得用于成交**
      （同一期 M8 那个坑）；腾讯通道 `f[30]` **到秒**、免 key、覆盖全市场。
      同时把港美股报价的 `source` 名不符实（实际来自腾讯 `_free_quote`，却标
      `official`）改直 —— 现在它如实是 `tencent-qt`。
    """
    names = _CHAIN_CN_A if market == "a" else _CHAIN_INTL
    return [(name, globals()[name]) for name in names]


def quote(code: str) -> Optional[dict]:
    """统一金融数据结构。**provider 全拿不到 → `None`**（不返回零价）。

    返回体（字段固定，换数据源不改字段）：
    `code / name / market / last_price / prev_close / open / high / low /
     bid1_price / bid1_volume / ask1_price / ask1_volume /
     snapshot_time / event_time / ingested_at / available_at / revision_id /
     source / orderbook / quote_quality / gaps`

    `market` 输出本仓统一三值（`CN_A`/`HK`/`US`），`quote_quality` 见附 B。

    **口径统一（L03）**：这个临时 dict 与持久化的 `fin_snapshot` 用**同一套字段名** ——
    `snapshot_time`（数据源回报的时刻 = 这张快照的「数据截止时间」，**正式名**；`event_time`
    保留为别名，老读取方不受影响）· `source` · `quote_quality` · `available_at` · `revision_id`。
    `available_at` / `revision_id` 数据源**不返回** → 恒 `None`（**不许拿 now() 冒充**，红线 5），
    与 `fin_snapshot.available_at` / `.revision_id` 同口径。
    """
    now = datetime.now(UTC)
    gaps: list[str] = []
    market = market_of(code)

    hit = None
    for fname, provider in _chain_for(market):
        hit = provider(code)
        if hit is not None:
            break
        gaps.append(_NO_DATA_GAP[fname])
    if hit is None:
        return None

    if hit.get("event_time") is None:
        # 数据源没给时刻。**仍然返回**，让上层按「不落快照」处理并留下缺口记录 ——
        # 直接返回 None 会让「行情还在、只是没时间戳」和「完全没有行情」混成一种。
        gaps.append("event_time: 数据源没有给出行情时刻")

    # hunter 网关的两个已知缺口（2026-10-02 实测，拍板 §七 同一条实测）：
    #   ① A 股报价的 `ts` 只到**日期**（`"2026-09-30"`），没有盘中时刻；
    #   ② `name` **就是代码本身**（`"600519"`），没有中文简称。
    # ① 对账本是致命的：快照时刻落成当天 00:00 之后，风控的交易时段校验必然判
    # 「不在交易时段内」、新鲜度必然判 stale —— 两条都会让委托**永远不成交**，
    # 而且不报错，看起来像策略没信号。② 只是难看，但把代码当名字显示给用户。
    # 两处都补一次腾讯通道（免 key、到秒、有真实中文简称），**只补字段、不换价格** ——
    # 价格与来源仍是 hunter（拍板 §一），补了什么如实记进 `gaps`。
    bare = code.split(".")[0].strip().upper()
    name_missing = not (hit.get("name") or "").strip() or str(hit.get("name")).strip().upper() == bare
    # 港美股那条链把腾讯排在前面：走到这里说明腾讯刚刚已经失败过一次，不再重复打
    # （一次 5 秒超时；degraded 状态下不打两次）。
    tencent_already_failed = market != "a"
    if _is_date_only(hit.get("event_time")) or (market == "a" and name_missing):
        tq = {} if tencent_already_failed else (_tencent_quote(code) or {})
        if _is_date_only(hit.get("event_time")):
            if tq.get("event_time") and not _is_date_only(tq["event_time"]):
                hit["event_time"] = tq["event_time"]
                gaps.append(f"event_time: {hit.get('source')} 只给到日期，"
                            f"盘中时刻取自腾讯通道（{tq['event_time'].isoformat()}）")
            else:
                gaps.append(f"event_time: {hit.get('source')} 只给到日期，"
                            f"且腾讯通道也取不到盘中时刻")
        if name_missing and (tq.get("name") or "").strip():
            hit["name"] = tq["name"]
            gaps.append(f"name: {hit.get('source')} 返回的是代码本身，"
                        f"证券简称取自腾讯通道（{tq['name']}）")

    orderbook = hit.get("bid1_price") is not None and hit.get("ask1_price") is not None
    if market == "a":
        if hit.get("bid1_price") is None:
            gaps.append("orderbook: 该来源没有买一/卖一盘口")
    elif not orderbook:
        # 港美股**结构性没有盘口**（N0 S2 实测，与 `_tencent_quote` 的注释一致）——
        # 如实记进缺口，快照据此记 `quote_quality='last_only'`。
        gaps.append("orderbook: 港美股通道没有买一/卖一盘口（快照 quote_quality=last_only）")

    return {
        "code": code,
        "name": hit.get("name"),
        "market": market_label(market),
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
        # L03 · `snapshot_time` 是 `event_time` 的**正式名**（= `fin_snapshot.snapshot_time`，
        # 也是这张快照的「数据截止时间」）；`event_time` 留作别名，不删。
        "snapshot_time": hit["event_time"].isoformat() if hit.get("event_time") else None,
        "ingested_at": now.isoformat(),
        # L03 · §6.2 缺的两个时间字段：数据源不报它们 → 恒 None（**不拿 now() 冒充**，红线 5）。
        # 与 `fin_snapshot.available_at` / `.revision_id` 同口径。
        "available_at": None,
        "revision_id": None,
        "source": hit.get("source"),
        "orderbook": orderbook,
        "quote_quality": quote_quality_of(orderbook),
        "gaps": gaps,
    }


# ── 标的元数据（fin_instrument 的同步素材）────────────────────────────────

def instrument(code: str, *, name: Optional[str] = None,
               board: Optional[str] = None,
               st_name: Optional[str] = None,
               require_st_name: bool = False) -> dict:
    """把「某个 A 股代码的涨跌停 / ST 元数据」解析出来。

    三个名称来源，口径不同（拍板 2026-10-02 §二 / §七）：

    | 参数 | 是谁 | 用途 |
    |---|---|---|
    | `st_name` | **行情通道**的证券简称（腾讯 `f(1)`） | **ST 判定的唯一权威** |
    | `name` | `company_master` / `stock_universe` 的中文名 | 展示用；不作为 ST 依据 |
    | `board` | master 的板块 | 归一后判涨跌停；判不出就由代码形态推 |

    板块：master 没有就由**代码形态**推（公开规则，不是猜）。ST：**只认
    `st_name`** —— hunter 网关的 `name` 是代码本身、`company_master` 只有 300
    行，两者都靠不住。`require_st_name=True` 时**拿不到简称即 `available=False`**
    （拍板 §七零容忍）：调用方（fin-worker）据此不写 `fin_instrument`，风控第 4
    条会拒绝该标的。**绝不猜 ST、绝不猜涨跌停。**
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

    # ST 只认行情通道的简称。空 / 等于代码本身 = 没拿到真简称。
    quote_name = (st_name or "").strip()
    master_name = (name or "").strip()
    st_usable = bool(quote_name) and quote_name != bare
    if require_st_name and not st_usable:
        return {
            "code": bare,
            "available": False,
            "reason": ("ST 状态判不出（行情通道没有返回证券简称）——拒绝该标的，绝不猜 ST"),
        }

    is_st = bool(st_usable and re.search(r"ST|退", quote_name, re.IGNORECASE))
    limit = BOARD_LIMIT[resolved_board]
    return {
        "code": bare,
        "available": True,
        "name": master_name or quote_name or bare,
        "exchange": derived_exchange,
        "board": resolved_board,
        "is_st": is_st,
        "limit_up_pct": limit,
        "limit_down_pct": limit,
        "lot_size": _BOARD_LOT,
        # 记的是**ST 判定**的来源（拍板 §二 ⑤ 要求 fin_instrument.source 留痕）
        "source": "quote_name" if st_usable else "company_master/stock_universe",
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


# ── 标的元数据 · 港美股（N3 放开市场）──────────────────────────────────────
#
# 与 A 股那支 `instrument()` **分开**：A 股要判板块推涨跌停、要查 ST 简称；
# 港美股**没有每日涨跌幅限制**（`limit_up_pct` / `limit_down_pct` 存 `NULL`，
# 不编一个幅度 —— `price_limit_mode='none'`，见 N2），但多了两件**必须有来源**
# 的事：**每手股数**（港股每只不同）与**交易所**（美股）。
#
# 来源（拍板 §3.3、N0 报告 S7）：
#   · 港股每手 = 港交所官方 `ListOfSecurities` 导出 `data/hk_master.csv`（随仓库分发）；
#   · 美股每手 = `1`（**事实，不是近似**）；
#   · 美股交易所 = hunter 网关的标的档案（`findata_db.us_quote` 的 `exchange`）。
# **判不出就不判**：拿不到来源的标的 `available=false` 且带原因，调用方跳过它
# （沿用一期「同步不上就拒绝该标的」口径）。

_US_EXCHANGES = ("NASDAQ", "NYSE", "AMEX", "CBOE")
_US_LOT = 1
_HK_LOT_HINT = "港交所官方 ListOfSecurities（data/hk_master.csv）"


def _hk_master_row(code: str) -> Optional[dict]:
    """港股主表一行（港交所官方 CSV）。不可用 → `None`（不抛）。"""
    try:
        from app.services.gm import findata_db

        return findata_db.hk_master(code)
    except Exception as exc:  # noqa: BLE001 —— 档案不可用按「判不出」处理
        logger.info("[fin_data] 港股主表不可用 {} · {}", code, exc.__class__.__name__)
        return None


def hk_universe_codes(limit: int = 5000) -> list[str]:
    """港股全量代码 —— 港交所官方清单（`data/hk_master.csv`，随仓库分发）。"""
    try:
        from app.services.gm import findata_db

        rows = findata_db.hk_master_all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[fin_data] 港股官方清单不可用：{}", exc)
        return []
    return [r["code"] for r in rows[:limit] if r.get("code")]


def _us_exchange_and_name(code: str) -> tuple[Optional[str], Optional[str]]:
    """美股交易所与名称 —— 来源 hunter 网关的标的档案。

    `stock_universe` / `company_master` 在本机与开源版都是空的或只有 A 股，
    所以交易所走网关；**判不出就判不出**，不拿代码形态猜一个交易所。
    """
    try:
        from app.services.gm import findata_db

        q = findata_db.us_quote(code)
    except Exception as exc:  # noqa: BLE001
        logger.info("[fin_data] 美股档案不可用 {} · {}", code, exc.__class__.__name__)
        return None, None
    if not q:
        return None, None
    exchange = (q.get("exchange") or "").strip().upper() or None
    name = (q.get("name") or q.get("name_en") or "").strip() or None
    return exchange, name


def instrument_intl(code: str, market: str) -> dict:
    """港 / 美标的的元数据（`fin_instrument` 的同步素材）。判不出 → `available=false`。"""
    bare = (code or "").split(".")[0].strip().upper()
    mkt = (market or "").strip().lower()

    if mkt == "hk":
        bare = bare.zfill(5)
        row = _hk_master_row(bare)
        lot = (row or {}).get("lot_size")
        if not lot:
            return {
                "code": bare,
                "available": False,
                "reason": (f"港股每手股数拿不到（{_HK_LOT_HINT} 里没有这只）"
                           "——拒绝该标的，不按 100 猜每手"),
            }
        return {
            "code": bare,
            "available": True,
            # 港交所公开数据里**只有英文名**（CSV 生成的注释写明）——不臆造中文名。
            "name": (row.get("name") or bare),
            "exchange": "HKEX",
            "board": "hk_main",
            "is_st": False,
            "limit_up_pct": None,
            "limit_down_pct": None,
            "lot_size": int(lot),
            "market": "HK",
            "currency": "HKD",
            "source": "hkex_listofsecurities(data/hk_master.csv)",
        }

    if mkt == "us":
        exchange, name = _us_exchange_and_name(bare)
        if exchange not in _US_EXCHANGES:
            return {
                "code": bare,
                "available": False,
                "reason": (f"美股交易所判不出（网关标的档案没有 {bare} 或没给 exchange，"
                           f"取到 {exchange!r}）——拒绝该标的，不猜交易所"),
            }
        return {
            "code": bare,
            "available": True,
            "name": name or bare,
            "exchange": exchange,
            "board": "us_main",
            "is_st": False,
            "limit_up_pct": None,
            "limit_down_pct": None,
            "lot_size": _US_LOT,
            "market": "US",
            "currency": "USD",
            "source": "hunter_gateway(us_quote.exchange)",
        }

    return {
        "code": bare,
        "available": False,
        "reason": f"不支持的市场 {market!r}（港美股同步只认 hk / us）",
    }
