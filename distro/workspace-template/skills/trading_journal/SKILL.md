---
name: trading_journal
description: 交易与研究日志闭环助手。记录交易前计划（触发条件、目标权重、失效规则）、盘中执行对照与交易后纪律复盘评估（仅用于模拟演练与投研复盘，严禁实盘下单）。
user-invocable: true
allowed-tools: mcp__broker_market__get_quote mcp__broker_funds__get_etf_quote mcp__portfolio__portfolio_rebalance mcp__fin_memory__fin_memory_query mcp__fin_memory__fin_memory_append_evidence
version: 3.3.0
author: HunterCode Team
license: MIT
metadata:
  tags: [finance, trading-journal, discipline, post-trade-review, fin-memory, p1]
hunter:
  display_name: 交易与研究日志
  icon: MenuBook
  category: 组合复盘
  prompt_tpl: 记录或复盘 {股票/ETF与操作计划} 的交易前计划、实际执行与纪律得分
  needs_tools:
    - broker_market_get_quote
    - broker_funds_get_etf_quote
    - portfolio_portfolio_rebalance
    - fin_memory_fin_memory_query
    - fin_memory_fin_memory_append_evidence
---

# 交易与研究日志闭环助手（`trading_journal` · P1 进阶技能）

## 一、技能定位与触发场景

当用户提问“帮我写一篇今天定投 510300.SH 的研究日志”“复盘一下我这周的操作有没有违反当初定的纪律？”“建立交易前计划与盘后复盘卡片”时触发本技能。

## 二、标准执行步骤（计划—对照—复盘三步闭环）

1. **Step 1：结构化记录【交易前研究计划（Pre-Trade Plan）】**
   - 明确记录三项要素：① **客观触发条件**（如“沪深300 PE分位 < 50% 且 ETF 折溢价率 < 0.30%”）；② **目标配置权重上限**（对照 `risk_profile` 单票上限）；③ **反证失效/暂停条件**。
2. **Step 2：核对客观行情与【实际执行记录（Execution Check）】**
   - 调用 `mcp__broker_market__get_quote` 或 `mcp__broker_funds__get_etf_quote` 获取当时的真实客观价格、IOPV 与折溢价率；
   - 核对是否在预设条件满足时执行，是否存在盘中追涨杀跌或超仓位上限行为。
3. **Step 3：生成【纪律执行评分与经验沉淀（Post-Trade Review）】**
   - 计算纪律执行得分（0～100分），识别情绪偏差（如 FOMO 追高、恐慌止损、频繁无效换手）；
   - 调用 `mcp__fin_memory__fin_memory_append_evidence` 将经验教训写入长期投研记忆库，并严格参照 `references/journal-template.md` 输出。

## 三、反幻觉与合规硬约束

1. **纯模拟演练与复盘隔离**：所有日志显著标注“模拟盘与投研复盘演练记录，非券商实盘成交”，本系统不包含任何实盘下单接口。
2. **重纪律轻短期盈亏**：评价标准以“是否严格遵守预设客观规则与风控上限”为核心，不以单日涨跌论英雄。

<!-- HCA:BEGIN 由 tools/build_skills.py 生成，勿手改 -->

## 参数

本技能的入参是**股票/ETF与操作计划**（代码或名称都可以）。用户这次给的是：

$ARGUMENTS

参数为空时，先问清楚要看哪只标的再开工，**不要随便挑一只演示**。

## 工具名对照

上面正文提到的工具，在 AtomCode 里的真名是：

| 正文里的写法 | 实际要调的工具 |
|---|---|
| `fin_memory_fin_memory_append_evidence` | `mcp__fin_memory__fin_memory_append_evidence` |
| `broker_funds_get_etf_quote` | `mcp__broker_funds__get_etf_quote` |
| `broker_market_get_quote` | `mcp__broker_market__get_quote` |
| `portfolio_portfolio_rebalance` | `mcp__portfolio__portfolio_rebalance` |
| `fin_memory_fin_memory_query` | `mcp__fin_memory__fin_memory_query` |

**按右边那一列调。** 左边是 HunterCode opencode 版的命名，这套发行版里不存在。

<!-- HCA:END -->
