"""市场代号在**各条通道上的写法**（N4）。

本仓统一的三值是 `CN_A` / `HK` / `US`（`market_time.canonical_market`）。但历史上
三个上游通道各有各的写法，散在各处容易写错（写错就是「拿 A 股日历 / 费率顶替港股」）：

| 通道 | A 股 | 港股 | 美股 | 谁用 |
|---|---|---|---|---|
| 本仓统一 | `CN_A` | `HK` | `US` | 账本 / 市场规则表 / 调度 |
| 交易日历 `GET /internal/calendar/trading-days` | `a` | `hk` | `us` | `sync_calendar` |
| K 线 ETL `POST /internal/etl/run-market` | `cn` | `hk` | `us` | `trigger_market_etl` |
| 标的同步 `GET /internal/fin/instruments` | `cn` | `hk` | `us` | `sync_instruments` |

集中一处，**别在调用点各写一遍映射**。
"""

from __future__ import annotations

# 本仓统一三值 → 各通道写法
CAL_MARKET: dict[str, str] = {"CN_A": "a", "HK": "hk", "US": "us"}
ETL_MARKET: dict[str, str] = {"CN_A": "cn", "HK": "hk", "US": "us"}
SYNC_MARKET: dict[str, str] = {"CN_A": "cn", "HK": "hk", "US": "us"}

# 各通道写法 → 本仓统一三值
CHANNEL_TO_CANON: dict[str, str] = {
    "a": "CN_A", "cn": "CN_A", "cn_a": "CN_A", "cn-a": "CN_A",
    "hk": "HK", "us": "US",
}


def cal_market(market: str) -> str:
    """本仓三值 → 交易日历通道写法。"""
    return CAL_MARKET[market]


def canonical(market: str) -> str:
    """任意通道写法 → 本仓三值。不认识的抛错（不猜）。"""
    key = str(market or "").strip().lower()
    try:
        return CHANNEL_TO_CANON[key]
    except KeyError:
        raise ValueError(f"未知市场代号 {market!r}（只认 CN_A/HK/US 及各通道写法）") from None
