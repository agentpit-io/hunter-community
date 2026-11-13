---
name: investor_panel
description: 66 位投资大佬评审团（接入券商数据信封与三栏证据格式）。模拟价值、成长、游资、量化等 9 大流派基于真实券商行情与财报数据对股票进行投票打分。
user-invocable: true
allowed-tools: mcp__uzi__stock_deep_analysis mcp__broker_market__get_quote mcp__broker_fundamental__get_financial_ratios mcp__broker_fundamental__get_valuation
version: 3.2.0
author: FloatFu-true & HunterCode Team
license: MIT
metadata:
  tags: [finance, investor-panel, voting, role-play, broker-envelope]
hunter:
  display_name: UZI 大佬评审团
  icon: Groups
  category: 综合分析
  prompt_tpl: 看看 66 位大佬对 {股票} 的投票结果
  needs_tools:
    - uzi_stock_deep_analysis
    - broker_market_get_quote
    - broker_fundamental_get_financial_ratios
    - broker_fundamental_get_valuation
---

# Investor Panel · 大佬评审团（券商数据信封 & 三栏证据适配版）

66 位投资者分成 9 个流派，各自用自己的方法论对同一只股票投票。
**这个 SKILL 的价值不在于给出结论，而在于把分歧摆出来** —— 让用户看清同一份券商权威数据在不同框架下能读出完全相反的意思。

## 执行流程

### 1. 取数与信封核验

- 若同会话上下文中已有该股票今日有效的行情与财务数据信封快照，直接复用；否则调用 `mcp__broker_market__get_quote`、`mcp__broker_fundamental__get_financial_ratios`、`mcp__broker_fundamental__get_valuation` 及 `mcp__uzi__stock_deep_analysis` 获取真实数据（行情 / K 线 / 财务 / 公告 / 新闻等）。
- 严格核对 `BrokerDataEnvelope` 中的 `provider`、`as_of`（业务时间）、`published_at`（财报披露时间）与 `missing_fields`。

⚠️ **`outline` 参数只传数据类小节，或者干脆不传。**
后端手里只有上面那几段数据，它写不出「投票分布」，也不知道芒格会怎么说；把角色类小节塞进 outline，它只会写一句「暂无数据」。**66 位大佬的戏份全部在你的正文里完成。**

### 2. 投票

按 `references/voting-rules.md` 的规则推导 —— **不是拍脑袋编比例**：

- 每个流派有一张打分维度表，逐维对照真实数据打 -2 到 +2 分
- 分数和 ≥ +3 投牛、-2~+2 投中性、≤ -3 投熊
- **数据缺失（`null`）的维度记 0 分并在【待核实问题】中注明缺失字段**；一个流派过半维度缺数据，该流派全部记中性
- 综合评分 = 50 + (牛票 - 熊票) / 66 × 50

### 3. 发言

9 个流派的方法论与成员名单：

- `references/group-a-classic-value.md` · 经典价值（8 席）
- `references/group-b-quality-growth.md` · 质量成长（8 席）
- `references/group-c-disruptive-tech.md` · 颠覆式创新（8 席）
- `references/group-d-macro-cycle.md` · 宏观与周期（8 席）
- `references/group-e-trend-momentum.md` · 趋势与动量（8 席）
- `references/group-f-hot-money.md` · 游资与情绪（8 席）
- `references/group-g-quant-systematic.md` · 量化与系统化（7 席）
- `references/group-h-contrarian-short.md` · 逆向与做空（6 席）
- `references/group-i-tail-risk.md` · 尾部风险与对冲（5 席）

每个流派文件末尾都有发言模板：**先引真实数字，再下判断**。

## 输出格式（含强制三栏证据区）

1. **一句话结论与数据信封摘要** —— 分歧点在哪，并标注 `[数据源：<provider> · 行情时间：<as_of> · 财报期：<report_period>]`
2. **三栏证据总览（强制输出）**：
   - **【已核实事实】**：本次评审引用的全部真实行情、ROE、PE/PB 分位、现金流与营收增速客观真值。
   - **【逻辑推断】**：9 大流派基于各自框架对上述客观数据做出的多空推演与分歧核心。
   - **【待核实问题】**：因数据缺失（`null`）未能打分的维度，以及未来能打破当前多空平衡的关键催化剂或财报验证点。
3. **投票分布** —— 牛 / 中性 / 熊 票数与比例、综合评分与档位
4. **分流派详述** —— 9 个流派**每个都要写到**：席位数、态度、打分依据（引用真实数字）、1-2 位代表人物的第一人称方法论推演点评
5. **最大分歧与跟踪清单** —— 哪两派结论相反，什么数据出来能让分歧收敛

## 硬约束

- **所有数字来自工具返回的数据**，一个都不许编。没有的就写 `—` 并说明缺失原因，绝不用 `0` 顶替。
- **人物观点标注是模拟**：写「模拟巴菲特的方法论推演」，不要写成「巴菲特说」。这是按公开方法论做的角色推演，不是本人言论，也不代表他们的真实持仓或表态。
- **不给买卖评级、目标价、仓位建议。** 综合评分是分歧度指标，不是推荐等级。
- 全程中文，英文只用于股票代码和 ROE / EPS / PEG 这类缩写。

<!-- HCA:BEGIN 由 tools/build_skills.py 生成，勿手改 -->

## 参数

本技能的入参是**股票**（代码或名称都可以）。用户这次给的是：

$ARGUMENTS

参数为空时，先问清楚要看哪只标的再开工，**不要随便挑一只演示**。

## 工具名对照

上面正文提到的工具，在 AtomCode 里的真名是：

| 正文里的写法 | 实际要调的工具 |
|---|---|
| `broker_fundamental_get_financial_ratios` | `mcp__broker_fundamental__get_financial_ratios` |
| `broker_fundamental_get_valuation` | `mcp__broker_fundamental__get_valuation` |
| `uzi_stock_deep_analysis` | `mcp__uzi__stock_deep_analysis` |
| `broker_market_get_quote` | `mcp__broker_market__get_quote` |

**按右边那一列调。** 左边是 HunterCode opencode 版的命名，这套发行版里不存在。

<!-- HCA:END -->
