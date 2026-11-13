---
name: lhb_analyzer
description: 龙虎榜深度分析器（接入券商数据信封与三栏证据格式）。结合券商行情盘口、异动公告与龙虎榜席位数据，识别游资席位、判断机构 vs 游资博弈。
version: 3.2.0
author: FloatFu-true & HunterCode Team
license: MIT
metadata:
  tags: [finance, a-share, lhb, hot-money, broker-envelope]
hunter:
  display_name: UZI 龙虎榜分析
  icon: QueryStats
  category: 事件与筛选
  prompt_tpl: 分析 {股票} 的龙虎榜，看看是哪家游资在买
  needs_tools:
    - uzi_stock_deep_analysis
    - broker_market_get_quote
    - broker_market_get_orderbook
    - broker_disclosure_search_announcements
---

# 龙虎榜深度分析（券商数据信封 & 三栏证据适配版）

## 一、数据流（已接入券商只读 MCP 与统一数据信封）

1. **取数与同会话复用**：
   - 若同会话上下文已有当日 `broker_market_get_quote` 或 `uzi_stock_deep_analysis` 快照，优先复用。
   - 调用 `broker_market_get_quote`、`broker_market_get_orderbook` 获取当日收盘价、涨跌停价、换手率、成交额与五档盘口状态。
   - 调用 `broker_disclosure_search_announcements` 与 `uzi_stock_deep_analysis` 获取交易异常波动公告与龙虎榜前五大买卖席位原始数据。
2. **信封时间戳与缺失值校验**：
   - 核验 `BrokerDataEnvelope` 的 `as_of`（行情时间）与 `published_at`（榜单/公告发布时间），严禁拿抓取时间（`retrieved_at`）冒充上榜日期。
   - 若某股票当日未上龙虎榜或席位数据缺失（`null`），直接如实告知“当日无龙虎榜披露数据（`—`）”，严禁编造席位名称或买卖金额。
3. **游资与机构席位识别**：
   - 对照客观席位百科（机构专用、沪深股通专用、知名游资常用营业部等）进行标签化，明确区分“营业部历史风格参考”与“当日真实买卖净额”。
4. **博弈结构与封板质量判断**：
   - 核算买五合计占总成交比例、卖五合计占比、净买入额，判断属于机构合力、一家独大还是游资对倒混战。

## 二、强制三栏证据输出格式

报告必须包含以下三栏结构：

1. **【已核实事实】**：
   - 标注 `[来源：<provider> · 上榜/行情时间：<as_of>]`；
   - 逐项列出真实涨跌幅、换手率、成交额、前五大买入/卖出席位名称及精确买卖金额、净额。
2. **【逻辑推断】**：
   - 基于席位结构推导的多空力量对比（如机构净买入 vs 游资一日游席位占比）、筹码集中度与次日溢价压力推演。
3. **【待核实问题】**：
   - 营业部席位背后是否为同一资金方（公开数据无法 100% 穿透确认）、异常波动公告中公司是否提示基本面风险、次日集合竞价承接力度待观察项。

## 三、合规底线
- 所有席位买卖金额与占比均以工具返回真值为准，严禁心算捏造。
- 严禁输出“跟着游资必涨”“明日稳封涨停”等违规诱导话术。
