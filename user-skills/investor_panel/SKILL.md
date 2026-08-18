---
name: investor_panel
description: 66 位投资大佬评审团。模拟价值、成长、游资、技术等 9 大流派对股票进行投票打分。
version: 3.9.4
author: FloatFu-true
license: MIT
metadata:
  tags: [finance, investor-panel, voting, role-play]
hunter:
  display_name: UZI 大佬评审团
  icon: Groups
  category: 综合分析
  prompt_tpl: 看看 66 位大佬对 {股票} 的投票结果
---

# Investor Panel · 大佬评审团 (Adapted)

## 调用上下文 (已适配本系统)

1. **获取输入**: 调用 `uzi_stock_deep_analysis` 获取个股实时维度数据。
2. **模拟投票**: AI 根据 `references/group-a-classic-value.md` 等流派方法论，模拟 66 位投资者（巴菲特、芒格、段永平、章盟主等）进行打分。
3. **语言风格**: 评论必须符合大佬的人设（如芒格的刻薄、章盟主的格局）。

## 输出格式
- **投票分布**: 牛/熊/中性比例。
- **核心观点**: 提取最具代表性的流派评论。
- **综合评分**: 基于大佬共识的最终分值。