---
name: portfolio_risk_check
description: 持仓组合风险暴露与压力测试巡检。计算单票最高占比、前三大行业集中度、市场/币种敞口、相关性、历史最大回撤与压力测试情景，对照用户 risk_profile 约束输出三栏证据风控报告。
version: 3.2.0
author: HunterCode Team
license: MIT
metadata:
  tags: [finance, portfolio, risk-check, stress-test, concentration, drawdown, broker-envelope, p0]
hunter:
  display_name: 组合风险巡检
  icon: Shield
  category: 组合级
  prompt_tpl: 检查我当前持仓组合 {持仓列表或留空读默认账本} 的行业集中度、单票占比与压力测试风险
  needs_tools:
    - portfolio_portfolio_stress
    - portfolio_portfolio_rebalance
    - broker_security_get_security_profile
    - broker_market_get_quote
    - broker_fundamental_get_valuation
---

# 持仓组合风险暴露与压力测试巡检（`portfolio_risk_check`）

## 一、技能定位与触发场景

当用户询问“检查一下我当前持仓的风险暴露”“我的模拟盘/持仓组合是不是太集中了？做个压力测试”时触发本技能。

## 二、标准执行步骤（四维组合风控体检）

1. **Step 1：获取组合持仓与压力测试数据（`portfolio` & `broker_*`）**
   - 调用 `portfolio_portfolio_stress` 与 `portfolio_portfolio_rebalance` 获取当前组合持仓权重、现金比例、用户 `risk_profile` 约束（如单票上限、港股上限）及压力测试结果；
   - 对持仓标的调用 `broker_security_get_security_profile`、`broker_market_get_quote`、`broker_fundamental_get_valuation` 补齐最新价、所属行业、ST/停牌状态与估值分位。
2. **Step 2：核算五大核心风险暴露指标（参照 `references/portfolio-risk-metrics.md`）**
   - **单票集中度**：第一大重仓股占比、前三大重仓股合计占比（对照用户 `risk_profile` 单票上限检查是否超标）；
   - **行业集中度**：按申万/中信一级行业汇总持仓权重，检查前三大行业占比是否超过 60% 警戒线；
   - **估值与波动暴露**：组合加权 PE/PB 分位、高波动/高 Beta 标的占比；
   - **流动性与信用排雷**：持仓中是否存在停牌、ST/\*ST、日均成交额极低或近期有大额减持/问询函的标的；
   - **历史回撤与极端情景压力测试**：在大盘下跌 10% / 行业景气下修 / 利率上行情景下的组合预计回撤幅度。
3. **Step 3：按三栏证据格式输出《组合风险巡检报告》**

## 三、合规与红线硬约束

1. **模拟盘与只读边界**：本技能仅做客观风险指标计算与暴露提示，**严禁直接触发任何实盘交易下单或未经用户确认篡改账本**。
2. **缺失数据诚实标注**：若用户尚未录入持仓且未传参，明确提示用户“当前持仓为空或未指定持仓列表，请先在模拟盘/自选组合添加标的或直接提供持仓明细”，绝不编造虚假持仓。
