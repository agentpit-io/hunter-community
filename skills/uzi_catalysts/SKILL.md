---
name: uzi_catalysts
description: "未来 60 天关键事件"
hunter:
  display_name: "UZI · 催化剂日历"
  icon: "🗓️"
  category: "事件与筛选"
  brand: "UZI"
  source_url: "https://github.com/wbh604/UZI-Skill"
  prompt_tpl: "/stock-deep-analyzer:catalysts {股票}"
  needs_tools:
    - uzi_stock_deep_analysis
  needs_data: []
---

# UZI · 催化剂日历

## 这个能力做什么

整理未来 60 天该股相关的关键事件日历：财报、指引、政策窗口、行业会议、股东大会、解禁、除权。附对股价的潜在影响判断。

## 怎么用

用户提问后,按下面的模板组织分析:

```
/stock-deep-analyzer:catalysts {股票}
```

## 需要的工具

- `uzi_stock_deep_analysis`

<!-- 迁移自 chat_skill.py 的 BUILTINS · 正文待逐个精修(_14 §6 Step A 第 4 项) -->
