---
name: uzi
description: A 股、港股与美股的投研总调度技能，覆盖深度研究、快速扫描、投委会评审、游资/龙虎榜(LHB)分析、杀猪盘识别、估值测算、IC 投资备忘录，以及 Bloomberg 风格的 HTML 报告。
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

分发**用 `use_skill` 工具**，参数是技能名；不要去 `read_file` 技能文件的路径。

1. **深度调研/报告**: `use_skill(name="deep_analysis")`
2. **大佬评审团**: `use_skill(name="investor_panel")`
3. **龙虎榜/游资**: `use_skill(name="lhb_analyzer")`
4. **风险/杀猪盘扫描**: `use_skill(name="trap_detector")`

> 这四个名字是**下划线**写法。原文写的是 `skills/deep-analysis/SKILL.md` 这类
> **连字符路径**，那是 hunter-community 里的目录名 —— 本发行版按 AtomCode 的技能
> 命名改成了下划线，而且技能是通过 `use_skill` 加载的、不在工作区那个相对路径下。
> 照原文去 `read_file` 会扑空，白烧一轮。

## 核心原则
- 严禁捏造数据，必须调用本系统内置工具获取实时行情与财务指标。
- 优先展示结论，再列示依据。