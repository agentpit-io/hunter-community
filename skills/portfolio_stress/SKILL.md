---
name: portfolio_stress
description: "含板块联动 + 减半建议 · 前置：/portfolio 录 shares+cost"
hunter:
  display_name: "情景模拟"
  icon: "🌊"
  category: "组合级"
  brand: "Hunter 内置"
  prompt_tpl: "如果 {股票} 跌 20% · 我组合会亏多少?"
  needs_tools:
    - portfolio_portfolio_stress
  needs_data: []
---

# 情景模拟

> 这个 SKILL 是 `portfolio_portfolio_stress` 的入口,**没有额外方法论** ——
> 真正干活的是那个工具。下面写的是什么时候用它、拿到结果后注意什么。

## 什么时候用

用户问「如果 XX 跌了我会亏多少」「大盘跌 10% 我扛得住吗」。

## 怎么做

调 `portfolio_portfolio_stress`,给出情景冲击下的组合回撤。

结论要包含三层:
1. **绝对亏损**:多少钱
2. **相对回撤**:占组合百分之多少
3. **集中度暴露**:亏损主要来自哪一两只 —— 这往往才是用户真正需要知道的

## 注意

- 情景是假设,不是预测,说清楚
- 相关性会在下跌时上升,单只跌 20% 时其他持仓通常也在跌 ——
  如果工具没考虑这点,要提醒用户实际情况可能更差

## 需要的工具

- `portfolio_portfolio_stress`
