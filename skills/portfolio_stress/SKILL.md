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

## 这个能力做什么

压力测试：假设某股跌 X%，通过板块联动系数估算组合整体回撤，给出可选的减仓/对冲建议。前置：需在 /portfolio 页录入 shares + cost。

## 怎么用

用户提问后,按下面的模板组织分析:

```
如果 {股票} 跌 20% · 我组合会亏多少?
```

## 需要的工具

- `portfolio_portfolio_stress`

<!-- 迁移自 chat_skill.py 的 BUILTINS · 正文待逐个精修(_14 §6 Step A 第 4 项) -->
