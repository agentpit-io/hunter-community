---
name: watchlist_daily
description: "自选涨跌排序 + Top 3 AI 归因 · 前置：先加自选"
hunter:
  display_name: "自选股日报"
  icon: "🌅"
  category: "组合级"
  brand: "Hunter 内置"
  prompt_tpl: "我的自选股今天谁最强、谁最弱?"
  needs_tools:
    - watchlist_watchlist_digest
  needs_data: []
---

# 自选股日报

## 这个能力做什么

读取你的自选股清单，按当日涨跌幅排序，对涨/跌 Top 3 用 LLM 做归因（板块/消息/资金面）。适合每日盘后 3 分钟摸清自选整体状况。前置：需先在自选页添加股票。

## 怎么用

用户提问后,按下面的模板组织分析:

```
我的自选股今天谁最强、谁最弱?
```

## 需要的工具

- `watchlist_watchlist_digest`

<!-- 迁移自 chat_skill.py 的 BUILTINS · 正文待逐个精修(_14 §6 Step A 第 4 项) -->
