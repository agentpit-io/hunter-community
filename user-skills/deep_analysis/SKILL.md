---
name: deep_analysis
description: 22 维度融合个股深度分析。生成包含基本面、技术面、资金面及投委会结论的综合报告。
version: 3.9.4
author: FloatFu-true
license: MIT
metadata:
  tags: [finance, deep-research, valuation, a-share]
hunter:
  display_name: UZI 深度分析
  icon: Article
  category: 投研报告
  prompt_tpl: 帮我写一份 {股票} 的深度投研报告
---

# UZI Deep Analysis (Adapted)

## 工作流 (已适配本系统)

1. **Step 1: 数据采集**
   - 调用 `uzi_stock_deep_analysis` 获取个股 22 维度深度数据（含财务、治理、估值分位）。
   - 调用 `watchlist_stock_news` 获取近期重大事项。
2. **Step 2: 逻辑加工**
   - 应用 UZI 180 条量化规则进行初筛。
   - 分析财务造假风险与行业竞争力。
3. **Step 3: 报告组装**
   - 参照 `references/report-template.md` 格式，输出包含「投资论点、风险点、估值区间」的最终报告。