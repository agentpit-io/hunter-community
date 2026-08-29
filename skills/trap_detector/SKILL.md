---
name: trap_detector
description: 杀猪盘检测器。扫描朋友推荐、内幕消息、基本面脱节等 8 个信号给出风险评级。
version: 3.9.4
author: FloatFu-true
license: MIT
metadata:
  tags: [finance, a-share, trap-detection, risk]
hunter:
  display_name: UZI 杀猪盘检测
  icon: ReportProblem
  category: 尽调风控
  prompt_tpl: 帮我测一下 {股票} 是不是杀猪盘
  needs_tools:
    - uzi_stock_deep_analysis
    - watchlist_stock_news
---

# Trap Detector · 杀猪盘检测器

## 8 信号扫描清单
1. 低质量账号推荐
2. 模板化话术
3. VIP群引流
4. 基本面与热度脱节
5. K线异常拉升
6. 老师人设推广
7. 跨平台联动
8. 虚假消息

## 风险评级
- 🟢 安全 | 🟡 注意 | 🟠 警惕 | 🔴 高度可疑