---
name: backtest_quality_auditor
description: 量化回测质量防坑审计器。专项检查前视偏差/未来函数（是否按 published_at 使用 PIT 财报）、幸存者偏差（是否含退市股与历史成分）、复权因子、印花税佣金滑点扣除、一字涨跌停与停牌处理。
version: 3.3.0
author: HunterCode Team
license: MIT
metadata:
  tags: [finance, quant, backtest-audit, look-ahead-bias, survivorship-bias, pit, p1]
hunter:
  display_name: 回测质量防坑审计
  icon: VerifiedUser
  category: 量化研究
  prompt_tpl: 对 {回测策略或因子方案} 进行前视偏差、幸存者偏差与交易成本6维质量审计
  needs_tools:
    - broker_fundamental_get_financial_statements
    - broker_security_get_index_constituents
    - broker_security_get_security_status
    - broker_education_get_risk_disclosure
---

# 量化回测质量防坑审计器（`backtest_quality_auditor` · P1 进阶量化技能）

## 一、技能定位与触发场景

当用户提问“审计一下我的量化回测有没有未来函数？”“为什么这个回测年化 40% 的策略一上实盘就亏钱？”“检验低市盈率高股息策略的回测质量”时触发本技能。

## 二、六大核心防坑审计维度（逐项硬核查）

1. **AUDIT-01：前视偏差与未来函数（Look-Ahead Bias / PIT Check）**
   - 检查财务数据是否严格按实际公告披露时间戳 `published_at <= as_of` 过滤（调用 `broker_fundamental_get_financial_statements` 验证）；
   - 若直接用报告期（如 `2025-12-31`）在 1 月份调仓使用年报数据，立即判定为 `FAIL`（严重未来函数）。
2. **AUDIT-02：幸存者偏差（Survivorship Bias）**
   - 检查股票池是否通过 `broker_security_get_index_constituents(index_code, as_of)` 获取历史时点成分股，是否保留样本期内被 ST 或退市的股票。
3. **AUDIT-03：复权方式规范性（Adjustment Consistency）**
   - 检查 K 线与技术指标计算是否显式声明 `前复权` 或 `后复权`，防止分红送转除权缺口制造假暴跌信号。
4. **AUDIT-04：手续费、印花税与双边滑点（Transaction Costs & Slippage）**
   - 核查是否足额扣除双边佣金、卖出印花税（5 bps）及双边冲击滑点（建议 ≥ 10 bps）。
5. **AUDIT-05：一字涨跌停成交可得性（Limit Up/Down Tradability）**
   - 检查是否校验 `limit_up_price` 与 `limit_down_price`，剔除一字涨停买不进、一字跌停卖不出的理想化虚假成交。
6. **AUDIT-06：停牌与缺失值防假 0（Suspension & Null Handling）**
   - 调用 `broker_security_get_security_status` 核查停牌期间（`is_halted=true`）是否冻结交易，缺失字段是否严格保留 `null` 而非填 `0`。

## 三、输出规范与合规硬约束

1. 参照 `references/backtest-audit-checklist.md`，输出 6 维审计打分表（0～100分）、评级（A/B/C级）、每项 `PASS/WARN/FAIL` 证据及具体整改代码建议。
2. 调用 `broker_education_get_risk_disclosure(risk_type="QUANT_BACKTEST")` 附加上标准量化回测局限性风险揭示书。
