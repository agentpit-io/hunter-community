---
name: compare
description: "多只股票横向比较"
hunter:
  display_name: "同业对比"
  icon: "🔍"
  category: "快速判断"
  brand: "finance-data"
  source_url: "https://finance-data.agentpit.io"
  prompt_tpl: "对比 {股票A} 和 {股票B} 的最新股价与 30 日振幅"
  needs_tools:
    - watchlist_stock_quickview
  needs_data: []
---

# 同业对比

## 这个能力做什么

多只股票横向比较：最新价、涨跌幅、成交量、30 日振幅、市值。适合做同业挑选或替代品判断。

## 怎么用

用户提问后,按下面的模板组织分析:

```
对比 {股票A} 和 {股票B} 的最新股价与 30 日振幅
```

## 需要的工具

- `watchlist_stock_quickview`

<!-- 迁移自 chat_skill.py 的 BUILTINS · 正文待逐个精修(_14 §6 Step A 第 4 项) -->
