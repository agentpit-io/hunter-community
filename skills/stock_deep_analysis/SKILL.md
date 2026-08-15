---
name: stock_deep_analysis
description: "22 维度融合 · 8 数据源 · 约 7s 出 LITE 报告"
hunter:
  display_name: "UZI · 深度分析"
  icon: "🎯"
  category: "综合分析"
  brand: "UZI"
  source_url: "https://github.com/wbh604/UZI-Skill"
  prompt_tpl: "深度分析 {股票}"
  needs_tools:
    - uzi_stock_deep_analysis
  needs_data: []
---

# UZI · 深度分析

> 这个 SKILL 是 `uzi_stock_deep_analysis` 的入口,**没有额外方法论** ——
> 真正干活的是那个工具。下面写的是什么时候用它、拿到结果后注意什么。

## 什么时候用

用户笼统地说「深度分析 XX」「帮我看看这只票」,没有指定具体角度时。

**如果用户指定了角度**(要估值、要财报、要尽调),用对应的专项 SKILL,
它们的方法论比这里细。这个 SKILL 是**总入口**。

## 怎么做

调 `uzi_stock_deep_analysis`,它一次拉七类数据(行情/K线/财务/龙虎榜/
十大股东/治理/新闻)并合成结构化报告。

拿到之后**不要照抄**,要做三件工具做不了的事:

1. **指出矛盾**:比如营收增长但经营现金流下滑、股价新高但机构在减持
2. **补足缺失**:报告里写「数据未 seed」的部分,说明缺什么、影响什么结论
3. **给出下一步**:基于看到的东西,建议用户接着做哪个专项分析

## 注意

- 七类数据里股东/治理目前只覆盖少数股票,缺了要明说
- 这是耗时工具(5-10 秒),先告诉用户在取数

## 需要的工具

- `uzi_stock_deep_analysis`
