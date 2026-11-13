---
name: factor_research
description: 量化因子有效性研究（IC/Rank IC、IR、5层分层回测单调性、因子合成、申万一级行业市值中性化与交易拥挤度分位）。
user-invocable: true
allowed-tools: mcp__broker_security__get_index_constituents mcp__broker_fundamental__get_financial_ratios mcp__broker_fundamental__get_valuation mcp__broker_market__get_kline mcp__broker_research__get_sector_data
version: 3.3.0
author: HunterCode Team
license: MIT
metadata:
  tags: [finance, quant, factor, ic, rank-ic, layered-backtest, crowding, p1]
hunter:
  display_name: 量化因子有效性研究
  icon: Analytics
  category: 量化研究
  prompt_tpl: 检验 {因子名称或组合} 的 IC、IR、五层分层收益单调性与当前拥挤度
  needs_tools:
    - broker_security_get_index_constituents
    - broker_fundamental_get_financial_ratios
    - broker_fundamental_get_valuation
    - broker_market_get_kline
    - broker_research_get_sector_data
---

# 量化因子有效性研究（`factor_research` · P1 进阶量化技能）

## 一、技能定位与触发场景

当用户提问“检验一下低市盈率叠加高股息因子”“帮我分析 ROE 质量成长因子的 IC、IR 和分层回测表现”“当前红利因子拥挤度高不高？”时触发本技能。

## 二、标准执行步骤（四步确定性因子检验）

1. **Step 1：锁定历史时点成分股（防幸存者偏差）**
   - 调用 `mcp__broker_security__get_index_constituents`（传入 `index_code` 与 `as_of`），获取各调仓期真实在籍的指数成分股，严禁拿当前存活股票池直接回推过去 3 年。
2. **Step 2：提取 PIT 基本面与复权行情（防未来函数）**
   - 调用 `mcp__broker_fundamental__get_financial_ratios`、`mcp__broker_fundamental__get_valuation` 获取 `published_at <= as_of` 的财务与估值指标；
   - 调用 `mcp__broker_market__get_kline`（显式声明 `adjustment="前复权"` 或 `"后复权"`）获取下期截面收益率。
3. **Step 3：读取确定性因子统计与分层回测真值**
   - 从量化研究工作台计算引擎读取：
     - **IC 均值 / Rank IC 均值**、**IC 标准差**、**信息比率 IR / Rank ICIR**、**t 统计量**、**IC 胜率（>0 占比）**；
     - **五层分组（Q1～Q5）年化收益率、超额收益率、最大回撤、夏普比率与分层单调性得分**；
     - **申万一级行业市值中性化**前后的纯 Alpha 贡献；
     - 调用 `mcp__broker_research__get_sector_data` 获取行业交易拥挤度近 1 年历史分位数。
4. **Step 4：按三栏证据格式输出《因子有效性研究报告》**
   - 参照 `references/factor-evaluation-spec.md`，强制区分**【已核实事实】【逻辑推断】【待核实问题】**三栏，并在报告末尾建议联动 `backtest_quality_auditor` 开展回测防坑审计。

## 三、反幻觉与合规硬约束

1. **严禁大模型心算 IC/IR 或分层收益率**：所有统计数字 100% 采信后端确定性引擎真值。
2. **严禁未来函数**：财报类因子必须核验 `published_at` 披露时间，绝不用报告期末日（如 12月31日）直接当成因子可用日。
3. **强制风险揭示**：报告底部必须声明“历史因子 IC 与回测超额收益不代表未来实盘表现，不构成投资建议”。

<!-- HCA:BEGIN 由 tools/build_skills.py 生成，勿手改 -->

## 参数

本技能的入参是**因子名称或组合**（代码或名称都可以）。用户这次给的是：

$ARGUMENTS

参数为空时，先问清楚要看哪只标的再开工，**不要随便挑一只演示**。

## 工具名对照

上面正文提到的工具，在 AtomCode 里的真名是：

| 正文里的写法 | 实际要调的工具 |
|---|---|
| `broker_fundamental_get_financial_ratios` | `mcp__broker_fundamental__get_financial_ratios` |
| `broker_security_get_index_constituents` | `mcp__broker_security__get_index_constituents` |
| `broker_fundamental_get_valuation` | `mcp__broker_fundamental__get_valuation` |
| `broker_research_get_sector_data` | `mcp__broker_research__get_sector_data` |
| `broker_market_get_kline` | `mcp__broker_market__get_kline` |

**按右边那一列调。** 左边是 HunterCode opencode 版的命名，这套发行版里不存在。

<!-- HCA:END -->
