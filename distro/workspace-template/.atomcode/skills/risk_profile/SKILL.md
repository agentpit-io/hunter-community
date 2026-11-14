---
name: risk_profile
description: "读/改风险偏好 + 现金 + 单票/HK 上限 · 接入券商数据信封与三栏证据格式 · 供组合建议与组合风险巡检自动应用"
user-invocable: true
allowed-tools: mcp__portfolio__update_risk_profile mcp__portfolio__portfolio_stress
version: 3.2.0
author: HunterCode Team
license: MIT
hunter:
  display_name: "风险画像"
  icon: "🎚"
  category: "组合级"
  brand: "Hunter 内置"
  prompt_tpl: "我风险偏保守 · 现金还有 5 万 · 单票别超过 20%"
  needs_tools:
    - portfolio_update_risk_profile
    - portfolio_portfolio_stress
  needs_data: []
---

# 风险画像（券商数据信封 & 三栏证据适配版）

> 这个 SKILL 是 `mcp__portfolio__update_risk_profile` 的入口，并与 `portfolio_risk_check`（组合风险巡检）联动。

## 一、什么时候用

用户描述自己的风险偏好（保守/稳健/进取）、可用现金、单票仓位上限、行业集中度上限、港美股敞口上限或最大回撤容忍度时。

## 二、怎么做

1. 调用 `mcp__portfolio__update_risk_profile` 把用户明确表达的风险约束记录到数据库。
2. 记完后**必须按三栏证据格式复述一遍**让用户确认：
   - **【已核实事实】**：用户本次明确输入并已写入系统的各项硬约束数值（如风险等级、可用现金、单票上限百分比、更新时间 `as_of`）。
   - **【逻辑推断】**：该风险画像对后续选股、定投及组合配置的影响边界（例如单票上限 20% 意味着至少需分散配置 5 只以上标的）。
   - **【待核实问题】**：用户尚未明确指定的约束项（如未提及最大回撤容忍度或行业集中度上限，系统保持 `null` / `—`，绝不替用户擅自假设填 `0`）。
3. 之后的组合建议与 `portfolio_risk_check` 巡检都要遵守这些约束 —— 如果某个持仓突破约束（比如单票超限），必须明确指出来而不是默默调整。

## 三、注意
- 这是**写入**操作，记错了会一直影响后续风险巡检，所以必须复述确认。
- 用户没说的参数保持为空（`—`），绝不替用户乱填数字。

<!-- HCA:BEGIN 由 tools/build_skills.py 生成，勿手改 -->

## 参数

用户这次调用带的参数（可能为空）：

$ARGUMENTS

参数为空时，先问清楚要看哪只标的再开工，**不要随便挑一只演示**。

## 工具名对照

上面正文提到的工具，在 AtomCode 里的真名是：

| 正文里的写法 | 实际要调的工具 |
|---|---|
| `portfolio_update_risk_profile` | `mcp__portfolio__update_risk_profile` |
| `portfolio_portfolio_stress` | `mcp__portfolio__portfolio_stress` |

**按右边那一列调。** 左边是 HunterCode opencode 版的命名，这套发行版里不存在。

<!-- HCA:END -->
