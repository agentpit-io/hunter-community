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

> 这个 SKILL 是 `watchlist_stock_news` 的入口,**没有额外方法论** ——
> 真正干活的是那个工具。下面写的是什么时候用它、拿到结果后注意什么。

## 什么时候用

用户问某只股票「最近有什么消息」「为什么涨/跌」。

## 怎么做

调 `watchlist_stock_news`,按时间倒序列出,每条给:日期 · 标题 · 来源。

用户问「为什么涨跌」时,**不要把新闻和股价强行因果化**。
诚实的说法是「同期有这几条消息」,而不是「因为这条消息所以涨了」——
除非涨跌幅与消息时点高度吻合且幅度显著,那时可以说"时点吻合"。

## 注意

- 没有新闻就说没有,不要拿旧闻凑数
- 新闻源的可靠性差异很大,标题党要标注

## 需要的工具

- `watchlist_stock_news`
