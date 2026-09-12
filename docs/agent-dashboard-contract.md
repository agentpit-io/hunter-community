# 小鹿智能体 · 前端设计思路与后端对接契约

> 页面:`apps/web/public/strategies/agent.html`(策略中心第四个 tab,在「策略工作台」与「数据」之间)
> 状态:**前后端都已接通(2026-09-12)**。后端 `apps/api/app/services/quant/agent_run.py`(流水线 + 面板)、
> `agent_vcp.py`(策略引擎,用户提供的 Backtrader「VCP 波段交易」逐条移植);§4 的三件事已定:
> 4.1 数据来自自家全市场日线 `rs_daily`;4.2 三个控制接口已实现(要登录);4.3 单实例。
> 成文:2026-09-09(上海时间)· 更新:2026-09-12

---

## 0. 一句话

小鹿智能体是一个**自迭代量化原型**:每个交易日自动跑「收集数据 → 滚动回测 → 复盘总结 → 调整规则」四步,
把每一笔交易、每一条教训、每一次规则改动记下来。这个页面是它的**唯一对外面孔**。

后端只需要实现**一个接口**:

```
GET /api/quant/agent/dashboard
```

前端一次请求拿全量,不做分片加载 —— 智能体一天只跑一次,数据量在几十 KB 量级,
拆成 8 个接口只会增加后端的组装负担和前端的竞态处理。

---

## 1. 页面顺序 = 产品叙事,不要随意调整

自上而下十一块,顺序是**它是谁 → 按什么做 → 今天跑了吗 → 成绩 → 曲线 → 规则 → 持仓与候选 → 今天做了什么 → 怎么演进过来的 → 学到了什么**:

| # | 区块 | 数据字段 | 回答的问题 |
|---|---|---|---|
| ① | 状态条 | 顶层字段 | 它在跑吗?花的是不是真钱? |
| ② | 当前策略 | `strategy` | 它现在按什么在做? |
| ③ | 护栏 | `guardrails` | 最坏能亏多少? |
| ④ | 今日流水线 | `pipeline` | 今天四步跑完了吗?哪步出问题? |
| ⑤ | 量化总览 | `overview` | 赚了多少?比大盘强吗? |
| ⑥ | 净值曲线 | `nav` | 赚的钱是选股来的还是大盘给的? |
| ⑦ | **规则手册** | `rules` | 它按哪几条规则买卖? |
| ⑧ | 持仓 / 观察列表 | `holdings` `watchlist` | 手里有什么?在等什么? |
| ⑨ | 今日操作报告 | `trades` | 今天每一笔为什么这么做? |
| ⑩ | 策略演进 | `versions` | 它是怎么变成现在这样的? |
| ⑪ | **每日成长总结** | `lessons` | 它学到了什么?落地了吗? |

`render_check.js` 里有**顺序断言**(6 条),改顺序会当场红。要改顺序请连断言一起改,
别绕过去 —— 这个顺序是 2026-09-09 用户当面定的。

---

## 2. 三条不可协商的约束

### 2.1 空的比假的好(仓内铁律)

**前端一个业务数字都没有硬编码**,全部来自接口。`Number.isFinite(v)` 为假就渲染 `—`。
后端算不出的字段**必须返回 `null`**,不要返回 `0`、`0.0`、`"-"` 或省略字段:

- `0` 会被渲染成 `0.0%`,读起来像个结论(「今天持平」),而真相是「没算出来」;
- 省略字段和 `null` 前端表现一样,但**省略会让人以为字段没实现**,`null` 明确表示「实现了,这次算不出」;
- 算不出的原因请用配套的 `*_na_reason` / `*_text` 字段说明(目前 `sharpe_na_reason` 有,其它按需加)。

`render_check.js` 的 fixture 里有一份**全 null 的 EMPTY**,断言输出里不出现 `NaN` / `undefined` / `null` 字面量,
且至少有 20 处 `—`。给某个字段加 `|| 0` 兜底,这条断言会红。

**枚举字段缺失时一律 `—`,不许猜默认值。** 三处前科都在同一天修掉:
交易的 `side` 缺失时原来渲染成「卖出」、教训的 `kind` 缺失时渲染成「亏损教训」、
护栏的 `long_only` 缺失时渲染成「只做多 · 不加杠杆」。
买卖方向是一行里最关键的一个字,交易方向决定用户怎么判断风险敞口 ——
**这类字段猜错比留空严重得多**,`render_check.js` 里各有一条断言盯着。

### 2.1.1 「字段缺失」和「空数组」是两件事

| 后端返回 | 前端表现 | 为什么 |
|---|---|---|
| 字段缺失 / `null` | 渲染一行骨架,内容全 `—` | 后端还没给,结构本身是信息 |
| `items: []` | 写业务结论:「今天没有成交」「当前空仓」「今日没有候选」 | 「今天没成交」本身就是一条有用的结论 |

**空数组不要用 `—` 表示** —— 那是把一条确定的结论降级成「不知道」。
后端确实查到没有,就返回空数组;查不到 / 没实现,就别给这个字段。

### 2.2 LLM 严禁自由生成用户可见的数字(仓内铁律)

`trades[].rationale`(每笔交易为什么这么做)和 `lessons[].what / why / learned`(成长总结)是**给模型润色的文本**,
里面必然出现数字。必须按 `hunter/api/app/routers/gm/recap.py::_rule_ai + _verify_numbers` 那套做:

1. 后端先用规则模板拼出**基线文本**,数字全部来自 `trade` / `kline` / `factor_value` 表;
2. LLM 只润色语气,**不改数字**;
3. **正则回读校验** —— 把润色后文本里的数字抽出来,与基线文本比对,对不上就**丢弃 LLM 结果、落回规则文本**。

Prompt 里写「严禁编造」对 flash 模型完全无效,必须强校验。

同时这些文本要过**语言守卫**(`app/services/lang_guard.py` 的 `sanitize_llm_text`),
判据用 `has_english_prose`,不要用「整段有没有中文」。

### 2.3 前端已做 HTML 转义,但后端别依赖它

`esc()` 对所有后端文本转义后才进 `innerHTML`。这是防线不是许可 ——
后端仍应保证 `rationale` / `learned` 这类字段是纯文本,不要塞 markdown 或 HTML(前端不解析,会原样显示标签)。

---

## 3. 接口契约

### 3.1 顶层

```jsonc
{
  "enabled": true,              // false → 前端走「尚未启动」空态
  "state": "running",           // running | paused | never_started
  "paper": true,                // true=纸上交易(棕色徽章) false=真实下单(红色徽章)
  "version": "v7",              // 当前策略版本号,展示用字符串
  "day_count": 34,              // 已运行的交易日数
  "iteration_count": 7,         // 自我迭代过多少版
  "last_run_text": "09-09 05:32 ET",   // 已格式化的字符串,前端不做时区换算
  "next_run_text": "09-10 05:30 ET"
}
```

> **时间为什么是预格式化字符串**:美股要同时显示 ET 和上海时间,格式化规则(夏令时、跨日)
> 放后端只写一次,放前端要在四个地方各写一遍。前端不做时区数学。
> 交易时间戳同理(`trades[].ts_market` / `ts_market_tz` / `ts_local`)。

### 3.1.1 `branch` / `branches` · 迭代方向(2026-09-12 加)

```jsonc
"branch": "buy",                       // 本次返回的是哪个方向;请求用 ?branch=base|buy|sell,不认识的落回 base
"branches": [                          // 全部方向,顺序固定;前端据此画切换卡,缺失 / 空数组 → 不画
  { "key": "base", "label": "基准 v1", "direction": "规则固定,不优化", "version": "v1",
    "pnl_pct": -0.26, "benchmark_pct": -1.3, "excess_pt": 1.03, "trades_total": 2, "win_rate": 0,
    "max_dd_pct": -0.6, "active": false }
]
```

三个方向各有自己的现金 / 持仓 / 成交 / 净值 / 版本;观察列表(筛选结果)共用。
`versions` 里 `status: "observing"` 的那条是优化器选出的候选,正在 5 天观察期;`rules[].status: "observing"` 标的是它动的那条规则。
「立即跑一次」「暂停」对所有方向一起生效。

### 3.2 `strategy` · 当前基于什么策略

```jsonc
{
  "name": "动量突破 + 财报后漂移",
  "version": "v7",
  "summary": "买强势股的突破,不猜底部 —— 动量排名前 10% 或财报后放量创新高时进,跌 6% 或从高点回撤 3% 时出。",
  "market_label": "美股",
  "market_note": "暂不支持 A 股 / 港股",   // 可空
  "universe": "S&P 500 + 纳指 100",
  "universe_size": 503,
  "rebalance": "事件驱动 · 无固定周期",
  "data_source": "日线 + 财报日历 · 延迟 15 分钟"
}
```

`summary` 写**人话**,不是参数罗列 —— 参数在规则手册里逐条写。

### 3.3 `guardrails` · 护栏

```jsonc
{
  "initial_capital": 10000, "max_position_pct": 15, "max_holdings": 8,
  "daily_loss_halt_pct": -3, "consecutive_loss_pause": 3, "long_only": true,
  "triggered_today": false,          // true/false/null,null 显示 —
  "triggered_text": "单日亏损已达 -3.2%,今日停止开仓"   // triggered_today=true 时必填
}
```

护栏条常驻在第一屏,和「纸上交易 / 真实下单」徽章一起 —— 自动交易智能体最该第一眼看到的是
**它花的是不是真钱**和**最坏能亏多少**。

### 3.4 `pipeline` · 今日四步

```jsonc
{
  "date": "2026-09-09",
  "steps": [
    { "key": "collect",  "name": "收集数据", "status": "ok",   "at": "05:30",
      "duration_ms": 2400,  "summary": "503 只 · 日线 + 财报日历 · 缺 2 只(停牌)" },
    { "key": "backtest", "name": "滚动回测", "status": "ok",   "at": "05:31", "duration_ms": 18000, "summary": "…" },
    { "key": "review",   "name": "复盘总结", "status": "ok",   "at": "05:32", "duration_ms": 900,   "summary": "…" },
    { "key": "adjust",   "name": "调整策略", "status": "warn", "at": "05:32", "duration_ms": 120,
      "summary": "改 1 条规则(R-03 止损 -8% → -6%),观察期 3 日,未计入版本号" }
  ]
}
```

`key` 决定图标(`collect` / `backtest` / `review` / `adjust`),`status` 决定配色
(`ok` 绿 / `warn` 橙底 / `fail` 红底 / `pending` 灰)。**某步失败要如实返回 `fail`**,
不要吞掉 —— 「今天第 2 步挂了」比「一切正常但数字不对」好排查得多。

### 3.5 `overview` · 量化总览

```jsonc
{
  "pnl_abs": 1024, "pnl_pct": 10.2, "equity": 11024,
  "benchmark_symbol": "SPY", "benchmark_pct": 6.4, "excess_pt": 3.8,
  "max_dd_pct": -10.0, "max_dd_abs": -1003, "dd_from": "08-26", "dd_to": "09-01",
  "trades_total": 100, "trades_win": 40, "win_rate": 40, "profit_factor": 2.7,
  "sharpe": 1.28, "risk_free_pct": 4.3,
  "holdings_count": 6, "max_holdings": 8, "invested_pct": 62, "cash": 4189
}
```

- 百分比一律**用数字 10.2 表示 10.2%**,不要 0.102,也不要带 `%` 的字符串。
- `excess_pt` 是超额**百分点**(智能体 − 基准),单独给,不要让前端减 —— 减法口径(算术差 vs 几何差)是后端的事。
- `sharpe` 是**组合级**、年化。样本不足就给 `null`,前端会写「样本不足或未计算」。

### 3.6 `nav` · 净值曲线

```jsonc
{
  "benchmark_symbol": "SPY",
  "points": [ { "date": "2026-08-01", "agent_pct": 0, "benchmark_pct": 0 }, … ],
  "version_marks": [ { "index": 8, "version": "v5" }, { "index": 21, "version": "v6" } ],
  "drawdown": { "from_index": 12, "to_index": 16, "pct": -10.0 }
}
```

- `agent_pct` / `benchmark_pct` 是**相对起始资金的累计百分比**(起点 0)。
- `benchmark_pct` 允许为 `null`(基准那天没数据),前端会断线,不会插值。
- `version_marks` / `drawdown` 用的是 **points 的下标**,不是日期 —— 前端不做日期查找。
  这是画换版竖线和回撤底色用的。
- y 轴范围前端自动算(取数据 min/max 各留 15% 余量,对齐到偶数),后端不用管。

> **为什么一定要画换版竖线**:自迭代做得对不对,看的是**换版之后曲线相对基准的斜率变了没有**,
> 不是看总收益。没有这条线,这页就退化成一个普通的业绩看板。

### 3.7 `rules` · 规则手册

```jsonc
[
  { "id": "R-02", "kind": "buy",              // buy | sell | risk
    "condition": "财报次日开盘后至第 3 日,量能 ≥ 2× 20 日均量且创 20 日新高",
    "since_text": "v7 改",                    // 展示用,如「v1 起」「v7 改」「观察期」
    "status": "active",                       // active | observing(观察期,前端标橙)
    "stats": [ { "label": "触发", "value": 19 },
               { "label": "触发后胜率", "value": "52%" },
               { "label": "样本", "value": "不足 15", "dim": true } ] }
]
```

`stats` 是**自由键值对数组**,不同类型的规则统计口径不同(买入规则看胜率,风控规则看「避免续亏多少个点」),
写死字段名会逼后端塞不适用的 0。`value` 为 `null` 显示 `—`;`dim: true` 表示这是说明性文字不是数字,渲染成灰色小字。

**每条规则必须带触发次数和触发后胜率** —— 没有这两个数,分不清一条规则是「有效」还是「只是没怎么触发」。

`id` 是交易记录和成长总结引用的锚点,**一旦分配不要复用**:R-03 改了参数仍是 R-03(版本号区分),
但删掉 R-03 之后新增的规则要用 R-08,不能回收 R-03。否则历史交易记录会指向一条语义完全不同的规则。

### 3.8 `holdings` · 持仓明细

```jsonc
{
  "as_of": "09-09 16:00 ET", "quote_delay_min": 15,
  "items": [
    { "symbol": "NVDA", "name": "英伟达", "cost": 178.40, "price": 195.62,
      "pnl_pct": 9.7, "hold_days": 18, "bench_pct": 2.1,
      "sharpe": 1.94, "sharpe_na_reason": null,
      "entry_rule": "R-02", "entry_rule_text": "财报后 3 日内放量突破 20 日高" }
  ]
}
```

- `bench_pct` 是**同期基准涨幅**(从买入日到今天),用来判断这只票是真强还是跟着大盘涨。
- **`sharpe` 在持有不足 10 个交易日时必须返回 `null` 并填 `sharpe_na_reason`**。
  个股夏普用持有期日收益算,四五天的样本得出的数字会在 0.2 和 3.0 之间乱跳,写出来只会误导。
  阈值定 10 个交易日是经验值,要改请连同这段注释一起改。
- `entry_rule_text` 是 hover 提示的完整条件,可空(空则 hover 显示规则号)。

### 3.9 `watchlist` · 今日观察列表

```jsonc
{
  "items": [
    { "symbol": "MU", "score": 92, "price": 142.30, "rule_id": "R-02", "rule_text": "…",
      "progress_pct": 82,
      "gap": "财报后第 2 日 · 量能 1.7× 均量,距 20 日高还差 0.8% —— 满足量能就差突破" },
    { "symbol": "SMCI", "price": 41.88, "blocked": true,
      "blocked_reason": "评分 90 但被护栏挡下:20 日波动率 68% 超过单票风险预算" }
  ]
}
```

**`gap` 写的是「还差什么才买」,不是一串代码。** 这是这一栏的全部价值:
用户要能看出智能体在等什么、也能判断它等得对不对。只给 symbol 和 score 等于没给。

`progress_pct` 是「距触发还有多远」的进度条(0~100),口径由后端定,但必须**同一条规则内可比**。
`blocked: true` 的项排在最后、半透明显示 —— **被否决的候选也要展示**,
否则用户不知道护栏在干活,会以为智能体漏掉了机会。

### 3.10 `trades` · 今日操作报告

```jsonc
{
  "date": "2026-09-09",
  "items": [
    { "ts_market": "09:31", "ts_market_tz": "ET", "ts_local": "21:31 沪",
      "side": "buy", "symbol": "SHOP", "shares": 41, "price": 121.55,
      "amount": 4983, "position_pct": 12.4,
      "rule_id": "R-02", "rule_name": "财报后放量突破",
      "rationale": "昨夜财报超预期,今日开盘 30 分钟成交量已达 20 日均量的 2.3 倍,盘中价格上破 20 日高点 $120.80,两个条件同时满足。按护栏「单票 ≤15%」和当前现金,取 41 股(12.4% 仓位)。止损挂在 $114.26(-6%)。",
      "followup": null, "adjustment": null },

    { "ts_market": "15:47", "ts_market_tz": "ET", "ts_local": "次日 03:47 沪",
      "side": "sell", "symbol": "TSLA", "shares": 14, "price": 232.80,
      "pnl_abs": -251, "pnl_pct": -7.1, "hold_days": 9,
      "rule_id": "R-03", "rule_name": "固定止损",
      "rationale": "…",
      "followup": "事后跟踪 · 卖出后 T+1 至 T+5 会自动回填,判断这次是「卖早了」还是「躲过了」",
      "adjustment": "已触发调整 · 今晚步骤 4 把 R-03 止损收到 -6%,进入 3 日观察期" }
  ]
}
```

- 买入填 `amount` + `position_pct`,卖出填 `pnl_abs` + `pnl_pct` + `hold_days`。
- `rationale` 见 §2.2:**数字必须来自成交与行情表,过正则回读校验**。
- `followup` 是**卖出后的事后跟踪**(T+1~T+5 那只票又走了多少)。这是成长总结的素材来源:
  没有它,「卖早了」这类教训无从谈起。回填前给提示文案,回填后给结论。
- `adjustment` 表示这笔交易**触发了一次策略调整**,链到当晚的成长总结。

**空数组不是错误。** 没有标的触发规则时返回 `items: []`,前端显示「今天没有成交 —— 空仓不动也是一种执行结果,不是故障」。

### 3.11 `versions` · 策略演进

```jsonc
[
  { "label": "v5 → v6", "date": "09-02",
    "change": "R-05 固定止盈 → 移动止盈",
    "reason": "多次在 +10% 出场后继续涨",
    "effect": "改后 4 笔 · 平均多留 2.1 pt",
    "status": "released" },                    // released | current | observing
  { "label": "v7 → v8?", "date": "观察期至 09-12",
    "change": "R-03 止损 -8% → -6%", "reason": "本周两笔止损吃掉三笔利润",
    "effect": "需连续 3 日无异常才并版", "status": "observing" }
]
```

`status: "current"` 的那格高亮为品牌色圆点,`observing` 半透明。
**每一版都要能追到 `reason`** —— 说不出为什么改的版本,和随机调参没有区别。

### 3.12 `lessons` · 每日成长总结

```jsonc
[
  { "date": "09-09", "title": "止损设太宽,两笔止损吃掉三笔的利润",
    "kind": "loss",                             // loss(亏损教训,左边框红) | validated(验证通过,绿)
    "what": "本周 R-03 触发 2 次,合计亏 $487;同期 3 笔盈利合计 $413,净亏 $74。",
    "why":  "-8% 是 v1 拍脑袋定的。回看 34 日全部 19 笔止损单:跌破 -6% 之后能重新翻红的只有 2 笔(10.5%)…",
    "learned": "止损线不该按「能承受多少」定,该按「跌到这里之后还有多大概率回来」定。昨天我还在把连续亏损归因为选股不准,今天按持仓拆开看才发现,选股胜率一直没变,变差的是单笔亏损的幅度。",
    "landed": { "status": "landed",             // landed | pending
                "text": "R-03 止损 -8% → -6%,今晚生效,3 个交易日观察期结束后并入 v8" } }
]
```

**`landed` 是这一块的核心,不是可选装饰。**

只写「学到了什么」而不落到规则上,下周会一字不差地再写一遍。
「已落地 / 暂不落地 + 理由」是判断这个智能体**真的在迭代**还是**在写日记**的唯一凭据。
`landed` 为 `null` 时前端显示「落地状态未记录」,那是个刺眼的提醒,不是可接受的默认值。

`learned` 里应当包含**与昨天的对比**(「昨天我还认为…今天发现…」),
这是用户明确要求的「比起昨天今天学到了什么」。

---

## 4. 还没定的三件事(需要后端一起拍板)

### 4.1 美股数据从哪来

开源版的 `klines` 表是 A 股(连 `000300` 指数行都没有)。美股日线目前在 SaaS 侧的
findata 库(`34.21.139.229`),开源版拿不到。三个选项:

1. 给开源版接一个公开美股日线源(yfinance / stooq),数据自持;
2. 通过 `finance-data` 服务代理,开源版只读;
3. 先只在 SaaS 落地这个页面,开源版留空态。

**这一条不定,后端无从开工。**

### 4.2 控制接口

页面上「立即跑一次」「暂停 / 恢复」「启动智能体」三个按钮**目前点了只弹「控制接口尚未实现」**
—— 故意不做假的成功提示。契约建议:

```
POST /api/quant/agent/run      { force: bool }   → 202 + { run_id }
POST /api/quant/agent/pause                      → 200 + { state }
POST /api/quant/agent/resume                     → 200 + { state }
```

「立即跑一次」是有副作用且不可撤销的(会产生交易记录),按仓内铁律
「不可撤销的副作用必须在发请求之前去重」,前端加闸门之外,**后端也要按 `run_id` 幂等**。

### 4.3 多用户

当前契约是**单实例**(一个部署一个智能体)。要做成 per-user 的话,
`dashboard` 要按 `Authorization` 里的用户分流,并考虑回测算力 —— 每个用户一份日更回测,
开源版的单机部署扛不住。原型阶段建议先单实例。

---

## 5. 验证方式

```bash
cd apps/web/public/strategies && node render_check.js agent.html
```

在 node 的 `vm` 里真跑一遍内联 `<script>`,用两份 fixture(`FULL` / 全 null 的 `EMPTY`)断言:

- 6 条**区块顺序**断言;
- 7 条**关键内容渲染**断言(规则挂在交易上、svg 画出来了、换版竖线、教训落地状态、样本不足显示 `—`…);
- 4 条**空数据**断言(不出现 `NaN` / `undefined` / `null` 字面量,且至少 20 处 `—`);
- 7 条**骨架**断言(后端 404、传 `{}` 进去时:九个区块都还在、不出现 `NaN`/`undefined`、
  不猜买卖方向 / 教训类型 / 交易方向、顶上有提示条说明为什么全是 `—`)。

`node --check`、`curl` 200、`docker build` 三个都**测不到内联 `<script>` 里的错**
(前科:`.join('` 断行导致整页白屏,而所有检查都是绿的)。本机没有 node 时打包到服务器上跑:

```bash
tar czf /tmp/ag.tgz -C apps/web/public strategies && scp /tmp/ag.tgz fin-r1:/tmp/ \
  && ssh fin-r1 "rm -rf /tmp/agchk && mkdir -p /tmp/agchk && tar xzf /tmp/ag.tgz -C /tmp/agchk \
     && cd /tmp/agchk/strategies && node render_check.js"
```

只跑检查、不碰生产。

**后端接完之后必须再跑一次真实浏览器验证** —— `render_check` 用的是假 DOM,
验的是「脚本能跑、渲染函数出 HTML」,验不了样式、滚动和交互。

---

## 6. 前端实现要点(改这个页面之前先读)

- **`apps/web/public/**` 是静态文件**,但开源版的 web 是 COPY 进镜像的,
  改完要 `docker compose build web && docker compose up -d web`,`restart` 无效。
  (SaaS 那边 `next start` 直接读磁盘,`git pull` 就生效 —— 两套部署不一样,别混。)
- **导航在 `app.js` 的 `renderShell()` 里**,不在各页面的 HTML。加 tab 要改那一处。
- **涨跌配色沿用站内中式惯例**(`--up` 红涨 / `--dn` 绿跌),虽然标的是美股。
  要换成美式绿涨红跌得整站一起换,不能只改这一页。页面底部有一行说明。
- **所有后端文本进 `innerHTML` 前过 `esc()`** —— `rationale` / `learned` 是 LLM 生成的。
- **`render(d)` 返回 html 字符串**(而不只是写进 container),`render_check.js` 靠这个返回值断言顺序。
- 净值曲线是**手绘 SVG**,没引 echarts。理由:这条曲线要画换版竖线和回撤底色两样 echarts 不直接支持的东西,
  手绘 60 行反而比配 option 短,也少一个 CDN 依赖。
