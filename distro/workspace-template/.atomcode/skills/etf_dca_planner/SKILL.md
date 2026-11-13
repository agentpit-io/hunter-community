---
name: etf_dca_planner
description: ETF 定投计算器与计划助手。调用确定性定投计算器完成正向现金流与历史净值回测（累计投入、总份额、期末市值、持仓均价、IRR、最大回撤）、目标金额反推与一次性买入对比，强制附带历史假设免责声明。
user-invocable: true
allowed-tools: mcp__broker_funds__get_etf_quote mcp__broker_funds__get_fund_nav mcp__broker_funds__get_fund_fee mcp__broker_education__get_risk_disclosure
version: 3.3.0
author: HunterCode Team
license: MIT
metadata:
  tags: [finance, etf, dca, irr, backtest, target-reverse, p1]
hunter:
  display_name: ETF定投计算器助手
  icon: Savings
  category: 量化研究
  prompt_tpl: 测算 {ETF代码与定投参数} 的历史定投收益率、IRR、最大回撤与目标反推计划
  needs_tools:
    - broker_funds_get_etf_quote
    - broker_funds_get_fund_nav
    - broker_funds_get_fund_fee
    - broker_education_get_risk_disclosure
---

# ETF 定投计算器与计划助手（`etf_dca_planner` · P1 进阶技能）

## 一、技能定位与触发场景

当用户询问“如果我每月定投 2000 元沪深300 ETF（510300.SH），坚持 3 年收益率和最大回撤是多少？”“我想 3 年攒够 10 万元，每月需要定投多少？”“定投和一次性买入比哪个更好？”时触发本技能。

## 二、标准执行步骤（四步确定性测算）

1. **Step 1：获取 ETF 行情、IOPV 折溢价与费率结构（`broker_funds`）**
   - 调用 `mcp__broker_funds__get_etf_quote` 查看当前二级市场价格、IOPV 参考净值、折溢价率与规模流动性；
   - 调用 `mcp__broker_funds__get_fund_fee` 获取管理费率、托管费率与申购费率。
2. **Step 2：拉取历史真实净值并运行正向定投回测（`services/etf_dca`）**
   - 调用 `mcp__broker_funds__get_fund_nav` 获取区间单位净值与分红序列；
   - 由后端确定性引擎算出：**累计投入本金、累计手续费、期末总份额、持仓单位成本、期末总市值、年化内部收益率（XIRR）、最大回撤**。
3. **Step 3：运行目标金额反推与定投 vs 一次性买入对比**
   - 根据用户目标金额与期限，在 3% / 6% / 8% / 10% 多情景年化假设下反推每期需投入金额；
   - 对比同等本金在期初一次性买入（Lump Sum）与分批定投（DCA）的成本平摊效果与回撤差异。
4. **Step 4：拉取官方定投风险揭示书并附上强制免责声明**
   - 调用 `broker_education_get_risk_disclosure(risk_type="ETF_DCA")`，严格按 `references/dca-plan-template.md` 输出三栏证据报告。

## 三、反幻觉与合规硬约束

1. **严禁大模型心算 IRR 或份额**：所有金额、份额、均价、IRR、最大回撤必须 100% 采信 `services/etf_dca` 计算引擎真值。
2. **强制附带免责声明**：每次输出末尾必须原样包含：**“基于历史净值与假设参数测算，历史表现不代表未来收益。”**

<!-- HCA:BEGIN 由 tools/build_skills.py 生成，勿手改 -->

## 参数

本技能的入参是**ETF代码与定投参数**（代码或名称都可以）。用户这次给的是：

$ARGUMENTS

参数为空时，先问清楚要看哪只标的再开工，**不要随便挑一只演示**。

## 工具名对照

上面正文提到的工具，在 AtomCode 里的真名是：

| 正文里的写法 | 实际要调的工具 |
|---|---|
| `broker_funds_get_etf_quote` | `mcp__broker_funds__get_etf_quote` |
| `broker_funds_get_fund_nav` | `mcp__broker_funds__get_fund_nav` |
| `broker_funds_get_fund_fee` | `mcp__broker_funds__get_fund_fee` |
| `broker_education_get_risk_disclosure` | `mcp__broker_education__get_risk_disclosure` |

**按右边那一列调。** 左边是 HunterCode opencode 版的命名，这套发行版里不存在。

<!-- HCA:END -->
