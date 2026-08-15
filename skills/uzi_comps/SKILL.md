---
name: uzi_comps
description: "PE/PB 分位分析"
hunter:
  display_name: "UZI · 同行对标"
  icon: "⚖️"
  category: "估值建模"
  brand: "UZI"
  source_url: "https://github.com/wbh604/UZI-Skill"
  prompt_tpl: "/stock-deep-analyzer:comps {股票}"
  needs_tools:
    - uzi_stock_deep_analysis
  needs_data: []
---

# UZI · 同行对标

## 这个能力做什么

选取可比公司，按 PE/PB/PS/EV-EBITDA 多倍数横向对比，给出 {股票} 在同业中的分位数与合理估值区间。

## 怎么用

用户提问后,按下面的模板组织分析:

```
/stock-deep-analyzer:comps {股票}
```

## 需要的工具

- `uzi_stock_deep_analysis`

<!-- 迁移自 chat_skill.py 的 BUILTINS · 正文待逐个精修(_14 §6 Step A 第 4 项) -->
