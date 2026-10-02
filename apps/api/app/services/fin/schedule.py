"""**A 股**六个交易时点的展示用副本（M6 自动交易页「它每天什么时候自动运行」）。

**权威在 `apps/fin-worker/app/points.py`** —— N4 起时点是**每市场一组**
（CN_A / HK / US 各六个），真正按时刻表跑的是 fin-worker 的 Temporal Schedule。
本文件是**给前端渲染的 CN_A 子集副本**（自动交易页现在只讲 A 股；多市场切换器
是 N5 的事），所以：

- 字段名与那个文件保持一致（`key` / `at` / `kind` / `title` / `cron`）；
- `tests/test_fin_schedule.py` 会**真的加载 `apps/fin-worker/app/points.py`**（它
  零外部依赖），把本副本与其中 `market == "CN_A"` 的那六个时点逐条比对 —— 两处漂了，
  测试当场红。这不是「约定」，是仓内「同一件事写在多处 = 两套指令打架」那条铁律的
  机器化形式。
- 副本里**没有** `workflow` / 执行逻辑，只有时刻与标题：展示层不该知道工作流类型名。

`cron` 用 5 段式（分 时 日 月 周），`1-5` = 周一到周五。**节假日不在 cron 里**
（由工作流第一步读 `fin_market_calendar` 挡掉）—— 页面上的说明文案必须跟着
说清「周末与节假日自动跳过」是靠日历，不是靠 cron。
"""

from __future__ import annotations

# 与 fin-worker/app/points.py 里 `market == "CN_A"` 的六个时点逐条对应
# （同序、同 key / at / kind / cron / title）。key 带 `CN_A-` 前缀，与权威一致。
POINTS: tuple[dict[str, str], ...] = (
    {"key": "CN_A-0915", "at": "09:15", "kind": "preopen",
     "cron": "15 9 * * 1-5", "title": "开盘前 · 日历同步与 T+1 日切"},
    {"key": "CN_A-0930", "at": "09:30", "kind": "decide",
     "cron": "30 9 * * 1-5", "title": "开盘 · 策略出意图并提交委托"},
    {"key": "CN_A-1130", "at": "11:30", "kind": "match",
     "cron": "30 11 * * 1-5", "title": "盘中 · 挂单再撮合（一）"},
    {"key": "CN_A-1300", "at": "13:00", "kind": "match",
     "cron": "0 13 * * 1-5", "title": "盘中 · 挂单再撮合（二）"},
    {"key": "CN_A-1455", "at": "14:55", "kind": "match",
     "cron": "55 14 * * 1-5", "title": "盘中 · 挂单再撮合（三）"},
    {"key": "CN_A-1530", "at": "15:30", "kind": "close",
     "cron": "30 15 * * 1-5", "title": "收盘 · 撤单 / 估值 / 对账"},
)

# 每个时点「对用户意味着什么」——人话。工程语义（打哪个端点）留在 fin-worker，
# 这一列只解释给用户听。
KIND_TEXT = {
    "preopen": "开盘前：同步交易日历，把昨天买进的持仓转为今天可卖（T+1 日切）",
    "decide": "开盘：按策略出买卖意图，过一遍风控后提交委托",
    "match": "盘中：拿最新行情快照再撮一遍还没成交的挂单",
    "close": "收盘：撤掉未成交的挂单、解冻资金、估值、对账，并生成当日报告",
}


def as_list() -> list[dict]:
    """给前端的时刻表：时刻 + 标题 + 人话说明 + 归属（谁在跑）。"""
    return [
        {"key": p["key"], "at": p["at"], "kind": p["kind"], "title": p["title"],
         "cron": p["cron"], "plain": KIND_TEXT.get(p["kind"], "")}
        for p in POINTS
    ]
