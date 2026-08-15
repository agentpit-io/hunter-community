---
name: uzi_panel_only
description: "只看专家评委结果"
hunter:
  display_name: "UZI · 66 评委投票"
  icon: "🗳️"
  category: "快速判断"
  brand: "UZI"
  source_url: "https://github.com/wbh604/UZI-Skill"
  prompt_tpl: "/stock-deep-analyzer:panel-only {股票}"
  needs_tools:
    - uzi_stock_deep_analysis
  needs_data: []
---

# UZI · 66 评委投票

## 这个能力做什么

跳过分析过程，直接产出 66 个专家评委的投票结果与共识度：买/持/卖分布、置信度、少数派意见摘要。

## 怎么用

用户提问后,按下面的模板组织分析:

```
/stock-deep-analyzer:panel-only {股票}
```

## 需要的工具

- `uzi_stock_deep_analysis`

<!-- 迁移自 chat_skill.py 的 BUILTINS · 正文待逐个精修(_14 §6 Step A 第 4 项) -->
