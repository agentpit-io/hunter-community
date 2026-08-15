---
name: uzi_screen
description: "5 套筛选 · value/growth/quality"
hunter:
  display_name: "UZI · 量化筛选"
  icon: "🧮"
  category: "事件与筛选"
  brand: "UZI"
  source_url: "https://github.com/wbh604/UZI-Skill"
  prompt_tpl: "/stock-deep-analyzer:screen {股票}"
  needs_tools:
    - uzi_stock_deep_analysis
  needs_data: []
---

# UZI · 量化筛选

## 这个能力做什么

5 套量化筛选（价值/成长/质量/动量/低波），报告 {股票} 在每套筛选下的排名与被选/落选原因，快速定位股票的风格属性。

## 怎么用

用户提问后,按下面的模板组织分析:

```
/stock-deep-analyzer:screen {股票}
```

## 需要的工具

- `uzi_stock_deep_analysis`

<!-- 迁移自 chat_skill.py 的 BUILTINS · 正文待逐个精修(_14 §6 Step A 第 4 项) -->
