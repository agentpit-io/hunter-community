---
name: thesis
description: "结构化投资逻辑 · 可跟踪验证"
hunter:
  display_name: "论点起草"
  icon: "📝"
  category: "投研报告"
  brand: "Hunter 内置"
  prompt_tpl: "帮我起草 {股票} 的投资论点,并列出 3 条关键假设"
  needs_tools: []
  needs_data: []
---

# 论点起草

## 这个能力做什么

纯 LLM 结构化输出投资论点：核心逻辑 + 3 条关键假设 + 反证信号。方便日后跟踪验证假设是否成立，形成可复盘的决策记录。

## 怎么用

用户提问后,按下面的模板组织分析:

```
帮我起草 {股票} 的投资论点,并列出 3 条关键假设
```

<!-- 迁移自 chat_skill.py 的 BUILTINS · 正文待逐个精修(_14 §6 Step A 第 4 项) -->
