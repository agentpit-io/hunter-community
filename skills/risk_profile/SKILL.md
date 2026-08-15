---
name: risk_profile
description: "读/改风险偏好 + 现金 + 单票/HK 上限 · 供组合建议自动应用"
hunter:
  display_name: "风险画像"
  icon: "🎚"
  category: "组合级"
  brand: "Hunter 内置"
  prompt_tpl: "我风险偏保守 · 现金还有 5 万 · 单票别超过 20%"
  needs_tools:
    - portfolio_update_risk_profile
  needs_data: []
---

# 风险画像

## 这个能力做什么

用自然语言更新你的风险偏好、可投现金、单票上限、HK 敞口上限。参数存 user_memory，供后续组合建议自动应用。

## 怎么用

用户提问后,按下面的模板组织分析:

```
我风险偏保守 · 现金还有 5 万 · 单票别超过 20%
```

## 需要的工具

- `portfolio_update_risk_profile`

<!-- 迁移自 chat_skill.py 的 BUILTINS · 正文待逐个精修(_14 §6 Step A 第 4 项) -->
