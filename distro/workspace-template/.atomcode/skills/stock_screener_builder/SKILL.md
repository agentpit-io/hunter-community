---
name: stock_screener_builder
description: 自然语言结构化选股器。将大白话选股要求转换为结构化筛选条件，严格校验每个字段在券商接口中的可用性（不存在字段绝不静默伪造），并返回客观筛选结果。
user-invocable: true
allowed-tools: mcp__screener__market_screen mcp__broker_security__get_index_constituents mcp__broker_fundamental__get_valuation mcp__broker_fundamental__get_financial_ratios
version: 3.3.0
author: HunterCode Team
license: MIT
metadata:
  tags: [finance, screener, natural-language, structured-filter, p1]
hunter:
  display_name: 自然语言选股器
  icon: FilterAlt
  category: 量化研究
  prompt_tpl: 把选股想法「{自然语言选股条件}」转化为结构化指标条件并执行筛选
  needs_tools:
    - screener_market_screen
    - broker_security_get_index_constituents
    - broker_fundamental_get_valuation
    - broker_fundamental_get_financial_ratios
---

# 自然语言结构化选股器（`stock_screener_builder` · P1 进阶技能）

## 一、技能定位与触发场景

当用户用大白话提问“帮我选出沪深300里市盈率低于15倍、股息率大于3%、ROE大于10%且不带ST的股票”“把这段选股逻辑转成结构化筛选条件”时触发本技能。

## 二、标准执行步骤（四步确定性转换与筛选）

1. **Step 1：自然语言拆解与字段字典对齐**
   - 将用户的自然语言描述拆解为原子条件：`{field, operator, value, unit}`；
   - 对照 `references/screener-field-catalog.md` 校验每个字段是否属于券商只读接口已支持的客观字段。
2. **Step 2：不可用字段诚实拦截（严禁静默伪造）**
   - 若用户要求包含非公开或未授权字段（如“主力内幕持仓”“明日涨停概率”），必须在 `unsupported_fields` 中如实列出并说明原因，**绝对禁止用其他字段冒充或瞎编数字**。
3. **Step 3：调用选股与基本面工具执行客观筛选**
   - 调用 `mcp__screener__market_screen`、`mcp__broker_security__get_index_constituents`、`mcp__broker_fundamental__get_valuation`、`mcp__broker_fundamental__get_financial_ratios` 执行筛选；
   - 默认自动加上合规安全底线：`is_st == false`（剔除 ST/*ST）与 `is_halted == false`（剔除停牌不可交易标的）。
4. **Step 4：按三栏证据格式输出《结构化选股结果报告》**
   - 明确展示已生效筛选条件表、不支持字段说明、符合条件的客观标的列表及数据时间戳 `as_of`。

## 三、反幻觉与合规硬约束

1. **客观筛选 ≠ 投资荐股**：必须在报告中声明“筛选结果仅代表历史与当前静态指标满足用户设定阈值，不构成买入推荐”。
2. **缺失值不冒充 0**：若某标的某项财务指标为 `null`（未披露），不得将其当成 `0` 混入 `< X` 的筛选条件中。

<!-- HCA:BEGIN 由 tools/build_skills.py 生成，勿手改 -->

## 参数

本技能的入参是**自然语言选股条件**（代码或名称都可以）。用户这次给的是：

$ARGUMENTS

参数为空时，先问清楚要看哪只标的再开工，**不要随便挑一只演示**。

## 工具名对照

上面正文提到的工具，在 AtomCode 里的真名是：

| 正文里的写法 | 实际要调的工具 |
|---|---|
| `broker_fundamental_get_financial_ratios` | `mcp__broker_fundamental__get_financial_ratios` |
| `broker_security_get_index_constituents` | `mcp__broker_security__get_index_constituents` |
| `broker_fundamental_get_valuation` | `mcp__broker_fundamental__get_valuation` |
| `screener_market_screen` | `mcp__screener__market_screen` |

**按右边那一列调。** 左边是 HunterCode opencode 版的命名，这套发行版里不存在。

<!-- HCA:END -->
