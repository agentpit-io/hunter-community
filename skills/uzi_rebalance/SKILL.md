---
name: uzi_rebalance
description: "v3.8 · 漂移 + 换手成本"
hunter:
  display_name: "UZI · 逐持仓再平衡"
  icon: "🔀"
  category: "组合级"
  brand: "UZI"
  source_url: "https://github.com/wbh604/UZI-Skill"
  prompt_tpl: "对我当前持仓做再平衡建议 · 目标与当前权重的漂移、交易清单、扣除 A 股印花税与佣金后的净换手成本"
  needs_tools:
    - uzi_stock_deep_analysis
  needs_data: []
---

# UZI · 逐持仓再平衡

## 这个能力做什么

v3.8 新增。逐持仓生成再平衡建议：目标 vs 当前权重漂移 + 交易清单 + 考虑 A 股印花税/佣金后的净换手成本。前置：需在 /portfolio 页录入持仓。

## 怎么用

用户提问后,按下面的模板组织分析:

```
对我当前持仓做再平衡建议 · 目标与当前权重的漂移、交易清单、扣除 A 股印花税与佣金后的净换手成本
```

## 需要的工具

- `uzi_stock_deep_analysis`

<!-- 迁移自 chat_skill.py 的 BUILTINS · 正文待逐个精修(_14 §6 Step A 第 4 项) -->
