---
name: watchlist_daily_brief
description: 自选股每日排雷与异动日报。逐只扫描用户自选股（或指定股票池）的行情快照、隔夜/最新公告、最新财报摘要与ST/停牌风险状态，按【已核实事实】【逻辑推断】【待核实问题】输出自选股体检日报。
version: 3.2.0
author: HunterCode Team
license: MIT
metadata:
  tags: [finance, watchlist, daily-brief, risk-scan, announcements, broker-envelope, p0]
hunter:
  display_name: 自选股日报
  icon: Visibility
  category: 日常工作流
  prompt_tpl: 扫描我的自选股 {股票或留空看全部} 今日行情异动与公告排雷
  needs_tools:
    - watchlist_watchlist_digest
    - watchlist_watchlist_rank
    - broker_security_get_security_status
    - broker_market_get_quote
    - broker_disclosure_search_announcements
    - broker_fundamental_get_financial_ratios
---

# 自选股每日排雷与异动日报（`watchlist_daily_brief`）

## 一、技能定位与触发场景

当用户询问“帮我看今天自选股有什么异动和公告”“我的自选股今天有没有踩雷？”“生成今日自选股日报”时触发本技能。

## 二、标准执行步骤（逐只遍历 · 零遗漏扫描）

1. **Step 1：获取用户自选股清单与快览**
   - 调用 `watchlist_watchlist_digest` 或 `watchlist_watchlist_rank` 获取用户当前自选股列表（若用户在参数中指定了股票代码列表，则直接扫描指定列表）。
2. **Step 2：逐只拉取券商行情、状态、公告与财务快照（支持同会话复用）**
   - 对自选股池中的标的，依次核查（若同会话已有当日快照则直接复用）：
     - `broker_security_get_security_status`：检查是否有停牌、ST/\*ST 风险警示或退市风险标记；
     - `broker_market_get_quote`：获取最新价、涨跌幅、换手率、成交额与量比（核对 `as_of` 时间戳）；
     - `broker_disclosure_search_announcements`：检索近 3～7 日发布的重大公告（如业绩预告、减持、回购、监管函、重大合同）；
     - `broker_fundamental_get_financial_ratios`：获取最新一期 ROE、营收/净利同比与净现比。
3. **Step 3：按风险与异动优先级排序**
   - 将自选股分为三档：
     - 🔴 **高优先级关注（风险/重大公告/大幅异动）**：当日涨跌幅绝对值 ≥ 5%、换手率异常放大、新发减持/问询函/业绩变脸公告，或处于停牌/ST 状态；
     - 🟡 **中优先级关注（常规催化/财报更新）**：新发常规公告、行业联动波动；
     - 🟢 **平稳运行**：无新增重大公告且波动在正常区间。
4. **Step 4：参照 `references/watchlist-brief-template.md` 输出三栏证据日报**

## 三、反幻觉与合规硬约束

1. **停牌与缺失字段禁填 0**：停牌股票涨跌幅或缺失财务指标必须展示为 `—`，并在 `warnings` 中注明“停牌”或“未披露”，绝不允许写 `0.00%`。
2. **公告引用必须带发布时间与来源**：每条公告必须注明 `published_at`（披露日期）与公告标题，若调用了 `get_announcement` 须标注 `[来源：公告标题 · 第X页]`。
3. **强制区分三栏证据**：每只重点自选股的点评必须明确分为【已核实事实】【逻辑推断】【待核实问题】。
