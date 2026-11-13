---
name: daily_market_brief
description: 每日 08:30 市场晨报（今日盘前观察卡）。自动读取 A/H/美股交易日历、核心指数、领涨领跌行业、宏观信号与合规新闻，输出带统一数据信封时间戳与三栏证据格式的《今日盘前观察卡》。
version: 3.2.0
author: HunterCode Team
license: MIT
metadata:
  tags: [finance, pre-market, daily-brief, calendar, macro, broker-envelope, p0]
hunter:
  display_name: 每日市场晨报
  icon: WbSunny
  category: 日常工作流
  prompt_tpl: 生成 {日期或市场} 的今日盘前市场晨报与观察卡
  needs_tools:
    - broker_market_get_market_calendar
    - broker_market_get_market_status
    - broker_market_get_market_overview
    - broker_disclosure_search_news
    - truesource_truesource_daily_brief
    - truesource_truesource_macro
---

# 每日市场晨报（`daily_market_brief` · 08:30 盘前观察卡）

## 一、技能定位与触发场景

当股民在每日盘前（08:30–09:25）或任意时间询问“今天 A 股市场盘前怎么看？”“生成今日市场晨报”“今天开盘有哪些重点观察项？”时触发本技能。

## 二、标准执行步骤（四步确定性取数）

1. **Step 1：先查交易日历与市场交易状态（防休市瞎报）**
   - 调用 `broker_market_get_market_calendar` 与 `broker_market_get_market_status`，确认当日 A 股（`CN_A`）、港股（`HK`）、美股（`US`）是否为交易日、是否半日市或节假日休市。
   - **非交易日诚实提示**：若当日为周末或法定节假日休市，必须在报告第一行醒目提示“今日市场休市，以下基于最近一个交易日收盘快照与假期累积资讯生成盘前观察”。
2. **Step 2：拉取核心指数与行业全景（`broker_market`）**
   - 调用 `broker_market_get_market_overview` 获取上证指数、深证成指、创业板指、沪深300、恒生指数等核心指数表现、市场涨跌家数分布、成交总额及领涨/领跌行业板块。
   - 核验返回的 `BrokerDataEnvelope`：记录 `provider`（数据源）、`as_of`（行情业务时间）、`retrieved_at`（抓取时间）与 `delay_seconds`。
3. **Step 3：拉取隔夜宏观与合规财经资讯（`broker_disclosure` & `truesource`）**
   - 调用 `broker_disclosure_search_news`、`truesource_truesource_daily_brief` 与 `truesource_truesource_macro` 获取隔夜监管政策、央行流动性、海外市场联动与重点行业催化剂。
   - 核验每条资讯的 `published_at`（发布时间）与权威信源级别。
4. **Step 4：按模板组装《今日盘前观察卡》**
   - 严格按照 `references/brief-template.md` 组装输出，强制区分**【已核实事实】【逻辑推断】【待核实问题】**三栏，并生成 3～5 条可在盘后被验证的“今日盘面观察条件”。

## 三、反幻觉与合规硬约束

1. **严禁混淆三个时间戳**：行情时间必须用 `as_of`，新闻发布时间必须用 `published_at`，绝不拿抓取时间 `retrieved_at` 冒充。
2. **严禁填假 0**：若某个指数或宏观指标未返回（`null`），表格中必须写 `—` 并在警告中说明缺失原因，绝不允许写 `0.00%`。
3. **数据源全断诚实停止**：若 `broker_market` 与资讯工具均连接失败，立即停止并告知“当前行情与资讯数据源连接失败，无法生成今日晨报”，严禁凭记忆编造指数点位。
4. **不荐股、不喊单**：只给客观观察条件（如“观察沪深两市早盘半小时成交额是否突破 X 亿元”），不给“今日必涨/满仓买入”等违规指令。
