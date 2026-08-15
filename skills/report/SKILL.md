---
name: report
description: "利润表 · 资产负债表 · 现金流量表"
hunter:
  display_name: "财报要点"
  icon: "📋"
  category: "投研报告"
  brand: "finance-data"
  source_url: "https://finance-data.agentpit.io"
  prompt_tpl: "{股票} 最新财报要点,3 条"
  needs_tools:
    - uzi_stock_deep_analysis
  needs_data: []
---

# 财报要点

## 这个能力做什么

拉取最新披露的三大报表关键指标（营收/净利润/毛利率/资产负债率/经营现金流），LLM 提炼 3 条要点。适合快速了解近期基本面变化，不做深度财务模型。

## 怎么用

用户提问后,按下面的模板组织分析:

```
{股票} 最新财报要点,3 条
```

## 需要的工具

- `uzi_stock_deep_analysis`

<!-- 迁移自 chat_skill.py 的 BUILTINS · 正文待逐个精修(_14 §6 Step A 第 4 项) -->
