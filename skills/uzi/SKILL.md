---
name: uzi
description: A-share, Hong Kong, and US stock analysis skill for deep research, quick scans, investor panel review, hot-money/LHB analysis, trap detection, valuation, IC memos, and Bloomberg-style HTML reports.
version: 3.9.4
author: FloatFu-true
license: MIT
metadata:
  tags: [finance, stocks, a-share, hong-kong, us-stocks, dcf, valuation, investor-panel, youzi, lhb, trap-detection]
  related_skills: [deep-analysis, investor-panel, lhb-analyzer, trap-detector]
hunter:
  display_name: UZI 投研总调度
  icon: Hub
  category: 综合分析
  prompt_tpl: 对 {股票} 进行 UZI 全方位深度扫描
  needs_tools:
    - uzi_stock_deep_analysis
---

# UZI Skill Root (Adapted)

作为投研任务的总调度器。当用户请求深度分析、评审团、龙虎榜或风险扫描时，由你负责协调数据采集并分发至子 SKILL。

## 执行规则 (已适配本系统)

1. **深度调研/报告**: 调用 `skills/deep-analysis/SKILL.md`。
2. **大佬评审团**: 调用 `skills/investor-panel/SKILL.md`。
3. **龙虎榜/游资**: 调用 `skills/lhb-analyzer/SKILL.md`。
4. **风险/杀猪盘扫描**: 调用 `skills/trap-detector/SKILL.md`。

## 核心原则
- 严禁捏造数据，必须调用本系统内置工具获取实时行情与财务指标。
- 优先展示结论，再列示依据。