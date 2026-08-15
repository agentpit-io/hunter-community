---
name: uzi_quick_scan
description: "30 秒结论"
hunter:
  display_name: "UZI · 30 秒速判"
  icon: "⚡"
  category: "快速判断"
  brand: "UZI"
  source_url: "https://github.com/wbh604/UZI-Skill"
  prompt_tpl: "对 {股票} 做 30 秒速判 · 给出买/卖/持结论 + 一句话理由 + 关键关注点"
  needs_tools:
    - uzi_stock_deep_analysis
  needs_data: []
---

# UZI · 30 秒速判

## 这个能力做什么

30 秒得出对 {股票} 的极简结论：买/卖/持 + 一句话理由 + 关键关注点。适合盘中决策或初筛。

## 怎么用

用户提问后,按下面的模板组织分析:

```
对 {股票} 做 30 秒速判 · 给出买/卖/持结论 + 一句话理由 + 关键关注点
```

## 需要的工具

- `uzi_stock_deep_analysis`

<!-- 迁移自 chat_skill.py 的 BUILTINS · 正文待逐个精修(_14 §6 Step A 第 4 项) -->
