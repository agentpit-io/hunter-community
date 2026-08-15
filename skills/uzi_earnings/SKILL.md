---
name: uzi_earnings
description: "beat/miss 检测 · 逐条对齐"
hunter:
  display_name: "UZI · 财报解读"
  icon: "💹"
  category: "投研报告"
  brand: "UZI"
  source_url: "https://github.com/wbh604/UZI-Skill"
  prompt_tpl: "深度解读 {股票} 最新财报 · 对齐一致预期做 beat/miss 检测 · 拆解 EPS 驱动因子 · 评估管理层指引质量"
  needs_tools:
    - uzi_stock_deep_analysis
  needs_data: []
---

# UZI · 财报解读

## 这个能力做什么

深度解读最新财报：对齐一致预期做 beat/miss 检测，拆解 EPS 驱动因子，管理层指引质量评分，一图看清季度表现。

## 怎么用

用户提问后,按下面的模板组织分析:

```
深度解读 {股票} 最新财报 · 对齐一致预期做 beat/miss 检测 · 拆解 EPS 驱动因子 · 评估管理层指引质量
```

## 需要的工具

- `uzi_stock_deep_analysis`

<!-- 迁移自 chat_skill.py 的 BUILTINS · 正文待逐个精修(_14 §6 Step A 第 4 项) -->
