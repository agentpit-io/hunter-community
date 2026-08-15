---
name: debate
description: "6 位分析师 · 2 轮辩论 · 60-90s 出深度报告"
hunter:
  display_name: "TradingAgents · 多专家辩论"
  icon: "⚖️"
  category: "综合分析"
  brand: "TradingAgents"
  source_url: "https://github.com/TauricResearch/TradingAgents"
  prompt_tpl: "对 {股票} 做多空辩论 · 给出买卖决策"
  needs_tools: []
  needs_data: []
---

# TradingAgents · 多专家辩论

## 这个能力做什么

TauricResearch 开源的多智能体金融辩论框架，模拟 6 位分析师从技术面/基本面/资金面/新闻/风险等角度多轮对话，汇聚成买/卖/持决策与置信度。适合争议股或大仓位决策前作压力测试。

## 怎么用

用户提问后,按下面的模板组织分析:

```
对 {股票} 做多空辩论 · 给出买卖决策
```

<!-- 迁移自 chat_skill.py 的 BUILTINS · 正文待逐个精修(_14 §6 Step A 第 4 项) -->
