---
name: uzi_model_update
description: "v3.8 · 新财报/指引 delta → DCF/thesis 影响"
hunter:
  display_name: "UZI · 模型增量更新"
  icon: "🔄"
  category: "估值建模"
  brand: "UZI"
  source_url: "https://github.com/wbh604/UZI-Skill"
  prompt_tpl: "基于 {股票} 最新财报或管理层指引 · 识别关键假设 delta · 更新 DCF 与投资论点 · 产出增量影响报告"
  needs_tools:
    - uzi_stock_deep_analysis
  needs_data: []
---

# UZI · 模型增量更新

## 这个能力做什么

v3.8 新增。基于最新披露的财报或管理层指引，识别关键假设的 delta，快速更新 DCF 与投资论点，产出增量影响报告。

## 怎么用

用户提问后,按下面的模板组织分析:

```
基于 {股票} 最新财报或管理层指引 · 识别关键假设 delta · 更新 DCF 与投资论点 · 产出增量影响报告
```

## 需要的工具

- `uzi_stock_deep_analysis`

<!-- 迁移自 chat_skill.py 的 BUILTINS · 正文待逐个精修(_14 §6 Step A 第 4 项) -->
