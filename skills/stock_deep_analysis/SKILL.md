---
name: stock_deep_analysis
description: "22 维度融合 · 8 数据源 · 约 7s 出 LITE 报告"
hunter:
  display_name: "UZI · 深度分析"
  icon: "🎯"
  category: "综合分析"
  brand: "UZI"
  source_url: "https://github.com/wbh604/UZI-Skill"
  prompt_tpl: "深度分析 {股票}"
  needs_tools:
    - uzi_stock_deep_analysis
  needs_data: []
---

# UZI · 深度分析

## 这个能力做什么

UZI-Skill 提供的综合深度分析：融合行情/K线/财务/龙虎榜/十大股东/治理/新闻/研报 8 大数据源共 22 维度，LLM 一次性合成多空核心观点 + 技术面 + 基本面 + 风险提示的结构化报告。适合决策前的全景扫查。

## 怎么用

用户提问后,按下面的模板组织分析:

```
深度分析 {股票}
```

## 需要的工具

- `uzi_stock_deep_analysis`

<!-- 迁移自 chat_skill.py 的 BUILTINS · 正文待逐个精修(_14 §6 Step A 第 4 项) -->
