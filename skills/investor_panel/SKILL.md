---
name: investor_panel
description: 66 位投资大佬评审团。模拟价值、成长、游资、量化等 9 大流派对股票进行投票打分。
version: 3.9.5
author: FloatFu-true
license: MIT
metadata:
  tags: [finance, investor-panel, voting, role-play]
hunter:
  display_name: UZI 大佬评审团
  icon: Groups
  category: 综合分析
  prompt_tpl: 看看 66 位大佬对 {股票} 的投票结果
  needs_tools:
    - uzi_stock_deep_analysis
---

# Investor Panel · 大佬评审团（Hunter 适配版）

66 位投资者分成 9 个流派，各自用自己的方法论对同一只股票投票。
**这个 SKILL 的价值不在于给出结论，而在于把分歧摆出来** ——
让用户看清同一份数据在不同框架下能读出完全相反的意思。

## 执行流程

### 1. 取数

调 `uzi_stock_deep_analysis` 拿这只股票的真实数据（行情 / K 线 / 财务 /
公告 / 新闻等）。

⚠️ **`outline` 参数只传数据类小节，或者干脆不传。**
后端手里只有上面那几段数据，它写不出「投票分布」，也不知道芒格会怎么说；
把角色类小节塞进 outline，它只会写一句「暂无数据」。
**66 位大佬的戏份全部在你的正文里完成。**

### 2. 投票

按 `references/voting-rules.md` 的规则推导 —— **不是拍脑袋编比例**：

- 每个流派有一张打分维度表，逐维对照真实数据打 -2 到 +2 分
- 分数和 ≥ +3 投牛、-2~+2 投中性、≤ -3 投熊
- **数据缺失的维度记 0 分**；一个流派过半维度缺数据，该流派全部记中性
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

## 输出格式

1. **一句话结论** —— 分歧点在哪
2. **投票分布** —— 牛 / 中性 / 熊 票数与比例、综合评分与档位
3. **分流派详述** —— 9 个流派**每个都要写到**：席位数、态度、打分依据
   （引用真实数字）、1-2 位代表人物的第一人称点评
4. **最大分歧** —— 哪两派结论相反，各自依据是什么
5. **跟踪清单** —— 什么数据出来能让分歧收敛

第 3 部分是主体。**不要只挑两三个流派说完就收尾** ——
用户点这个 SKILL 就是为了看 66 个人怎么吵起来的。

## 硬约束

- **所有数字来自工具返回的数据**，一个都不许编。没有的就说没有。
- **人物观点标注是模拟**：写「模拟巴菲特的方法论推演」，不要写成
  「巴菲特说」。这是按公开方法论做的角色推演，不是本人言论，
  也不代表他们的真实持仓或表态。
- **不给买卖评级、目标价、仓位建议。** 综合评分是分歧度指标，不是推荐等级。
- 全程中文，英文只用于股票代码和 ROE / EPS / PEG 这类缩写。
