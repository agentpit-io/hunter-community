---
name: uzi_initiate
description: "JPM/GS 格式 · 完整首覆"
hunter:
  display_name: "UZI · 机构首次覆盖"
  icon: "📖"
  category: "投研报告"
  brand: "UZI"
  source_url: "https://github.com/wbh604/UZI-Skill"
  prompt_tpl: "/stock-deep-analyzer:initiate {股票}"
  needs_tools:
    - uzi_stock_deep_analysis
  needs_data: []
---

# UZI · 机构首次覆盖

## 这个能力做什么

按 JPM/GS 卖方研究员标准格式，产出机构首次覆盖报告：公司概况、投资亮点、估值模型、风险因素、评级/目标价。

## 怎么用

用户提问后,按下面的模板组织分析:

```
/stock-deep-analyzer:initiate {股票}
```

## 需要的工具

- `uzi_stock_deep_analysis`

<!-- 迁移自 chat_skill.py 的 BUILTINS · 正文待逐个精修(_14 §6 Step A 第 4 项) -->
