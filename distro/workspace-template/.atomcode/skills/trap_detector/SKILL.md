---
name: trap_detector
description: 杀猪盘与荐股风险检测器（接入券商数据信封与三栏证据格式）。结合券商主数据ST/退市警示、财务质量、公告与量价异动，扫描 8 个信号给出客观风险评级。
user-invocable: true
allowed-tools: mcp__uzi__stock_deep_analysis mcp__watchlist__stock_news mcp__broker_security__get_security_status mcp__broker_market__get_quote mcp__broker_fundamental__get_financial_ratios mcp__broker_disclosure__search_announcements
version: 3.2.0
author: FloatFu-true & HunterCode Team
license: MIT
metadata:
  tags: [finance, a-share, trap-detection, risk, broker-envelope]
hunter:
  display_name: UZI 杀猪盘检测
  icon: ReportProblem
  category: 尽调风控
  prompt_tpl: 帮我测一下 {股票} 是不是杀猪盘
  needs_tools:
    - uzi_stock_deep_analysis
    - watchlist_stock_news
    - broker_security_get_security_status
    - broker_market_get_quote
    - broker_fundamental_get_financial_ratios
    - broker_disclosure_search_announcements
---

# Trap Detector · 杀猪盘与庄股风险检测器（券商数据信封 & 三栏证据版）

## 一、检测工作流（接入券商只读 MCP）

1. **取数与同会话复用**：
   - 优先复用同会话内已有的 `mcp__broker_market__get_quote`、`mcp__broker_fundamental__get_financial_ratios` 快照；
   - 调用 `mcp__broker_security__get_security_status` 检查是否处于 ST / \*ST / 退市整理期或异常停牌状态；
   - 调用 `mcp__broker_market__get_quote` 核查总市值、流通市值、换手率、振幅与盘口挂单厚度；
   - 调用 `mcp__broker_fundamental__get_financial_ratios` 与 `mcp__broker_disclosure__search_announcements` 核查扣非净利润、经营现金流、大股东减持/质押或监管问询函。

2. **8 信号扫描清单**：
   1. 低质量/非持牌账号集中推荐
   2. 模板化“稳赚/内幕/拉升在即”话术
   3. 微信/QQ/VIP 群引流接盘
   4. **基本面与热度严重脱节**（连年亏损/微利却被吹成跨界龙头）
   5. **K 线与流动性异常**（小市值、日成交极低后突然连续暴涨或尾盘偷袭）
   6. 伪造“老师/私募大佬”人设推广
   7. 跨平台同时间点水军联动
   8. **虚假消息或蹭热点无订单**（对照公司官方澄清公告核实）

## 二、强制三栏证据输出格式

1. **【已核实事实】**：
   - 标注 `[来源：<provider> · 业务时间：<as_of> / 披露时间：<published_at>]`；
   - 列出券商接口返回的真实市值、换手率、ST/监管状态、扣非净利润、净现比、股东减持/澄清公告原文（带 `[来源：公告 · 第X页]`）。缺失字段填 `—`，绝不填 `0`。
2. **【逻辑推断】**：
   - 逐项对照 8 大信号说明哪些客观特征（如流通盘过小 + 业绩亏损 + 短期换手率畸高）符合庄股或杀猪盘操纵高危画像。
3. **【待核实问题】**：
   - 用户遇到该股票的具体推荐渠道（是否有群聊指令要求固定时间挂单买入）、尚未披露的监管核查进展等。

## 三、风险评级标准
- 🟢 **低风险（正常交易标的）** | 🟡 **注意（基本面偏弱或波动偏大）** | 🟠 **警惕（存在明显蹭热点/大股东减持/流动性脆弱）** | 🔴 **高度可疑（典型杀猪盘/庄股出货特征，强烈建议远离）**

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
| `broker_disclosure_search_announcements` | `mcp__broker_disclosure__search_announcements` |
| `broker_security_get_security_status` | `mcp__broker_security__get_security_status` |
| `broker_market_get_quote` | `mcp__broker_market__get_quote` |
| `uzi_stock_deep_analysis` | `mcp__uzi__stock_deep_analysis` |
| `watchlist_stock_news` | `mcp__watchlist__stock_news` |

**按右边那一列调。** 左边是 HunterCode opencode 版的命名，这套发行版里不存在。

<!-- HCA:END -->
