---
name: deep_analysis
description: 22 维度融合个股深度分析（接入券商数据信封与三栏证据格式）。结合券商官方行情、三大财务报表、估值分位与公告原文，生成包含【已核实事实】【逻辑推断】【待核实问题】的综合投研报告。
version: 3.2.0
author: FloatFu-true & HunterCode Team
license: MIT
metadata:
  tags: [finance, deep-research, valuation, a-share, broker-envelope]
hunter:
  display_name: UZI 深度分析
  icon: Article
  category: 投研报告
  prompt_tpl: 帮我写一份 {股票} 的深度投研报告
  needs_tools:
    - uzi_stock_deep_analysis
    - watchlist_stock_news
    - broker_security_get_security_profile
    - broker_market_get_quote
    - broker_fundamental_get_financial_statements
    - broker_fundamental_get_valuation
    - broker_disclosure_search_announcements
---

# UZI Deep Analysis · 22 维度个股深度投研（券商数据信封版）

## 一、工作流（已接入券商只读 MCP 与统一数据信封）

1. **Step 1：数据采集与同会话复用**
   - 若同会话上下文已有该标的今日有效的 `broker_market_get_quote`、`broker_fundamental_get_financial_statements` 或 `uzi_stock_deep_analysis` 快照，优先复用，避免重复请求。
   - 调用 `broker_security_get_security_profile` 与 `broker_market_get_quote` 获取证券主数据、复权方式、最新报价与停复牌状态。
   - 调用 `broker_fundamental_get_financial_statements` 与 `broker_fundamental_get_valuation` 获取最新报告期三大报表及 PE/PB/PS 历史分位（注意核对 `published_at` 披露时间，严禁使用未来数据）。
   - 调用 `broker_disclosure_search_announcements`、`watchlist_stock_news` 与 `uzi_stock_deep_analysis` 补齐 22 维度深度数据与近期重大事项。

2. **Step 2：数据信封核验与缺失值处理**
   - 检查每个工具返回的 `BrokerDataEnvelope`：核实 `provider`、`as_of`（业务时间）、`published_at`（披露时间）、`retrieved_at`（抓取时间）及 `delay_seconds`。
   - 对 `missing_fields` 或值为 `null` 的指标，在报告中统一填 `—` 并注明原因（如“当期季报未披露该明细”或“标的今日停牌”），**严禁填 `0` 或凭记忆估算**。
   - 若发生主备数据源降级（`is_fallback=true`），必须在报告首部标明备用源名称与降级原因。

3. **Step 3：逻辑加工与三栏证据组装**
   - 应用 UZI 量化体检规则与 90 项基本面/56 项技术指标客观真值进行交叉检验。
   - 参照 `references/report-template.md` 格式，严格按以下**三栏证据结构**输出最终报告：
     - **【已核实事实】**：列出所有带券商数据信封时间戳与来源的客观财务数字、行情价格、估值分位与公告原文条款（引用公告须带 `[来源：公告标题 · 第X页]`）。
     - **【逻辑推断】**：基于上述事实推导的护城河评估、盈利持续性判断、多空因素权衡与估值合理解释。
     - **【待核实问题】**：当前数据缺失项、财务异常待查点（如应收/存货激增原因）及未来 1～2 个季度的核心验证跟踪指标。

## 二、合规与反幻觉硬约束
- 报告中出现的所有百分比、金额、财务比率与价格必须 100% 匹配工具返回的 JSON 真值，严禁大模型心算改写。
- 不提供任何具体买卖点位指令或收益保底承诺，文末附上标准风险提示。
