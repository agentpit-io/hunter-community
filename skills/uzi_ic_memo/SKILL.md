---
name: uzi_ic_memo
description: "三情景回报 · IC 汇报格式"
hunter:
  display_name: "UZI · 投委会备忘录"
  icon: "📄"
  category: "投研报告"
  brand: "UZI"
  source_url: "https://github.com/wbh604/UZI-Skill"
  prompt_tpl: "/stock-deep-analyzer:ic-memo {股票}"
  needs_tools:
    - uzi_stock_deep_analysis
  needs_data: []
---

# UZI · 投委会备忘录

## 这个能力做什么

投资委员会 (IC) 汇报格式的备忘录：base/bull/bear 三情景对应回报分布、概率、关键假设与风险预案。

## 怎么用

用户提问后,按下面的模板组织分析:

```
/stock-deep-analyzer:ic-memo {股票}
```

## 需要的工具

- `uzi_stock_deep_analysis`

<!-- 迁移自 chat_skill.py 的 BUILTINS · 正文待逐个精修(_14 §6 Step A 第 4 项) -->
