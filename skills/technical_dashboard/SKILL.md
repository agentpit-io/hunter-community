---
name: technical_dashboard
description: 7 大类 56 项技术指标确定性看板。调用券商前复权 K 线与 56 项技术指标确定性计算引擎，输出趋势型、超买超卖型、能量型、成交量型、通道路径型、波动率型、市场广度型指标真值、近20日分位数及多指标冲突诊断。
version: 3.2.0
author: HunterCode Team
license: MIT
metadata:
  tags: [finance, technical-indicators, 56-indicators, macd, rsi, kdj, boll, atr, broker-envelope, p0]
hunter:
  display_name: 56项技术面看板
  icon: ShowChart
  category: 量化与技术
  prompt_tpl: 用 56 项技术指标看板全面诊断 {股票} 的趋势、动量与指标冲突
  needs_tools:
    - broker_market_get_quote
    - broker_market_get_kline
    - broker_market_get_orderbook
---

# 7 大类 56 项技术指标确定性看板（`technical_dashboard`）

## 一、技能定位与触发场景

当用户询问“用技术指标全面看一下 600036”“看看某股票的 MACD、RSI、布林带和 56 项技术面看板怎么走”时触发本技能。

## 二、标准执行步骤（代码算真值 · AI 写客观解读）

1. **Step 1：拉取带复权因子的 K 线与实时报价（`broker_market`）**
   - 调用 `broker_market_get_quote` 与 `broker_market_get_kline`（显式指定 `adjustment="qfq"` 前复权，获取近 250 日 K 线）。
   - **复权方式前置检查**：计算技术指标必须明确复权方式（默认前复权 `qfq`），防止除权除息缺口导致均线与 MACD 失真。
2. **Step 2：读取 7 大类 56 项技术指标确定性计算结果**
   - 依托后端 `services/indicators/calculator.py` 确定性计算引擎输出的 7 大类 56 项指标结构化 JSON（**严禁大模型自己心算任何一项技术指标**）：
     1. **趋势型（18 项）**：`MA5/10/20/30/60/120/250`、`EMA12/26/50`、`WMA20`、`VWAP`、`MACD(DIF/DEA/HIST)`、`DMI(+DI/-DI/ADX/ADXR)`、`SAR`
     2. **超买超卖型（11 项）**：`RSI6/12/14`、`KDJ(K/D/J)`、`WR14`、`CCI14`、`ROC12`、`MTM12`、`BIAS20`
     3. **能量型（7 项）**：`OBV`、`VR26`、`CR26`、`AR26`、`BR26`、`PSY12`、`EMV14`
     4. **成交量型（6 项）**：`VOL_MA5/20`、`VOL_RATIO`（量比）、`TURNOVER_RATE`（换手率）、`VOSC`、`MFI14`
     5. **通道路径型（6 项）**：`BOLL(UPPER/MID/LOWER)`、`KELTNER_CHN`、`DONCHIAN_CHN`、`ENE_CHN`
     6. **波动率型（4 项）**：`ATR14`、`NATR14`、`HV20`（20日历史波动率）、`BOLL_WIDTH`
     7. **市场广度型（4 项）**：`ADL`、`ADR10`、`ABI10`、`ARMS10`
3. **Step 3：多指标信号冲突客观说明（核心特色）**
   - 必须如实指出不同类别指标之间的客观矛盾（例如：“中长期均线簇 MA20/MA60 呈多头排列，但短期摆动指标 RSI6 与 KDJ_J 已进入 >80 超买区，且缩量导致 OBV 动能未同步放大”），帮助股民看清盲区，绝不报喜不报忧。
4. **Step 4：参照 `references/indicator-catalog-56.md` 输出三栏证据看板**

## 三、反幻觉与合规硬约束

1. **零心算铁律**：正文引用的每一个指标数值、分位数必须与计算引擎 JSON 100% 匹配，经正则数字回读校验器核对。
2. **样本不足诚实置空**：上市未满 250 个交易日的次新股，`MA250` 等长周期指标为 `null`，必须展示为 `—（上市天数不足）`，严禁填 `0`。
3. **严禁利用技术指标喊单**：只陈述客观状态（金叉/死叉/超买/超卖/通道突破），严禁写“金叉必涨、立即买入”。
