---
name: uzi_thesis
description: "5 支柱监控 · 假设跟踪"
hunter:
  display_name: "UZI · 投资逻辑追踪"
  icon: "🧭"
  category: "投研报告"
  brand: "UZI"
  source_url: "https://github.com/wbh604/UZI-Skill"
  prompt_tpl: "为 {股票} 建立 5 支柱投资论点(增长/护城河/管理层/财务/催化)· 跟踪支柱状态与假设漂移"
  needs_tools:
    - uzi_stock_deep_analysis
  needs_data: []
---

# UZI · 投资逻辑追踪

## 这个能力做什么

为 {股票} 建立 5 支柱投资论点框架（增长/护城河/管理层/财务/催化），后续每次调用做支柱状态更新与假设漂移预警。

## 怎么用

用户提问后,按下面的模板组织分析:

```
为 {股票} 建立 5 支柱投资论点(增长/护城河/管理层/财务/催化)· 跟踪支柱状态与假设漂移
```

## 需要的工具

- `uzi_stock_deep_analysis`

<!-- 迁移自 chat_skill.py 的 BUILTINS · 正文待逐个精修(_14 §6 Step A 第 4 项) -->
