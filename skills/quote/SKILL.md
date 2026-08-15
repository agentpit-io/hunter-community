---
name: quote
description: "查一只或多只股票的最新价与近期波动 · 支持横向对比"
hunter:
  display_name: "行情速查"
  icon: "📊"
  category: "快速判断"
  brand: "finance-data"
  source_url: "https://finance-data.agentpit.io"
  prompt_tpl: "查 {股票} 最新股价 · 多只用顿号分隔可横向对比"
  needs_tools:
    - watchlist_stock_quickview
  needs_data: []
  aliases: "compare"
---

# 行情速查

> 这个 SKILL 是 `watchlist_stock_quickview` 的入口,**没有额外方法论** ——
> 真正干活的是那个工具。下面写的是什么时候用它、拿到结果后注意什么。

## 什么时候用

用户问价格、涨跌、成交量,或者想把几只放一起比。

## 怎么做

调 `watchlist_stock_quickview`。多只标的就逐个调,然后并成一张表:

```
代码    名称    最新价   涨跌幅   成交额   52周分位
```

用户如果还问了「贵不贵」,补一句 52 周分位所处位置 ——
但**不要顺势展开估值判断**,那是同行对标和 DCF 的事,这里只陈述数据。

## 注意

- 行情有延迟,标注出来
- 拿不到就说拿不到,**一个数字都不许编**
- 不给买卖建议

## 需要的工具

- `watchlist_stock_quickview`
