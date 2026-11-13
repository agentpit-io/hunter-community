---
name: after_close_review
description: 每日 15:30 盘后复盘与经验记忆沉淀。拉取全天收盘行情与自选股表现，对比 08:30 早盘观察条件与实际收盘结果，核对原投资论点是否变化，将复盘证据写入 fin_memory 并生成次日观察清单。
version: 3.2.0
author: HunterCode Team
license: MIT
metadata:
  tags: [finance, after-close, review, fin-memory, thesis-check, broker-envelope, p0]
hunter:
  display_name: 15:30 盘后复盘
  icon: FactCheck
  category: 日常工作流
  prompt_tpl: 对 {日期或股票} 进行今日 15:30 盘后复盘并写入投研记忆
  needs_tools:
    - broker_market_get_market_overview
    - broker_market_get_quote
    - broker_disclosure_search_announcements
    - fin_memory_fin_memory_query
    - fin_memory_fin_memory_append_evidence
    - hcapack_thesis_evidence
---

# 每日 15:30 盘后复盘与经验沉淀（`after_close_review`）

## 一、技能定位与触发场景

当股民在收盘后（15:00/15:30 后）询问“帮我做一下今天的盘后复盘”“对比一下今天早盘的预期和实际收盘结果”“把今天的复盘经验记进记忆库”时触发本技能。

## 二、标准执行步骤（预期-现实对照 · 记忆闭环）

1. **Step 1：拉取全天收盘全景与自选股收盘快照（`broker_market` & `broker_disclosure`）**
   - 调用 `broker_market_get_market_overview` 获取三大指数全天收盘点位、全天成交总额、涨跌停家数与领涨领跌板块；
   - 对重点关注标的调用 `broker_market_get_quote` 与 `broker_disclosure_search_announcements`，获取 15:00 收盘价（核对 `as_of` 为当日收盘时刻）及盘后最新披露公告。
2. **Step 2：调取历史投资论点与早盘观察记录（`fin_memory` & `hcapack`）**
   - 调用 `fin_memory_fin_memory_query`（必要时配合 `hcapack_thesis_evidence`）检索用户此前记录的早盘观察条件、买入理由（Investment Thesis）与失效条件（Kill Criteria）。
3. **Step 3：早盘预期 vs 收盘现实逐项核对（参照 `references/review-template.md`）**
   - 逐条比对：早盘设定的量能开关、板块承接条件、个股支撑位在今日收盘时是**“✅ 已兑现 / ❌ 未兑现（被证伪） / ⏳ 尚待观察”**；
   - 评估核心投资论点状态：**强化（Strengthened） / 维持（Intact） / 动摇（Weakened） / 失效（Broken）**。
4. **Step 4：将复盘客观证据沉淀至 `fin_memory` 并生成次日观察清单**
   - 调用 `fin_memory_fin_memory_append_evidence` 将今日经过核实的收盘数据、公告事实与论点状态变更写入持久化金融记忆库；
   - 生成明天 08:30 盘前需要衔接跟踪的 3 条次日观察清单。

## 三、反幻觉与合规硬约束

1. **收盘时间严禁混同抓取时间**：收盘后调用行情接口时，信封中的 `as_of` 必须为收盘时刻（如 `15:00:00+08:00`），`retrieved_at` 为当前复盘时刻，严禁混为一谈。
2. **客观归因、拒绝马后炮**：对早盘判断失误的项要老老实实标注“未兑现”并分析哪个前提条件未满足，绝不篡改早盘原始记录。
