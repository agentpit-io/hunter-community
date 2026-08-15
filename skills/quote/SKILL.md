---
name: quote
description: "实时开高低收 · 成交量 · 五档盘口"
hunter:
  display_name: "行情速查"
  icon: "📊"
  category: "快速判断"
  brand: "finance-data"
  source_url: "https://finance-data.agentpit.io"
  prompt_tpl: "查 {股票} 最新股价"
  needs_tools:
    - watchlist_stock_quickview
  needs_data: []
---

# 行情速查

## 这个能力做什么

调用 finance-data 数据源获取 A 股/港股实时行情，包含开高低收、涨跌幅、成交量与五档盘口。数据延迟 <3 秒，交易时段每 30 秒滚动更新。适合决策前快速核对当前价位。

## 怎么用

用户提问后,按下面的模板组织分析:

```
查 {股票} 最新股价
```

## 需要的工具

- `watchlist_stock_quickview`

<!-- 迁移自 chat_skill.py 的 BUILTINS · 正文待逐个精修(_14 §6 Step A 第 4 项) -->
