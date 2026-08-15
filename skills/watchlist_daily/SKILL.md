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

> 这个 SKILL 是 `watchlist_watchlist_digest` 的入口,**没有额外方法论** ——
> 真正干活的是那个工具。下面写的是什么时候用它、拿到结果后注意什么。

## 什么时候用

用户问自选股整体表现、今天谁强谁弱。

## 怎么做

调 `watchlist_watchlist_digest` 一次拿全部自选股行情,然后:

1. 按涨跌幅排序,给出最强/最弱各 3 只
2. 指出**异动**:涨跌幅显著偏离自选股均值的
3. 若有成交额异常放大的,单独点出来

## 注意

- 自选股为空时,提示用户先添加,不要报错
- 只陈述表现,不对每只都给操作建议

## 需要的工具

- `watchlist_watchlist_digest`
