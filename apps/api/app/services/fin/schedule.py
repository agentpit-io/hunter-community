"""三个市场的交易时点展示副本（M6 起为 A 股一份，N5 起按市场各一份）。

**权威在 `apps/fin-worker/app/points.py` 与 `fin_market_rule.points`** —— N4 起时点是
**每市场一组**（CN_A / HK / US 各六个），真正按时刻表跑的是 fin-worker 的 Temporal Schedule。
本文件是**给前端渲染的副本**，所以：

- 字段名与那个文件保持一致（`key` / `at` / `kind` / `title` / `cron`）；
- `tests/test_fin_schedule.py` 会**真的加载 `apps/fin-worker/app/points.py`**（它
  零外部依赖），把 `POINTS`（CN_A 六个）与其中 `market == "CN_A"` 的那六个逐条比对 ——
  两处漂了，测试当场红。这不是「约定」，是仓内「同一件事写在多处 = 两套指令打架」那条
  铁律的机器化形式。
- 副本里**没有** `workflow` / 执行逻辑，只有时刻与标题：展示层不该知道工作流类型名。

`cron` 用 5 段式（分 时 日 月 周），`1-5` = 周一到周五。**节假日不在 cron 里**
（由工作流第一步读 `fin_market_calendar` 挡掉）—— 页面上的说明文案必须跟着
说清「周末与节假日自动跳过」是靠日历，不是靠 cron。

N5：时点表按市场取（`times_of(cur, market)` 读 `fin_market_rule.points`，
读不到回落 `DEFAULT_POINTS`），前端市场切换器切到哪个市场就显示哪一组。
"""

from __future__ import annotations

from typing import Optional

# 一天的「形状」：六个时点，每个的 kind（与 fin-worker/app/points.py 的 POINT_SHAPE 同序）。
POINT_SHAPE: tuple[str, ...] = ("preopen", "decide", "match", "match", "match", "close")

# 兜底时点（DB 的 `fin_market_rule.points` 读不到时用；与 `0030` 的种子逐项一致，
# 也与 fin-worker 的 `DEFAULT_POINTS` 一致）。**权威是 DB 那一列**，这里只是兜底。
DEFAULT_POINTS: dict[str, list[str]] = {
    "CN_A": ["09:15", "09:30", "11:30", "13:00", "14:55", "15:30"],
    "HK":   ["09:15", "09:30", "12:00", "13:00", "15:55", "16:15"],
    "US":   ["09:15", "09:30", "12:00", "14:55", "15:55", "16:15"],
}
MARKET_LABEL = {"CN_A": "A 股", "HK": "港股", "US": "美股"}

# 每个时点「对用户意味着什么」——人话。工程语义（打哪个端点）留在 fin-worker，
# 这一列只解释给用户听。
KIND_TEXT = {
    "preopen": "开盘前：同步交易日历，把昨天买进的持仓转为今天可卖（A 股 T+1 日切）",
    "decide": "开盘：按策略出买卖意图，过一遍风控后提交委托",
    "match": "盘中：拿最新行情快照再撮一遍还没成交的挂单",
    "close": "收盘：撤掉未成交的挂单、解冻资金、估值、对账，并生成当日报告",
}
KIND_TITLE = {
    "preopen": "开盘前 · 日历同步与 T+1 日切",
    "decide":  "开盘 · 策略出意图并提交委托",
    "match":   "盘中 · 挂单再撮合",
    "close":   "收盘 · 撤单 / 估值 / 对账",
}
# 三个 match 时点在标题里带序号（与 fin-worker 的 match_a/b/c 标题一致）。
_MATCH_ORD = {0: "（一）", 1: "（二）", 2: "（三）"}


def _rows_for(market: str, times: list[str]) -> tuple[dict[str, str], ...]:
    out: list[dict[str, str]] = []
    seen_match = 0
    for kind, at in zip(POINT_SHAPE, times):
        hh, mm = at.split(":")
        title = KIND_TITLE[kind]
        if kind == "match":
            title = title + _MATCH_ORD.get(seen_match, "")
            seen_match += 1
        out.append({
            "key": f"{market}-{at.replace(':', '')}", "at": at, "kind": kind,
            "cron": f"{int(mm)} {int(hh)} * * 1-5", "title": title,
        })
    return tuple(out)


# 与 fin-worker/app/points.py 里 `market == "CN_A"` 的六个时点逐条对应（同序、同字段）。
# ⚠️ `POINTS` 保留为「CN_A 六个」的旧接口 —— `test_fin_schedule.py` 拿它比对 fin-worker。
POINTS: tuple[dict[str, str], ...] = _rows_for("CN_A", DEFAULT_POINTS["CN_A"])
# 三个市场各一组（N5 市场切换器用）。
POINTS_BY_MARKET: dict[str, tuple[dict[str, str], ...]] = {
    m: _rows_for(m, DEFAULT_POINTS[m]) for m in DEFAULT_POINTS
}


def rows_for(market: str, times: Optional[list[str]] = None) -> tuple[dict[str, str], ...]:
    """某市场的时点行。`times` 给了就用它（DB 的 `fin_market_rule.points`），否则用兜底。"""
    if times:
        try:
            return _rows_for(market, list(times))
        except (ValueError, AttributeError):
            pass
    return POINTS_BY_MARKET.get(market, POINTS)


def times_of(cur, market: str) -> list[str]:
    """从 `fin_market_rule.points` 读该市场的时点；读不到回落兜底（**不猜**）。"""
    if cur is not None:
        try:
            cur.execute("SELECT points FROM fin_market_rule WHERE market = %s", (market,))
            row = cur.fetchone()
            pts = (row or {}).get("points") if isinstance(row, dict) else (row[0] if row else None)
            if pts:
                return [str(x) for x in pts]
        except Exception:  # noqa: BLE001 —— 展示层读不到就回落兜底，不打断页面
            pass
    return list(DEFAULT_POINTS.get(market, DEFAULT_POINTS["CN_A"]))


def as_list(market: str = "CN_A", times: Optional[list[str]] = None) -> list[dict]:
    """给前端的时刻表：时刻 + 标题 + 人话说明。默认 A 股（保旧调用点行为不变）。"""
    return [
        {"key": p["key"], "at": p["at"], "kind": p["kind"], "title": p["title"],
         "cron": p["cron"], "plain": KIND_TEXT.get(p["kind"], "")}
        for p in rows_for(market, times)
    ]
