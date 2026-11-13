---
name: earnings_review
description: 上市公司定期财报（一季报/中报/三季报/年报）质量分析与排雷体检。基于券商三大财务报表与财务比率，全面检验营收/净利/扣非同比环比、净现比、应收账款与存货异动、商誉与有息负债结构。
version: 3.2.0
author: HunterCode Team
license: MIT
metadata:
  tags: [finance, earnings, financial-statements, cash-flow, quality-check, broker-envelope, p0]
hunter:
  display_name: 财报质量分析
  icon: Assessment
  category: 投研报告
  prompt_tpl: 分析 {股票} 最新一期财报的盈利质量、现金流匹配度与排雷项
  needs_tools:
    - broker_fundamental_get_financial_statements
    - broker_fundamental_get_financial_ratios
    - broker_fundamental_get_valuation
    - broker_disclosure_search_announcements
---

# 上市公司财报质量分析与排雷体检（`earnings_review`）

## 一、技能定位与触发场景

当用户询问“分析某股票刚出的三季报/年报质量怎么样”“帮我看看这份财报有没有水分或排雷项”时触发本技能。

## 二、标准执行步骤（四维财务交叉体检）

1. **Step 1：拉取 Point-in-Time 三大财务报表与比率（`broker_fundamental`）**
   - 调用 `broker_fundamental_get_financial_statements` 与 `broker_fundamental_get_financial_ratios` 获取资产负债表、利润表、现金流量表及核心比率。
   - **严格校验报告期（`report_period`）、披露时间（`published_at`）与会计重述状态（`restated`）**：若存在会计准则调整或前期差错更正重述，必须同时展示“调整前 vs 调整后”数据对比。
2. **Step 2：利润表含金量拆解（营收 vs 归母 vs 扣非）**
   - 核查营业总收入、归母净利润、扣非归母净利润的同比增速与单季环比变化；
   - 核查毛利率、净利率、三费率（销售/管理/财务费用率）与研发费用率变动。
3. **Step 3：现金流与资产负债表排雷检验（参照 `references/earnings-quality-rules.md`）**
   - **净现比检验**：经营活动产生的现金流量净额 / 净利润，检验赚到的是“真金白银”还是“纸面富贵”；
   - **应收与存货异动检验**：对比应收账款增速、存货增速是否远超营业收入增速；
   - **存贷双高与债务压力检验**：对比货币资金与短期借款+一年内到期非流动负债，核查资产负债率、流动比率、速动比率与商誉占净资产比例。
4. **Step 4：按三栏证据格式输出《财报质量体检报告》**

## 三、反幻觉与数字一致性硬约束

1. **严禁大模型心算财务比率**：所有增速、比率、利润率必须 100% 采信 `broker_fundamental` 或 90 项基本面计算引擎返回的真值，出口经正则数字回读校验器逐一核对。
2. **未披露科目禁填 0**：季报未披露的细分附注科目必须标记为 `—`（`null`），绝不允许填 `0`。
