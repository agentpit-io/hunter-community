---
name: uzi_dcf
description: "WACC + 5×5 敏感性表"
hunter:
  display_name: "UZI · DCF 估值"
  icon: "💰"
  category: "估值建模"
  brand: "UZI"
  source_url: "https://github.com/wbh604/UZI-Skill"
  prompt_tpl: "对 {股票} 做 DCF 估值 · 估算 WACC、构建 10 年预测期 + 永续期模型 · 输出内在价值与 5×5 敏感性表"
  needs_tools:
    - uzi_stock_deep_analysis
  needs_data: []
---

# UZI · DCF 估值

## 这个能力做什么

对 {股票} 做 DCF 现金流折现估值：估算 WACC、构建 10 年预测期 + 永续期模型，输出内在价值、隐含 upside/downside，附 5×5 敏感性表（永续增速 × WACC）。

## 怎么用

用户提问后,按下面的模板组织分析:

```
对 {股票} 做 DCF 估值 · 估算 WACC、构建 10 年预测期 + 永续期模型 · 输出内在价值与 5×5 敏感性表
```

## 需要的工具

- `uzi_stock_deep_analysis`

<!-- 迁移自 chat_skill.py 的 BUILTINS · 正文待逐个精修(_14 §6 Step A 第 4 项) -->
