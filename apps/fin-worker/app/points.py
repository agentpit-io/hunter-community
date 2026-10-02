"""六个交易时点（`05 §3.2` M-14）。

| 时点 | 上海时间 | 干什么 | 打 paper 的哪个端点 |
|---|---|---|---|
| `preopen` | 09:15 | 同步交易日历 + T+1 日切（昨天买进的今天可卖） | `PUT /market-calendar/{d}` · `POST /projects/{id}/confirm-t1` |
| `decide`  | 09:30 | 固定示例策略出意图 → 提交委托 | `POST /orders` |
| `match`   | 11:30 | 拿新快照再撮一遍挂单 | `POST /projects/{id}/orders/match-open` |
| `match`   | 13:00 | 同上 | 同上 |
| `match`   | 14:55 | 同上（尾盘） | 同上 |
| `close`   | 15:30 | 收盘撤未成交挂单 + 估值 + 对账 | `POST /projects/{id}/orders/expire` · `/valuation` · `/recon` |

**`preopen` 的 T+1 日切排在 `decide` 之前**（`M3 报告 · 遗留 5`）：日切必须发生在
**没有挂单的时候** —— 昨天的挂单已在昨天 15:30 撤掉，今天的还没下，09:15 正好是那个缝。

**时点只归 Temporal**（`01方案 §5.3`）。这里写 `cron` 只是给 Temporal Schedule 用的
表达式，**不是**给系统 crontab 用的 —— 全仓不允许再出现第二个调度器。

`cron` 用 5 段式（分 时 日 月 周），`1-5` 表示周一到周五。**节假日不在 cron 里表达**，
由工作流第一步读 `fin_market_calendar` 挡掉（`cron` 只负责「工作日」，日历负责「交易日」）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Point:
    key: str            # 供幂等键使用（纯标识）
    at: str             # 上海时间 HH:MM（展示与审计用）
    kind: str           # preopen / decide / match / close
    workflow: str       # Temporal 工作流类型名
    cron: str           # Temporal Schedule 的 cron（周日 1-5 = 周一到周五）
    title: str


POINTS: tuple[Point, ...] = (
    Point("0915", "09:15", "preopen", "fin.point_0915", "15 9 * * 1-5", "开盘前 · 日历同步与 T+1 日切"),
    Point("0930", "09:30", "decide",  "fin.point_0930", "30 9 * * 1-5", "开盘 · 策略出意图并提交委托"),
    Point("1130", "11:30", "match",   "fin.point_1130", "30 11 * * 1-5", "午间 · 挂单再撮合"),
    Point("1300", "13:00", "match",   "fin.point_1300", "0 13 * * 1-5", "午后 · 挂单再撮合"),
    Point("1455", "14:55", "match",   "fin.point_1455", "55 14 * * 1-5", "尾盘 · 挂单再撮合"),
    Point("1530", "15:30", "close",   "fin.point_1530", "30 15 * * 1-5", "收盘 · 撤单 / 估值 / 对账"),
)

BY_KEY: dict[str, Point] = {p.key: p for p in POINTS}
BY_WORKFLOW: dict[str, Point] = {p.workflow: p for p in POINTS}


def point_of(workflow_name: str) -> Point:
    try:
        return BY_WORKFLOW[workflow_name]
    except KeyError:
        raise KeyError(f"未知的时点工作流：{workflow_name}")
