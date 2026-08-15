---
name: uzi_lbo
description: "PE 买方能赚多少 IRR"
hunter:
  display_name: "UZI · LBO 测试"
  icon: "🏦"
  category: "估值建模"
  brand: "UZI"
  source_url: "https://github.com/wbh604/UZI-Skill"
  prompt_tpl: "对 {股票} 做 LBO 测试 · 模拟 PE 买方在合理杠杆下的进入/退出 · 输出 5 年 IRR 与 MoM"
  needs_tools:
    - uzi_stock_deep_analysis
  needs_data: []
---

# UZI · LBO 测试

## 这个能力做什么

杠杆收购 (LBO) 情景测试：模拟 PE 买方在合理杠杆结构下的进入/退出，输出 5 年期 IRR 与 MoM，判断该标的对 PE 买方的吸引力。

## 怎么用

用户提问后,按下面的模板组织分析:

```
对 {股票} 做 LBO 测试 · 模拟 PE 买方在合理杠杆下的进入/退出 · 输出 5 年 IRR 与 MoM
```

## 需要的工具

- `uzi_stock_deep_analysis`

<!-- 迁移自 chat_skill.py 的 BUILTINS · 正文待逐个精修(_14 §6 Step A 第 4 项) -->
