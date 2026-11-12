"""港美股交易日历 · 官方来源 + 人工种子兜底 + 对账（拍板 §3.1 三步）。

`apps/api` 的 `GET /internal/calendar/trading-days` 在 N2 之前对 hk/us **直接 501**
（「拿 A 股日历冒充就是编数据」）。N2 按 `拍板-2026-10-03-港美股必须可交易.md` §3.1
把来源建起来 —— **不是**让你编，是让你把可核对的来源接上：

1. **结构化接口**（优先）：官方页内嵌的结构化数据 ——
   · 港股：HKEX `HKEX-Calendar` 页里的 `var DataSource = '{"monthly":[...]}'`，
     休市条目 `holidayIcon == "HongKongPublicHolidays"`；半日市另有一条；
   · 美股：NYSE `hours-calendars` 页那张 `<table>`（2026 / 2027 / 2028 三列）。
2. **人工种子兜底**（拿不到接口时）：本模块内的 `MANUAL_SEED`，**每一条休市日都带
   来源 URL 与录入日期**，`calendar_source='manual_seed'`（拍板 §3.1 第 2 条）。
3. **对账**（必做 · 防编造）：`reconcile()` 用行情通道（指数日线）回验 ——
   过去 ≥20 个自然日逐日比对「日历说法」与「行情有没有数据」。

**本模块只做只读取数与纯解析**：写库在 `scripts/seed_hk_us_calendar.py`（ops 脚本），
调度在 N4。解析函数都是**纯函数**（输入 HTML 文本，输出日期集合），可离线单测。

⚠️ 这是**抓取页面内嵌 JSON / HTML 表**，不是有文档的公开 REST 接口；官方换页面结构
就会失效 —— 所以一定要有第 2 步兜底与第 3 步对账，且失败要**如实上报**（不静默降级）。
"""

from __future__ import annotations

import html as _html
import json
import re
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from loguru import logger

# 官方来源（拍板 §3.1 第 1 步）。
OFFICIAL_SOURCES = {
    "HK": "https://www.hkex.com.hk/News/HKEX-Calendar?sc_lang=en",
    "US": "https://www.nyse.com/markets/hours-calendars",
}

# 港美股各自的常设交易时段（当地时刻）。**L02 起不再在这里手写一份** ——
# 唯一来源是 `app.services.fin.market_sessions`（同一份事实还要给 fin-worker 用，
# 两份文件逐字节相同 + parity 用例；权威其实在 `fin_market_rule.sessions`，
# 常量只是读不到 DB 时的兜底，守护用例直接比对迁移种子）。
# 本模块只用到 HK / US 两行（A 股日历走 `0039`），所以这里**由全量导出子集**，
# 取值逐字不变；`scripts/seed_hk_us_calendar.py` 用它写 `fin_market_calendar.sessions`。
from app.services.fin.market_sessions import MARKET_SESSIONS as _ALL_SESSIONS

MARKET_SESSIONS = {m: _ALL_SESSIONS[m] for m in ("HK", "US")}

MARKET_LABEL = {"HK": "港股", "US": "美股"}

# ── 人工种子（拍板 §3.1 第 2 步）────────────────────────────────────────────
# 官方接口拿不到时的兜底。**只放休市日**（`date → 名称`）。
# `source` = 交易所官方页面 URL；`seeded_at` = 录入日期。这份内容由在线抓取
# 官方页得到后固化为文本（见成果报告的对账读数），不是凭记忆写的。
MANUAL_SEED: dict[str, dict] = {
    "HK": {
        "source": OFFICIAL_SOURCES["HK"],
        "seeded_at": "2026-10-03",
        "holidays": {
            "2026-01-01": "The first day of January",
            "2026-02-17": "Lunar New Year's Day",
            "2026-02-18": "The second day of Lunar New Year",
            "2026-02-19": "The third day of Lunar New Year",
            "2026-04-03": "Good Friday",
            "2026-04-04": "The day following Good Friday",
            "2026-04-06": "The day following Ching Ming Festival",
            "2026-04-07": "The day following Easter Monday",
            "2026-05-01": "Labour Day",
            "2026-05-25": "The day following the Birthday of the Buddha",
            "2026-06-19": "Tuen Ng Festival",
            "2026-07-01": "Hong Kong Special Administrative Region Establishment Day",
            "2026-09-26": "The day following the Chinese Mid-Autumn Festival",
            "2026-10-01": "National Day",
            "2026-10-19": "The day following Chung Yeung Festival",
            "2026-12-25": "Christmas Day",
            "2026-12-26": "The first weekday after Christmas Day",
        },
    },
    "US": {
        "source": OFFICIAL_SOURCES["US"],
        "seeded_at": "2026-10-03",
        "holidays": {
            "2026-01-01": "New Year's Day",
            "2026-01-19": "Martin Luther King, Jr. Day",
            "2026-02-16": "Washington's Birthday",
            "2026-04-03": "Good Friday",
            "2026-05-25": "Memorial Day",
            "2026-06-19": "Juneteenth National Independence Day",
            "2026-07-03": "Independence Day (observed)",
            "2026-09-07": "Labor Day",
            "2026-11-26": "Thanksgiving Day",
            "2026-12-25": "Christmas Day",
        },
    },
}

# 覆盖区间（来自官方页自述）：港股 `calendarDataSourceRanage`、美股三列表。
COVERAGE = {"HK": (date(2025, 10, 1), date(2027, 10, 31)),
            "US": (date(2026, 1, 1), date(2028, 12, 31))}


# ── 纯解析：官方页面 → 休市日 ──────────────────────────────────────────────

_HKEX_DATA_RE = re.compile(r"DataSource\s*=\s*'(\{.*?\})'\s*;", re.S)


def parse_hkex_calendar(page: str) -> tuple[dict[str, str], list[str], tuple[date, date]]:
    """HKEX 页 → `(休市日 dict[iso,name], 半日市 list[iso], 覆盖区间)`。**纯函数**。

    半日市**不是休市**（当日下午休市，上午仍交易）—— 按拍板 §3.1 第 4 条
    「按正常交易时段放宽处理」，所以它不进休市集合，只单独返回供留痕。
    """
    m = _HKEX_DATA_RE.search(page or "")
    if not m:
        raise ValueError("HKEX 页面里找不到 `var DataSource = '{...}'`（页面结构可能变了）")
    data = json.loads(m.group(1))
    monthly = data.get("monthly") or []
    holidays: dict[str, str] = {}
    half_days: list[str] = []
    for entry in monthly:
        name = str(entry.get("name") or "")
        start = str(entry.get("startdate") or "")[:10]
        if not start:
            continue
        if entry.get("holidayIcon") == "HongKongPublicHolidays":
            holidays[start] = name
        elif "Half-Day Trading Day" in name or "Half Day Trading Day" in name:
            half_days.append(start)
    rng = _parse_hkex_range(page)
    return holidays, sorted(half_days), rng


def _parse_hkex_range(page: str) -> tuple[date, date]:
    """`calendarDataSourceRanage = [new Date(2025,9,1,...), new Date(2027,9,31,...)]`。

    JS 的月份是 0 基，数字序列是 `[y,m,d,...] × 2`。解析不出来就用覆盖常量兜底
    （兜底值也只是「别把窗口开超」的保守值，不是编日期）。
    """
    m = re.search(r"calendarDataSourceRanage\s*=\s*\[(.*?)\]", page, re.S)
    if not m:
        return COVERAGE["HK"]
    nums = [int(x) for x in re.findall(r"\d+", m.group(1))]
    try:
        start = date(nums[0], nums[1] + 1, nums[2])
        end = date(nums[6], nums[7] + 1, nums[8])
    except (IndexError, ValueError):
        return COVERAGE["HK"]
    return start, end


_TABLE_RE = re.compile(r"<table[^>]*>(.*?)</table>", re.S)
_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL_RE = re.compile(r"<t[hd][^>]*>(.*?)</t[hd]>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")
_DATE_RE = re.compile(r"(January|February|March|April|May|June|July|August|"
                      r"September|October|November|December)\s+(\d{1,2})")


def parse_nyse_calendar(page: str, year: int) -> dict[str, str]:
    """NYSE `hours-calendars` 页的休市日表 → `dict[iso, name]`。**纯函数**。

    表头是 `Holiday | 2026 | 2027 | 2028`；每一行第一格是名称，之后各列按年份对齐。
    单元格形如 `Friday, January 1`、`Friday, July 3 (Independence Day observed)`、
    `Thursday, November 26***`（脚注星号）、`—*`（该年无此假日）—— 都要能处理。
    """
    table = _TABLE_RE.search(page or "")
    if not table:
        raise ValueError("NYSE 页面里找不到休市日表格（页面结构可能变了）")
    rows = _ROW_RE.findall(table.group(1))
    if not rows:
        raise ValueError("NYSE 休市日表格是空的")
    header = [_clean(c) for c in _CELL_RE.findall(rows[0])]
    try:
        col = header.index(str(year))
    except ValueError:
        raise ValueError(f"NYSE 表格里没有 {year} 这一列（只有 {header[1:]}）") from None
    holidays: dict[str, str] = {}
    for row in rows[1:]:
        cells = [_clean(c) for c in _CELL_RE.findall(row)]
        if len(cells) <= col:
            continue
        name, cell = cells[0], cells[col]
        d = _parse_month_day(cell, year)
        if d is not None and name:
            holidays[d.isoformat()] = name
    return holidays


def _clean(cell: str) -> str:
    text = _html.unescape(_TAG_RE.sub("", cell or ""))
    return re.sub(r"\s+", " ", text).strip()


def _parse_month_day(cell: str, year: int) -> Optional[date]:
    if not cell or cell.strip() in ("—", "—*", "-"):
        return None
    m = _DATE_RE.search(cell)
    if not m:
        return None
    month = _MONTHS.index(m.group(1)) + 1
    day = int(m.group(2))
    try:
        return date(year, month, day)
    except ValueError:
        return None


# ── 纯函数：交易日 = 工作日 − 休市日 ───────────────────────────────────────

def trading_days(start: date, end: date, holidays) -> list[str]:
    """区间内的交易日（升序 ISO）。休市日按字符串或 `date` 集合都认。"""
    hol = {str(h)[:10] for h in (holidays or ())}
    out: list[str] = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5 and cur.isoformat() not in hol:
            out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def reconcile(start: date, end: date, calendar_days, bar_days) -> dict:
    """对账（拍板 §3.1 第 3 步）：日历说法 vs 行情实际有数据的日子。**纯函数**。

    返回 `{window, n_weekdays, n_calendar, n_bars, agree, cal_open_no_bar,
    bar_open_cal_closed, mismatches}`。**不一致的日期一个不许漏**。
    """
    cal = {str(d)[:10] for d in calendar_days}
    bars = {str(d)[:10] for d in bar_days}
    window = [d.isoformat() for d in _days(start, end)]
    in_win = set(window)
    cal_w = cal & in_win
    bar_w = bars & in_win
    cal_open_no_bar = sorted(cal_w - bar_w)
    bar_open_cal_closed = sorted(bar_w - cal_w)
    return {
        "window": [start.isoformat(), end.isoformat()],
        "n_weekdays": sum(1 for d in window if date.fromisoformat(d).weekday() < 5),
        "n_calendar": len(cal_w),
        "n_bars": len(bar_w),
        "agree": len(cal_w & bar_w),
        "cal_open_no_bar": cal_open_no_bar,
        "bar_open_cal_closed": bar_open_cal_closed,
        "mismatches": sorted(set(cal_open_no_bar) | set(bar_open_cal_closed)),
    }


def _days(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


# ── 取数（IO）：官方接口 → 兜底人工种子 ────────────────────────────────────

def _fetch(url: str, timeout: float = 20.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def load_official(market: str, *, timeout: float = 20.0) -> tuple[dict[str, str], list[str]]:
    """在线抓官方页 → `(休市日, 半日市)`。失败**抛异常**（调用方决定兜底）。"""
    page = _fetch(OFFICIAL_SOURCES[market], timeout=timeout)
    if market == "HK":
        holidays, half_days, _rng = parse_hkex_calendar(page)
        return holidays, half_days
    year = datetime.now(timezone.utc).year
    # 美股页一次给三年，把覆盖区间里的年份都取出来（当前年 + 后两年）。
    holidays: dict[str, str] = {}
    for y in (year, year + 1, year + 2):
        try:
            holidays.update(parse_nyse_calendar(page, y))
        except ValueError:
            continue
    if not holidays:
        raise ValueError("NYSE 页面里没解析出任何休市日")
    return holidays, []


def market_holidays(market: str, *, timeout: float = 20.0) -> dict:
    """`{holidays, source, source_label, fetched_at, ok, error, half_days}`。

    先试官方接口（`calendar_source='hkex_official'` / `'nyse_official'`）；
    失败 → 用 `MANUAL_SEED`（`calendar_source='manual_seed'`），并把失败原因**带出来**，
    让调用方（报告 / 回执）能如实标注（拍板 §3.1 第 5 条：不许静默降级）。
    """
    market = market.upper()
    label = "hkex_official" if market == "HK" else "nyse_official"
    try:
        holidays, half_days = load_official(market, timeout=timeout)
        return {"market": market, "holidays": holidays, "half_days": half_days,
                "source": OFFICIAL_SOURCES[market], "source_label": label,
                "fetched_at": datetime.now(timezone.utc).isoformat(), "ok": True,
                "error": None}
    except Exception as exc:  # noqa: BLE001 —— 官方接口失败时兜底到人工种子
        seed = MANUAL_SEED.get(market) or {}
        logger.warning("[market_calendar] {} 官方接口失败（{}），回落人工种子（{} 条，来源 {}）",
                       market, exc, len(seed.get("holidays") or {}), seed.get("source"))
        return {"market": market, "holidays": dict(seed.get("holidays") or {}),
                "half_days": [], "source": seed.get("source") or OFFICIAL_SOURCES[market],
                "source_label": "manual_seed", "fetched_at": seed.get("seeded_at"),
                "ok": False, "error": str(exc)}
