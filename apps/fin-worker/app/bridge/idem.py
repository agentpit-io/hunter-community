"""幂等键 · 从**业务身份**推导，不从随机数推导。

`01方案 §11.1`：「重试、会话重建和 Worker 重启**不能产生新的业务幂等键**」。

这条要求的工程含义是：幂等键必须是「这件事是谁、在哪一天、哪个时点、要做哪一笔」
的纯函数。Worker 崩了、Temporal 重放、Activity 被 at-least-once 重试第二次，
推导出来的都是**同一个字符串** —— 于是 `paper` 的 `fin_idempotency` 认出它，
返回原回执，`fin_trade` 里不会长出第二笔。

反例（**不要这么写**）：键里塞 `uuid4()` / `time.time()` / `activity.info().attempt`。
那样每次重试都是新键，幂等表形同虚设，重试必然重复下单 —— 而且**不报错**，
只是账本里悄悄多一笔。
"""

from __future__ import annotations


def order_key(project_id: str, trade_date: str, point: str, decision_id: str) -> str:
    """一笔委托的幂等键。

    `decision_id` 由策略侧的确定逻辑生成（示例策略用「策略 + 日期 + 时点 + 代码」，
    不是随机数）—— 同一时点重跑拿到同一个 decision_id，键就稳定。
    """
    return f"fin-order:{project_id}:{trade_date}:{point}:{decision_id}"


def point_job_key(project_id: str, trade_date: str, point: str) -> str:
    """某个时点登记的业务长任务（`fin_job`）的幂等键。

    同一项目 + 同一交易日 + 同一时点 = 同一件事，永远只登记一条 job。
    """
    return f"fin-job:{project_id}:{trade_date}:{point}"


def etc_job_key(market: str, trade_date: str) -> str:
    """K 线 ETL 触发任务的幂等键（`fin-worker` 侧只用于记录，不写账本）。"""
    return f"fin-etl:{market}:{trade_date}"


def sample_decision_id(strategy_key: str, trade_date: str, point: str, code: str) -> str:
    """示例策略的 decision_id：确定性，不含时间戳与随机数。"""
    return f"{strategy_key}-{trade_date}-{point}-{code}"
