---
name: uzi_segmental_model
description: "分业务线建模 · 波动分析"
hunter:
  display_name: "UZI · 分部建模"
  icon: "🧩"
  category: "估值建模"
  brand: "UZI"
  source_url: "https://github.com/wbh604/UZI-Skill"
  prompt_tpl: "对 {股票} 做分部建模 · 按业务分部分别建收入预测 · 分析各分部对整体估值的贡献与敏感度"
  needs_tools:
    - uzi_stock_deep_analysis
  needs_data: []
---

# UZI · 分部建模

## 这个能力做什么

按公司业务分部（主营/其他）分别建收入预测模型，并做各分部对整体估值的贡献与波动敏感度分析。

## 怎么用

用户提问后,按下面的模板组织分析:

```
对 {股票} 做分部建模 · 按业务分部分别建收入预测 · 分析各分部对整体估值的贡献与敏感度
```

## 需要的工具

- `uzi_stock_deep_analysis`

<!-- 迁移自 chat_skill.py 的 BUILTINS · 正文待逐个精修(_14 §6 Step A 第 4 项) -->
