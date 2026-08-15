---
name: stock_news
description: "5 条精选 · 每条 AI 影响短评（利好/利空/中性/强利好）"
hunter:
  display_name: "关键新闻"
  icon: "📰"
  category: "事件与筛选"
  brand: "finance-data"
  source_url: "https://finance-data.agentpit.io"
  prompt_tpl: "{股票} 最近有什么关键新闻?"
  needs_tools:
    - watchlist_stock_news
  needs_data: []
---

# 关键新闻

## 这个能力做什么

抓取近 7 天该股相关新闻，去重后 LLM 精选 5 条并逐条打影响标签（利好/利空/中性/强利好）。适合快速摸清市场情绪与近期催化。

## 怎么用

用户提问后,按下面的模板组织分析:

```
{股票} 最近有什么关键新闻?
```

## 需要的工具

- `watchlist_stock_news`

<!-- 迁移自 chat_skill.py 的 BUILTINS · 正文待逐个精修(_14 §6 Step A 第 4 项) -->
