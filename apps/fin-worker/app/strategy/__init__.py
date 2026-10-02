"""策略侧（出意图的那一半）。

一期只有一个 **固定示例策略**（`08 §二`：本轮目标是把管道跑通，不是把策略做强）。
它「按档位参数出意图」：读项目的档位（`play` / `manage` / `operate`），
按档位给一个固定的示例手数，产出一份 `StrategyDecision`。

**它不产生新的投资观点** —— 买什么是配置里写死的示例标的，买多少是档位决定的，
没有任何「分析」。真正做分析的是研究区（`opencode` / 迭代智能体），
它们把结论写成 `StrategyDecision` 交给桥（`01方案 §11.3`）。
"""

from app.strategy.sample import SAMPLE_LOTS, build_decision  # noqa: F401
