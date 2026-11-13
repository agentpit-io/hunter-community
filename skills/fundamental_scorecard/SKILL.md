---
name: fundamental_scorecard
description: 6 大维度 90 项基本面体检评分卡。调用券商财务与估值接口及 90 项基本面确定性计算引擎，输出盈利能力、成长能力、偿债安全、现金流质量、估值水平、公司治理六维客观评分卡、同行业分位数与财报重述比对。
version: 3.2.0
author: HunterCode Team
license: MIT
metadata:
  tags: [finance, fundamentals, 90-indicators, scorecard, valuation, roe, roic, broker-envelope, p0]
hunter:
  display_name: 90项基本面评分卡
  icon: FactCheck
  category: 投研报告
  prompt_tpl: 给 {股票} 生成 6 大维度 90 项基本面体检评分卡与行业分位对比
  needs_tools:
    - broker_security_get_security_profile
    - broker_fundamental_get_financial_statements
    - broker_fundamental_get_financial_ratios
    - broker_fundamental_get_valuation
    - broker_fundamental_get_shareholders
    - broker_fundamental_get_corporate_actions
---

# 6 大维度 90 项基本面体检评分卡（`fundamental_scorecard`）

## 一、技能定位与触发场景

当用户询问“给这只股票做个基本面体检”“看看某公司的 90 项基本面评分卡、六维得分和行业分位”时触发本技能。

## 二、标准执行步骤（六维 90 项确定性体检）

1. **Step 1：拉取全套基本面与治理数据（`broker_fundamental` & `broker_security`）**
   - 调用 `broker_security_get_security_profile` 获取所属申万/中信行业分类、上市状态；
   - 调用 `broker_fundamental_get_financial_statements`、`broker_fundamental_get_financial_ratios`、`broker_fundamental_get_valuation`、`broker_fundamental_get_shareholders`、`broker_fundamental_get_corporate_actions`。
   - **严格核验 Point-in-Time 披露时间（`published_at`）与报告期（`report_period`）**，防止将未披露信息混入历史分析。
2. **Step 2：读取 6 大维度 90 项基本面计算引擎结果**
   - 依托后端 `services/fundamentals/calculator.py` 计算出的 6 大维度 90 项指标真值与客观评分（0～100 分）：
     1. **盈利能力（18 项）**：ROE(加权/摊薄/扣非)、ROA、ROIC、毛利率、净利率、营业利润率、EBITDA 利润率等；
     2. **成长能力（15 项）**：营收/归母净利/扣非净利同比增速、3 年复合增速(CAGR)、单季环比、总资产/净资产增速等；
     3. **偿债与安全性（16 项）**：资产负债率、有息负债率、流动比率、速动比率、现金比率、利息保障倍数、商誉占净资产比等；
     4. **现金流质量（15 项）**：净现比、收现比、自由现金流(FCF)、每股经营现金流、应收账款周转天数(DSO)、存货周转天数(DIO)、现金转换周期(CCC)等；
     5. **估值水平（14 项）**：PE(TTM/静态)、PB(MRQ)、PS(TTM)、PCF、EV/EBITDA、PEG、股息率及近 5 年历史分位数；
     6. **公司治理与股东（12 项）**：前十大股东合计持股、第一大股东持股、机构持股比例、股权质押占总股本比、近 3 年累计分红率、连续分红年数、审计意见类型等。
3. **Step 3：会计准则重述状态核查**
   - 若财报存在重述（`restated == true`），必须在报告中专门列出“调整前 vs 调整后”核心指标对照。
4. **Step 4：参照 `references/scorecard-90-spec.md` 输出三栏证据评分卡**

## 三、反幻觉与合规硬约束

1. **缺失值禁填 0**：金融股（银行/保险/券商）不适用的工业指标（如存货周转天数、毛利率）或未披露项，一律输出 `—` 并注明行业口径差异或未披露，严禁填 `0`。
2. **客观体检非买卖评级**：六维综合得分仅反映历史财务稳健度与估值分位，不构成投资推荐等级。
