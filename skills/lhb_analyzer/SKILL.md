---
name: lhb_analyzer
description: 龙虎榜深度分析器。识别游资席位、判断机构 vs 游资博弈、寻找辨识度龙头。
version: 3.9.4
author: FloatFu-true
license: MIT
metadata:
  tags: [finance, a-share, lhb, hot-money]
hunter:
  display_name: UZI 龙虎榜分析
  icon: QueryStats
  category: 事件与筛选
  prompt_tpl: 分析 {股票} 的龙虎榜，看看是哪家游资在买
  needs_tools:
    - uzi_stock_deep_analysis
---

# 龙虎榜深度分析 (Adapted)

## 数据流 (已适配本系统)

1. 调用系统内置龙虎榜工具获取原始席位数据。
2. **游资识别**: 对照 UZI 席位百科（章盟主、佛山、拉萨天团等）进行标签化。
3. **射程判断**: 分析该股票属性（股本、题材、封板质量）是否符合特定游资的操作习惯。
4. **博弈分析**: 判断是机构抱团还是游资混战。