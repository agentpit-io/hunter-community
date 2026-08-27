"""港股 / 美股的免费取数通道 —— 国内直连,不需要 VPN。

方案见 doc/开源hunter-community/01详细工作目录/11量化策略/
      25_20260827_港美股自选股支持_方案.md

## 为什么单独一个模块,而不是改 finance_data_client

`finance_data_client` 那条链路(用户源 → SaaS → provider)是**通的**,
A 股验过 88 分钟零失败。这里只提供"官方链路拿不到时的兜底通道",
在**原路径返回空之后**才介入 —— A 股行为逐字节不变。

改坏现成能用的东西,比多写一个分支贵得多。

## 源的选择,和被否掉的两个

| | 用什么 | 实测(国内 IP 134.175.198.216) |
|---|---|---|
| 港股日线 | 腾讯 `hk00700` | 30/30 只成功 · 根数中位 801 |
| 美股日线 | 新浪 `stock_us_daily` | 30/30 只 · 30 只共 16 秒 |
| 港美报价 | 腾讯 `qt.gtimg.cn` | 各 100/100 次 · 中位 70ms |

**否掉 Yahoo**:国内 IP 连试两次都是 `HTTP 429 Edge: Too Many Requests`,
同一时刻海外出口 200。服务号(hunter)的 `gm/yahoo_hk.py` 能用,
是因为它跑在有海外出口的机器上 —— 那是我们的环境,不是用户的。

**否掉东财**:本项目里东财一路不稳(指数日线、行业板块、北交所、
`stock_us_hist` 都栽过 `RemoteDisconnected`),本次实测依然失败。

## 港股为什么几乎白送

腾讯港股日线和 A 股是**同一个接口、同一个域名**,只差前缀。
限流退避、字段顺序那些坑全是现成的。

⚠ 同一个域名也意味着**同一套限流**。这里的 100 次压测远低于
A 股那次触发限流的量级(几千次),批量下港股时要复用
`data_job` 那套退避,不能直接连打。
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta

import requests
from loguru import logger as log

_UA = {"User-Agent": "Mozilla/5.0"}
_TIMEOUT = 12

_QT = "https://qt.gtimg.cn/q="
_KLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


# ═══════════════════════════════════════════════════════════
# 市场判定
# ═══════════════════════════════════════════════════════════

def market_of(code: str) -> str:
    """'a' | 'hk' | 'us' —— 只看代码本身,不查表。

    查表(STOCK_MAP / watchlist)判市场有个实测踩过的问题:watchlist 里
    market 字段可能是脏的。而代码形态本身是可靠的:
      · 5 位纯数字 → 港股(00700 / 09988)
      · 含字母     → 美股(NVDA / BRK.B)
      · 6 位纯数字 → A 股
    """
    s = (code or "").strip().upper()
    if not s:
        return "a"
    if s.endswith(".HK"):
        return "hk"
    if s.endswith(".US"):
        return "us"
    bare = s.split(".")[0]
    if bare.isdigit():
        # ⚠ 5 位 = 港股。**这一条必须排在 A 股前缀判断之前** ——
        # 00700 以 "00" 开头,A 股规则会判成深圳,然后系统认真去
        # 深交所要一只叫 00700 的股票(实测 to_symbol('00700')='00700.SZ')
        return "hk" if len(bare) == 5 else "a"
    return "us"


def _hk_symbol(code: str) -> str:
    """港股腾讯代码:hk + 5 位补零。00700 / 700 都要变成 hk00700"""
    return "hk" + code.split(".")[0].strip().upper().zfill(5)


def _us_symbol(code: str) -> str:
    return code.split(".")[0].strip().upper()


# ═══════════════════════════════════════════════════════════
# 实时报价 · 腾讯 qt.gtimg.cn(港股 + 美股 + A 股都能走)
# ═══════════════════════════════════════════════════════════

def _qt_code(code: str) -> str | None:
    m = market_of(code)
    if m == "hk":
        return _hk_symbol(code)
    if m == "us":
        return "us" + _us_symbol(code)
    return None          # A 股不走这里 —— 原链路已经通了,别抢


def _iso_ts(raw: str) -> str:
    """腾讯的时间戳有三种格式,统一成 `YYYY-MM-DD HH:MM:SS`。

    实测:
        港股  2026/08/27 16:03:00     ← 斜杠
        美股  2026-08-26 16:00:01     ← 横杠
        A股   20260827155755          ← 纯数字

    **为什么必须统一**:上层到处在做 `ts[:10] == today`,而 today 是
    `2026-08-27`。斜杠格式永远匹配不上 —— 表现是港美股每一轮都被判成
    "数据过期",于是每 30 秒多打一次上游做无谓的兜底查询。
    不报错,只是白干活,而且很难发现。
    """
    t = (raw or "").strip()
    if not t:
        return ""
    if len(t) == 14 and t.isdigit():          # 20260827155755
        return f"{t[:4]}-{t[4:6]}-{t[6:8]} {t[8:10]}:{t[10:12]}:{t[12:14]}"
    return t.replace("/", "-")


def quote(code: str) -> dict | None:
    """港美股实时报价。拿不到返回 None —— **不返回 0 价**。

    返回 0 比返回 None 危险得多:界面上 0 和真实价格长得一样,
    用户看到 "0.00" 会以为股票跌没了,而不是"数据没拿到"。
    """
    qt = _qt_code(code)
    if not qt:
        return None
    try:
        r = requests.get(_QT + qt, headers=_UA, timeout=_TIMEOUT)
        # 腾讯这个接口是 GBK,不是 UTF-8。按 UTF-8 解会把中文名字变成乱码,
        # 而且**不报错** —— 表现是股票名字显示成一堆问号
        text = r.content.decode("gbk", "ignore")
    except Exception as e:                                    # noqa: BLE001
        log.warning("[market_source] 报价请求失败 {} · {}", code, e)
        return None

    if '"' not in text:
        return None
    f = text.split('"')[1].split("~")
    if len(f) < 6:
        return None

    def _n(i: int) -> float:
        try:
            return float(f[i])
        except (ValueError, IndexError):
            return 0.0

    price = _n(3)
    if price <= 0:
        # 价格是 0 说明这个代码腾讯没有 —— 明确当成"拿不到"
        return None
    prev = _n(4)
    return {
        "code":       code,
        "name":       f[1] if len(f) > 1 else code,
        "price":      price,
        "prev_close": prev,
        "open":       _n(5),
        "high":       _n(33) or 0.0,
        "low":        _n(34) or 0.0,
        "volume":     int(_n(6)),
        "amount":     _n(37),
        "change_amt": round(price - prev, 4) if prev else 0.0,
        "change_pct": round((price - prev) / prev * 100, 2) if prev else 0.0,
        # 数据时间:美股休市时这里是**上一个收盘时刻**,不是"现在"。
        # 带出去让上层能判断新鲜度,而不是让用户以为是实时价
        "ts":         _iso_ts(f[30] if len(f) > 30 else ""),
        "market":     market_of(code).upper(),
        "asset_type": "stock",
    }


# ═══════════════════════════════════════════════════════════
# 港股日线 · 腾讯(和 A 股同一个接口)
# ═══════════════════════════════════════════════════════════

def hk_daily(code: str, limit: int = 800) -> list[dict]:
    """港股前复权日线。实测 30/30 只成功,根数中位 801。"""
    # 上限 800 的理由同 A 股(local_kline):要更多反而给更少
    n = max(60, min(800, limit))
    sym = _hk_symbol(code)
    try:
        r = requests.get(_KLINE, params={"param": f"{sym},day,,,{n},qfq"},
                         headers=_UA, timeout=_TIMEOUT + 8)
        data = (r.json() or {}).get("data") or {}
    except Exception as e:                                    # noqa: BLE001
        log.warning("[market_source] 港股日线失败 {} · {}", code, e)
        return []
    if not data:
        return []
    node = data.get(sym) or next(iter(data.values()), {})
    bars = node.get("qfqday") or node.get("day") or []
    # 腾讯有最少返回量(要 30 根会给 61 根),照 limit 截一下 ——
    # 不截的话上层要 30 根拿到 61 根,图上多出来一截
    if limit:
        bars = bars[-limit:]
    out = []
    for b in bars:
        # 腾讯字段顺序 [date, open, close, high, low, volume]
        # ⚠ **close 排在 high 前面**。搞错会把收盘价当最高价,
        # 而这种错在图上看不出来 —— 数字都在合理范围,K 线照样能画
        try:
            out.append({"ts": str(b[0])[:10], "open": float(b[1]),
                        "close": float(b[2]), "high": float(b[3]),
                        "low": float(b[4]), "volume": int(float(b[5] or 0))})
        except (ValueError, IndexError, TypeError):
            continue
    return out


# ═══════════════════════════════════════════════════════════
# 美股日线 · 新浪(经 akshare)
# ═══════════════════════════════════════════════════════════

def us_daily(code: str, limit: int = 800) -> list[dict]:
    """美股日线。实测给全历史 —— NVDA 5751 行(1999 起)、MSFT 9637 行(1986 起)。

    比 A 股那边腾讯只肯给 3.25 年慷慨得多。
    """
    sym = _us_symbol(code)
    try:
        import akshare as ak
        df = ak.stock_us_daily(symbol=sym)
    except Exception as e:                                    # noqa: BLE001
        log.warning("[market_source] 美股日线失败 {} · {}", code, e)
        return []
    if df is None or len(df) == 0:
        return []
    if limit:
        df = df.tail(limit)
    out = []
    for r in df.to_dict(orient="records"):
        try:
            out.append({"ts": str(r.get("date"))[:10],
                        "open": float(r.get("open") or 0),
                        "high": float(r.get("high") or 0),
                        "low": float(r.get("low") or 0),
                        "close": float(r.get("close") or 0),
                        "volume": int(float(r.get("volume") or 0))})
        except (ValueError, TypeError):
            continue
    return out


def daily(code: str, limit: int = 800) -> list[dict]:
    """按市场分发。A 股不走这里 —— 原链路已经通了。"""
    m = market_of(code)
    if m == "hk":
        return hk_daily(code, limit)
    if m == "us":
        return us_daily(code, limit)
    return []


# ═══════════════════════════════════════════════════════════
# 分时 · 港美股腾讯不给,明确返回空
# ═══════════════════════════════════════════════════════════

def minutes(code: str) -> list[dict]:
    """港美股分时。

    **目前返回空,并且这是有意的。** 腾讯那个分时接口只覆盖 A 股;
    港美股要另找源,还没验过。返回空让上层显示"暂无分时",
    比返回一条编的曲线强 —— 假的分时在图上和真的完全一样。
    """
    return []
