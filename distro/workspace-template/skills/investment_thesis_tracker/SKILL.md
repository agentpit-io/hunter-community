---
name: investment_thesis_tracker
description: 投资论点与反证条件持续跟踪器。记录买入理由、关键催化剂、量化反证失效条件与复核期限，自动对照最新上市公司公告与财报数据核查论点是否被破坏。
user-invocable: true
allowed-tools: mcp__broker_disclosure__search_announcements mcp__broker_disclosure__get_announcement mcp__broker_fundamental__get_financial_ratios mcp__broker_security__get_security_status mcp__fin_memory__fin_memory_query mcp__fin_memory__fin_memory_append_evidence
version: 3.3.0
author: HunterCode Team
license: MIT
metadata:
  tags: [finance, thesis, invalidation, announcement-check, portfolio-review, p1]
hunter:
  display_name: 投资论点跟踪器
  icon: FactCheck
  category: 组合复盘
  prompt_tpl: 核查 {股票代码与投资论点} 的最新财报与公告是否触发反证失效条件
  needs_tools:
    - broker_disclosure_search_announcements
    - broker_disclosure_get_announcement
    - broker_fundamental_get_financial_ratios
    - broker_security_get_security_status
    - fin_memory_fin_memory_query
    - fin_memory_fin_memory_append_evidence
---

# 投资论点与反证条件跟踪器（`investment_thesis_tracker` · P1 进阶技能）

## 一、技能定位与触发场景

当用户提问“帮我记录买入 600036 的核心投资论点和反证退出条件”“刚出的三季报和公告有没有破坏我当初买这只票的理由？”“检查我的持仓论点看板有没有失效的股票”时触发本技能。

## 二、标准执行步骤（四步论点核查）

1. **Step 1：读取或结构化登记投资论点四要素**
   - 调用 `mcp__fin_memory__fin_memory_query` 读取用户已登记的论点，或将用户输入结构化为四要素：
     ① **核心买入逻辑（Core Rationale）**；② **预期催化剂（Catalysts）**；③ **可量化的反证失效条件（Invalidation Conditions）**；④ **最晚复核日期（Review Deadline）**。
2. **Step 2：拉取最新交易状态、公告与财报证据**
   - 调用 `mcp__broker_security__get_security_status` 检查是否出现 ST 风险警示或重大事项停牌；
   - 调用 `mcp__broker_disclosure__search_announcements`、`mcp__broker_disclosure__get_announcement` 与 `mcp__broker_fundamental__get_financial_ratios` 拉取最新披露的客观数据。
3. **Step 3：逐条比对催化剂与反证条件**
   - 判定论点当前状态：`INTACT`（稳健成立）、`WATCH`（核心指标观察中）、`BROKEN_OR_HALTED`（触发反证条件或停牌预警）；
   - 引用公告页码标注 `[来源：公告编号 · 第X页]`，并将最新核查证据通过 `mcp__fin_memory__fin_memory_append_evidence` 持久化归档。
4. **Step 4：按三栏证据格式输出《投资论点核查卡片》**
   - 参照 `references/thesis-tracking-rules.md` 输出客观核查结果，绝不替用户下达买卖指令。

## 三、反幻觉与合规硬约束

1. **停牌/未披露严禁假装正常**：若标的处于停牌状态（`is_halted=true` 或价格为 `null`），必须立即在状态栏标红提示“停牌核查中”，绝不填 `0`。
2. **只做证据比对，不催促交易**：即使触发反证条件，也只客观陈述“哪一条预设反证条件已被最新公告/财报触发”，由投资者自主决策。

<!-- HCA:BEGIN 由 tools/build_skills.py 生成，勿手改 -->

## 参数

本技能的入参是**股票代码与投资论点**（代码或名称都可以）。用户这次给的是：

$ARGUMENTS

参数为空时，先问清楚要看哪只标的再开工，**不要随便挑一只演示**。

## 工具名对照

上面正文提到的工具，在 AtomCode 里的真名是：

| 正文里的写法 | 实际要调的工具 |
|---|---|
| `broker_fundamental_get_financial_ratios` | `mcp__broker_fundamental__get_financial_ratios` |
| `broker_disclosure_search_announcements` | `mcp__broker_disclosure__search_announcements` |
| `fin_memory_fin_memory_append_evidence` | `mcp__fin_memory__fin_memory_append_evidence` |
| `broker_security_get_security_status` | `mcp__broker_security__get_security_status` |
| `broker_disclosure_get_announcement` | `mcp__broker_disclosure__get_announcement` |
| `fin_memory_fin_memory_query` | `mcp__fin_memory__fin_memory_query` |

**按右边那一列调。** 左边是 HunterCode opencode 版的命名，这套发行版里不存在。

<!-- HCA:END -->
