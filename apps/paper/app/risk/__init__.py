"""确定性风控 · 六条（**按市场取参**，`05 §3.2` M-11 + 二期 N2）。

每条一个**纯函数**：输入是普通 dict / Decimal，输出是 `RiskResult`，不碰数据库、
不取当前时间、不读环境变量。这样每一条都能在没有库、没有网络的情况下单测，
而且「人的单」与「AI 的单」走的是同一批函数——**代码里没有任何按 `source` 分支的逻辑**
（`09 §六-10`：执行路径上不读 `source`）。`source` 只随委托落库，供二期做归因统计。

六条：交易时段 · 可卖数量（T+N / 当日）· 整手 · 价格带（pct / none / band）·
费用（按市场费率行）· 资金与持仓校验。

**规则逻辑一条没变，参数的来源由写死 A 股改成 `fin_market_rule` 一行**（N2）。
`engine.evaluate()` 按顺序跑完六条，把所有不过的原因拼成 `decline_reason`。
"""

from app.risk.engine import RiskInputs, RiskOutcome, evaluate
from app.risk.result import RiskResult

__all__ = ["RiskResult", "RiskInputs", "RiskOutcome", "evaluate"]
