---
name: uzi_scan_trap
description: "拉高出货 / 财务造假信号"
hunter:
  display_name: "UZI · 杀猪盘排查"
  icon: "⚠️"
  category: "尽调风控"
  brand: "UZI"
  source_url: "https://github.com/wbh604/UZI-Skill"
  prompt_tpl: "排查 {股票} 是否有杀猪盘特征 · 异常拉升与基本面脱节、股东减持时点、财务造假信号、社交推票热度 · 给出预警等级"
  needs_tools:
    - uzi_stock_deep_analysis
  needs_data: []
---

# UZI · 杀猪盘排查

## 这个能力做什么

扫描杀猪盘典型模式：异常拉升 vs 基本面脱节、股东减持时点、财务造假特征、社交平台推票热度等，给出预警等级。

## 怎么用

用户提问后,按下面的模板组织分析:

```
排查 {股票} 是否有杀猪盘特征 · 异常拉升与基本面脱节、股东减持时点、财务造假信号、社交推票热度 · 给出预警等级
```

## 需要的工具

- `uzi_stock_deep_analysis`

<!-- 迁移自 chat_skill.py 的 BUILTINS · 正文待逐个精修(_14 §6 Step A 第 4 项) -->
