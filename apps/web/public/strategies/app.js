// 策略中心 · 前端交互 + mock 数据
// 后端接入前 · 所有数据硬编码；接入时替换 loadFactors / loadOfficial 等函数

// ═════════════════════════════════════════════════════════════════
// 常量：20 因子
// ═════════════════════════════════════════════════════════════════
// ⚠️ **这里不放 ic / ann / vol**(_17 全量排查)。
//
// 原来每个因子都写着 `ic: 0.062, ann: 0.085, vol: 0.22` —— 20 个因子
// 60 个数字全是编的。因子广场直接把它们当"历史表现"渲染出来,
// 而真实的 IC 要靠 ic_engine 算(本地 factor_ic 表 0 行)。
//
// 现在:真 IC 从 /api/quant/factors/ic-ranking 拉,没有就显示 "—"。
// **一个因子有没有用,是这套系统最核心的问题 —— 不能用编的数回答。**
const FACTORS = [
  // 价值 4
  { key: 'pe_inv',        cat: '价值', name: '市盈率倒数',    icon: '💰', desc: '1 / TTM PE · 低估值分数高' },
  { key: 'pb_inv',        cat: '价值', name: '市净率倒数',    icon: '💰', desc: '1 / PB · 破净股偏防御' },
  { key: 'dividend_yield',cat: '价值', name: '股息率',        icon: '💰', desc: '近 12 月现金分红 / 市值' },
  { key: 'ev_ebitda_inv', cat: '价值', name: 'EV/EBITDA 倒数',icon: '💰', desc: '企业价值 / 息税折旧摊销前利润 倒数' },
  // 质量 4
  { key: 'roe',           cat: '质量', name: 'ROE',           icon: '🏆', desc: 'TTM 净利润 / 平均归母权益 · 巴菲特最爱' },
  { key: 'roa',           cat: '质量', name: 'ROA',           icon: '🏆', desc: '剔除杠杆的经营效率' },
  { key: 'gross_margin',  cat: '质量', name: '毛利率',        icon: '🏆', desc: '(营收 - 成本) / 营收 · 护城河' },
  { key: 'debt_ratio_inv',cat: '质量', name: '1/负债率',      icon: '🏆', desc: '低杠杆抗风险' },
  // 成长 2
  { key: 'revenue_growth_yoy',  cat: '成长', name: '营收同比', icon: '🚀', desc: '最近季营收 vs 去年' },
  { key: 'earnings_growth_yoy', cat: '成长', name: '净利同比', icon: '🚀', desc: '最近季归母净利 vs 去年' },
  // 动量 3
  { key: 'momentum_1m',     cat: '动量', name: '1 月动量',    icon: '📈', desc: '近 20 日涨幅 · 短反转', note: '反向 IC' },
  { key: 'momentum_6m',     cat: '动量', name: '6 月动量',    icon: '📈', desc: '近 120 日涨幅' },
  { key: 'momentum_12m_1m', cat: '动量', name: '12M-1M 动量', icon: '📈', desc: '剔除最近 1 月的 11 月涨幅 · 学术经典' },
  // 技术/资金 5
  { key: 'kronos',    cat: 'ML',   name: 'Kronos 技术', icon: '🧠', desc: '清华 Kronos 时序大模型输出' },
  { key: 'ma_align',  cat: '技术', name: '均线趋势',    icon: '📉', desc: 'MA5/10/20/60 多头排列打分' },
  { key: 'macd',      cat: '技术', name: 'MACD 动量',   icon: '📉', desc: 'MACD_bar / ATR14' },
  { key: 'main_flow', cat: '资金', name: '主力净流入',  icon: '💵', desc: '5 日主力资金净流入占比' },
  { key: 'rsi',       cat: '技术', name: 'RSI 超买卖',  icon: '📉', desc: 'RSI14 分段映射' },
  // 低波 & 其他 2
  { key: 'vol_20d_inv',cat: '波动', name: '低波动',     icon: '🛡', desc: '低波异象 · 稳定跑赢波动' },
  { key: 'candle_5d',  cat: '技术', name: '近5日 K 线', icon: '📉', desc: '近 5 日阳线比例' },
]

const CAT_ORDER = ['价值', '质量', '成长', '动量', 'ML', '技术', '资金', '波动']
const CAT_ICON = { '价值': '💰', '质量': '🏆', '成长': '🚀', '动量': '📈', 'ML': '🧠', '技术': '📉', '资金': '💵', '波动': '🛡' }

// ═════════════════════════════════════════════════════════════════
// 常量：6 官方精选策略
// ═════════════════════════════════════════════════════════════════
// ⚠️ **这里不放 metrics**(`_17` 全量排查)。
//
// 原来 6 个官方策略每个都带 `metrics: {ann_ret:0.152, sharpe:1.28, ...}` ——
// 全是编的,而策略广场把它们当"历史表现"渲染成卡片上的年化/夏普/回撤。
// 用户据此挑策略,挑的是一组不存在的业绩。
//
// **官方策略是一份配置,不是一份成绩单。** 想知道表现就跑一次回测 ——
// 那时的数字才是真的,而且带着"这次回测的成色"一起给出来。
const OFFICIAL_STRATEGIES = [
  {
    id: 'official_deep_value', icon: 'gem', name: '深度价值',
    desc: '低估值 + 高分红 · 抗跌型长期持仓',
    factors: [ { key: 'pe_inv', w: 40 }, { key: 'pb_inv', w: 30 }, { key: 'dividend_yield', w: 30 } ],
    config: { universe: 'hs300', top_n: 20, rebalance: 'Q', cost_bps: 15, benchmark: '000300' },
  },
  {
    id: 'official_high_div', icon: 'rice', name: '高股息防御',
    desc: '收租型 · 熊市抗跌 · 极稳组合',
    factors: [ { key: 'dividend_yield', w: 50 }, { key: 'vol_20d_inv', w: 30 }, { key: 'debt_ratio_inv', w: 20 } ],
    config: { universe: 'a_all', top_n: 20, rebalance: 'H', cost_bps: 15, benchmark: '000300' },
  },
  {
    id: 'official_quality_momo', icon: 'target', name: '质量+动量',
    desc: '主流经典 · Barra 简化版 · 均衡型',
    factors: [ { key: 'roe', w: 35 }, { key: 'gross_margin', w: 15 }, { key: 'momentum_12m_1m', w: 35 }, { key: 'momentum_6m', w: 15 } ],
    config: { universe: 'hs300', top_n: 20, rebalance: 'M', cost_bps: 15, benchmark: '000300' },
  },
  {
    id: 'official_small_growth', icon: 'sprout', name: '小盘成长',
    desc: '中证 500 · 高弹性 · 牛市攻击型',
    factors: [ { key: 'revenue_growth_yoy', w: 40 }, { key: 'momentum_6m', w: 30 }, { key: 'roe', w: 30 } ],
    config: { universe: 'zz500', top_n: 30, rebalance: 'M', cost_bps: 15, benchmark: '000905' },
  },
  {
    id: 'official_hs300_enhance', icon: 'hs300', name: '沪深 300 增强',
    desc: '基准增强 · 稳定跑赢 · 机构风格',
    factors: [ { key: 'pe_inv', w: 20 }, { key: 'roe', w: 30 }, { key: 'momentum_12m_1m', w: 30 }, { key: 'vol_20d_inv', w: 20 } ],
    config: { universe: 'hs300', top_n: 30, rebalance: 'M', cost_bps: 15, benchmark: '000300' },
  },
  {
    id: 'official_hk_high_div', icon: 'hk', name: '港股高息',
    desc: '港股通 · 收租型 · 汇率对冲',
    factors: [ { key: 'dividend_yield', w: 60 }, { key: 'pe_inv', w: 20 }, { key: 'roe', w: 20 } ],
    config: { universe: 'hk_all', top_n: 20, rebalance: 'H', cost_bps: 25, benchmark: 'HSI' },
  },
]

// ═════════════════════════════════════════════════════════════════
// 常量：Top 20 持仓（硬编码 · 后端接入后由 scan API 返回）
// ═════════════════════════════════════════════════════════════════
// TOP_HOLDINGS 已删(`_17` 全量排查)——
// 原来是 20 只写死的"持仓"(茅台 2.34 / 格力 2.18 / 五粮液 2.05 …),
// **没有任何地方引用它**。留着的风险是将来有人以为那是真数据接上去。
// 真持仓走 /api/quant/scan 与回测返回的 positions。

// 股票池名字
const UNIVERSE_NAME = {
  hs300: '沪深 300', zz500: '中证 500', hs800: '沪深 800',
  a_all: 'A 股全市场 · 剔 ST/次新', hk_all: '港股通', my_watchlist: '我的自选'
}
const REBALANCE_NAME = { W: '周度', M: '月度', Q: '季度', H: '半年' }
const BENCHMARK_NAME = { '000300': '沪深 300', '000905': '中证 500', '399006': '创业板指', 'HSI': '恒生指数' }

// ═════════════════════════════════════════════════════════════════
// localStorage 存取
// ═════════════════════════════════════════════════════════════════
const LS_DRAFT = 'hunter_strategy_draft'
const LS_MINE  = 'hunter_my_strategies'

function loadDraft() {
  try {
    const d = JSON.parse(localStorage.getItem(LS_DRAFT) || 'null')
    if (d && Array.isArray(d.factors)) return d
  } catch {}
  return defaultDraft()
}
/** 回测结果的 localStorage 键 —— 与 workbench/backtest 页共用一个名字 */
const LS_RESULT = 'hunter_backtest_result'

/** 两个策略是不是同一套配置(因子集 + 权重 + 股票池 + 换仓 + 成本档) */
function sameStrategyShape(a, b) {
  if (!a || !b) return false
  const sig = (x) => JSON.stringify({
    f: (x.factors || []).map((k, i) => [k, (x.weights || [])[i]])
                        .sort((p, q) => String(p[0]).localeCompare(String(q[0]))),
    c: x.config || {},
    // 只改 RSI 超卖线、因子和权重一个没动 —— 也是换了一个策略。
    // 不把 params 算进签名的话,结果区会继续显示上一次的年化/夏普,
    // 用户以为"调参没用",其实是根本没重跑
    p: x.params || {},
  })
  try { return sig(a) === sig(b) } catch { return false }
}

function saveDraft(d) {
  d.updated_at = Date.now()

  // ⚠️ 换了策略就把上一次的回测结果作废。
  //
  // 原来只写 draft 不动 result。而 backtest.html 是这么取数的:
  //     配置行  ← draft(刚换的新策略)
  //     结果区  ← localStorage['hunter_backtest_result'](上一次跑的)
  //
  // 于是从策略广场点「高股息防御」和点「质量+动量」,页面顶部的因子、
  // 股票池、换仓都跟着变了,底下的年化/夏普/回撤/换手却**一个数字都不动**,
  // 连"655ms"这个耗时都一模一样 —— 因为压根就是上一次那份结果。
  //
  // 用户看到的是"换了策略回测没变化",最自然的结论是回测功能是假的。
  //
  // 这里按配置指纹判断:配置没变(比如只改了名字)就留着结果,
  // 真换了策略就清掉,让 backtest.html 显示「尚未回测」。
  try {
    const prev = JSON.parse(localStorage.getItem(LS_DRAFT) || 'null')
    if (prev && !sameStrategyShape(prev, d)) {
      localStorage.removeItem(LS_RESULT)
    }
  } catch { localStorage.removeItem(LS_RESULT) }

  localStorage.setItem(LS_DRAFT, JSON.stringify(d))
}
function defaultDraft() {
  // 默认预置一个"价值 + 质量 + 动量"经典组合
  return {
    factors: ['pe_inv', 'dividend_yield', 'roe', 'momentum_12m_1m', 'vol_20d_inv'],
    weights: { pe_inv: 25, dividend_yield: 15, roe: 30, momentum_12m_1m: 20, vol_20d_inv: 10 },
    config: { universe: 'hs300', top_n: 20, rebalance: 'M', cost_bps: 15, benchmark: '000300' },
    name: '',
    updated_at: Date.now(),
  }
}
function loadMine() {
  try { return JSON.parse(localStorage.getItem(LS_MINE) || '[]') } catch { return [] }
}
function saveMine(list) {
  localStorage.setItem(LS_MINE, JSON.stringify(list.slice(-50)))
}

// ═════════════════════════════════════════════════════════════════
// C3 · 用户策略 CRUD 后端 API(fallback localStorage)
// ═════════════════════════════════════════════════════════════════
function getToken() {
  try { return localStorage.getItem('hunter_token') || '' } catch { return '' }
}
function apiHeaders(extra) {
  const t = getToken()
  return Object.assign(
    { 'Content-Type': 'application/json' },
    t ? { 'Authorization': 'Bearer ' + t } : {},
    extra || {}
  )
}

// 前端 record 格式:{factors: [key], weights: {key: pct}}
// 后端 API 格式:{factors: [{key, weight_pct}]}
function strategyToApi(record) {
  return {
    name: record.name,
    description: record.description || '',
    // 自定义参数一起存。不存的话:用户把 RSI 超卖线调到 20、
    // 存成策略、明天打开 —— 参数悄悄变回 30,选出来的票也变了,
    // 而界面上没有任何提示
    factors: (record.factors || []).map(k => {
      const f = { key: k, weight_pct: (record.weights || {})[k] || 0 }
      if (hasParams(k) && !isDefaultParams(k, record.params)) f.params = paramsOf(k, record.params)
      return f
    }),
    config: record.config || {},
  }
}
function strategyFromApi(row) {
  const factors = []
  const weights = {}
  const params = {}
  for (const f of (row.factors || [])) {
    factors.push(f.key)
    weights[f.key] = f.weight_pct
    if (f.params) params[f.key] = f.params      // 存进去的自定义参数要读回来
  }
  return {
    id: row.id,             // 后端整数 id · 前端 detect 用 (typeof id === 'number' → 后端)
    name: row.name,
    description: row.description || '',
    factors,
    weights,
    params,
    config: row.config || {},
    created_at: row.created_at ? new Date(row.created_at).getTime() : Date.now(),
    _from_api: true,
  }
}

async function loadMineFromApi() {
  if (!getToken()) return null
  try {
    const r = await fetch('/api/quant/strategies/mine', { headers: apiHeaders() })
    if (r.status === 401) return null
    if (!r.ok) return null
    const d = await r.json()
    return (d.strategies || []).map(strategyFromApi)
  } catch { return null }
}

async function createStrategyApi(record) {
  const r = await fetch('/api/quant/strategies', {
    method: 'POST',
    headers: apiHeaders(),
    body: JSON.stringify(strategyToApi(record)),
  })
  if (!r.ok) throw new Error('HTTP ' + r.status)
  return await r.json()  // {id}
}

async function deleteStrategyApi(id) {
  const r = await fetch('/api/quant/strategies/' + encodeURIComponent(id), {
    method: 'DELETE',
    headers: apiHeaders(),
  })
  if (!r.ok) throw new Error('HTTP ' + r.status)
  return true
}

// C5 · 社区分享
async function toggleShareApi(id, isPublic) {
  const r = await fetch('/api/quant/strategies/' + encodeURIComponent(id) + '/share', {
    method: 'PATCH',
    headers: apiHeaders(),
    body: JSON.stringify({is_public: isPublic}),
  })
  if (!r.ok) throw new Error('HTTP ' + r.status)
  return await r.json()
}

async function forkStrategyApi(id) {
  const r = await fetch('/api/quant/strategies/' + encodeURIComponent(id) + '/fork', {
    method: 'POST',
    headers: apiHeaders(),
  })
  if (!r.ok) throw new Error('HTTP ' + r.status)
  return await r.json()   // {id, fork_from, name}
}

async function loadLeaderboardApi(period, sort, limit) {
  const q = new URLSearchParams({
    period: period || '1y',
    sort: sort || 'sharpe',
    limit: String(limit || 20),
  }).toString()
  try {
    const r = await fetch('/api/quant/leaderboard?' + q)
    if (!r.ok) return null
    const d = await r.json()
    return d.strategies || []
  } catch { return null }
}

// 首次登录后 · 把 localStorage 里的历史策略上传到后端 · 只跑一次
async function migrateLocalToServer() {
  if (!getToken()) return
  if (localStorage.getItem('hunter_strategies_migrated_v1') === '1') return
  const local = loadMine()
  if (!local.length) {
    localStorage.setItem('hunter_strategies_migrated_v1', '1')
    return
  }
  let ok = 0
  for (const s of local) {
    try {
      await createStrategyApi(s)
      ok++
    } catch {}
  }
  if (ok > 0) toast(`✓ 已把 ${ok} 个本地策略同步到账户`, 'success')
  localStorage.setItem('hunter_strategies_migrated_v1', '1')
}

// ═════════════════════════════════════════════════════════════════
// 工具
// ═════════════════════════════════════════════════════════════════
function factorByKey(key) { return FACTORS.find(f => f.key === key) }

// ═════════════════════════════════════════════════════════════════
// 因子参数 · 界面镜像
// ═════════════════════════════════════════════════════════════════
//
// 产品经理:「这些因子大部分需要进一步设置参数,比如 RSI 超买卖多少
// 才算超,让客户自己设置。现在这种只拖动滚动条调节没有意义。」
//
// 这份表只管**怎么画**(范围、步长、说明文字)。真正算的时候以后端
// `FACTOR_PARAMS` 为准 —— 后端会夹取越界值、丢弃没登记的字段,
// 所以就算这里和后端偶尔对不上,也不会算出脏结果。
// 后端接口 `/api/quant/factors` 也返回同一份表,可用于比对。
//
// 不在表里的因子 = 没有可调参数(pe_inv 就是 1/PE,没什么好调)。
const FACTOR_PARAMS = {
  rsi: [
    { key:'period',     label:'RSI 周期',  def:14, min:2,  max:60, step:1, unit:'日',
      hint:'算 RSI 用最近多少天。短了灵敏也更吵,长了稳但滞后。' },
    { key:'oversold',   label:'超卖线',    def:30, min:5,  max:45, step:1, unit:'',
      hint:'RSI 低于这条线算超卖(打高分)。越低越苛刻。' },
    { key:'overbought', label:'超买线',    def:70, min:55, max:95, step:1, unit:'',
      hint:'RSI 高于这条线算超买(打低分)。这是个反向因子。' },
  ],
  macd: [
    { key:'fast',      label:'快线 EMA',   def:12, min:3,  max:50,  step:1, unit:'日', hint:'短周期均线。' },
    { key:'slow',      label:'慢线 EMA',   def:26, min:10, max:120, step:1, unit:'日', hint:'长周期均线,要大于快线。' },
    { key:'signal',    label:'信号线',     def:9,  min:2,  max:40,  step:1, unit:'日', hint:'对 MACD 再平滑一次,差值就是柱子。' },
    { key:'atr_period',label:'ATR 归一',   def:14, min:5,  max:60,  step:1, unit:'日', hint:'用 ATR 归一,不同价位的票才能比。' },
  ],
  ma_align: [
    { key:'ma1', label:'均线 1', def:5,  min:2,  max:20,  step:1, unit:'日', hint:'多头排列最短的那条。' },
    { key:'ma2', label:'均线 2', def:10, min:3,  max:60,  step:1, unit:'日', hint:'' },
    { key:'ma3', label:'均线 3', def:20, min:5,  max:120, step:1, unit:'日', hint:'' },
    { key:'ma4', label:'均线 4', def:60, min:10, max:250, step:1, unit:'日', hint:'最长那条。四条依次向上 = 满分。' },
  ],
  vol_20d_inv: [
    { key:'window', label:'波动率窗口', def:20, min:5, max:120, step:1, unit:'日',
      hint:'用多少天的收益率算标准差。窗口越长越平滑。' },
  ],
}

/** 这个因子有没有可调参数 */
function hasParams(key) { return (FACTOR_PARAMS[key] || []).length > 0 }

/** 某因子当前生效的参数 = 默认值 覆盖上 draft 里存的 */
function paramsOf(key, draftParams) {
  const spec = FACTOR_PARAMS[key] || []
  const saved = (draftParams || {})[key] || {}
  const out = {}
  spec.forEach(p => {
    const v = Number(saved[p.key])
    out[p.key] = Number.isFinite(v) ? Math.max(p.min, Math.min(p.max, v)) : p.def
  })
  return out
}

/** 是不是全都还是默认值(是的话不必往后端传,省一次实时计算) */
function isDefaultParams(key, draftParams) {
  const spec = FACTOR_PARAMS[key] || []
  const cur = paramsOf(key, draftParams)
  return spec.every(p => cur[p.key] === p.def)
}

/** 组装发给后端的 factors 数组 · 只有改过参数的才带 params */
function factorsPayload(draft) {
  return (draft.factors || [])
    .map(k => {
      const f = { key: k, weight_pct: draft.weights[k] || 0 }
      if (hasParams(k) && !isDefaultParams(k, draft.params)) f.params = paramsOf(k, draft.params)
      return f
    })
    .filter(f => f.weight_pct > 0)
}

function fmtPct(v, digits=1) { return (v>=0?'+':'') + (v*100).toFixed(digits) + '%' }
function draftFactorNames(d) {
  return d.factors.map(k => (factorByKey(k) || {}).name || k).join(' · ')
}
function draftWeightLine(d) {
  return d.factors.map(k => `${(factorByKey(k) || {}).name || k} ${d.weights[k]||0}%`).join(' · ')
}

// 生成 chat prompt 并跳转
function askHunterFromWorkbench() {
  const d = loadDraft()
  const cfg = d.config
  const prompt = [
    '我在组一个量化策略:',
    `· 因子: ${draftFactorNames(d)}`,
    `· 权重: ${d.factors.map(k => d.weights[k] + '%').join(' / ')}`,
    `· 股票池: ${UNIVERSE_NAME[cfg.universe]} · 持仓 Top ${cfg.top_n}`,
    `· 换仓: ${REBALANCE_NAME[cfg.rebalance]} · 单边成本 ${cfg.cost_bps} bps · 基准 ${BENCHMARK_NAME[cfg.benchmark]}`,
    '',
    '帮我看这个组合有什么风险? 因子会不会高度相关导致过拟合?'
  ].join('\n')
  location.href = '/chat?q=' + encodeURIComponent(prompt)
}
function askHunterFromBacktest() {
  const d = loadDraft()
  // **这是全部假数据里最糟的一处**(`_17` 全量排查):
  // 原来是 `d._metrics || {ann_ret:0.184, sharpe:1.42, max_dd:-0.143, win_rate:0.62}` ——
  // 没跑过回测时把写死的数字**当成真结果发给模型**,让它分析"这些指标合理吗"。
  // 模型会认真点评一组根本不存在的业绩,而用户以为那是自己策略的表现。
  // 连"基准 +6.2%""2024-Q4"这些也是编进 prompt 的。
  const real = JSON.parse(localStorage.getItem('quant_backtest_result') || 'null')
  const m = real?.metrics || d._metrics
  if (!m) {
    alert('还没有回测结果 —— 先到「策略工作台」跑一次回测,再让 Hunter 分析。')
    return
  }
  const q = real?.quality
  const lines = [
    `我用【${draftFactorNames(d)}】策略回测,区间 ${real?.start || '?'} → ${real?.end || '?'}:`,
    `· 年化收益 ${fmtPct(m.ann_ret)}`,
    `· 夏普 ${(m.sharpe ?? 0).toFixed(2)}`,
    `· 最大回撤 ${fmtPct(m.max_dd)}`,
  ]
  if (m.win_rate != null) lines.push(`· 期胜率 ${(m.win_rate*100).toFixed(0)}%(共 ${m.n_periods || '?'} 期)`)
  if (m.turnover != null) lines.push(`· 平均换手 ${(m.turnover*100).toFixed(0)}%`)
  // 把**成色**一起告诉模型 —— 不说的话它会当成一个可信结果来点评,
  // 而用户最需要知道的恰恰是"这个结果能不能信"
  if (!real?.benchmark) lines.push('· 注:基准未接入,没有超额收益数据')
  if (q && q.survivorship_ok === false) lines.push('· 注:股票池用的是当前成分,存在幸存者偏差,收益可能偏高')
  if (m.n_periods && m.n_periods < 12) lines.push(`· 注:只有 ${m.n_periods} 期,样本不足一年`)
  lines.push('', '这些指标合理吗? 在什么市场环境下会失效? 我该注意什么?')
  location.href = '/chat?q=' + encodeURIComponent(lines.join('\n'))
}
function askHunterAboutOfficial(strategy) {
  const prompt = `给我详细讲一下【${strategy.name}】这个官方策略。适用什么市场环境? 主要风险有哪些? 与"${strategy.factors.map(f=>factorByKey(f.key).name).slice(0,2).join(' + ')}"这种搭配的经典理论依据是什么?`
  location.href = '/chat?q=' + encodeURIComponent(prompt)
}

// ═════════════════════════════════════════════════════════════════
// UI · 顶栏 + tab · 每页共用
// ═════════════════════════════════════════════════════════════════
function renderShell(activeTab, title, subTitle, actions) {
  return `
<div class="app">
  <!-- 左侧竖排图标栏已移除(2026-09-01)。
       它是老版四入口导航的遗留:对话 / 自选 / 策略 / 推送。
       08-30 导航重构后主站顶栏只剩「策略中心」一个入口,自选股移进了
       /chat 左侧标签 —— 这条竖栏指向的 /watchlist、/push 都不再是
       一级入口,留着只会让用户在两套导航之间来回跳。
       取而代之:标题左边一个「返回对话」按钮,一步回 /chat。 -->
  <div class="main">
    <div class="topbar">
      <a href="/chat" title="返回对话"
         style="color:var(--muted);display:inline-flex;align-items:center;gap:4px;
                padding:4px 10px 4px 6px;border:1px solid var(--divider);border-radius:8px;
                font-size:12.5px;margin-right:10px;text-decoration:none">
        <span style="font-size:14px;line-height:1">←</span> 返回对话
      </a>
      <a href="/strategies/index.html" style="color:var(--text);display:inline-flex;align-items:center;gap:8px">
        <span class="hi hi-deer" style="font-size:22px;color:var(--brand)"></span>
        <div class="title">策略中心</div>
      </a>
      <div class="sub">· ${subTitle}</div>
      <div class="actions">${actions || ''}</div>
    </div>
    <div class="tabs-h">
      <a href="/strategies/index.html"     class="tab-h ${activeTab==='marketplace'?'active':''}">策略广场</a>
      <a href="/strategies/factors.html"   class="tab-h ${activeTab==='factors'?'active':''}">因子广场</a>
      <a href="/strategies/workbench.html" class="tab-h ${activeTab==='workbench'?'active':''}">策略工作台</a>
      <!-- 「回测结果」tab 已移除(问题24)。
           9/1 把回测结果并进了策略工作台(跑完就地出结果,不跳页),
           这个 tab 就成了一个**多余的中转**:用户点进去多半是空的,
           还得再找回工作台去跑。
           页面本身保留 —— 工作台里「查看完整报告 →」还指着它,
           那里有因子分档验证 / Bootstrap / 逐笔明细 / 下单 CSV,
           是"决定要用这个策略之后"才看的东西。只是不再占一个一级入口。 -->
      <a href="/strategies/data.html"      class="tab-h ${activeTab==='data'?'active':''}">数据</a>
    </div>
    <div id="page-content"></div>
  </div>
</div>`
}

// ═════════════════════════════════════════════════════════════════
// Toast + Modal helpers
// ═════════════════════════════════════════════════════════════════
function toast(msg, kind='') {
  let wrap = document.querySelector('.toast-wrap')
  if (!wrap) { wrap = document.createElement('div'); wrap.className = 'toast-wrap'; document.body.appendChild(wrap) }
  const el = document.createElement('div')
  el.className = 'toast ' + kind
  el.textContent = msg
  wrap.appendChild(el)
  setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity .3s'; setTimeout(() => el.remove(), 400) }, 2600)
}
function openModal(html) {
  const mask = document.createElement('div')
  mask.className = 'modal-mask'
  mask.innerHTML = `<div class="modal-shell">${html}</div>`
  document.body.appendChild(mask)
  mask.addEventListener('click', e => { if (e.target === mask) mask.remove() })
  mask.querySelectorAll('[data-close]').forEach(b => b.addEventListener('click', () => mask.remove()))
  return mask
}

// 保存为策略 dialog
function openSaveDialog(afterSave) {
  const d = loadDraft()
  const defaultName = d.name || `${draftFactorNames(d).split(' · ').slice(0,3).join('+')}`
  const modal = openModal(`
    <div class="modal-hd"><span class="hi hi-save" style="color:var(--brand)"></span> 保存为我的策略 <span class="close" data-close>×</span></div>
    <div class="modal-body">
      <div class="field" style="margin-bottom:12px">
        <div class="lbl">策略名称</div>
        <input id="save-name" class="input" placeholder="给策略起个好记的名字" value="${defaultName.replace(/"/g,'&quot;')}" maxlength="30" />
      </div>
      <label style="display:flex;gap:8px;align-items:center;padding:10px 12px;background:var(--panel);border-radius:8px;cursor:pointer;margin-bottom:12px">
        <input type="checkbox" id="save-public" style="accent-color:var(--brand)">
        <span style="font-size:12px;color:var(--text);display:inline-flex;align-items:center;gap:5px"><span class="hi hi-globe" style="color:var(--brand)"></span> <b>同时分享到社区</b>(其他用户可看/fork)</span>
      </label>
      <div style="color:var(--muted);font-size:12px;line-height:1.7">
        · 保存后可在 <b style="color:var(--text)">策略广场 · 我的策略</b> 找到<br>
        · 分享的策略会出现在 <b style="color:var(--text)">社区精选</b> · 支持随时关闭
      </div>
    </div>
    <div class="modal-ft">
      <button class="btn" data-close>取消</button>
      <button class="btn primary" id="save-ok">保存</button>
    </div>
  `)
  modal.querySelector('#save-ok').addEventListener('click', async () => {
    const btn = modal.querySelector('#save-ok')
    btn.disabled = true; btn.textContent = '保存中…'
    const name = modal.querySelector('#save-name').value.trim() || defaultName
    const record = {
      id: 'my_' + Date.now(),
      name,
      factors: d.factors,
      weights: d.weights,
      config: d.config,
      created_at: Date.now(),
    }
    // C3 · 先试 API · 失败 fallback localStorage
    let apiOk = false
    const isPublic = !!(modal.querySelector('#save-public')?.checked)
    if (getToken()) {
      try {
        const r = await createStrategyApi(record)
        record.id = r.id           // 用后端整数 id
        record._from_api = true
        apiOk = true
        // C5 · 用户勾了分享 · 保存后立即 PATCH is_public=true
        if (isPublic) {
          try { await toggleShareApi(r.id, true) }
          catch (e) { console.warn('[save-strategy] share 失败', e) }
        }
      } catch (e) {
        console.warn('[save-strategy] API 失败 · 落本地', e)
      }
    }
    const mine = loadMine()
    mine.unshift(record)
    saveMine(mine)
    d.name = name
    saveDraft(d)
    modal.remove()
    toast(apiOk ? '✓ 已保存到账户 · 换设备可见' : '✓ 已保存(本地 · 登录后可同步)', 'success')
    if (afterSave) afterSave()
  })
}

// C3 · 删除自己的策略 · 优先调 API · fallback 删本地
async function deleteMyStrategy(id) {
  if (!confirm('确认删除这个策略?')) return false
  let apiOk = false
  if (getToken() && typeof id === 'number') {
    try { await deleteStrategyApi(id); apiOk = true }
    catch (e) { console.warn('[delete-strategy] API 失败', e) }
  }
  const mine = loadMine().filter(s => String(s.id) !== String(id))
  saveMine(mine)
  toast(apiOk ? '✓ 已从账户删除' : '✓ 已从本地删除', 'success')
  return true
}

// 订阅推送 dialog
function openSubscribeDialog() {
  openModal(`
    <div class="modal-hd"><span class="hi hi-bell" style="color:var(--brand)"></span> 订阅每日推送 <span class="close" data-close>×</span></div>
    <div class="modal-body">
      <div style="line-height:1.75;color:var(--text)">
        每交易日 <b>15:30 收盘后</b>，推送当日策略 <b>Top 20 持仓与变动</b>：
      </div>
      <div style="margin:14px 0 8px">
        <label style="display:flex;align-items:center;gap:8px;padding:10px 12px;background:var(--panel);border-radius:8px;cursor:pointer">
          <input type="checkbox" checked style="accent-color:var(--brand)"> WeChat 模板消息
        </label>
      </div>
      <div style="margin-bottom:16px">
        <label style="display:flex;align-items:center;gap:8px;padding:10px 12px;background:var(--panel);border-radius:8px;cursor:pointer">
          <input type="checkbox" style="accent-color:var(--brand)"> 飞书群机器人
        </label>
      </div>
      <div style="padding:10px 12px;background:var(--tag-warn-bg);color:var(--tag-warn-fg);border-radius:8px;font-size:11.5px">
        提示 · 每日推送功能将在下一版本上线，届时自动开启订阅
      </div>
    </div>
    <div class="modal-ft">
      <button class="btn" data-close>取消</button>
      <button class="btn primary" id="sub-ok">确认订阅</button>
    </div>
  `).querySelector('#sub-ok').addEventListener('click', () => {
    document.querySelector('.modal-mask').remove()
    toast('✓ 订阅成功 · 每交易日 15:30 推送', 'success')
  })
}

// 分享
function shareCurrentUrl() {
  navigator.clipboard.writeText(location.href).then(
    () => toast('✓ 已复制链接到剪贴板', 'success'),
    () => toast('复制失败,请手动选中地址栏', 'warn')
  )
}
