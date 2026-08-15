---
name: forecast
description: "清华 Kronos 金融时序大模型"
hunter:
  display_name: "Kronos · 走势预测"
  icon: "📈"
  category: "快速判断"
  brand: "Kronos"
  source_url: "https://github.com/shiyu-coder/Kronos"
  prompt_tpl: "用 Kronos 预测 {股票} 未来 5 天走势"
  needs_tools: []
  needs_data: []
---

# Kronos · 走势预测

## 这个能力做什么

清华大学开源的金融时序 Transformer 模型，直接对 K 线序列建模，输出未来 N 天开高低收预测与置信区间。擅长捕捉短期动量与技术图形的续接概率，作辅助判断而非交易信号。

## 怎么用

用户提问后,按下面的模板组织分析:

```
用 Kronos 预测 {股票} 未来 5 天走势
```

<!-- 迁移自 chat_skill.py 的 BUILTINS · 正文待逐个精修(_14 §6 Step A 第 4 项) -->
