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

> 这个 SKILL 是 `portfolio_update_risk_profile` 的入口,**没有额外方法论** ——
> 真正干活的是那个工具。下面写的是什么时候用它、拿到结果后注意什么。

## 什么时候用

用户描述自己的风险偏好、可用资金、单票上限等约束时。

## 怎么做

调 `portfolio_update_risk_profile` 把约束记下来。记完**复述一遍**让用户确认。

之后的组合建议都要遵守这些约束 —— 如果某个建议会突破约束(比如单票超限),
明确指出来而不是默默调整。

## 注意

- 这是**写入**操作,记错了会一直影响后续建议,所以要复述确认
- 用户没说的不要替他假设

## 需要的工具

- `portfolio_update_risk_profile`
