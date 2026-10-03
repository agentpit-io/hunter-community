"""每个市场的**一组调度时点**（N4 · `plan/N4.md` §一.1）。

一期的六个时点是 A 股的固定上海时间（`09:15 / 09:30 / 11:30 / 13:00 / 14:55 / 15:30`）。
二期三个市场的交易时段不同，所以时点**按市场各一组**：

| 市场 | 时区 | 时点（市场当地时间） |
|---|---|---|
| `CN_A` | `Asia/Shanghai` | 09:15 / 09:30 / 11:30 / 13:00 / 14:55 / 15:30 |
| `HK` | `Asia/Hong_Kong` | 09:15 / 09:30 / 12:00 / 13:00 / 15:55 / 16:15 |
| `US` | `America/New_York` | 09:15 / 09:30 / 12:00 / 14:55 / 15:55 / 16:15 |

**时点的来源是 `fin_market_rule.points`**（一行一市场，`0030` 落表）—— fin-worker
**没有账本库连接**（`01方案 §5.1`），所以经 paper 的 `GET /api/v1/market-rules` 读
（`schedules.ensure_schedules` 启动时读一次）。本文件的 `DEFAULT_POINTS` 是**离线 / DB
读不到时的兜底**，与 `0030` 的种子逐项一致。

**时区一律用 IANA 时区名**（`Asia/Hong_Kong` / `America/New_York`），**不写死 ±N 偏移** ——
夏令时交给 Temporal 的 `ScheduleSpec(time_zone_name=...)` 自动跟随（`plan/N4.md` §一.1）。

**时点只归 Temporal**（`01方案 §5.3`）。这里写 `cron` 只是给 Temporal Schedule 用的
表达式，**不是**给系统 crontab 用的 —— 全仓不允许再出现第二个调度器。

`cron` 用 5 段式（分 时 日 月 周），`1-5` 表示周一到周五。**节假日不在 cron 里表达**，
由工作流第一步读 `fin_market_calendar` 挡掉（`cron` 只负责「工作日」，日历负责「交易日」）。
"""

from __future__ import annotations

from dataclasses import dataclass

# 一天的「形状」：六个时点，每个是 `(role, kind)`。
#   · `role` 决定 Temporal 工作流类型名（同一角色在不同市场共用同一个工作流类型，
#     市场经 `args` 传进去）；
#   · `kind` 决定这个时点**干什么**（工作流里的分支）。
# 三个市场的时点数量与顺序都一样（见下表），只有**时刻**不同 —— 所以「形状」固定。
POINT_SHAPE: tuple[tuple[str, str], ...] = (
    ("preopen", "preopen"),
    ("decide", "decide"),
    ("match_a", "match"),
    ("match_b", "match"),
    ("match_c", "match"),
    ("close", "close"),
)

# 兜底时点（DB 的 `fin_market_rule.points` 读不到时用；与 `0030` 的种子逐项一致）。
DEFAULT_POINTS: dict[str, list[str]] = {
    "CN_A": ["09:15", "09:30", "11:30", "13:00", "14:55", "15:30"],
    "HK":   ["09:15", "09:30", "12:00", "13:00", "15:55", "16:15"],
    "US":   ["09:15", "09:30", "12:00", "14:55", "15:55", "16:15"],
}


@dataclass(frozen=True)
class Point:
    key: str            # 供幂等键使用（`{market}-{HHMM}`，跨市场唯一）
    market: str         # CN_A / HK / US
    at: str             # 市场当地时间 HH:MM（展示与审计用）
    kind: str           # preopen / decide / match / close
    role: str           # preopen / decide / match_a / match_b / match_c / close
    workflow: str       # Temporal 工作流类型名（按 role）
    cron: str           # Temporal Schedule 的 cron（周 1-5 = 周一到周五）
    title: str


def cron_of(at: str) -> str:
    """`HH:MM` → Temporal 的 5 段 cron（周 1-5 = 周一到周五）。

    公开名字（不是 `_cron_of`）：复核 Schedule（`schedules.review_specs`）也要把
    「时段末点 + 延迟」这个时刻翻成 cron —— 一处口径，两处调用，不另抄一份。
    """
    hh, mm = at.split(":")
    return f"{int(mm)} {int(hh)} * * 1-5"


def points_for(market: str, times: list[str]) -> tuple[Point, ...]:
    """按市场的**时点列表**建一组 `Point`。`times` 必须与 `POINT_SHAPE` 等长。"""
    if len(times) != len(POINT_SHAPE):
        raise ValueError(
            f"{market} 的调度时点必须是 {len(POINT_SHAPE)} 个（{list(t for _, t in POINT_SHAPE)} 的形状），"
            f"实际 {len(times)} 个：{times!r}"
        )
    out: list[Point] = []
    for (role, kind), at in zip(POINT_SHAPE, times):
        out.append(Point(
            key=f"{market}-{at.replace(':', '')}",
            market=market, at=at, kind=kind, role=role,
            workflow=f"fin.point_{role}",
            cron=cron_of(at),
            title=_TITLES[role],
        ))
    return tuple(out)


_TITLES = {
    "preopen": "开盘前 · 日历同步与 T+1 日切",
    "decide":  "开盘 · 策略出意图并提交委托",
    "match_a": "盘中 · 挂单再撮合（一）",
    "match_b": "盘中 · 挂单再撮合（二）",
    "match_c": "盘中 · 挂单再撮合（三）",
    "close":   "收盘 · 撤单 / 估值 / 对账",
}


# ── 兜底：不含 DB 时的三组时点（schedule 启动失败也不会完全没有调度）──────────
def default_points() -> dict[str, tuple[Point, ...]]:
    return {m: points_for(m, times) for m, times in DEFAULT_POINTS.items()}


# ── 三个市场的默认时点（不含 DB 时用；`0030` 种子逐项一致）──────────────────
MARKETS: tuple[str, ...] = ("CN_A", "HK", "US")

# A 股的六个时点（一期口径，供旧调用点 / 测试用）
POINTS: tuple[Point, ...] = points_for("CN_A", DEFAULT_POINTS["CN_A"])

# 三个市场的全部时点（运维端点 / 自检列出）。key 形如 `CN_A-0915`，跨市场唯一。
ALL_POINTS: tuple[Point, ...] = tuple(
    p for m in MARKETS for p in points_for(m, DEFAULT_POINTS[m])
)
BY_KEY: dict[str, Point] = {p.key: p for p in ALL_POINTS}


def resolve_point_key(key: str) -> Point | None:
    """运维触发用：接受完整 key（`CN_A-0915`）或**裸 HHMM**（`0915` → A 股）。"""
    if key in BY_KEY:
        return BY_KEY[key]
    bare = str(key).strip()
    if len(bare) == 4 and bare.isdigit():
        return BY_KEY.get(f"CN_A-{bare}")
    return None
