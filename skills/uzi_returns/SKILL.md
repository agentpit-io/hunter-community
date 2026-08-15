---
name: uzi_returns
description: "v3.8 · 按持仓/行业拆解"
hunter:
  display_name: "UZI · 组合收益归因"
  icon: "📊"
  category: "组合级"
  brand: "UZI"
  source_url: "https://github.com/wbh604/UZI-Skill"
  prompt_tpl: "对我当前组合做收益归因 · 按持仓、行业、风格因子拆解累计收益 · 列出 Top 贡献与 Top 拖累"
  needs_tools:
    - uzi_stock_deep_analysis
  needs_data: []
---

# UZI · 组合收益归因

## 这个能力做什么

v3.8 新增。对当前组合做收益归因：按持仓、行业、风格因子多维度拆解累计收益，输出 Top 贡献与 Top 拖累。前置：需在 /portfolio 页录入持仓。

## 怎么用

用户提问后,按下面的模板组织分析:

```
对我当前组合做收益归因 · 按持仓、行业、风格因子拆解累计收益 · 列出 Top 贡献与 Top 拖累
```

## 需要的工具

- `uzi_stock_deep_analysis`

<!-- 迁移自 chat_skill.py 的 BUILTINS · 正文待逐个精修(_14 §6 Step A 第 4 项) -->
