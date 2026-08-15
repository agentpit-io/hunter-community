---
name: uzi_dd
description: "5 工作流 · 21 项检查"
hunter:
  display_name: "UZI · 尽调清单"
  icon: "🔎"
  category: "尽调风控"
  brand: "UZI"
  source_url: "https://github.com/wbh604/UZI-Skill"
  prompt_tpl: "/stock-deep-analyzer:dd {股票}"
  needs_tools:
    - uzi_stock_deep_analysis
  needs_data: []
---

# UZI · 尽调清单

## 这个能力做什么

完整尽调清单：5 大工作流（财务/法律/运营/管理层/行业）覆盖 21 项检查点，逐条给出结论 + 证据链接 + 风险等级。

## 怎么用

用户提问后,按下面的模板组织分析:

```
/stock-deep-analyzer:dd {股票}
```

## 需要的工具

- `uzi_stock_deep_analysis`

<!-- 迁移自 chat_skill.py 的 BUILTINS · 正文待逐个精修(_14 §6 Step A 第 4 项) -->
