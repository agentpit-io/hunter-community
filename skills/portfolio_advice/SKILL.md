---
name: portfolio_advice
description: "当前 vs 目标权重 + 加/减仓动作 · 前置：/portfolio 录 shares+cost"
hunter:
  display_name: "组合级建议"
  icon: "🎯"
  category: "组合级"
  brand: "Hunter 内置"
  prompt_tpl: "帮我看看我的持仓怎么调仓"
  needs_tools:
    - portfolio_portfolio_rebalance
  needs_data: []
---

# 组合级建议

## 这个能力做什么

对比当前持仓权重与目标配置（按风险画像动态计算），给出具体加/减仓动作与量级。前置：需在 /portfolio 页录入 shares + cost。

## 怎么用

用户提问后,按下面的模板组织分析:

```
帮我看看我的持仓怎么调仓
```

## 需要的工具

- `portfolio_portfolio_rebalance`

<!-- 迁移自 chat_skill.py 的 BUILTINS · 正文待逐个精修(_14 §6 Step A 第 4 项) -->
