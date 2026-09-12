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
  // F-1 · 纯日线 7 因子(2026-09-09)· 与后端 factor_defs.py 同步 · 近似口径写在 desc 里
  { key: 'vol_ratio_20', cat: '技术',   name: '量比',            icon: '📊', desc: '今日成交量 / 前 20 日均量 · 方向待 IC 验证' },
  { key: 'high52_prox',  cat: '动量',   name: '52 周高点距离',   icon: '📈', desc: 'close / 过去 250 日最高价 · 越接近 1 越强势' },
  { key: 'turnover_20',  cat: '流动性', name: '20 日换手率',     icon: '🔄', desc: '20 日均成交股数 / 估算总股本 · 多作过滤条件' },
  { key: 'amihud_20',    cat: '流动性', name: 'Amihud 非流动性', icon: '🔄', desc: 'mean(|日收益| / 成交额) · 成交额用 量×100×收盘 近似', note: '反向 IC' },
  { key: 'size_inv',     cat: '规模',   name: '小市值',          icon: '📏', desc: '−ln(总市值) · 市值 = close × 估算总股本 · 注意幸存者偏差' },
  { key: 'beta_60',      cat: '波动',   name: '市场贝塔',        icon: '🛡', desc: '60 日个股收益对沪深 300 的回归斜率 · 指数历史不足时不产出' },
  { key: 'ret_skew_60',  cat: '波动',   name: '收益偏度',        icon: '🛡', desc: '60 日日收益分布偏度 · 负偏 = 暴跌尾部风险' },
]

const CAT_ORDER = ['价值', '质量', '成长', '动量', 'ML', '技术', '资金', '波动', '流动性', '规模']
const CAT_ICON = { '价值': '💰', '质量': '🏆', '成长': '🚀', '动量': '📈', 'ML': '🧠', '技术': '📉', '资金': '💵', '波动': '🛡', '流动性': '🔄', '规模': '📏' }

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
  a_all: 'A 股全市场 · 剔 ST/次新', hk_all: '港股通', my_watchlist: '我的自选',
  us_all: '美股 · 全美股(已下载)'
}
const REBALANCE_NAME = { W: '周度', M: '月度', Q: '季度', H: '半年' }
const BENCHMARK_NAME = { '000300': '沪深 300', '000905': '中证 500', '399006': '创业板指', 'HSI': '恒生指数',
                         '.INX': '标普 500' }
// 代码 → 市场(和后端 quant/market.py 同一口径:纯数字 = A 股,含字母 = 美股)
const isUsCode = c => !/^[0-9]+$/.test(String(c || ''))

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

// ═════════════════════════════════════════════════════════════════
// 登录态续期 —— 策略中心这几页自己管,因为它们**不经过主站的 AuthGuard**
// ═════════════════════════════════════════════════════════════════
// 2026-09-11 用户报「明明登录了,保存扫描策略却经常提示要登录」。
//
// 根因三层叠在一起:
//   1. access token 只有 1 小时(auth.py JWT_ACCESS_TTL=3600),refresh token 30 天。
//   2. 主站(/chat 等 React 页)靠 components/AuthGuard.tsx 在撞到 401 时拿
//      refresh token 续期。**但 /strategies/*.html 是 public/ 下的纯静态页,
//      根本不加载 AuthGuard** —— 只会拿 localStorage 里现存的 token 去请求,从不续期。
//      所以只要距离主站上次续期超过 1 小时,这几页的所有登录态请求都失效。
//      「经常」而不是「每次」,就是这么来的。
//   3. **/api/quant/ 是免登录前缀,走可选身份识别**:过期 token 不被拒,
//      而是被**静默当成匿名**。所以中间件不告警,读接口(我的策略、我的扫描策略)
//      返回的是 200 的匿名结果 —— 不会出现 401。只有写接口在路由里判 uid 才报出来。
//
// 第 3 条决定了修法:**光在 401 时续期不够**,读接口压根不 401,只是悄悄变空。
// 所以两道都要:
//   ① 发请求**之前**:token 快过期(<60s)就先续,再把新 token 换进请求头
//   ② 发请求**之后**:仍然 401 就续一次、重发一次
//
// 两个坑,都照 AuthGuard 那边踩过的来:
//   · refresh token 是**一次性**的(后端 rotate:用一次就作废旧的)。页面同时发好几个
//     请求会一起去续,没有单飞锁的话第一个成功、后面全拿着作废的旧 token 失败。
//   · **跨标签页**同理:对话页和这里同时续,必有一边失败。失败时先回头看一眼
//     localStorage —— token 已经被别的标签页换新了,就直接用它,不算失败。
//   · 续期失败**不清 token、不跳登录页**。清掉会把用户在对话页的登录也一起踢掉,
//     而失败很可能只是上面那个跨标签页竞态。交给调用方显示「登录已失效」即可。
const AUTH_SKEW_SEC = 60

function jwtExp(tok) {
  try {
    let p = String(tok).split('.')[1] || ''
    p = p.replace(/-/g, '+').replace(/_/g, '/')
    while (p.length % 4) p += '='
    const exp = JSON.parse(atob(p)).exp
    return Number.isFinite(exp) ? exp : null
  } catch (e) { return null }
}
// 解不出 exp 就不猜(返回 false),交给 ② 的 401 兜底
function tokenStale(tok) {
  const exp = jwtExp(tok)
  return exp != null && exp - Date.now() / 1000 < AUTH_SKEW_SEC
}

const _rawFetch = (typeof window !== 'undefined' && typeof window.fetch === 'function')
  ? (window.__hunterOriginalFetch || window.fetch).bind(window) : null

function _storeTokens(d) {
  const tok = d && (d.access_token || d.token)
  if (!tok) return null
  try {
    localStorage.setItem('hunter_token', tok)
    if (d.refresh_token) localStorage.setItem('hunter_refresh', d.refresh_token)
  } catch (e) { /* 隐私模式 */ }
  return tok
}

let _refreshInflight = null
async function refreshAuth() {
  if (!_rawFetch) return null
  if (_refreshInflight) return _refreshInflight
  _refreshInflight = (async function () {
    const before = getToken()
    let rt = null
    try { rt = localStorage.getItem('hunter_refresh') } catch (e) { /* 隐私模式 */ }
    if (rt) {
      try {
        const r = await _rawFetch('/api/auth/refresh', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: rt }), cache: 'no-store',
        })
        if (r.ok) { const tok = _storeTokens(await r.json()); if (tok) return tok }
      } catch (e) { /* 网络问题,往下走 */ }
    }
    // 别的标签页刚续过(refresh token 已被它用掉)—— 用它存下的新 token
    const now = getToken()
    if (now && now !== before && !tokenStale(now)) return now
    // 单用户模式:和 AuthGuard / localSession.ts 一样静默重取;多用户实例返回 403
    try {
      const r = await _rawFetch('/api/auth/local-session', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, cache: 'no-store',
      })
      if (r.ok) { const tok = _storeTokens(await r.json()); if (tok) return tok }
    } catch (e) { /* ignore */ }
    return null
  })()
  try { return await _refreshInflight } finally { _refreshInflight = null }
}

// 把请求头里的 Authorization 换成新 token(大小写不敏感)。
// 只处理字符串 URL + 普通对象 / Headers / 数组三种 headers 写法 —— 本目录都是这么调的。
function withAuth(init, tok) {
  const i = Object.assign({}, init || {})
  const h = i.headers
  const v = 'Bearer ' + tok
  if (h && typeof h.set === 'function' && typeof Headers === 'function') {
    const hh = new Headers(h); hh.set('Authorization', v); i.headers = hh
  } else if (Array.isArray(h)) {
    i.headers = h.filter(function (p) { return String(p[0]).toLowerCase() !== 'authorization' })
                 .concat([['Authorization', v]])
  } else {
    const o = {}
    Object.keys(h || {}).forEach(function (k) { if (k.toLowerCase() !== 'authorization') o[k] = h[k] })
    o['Authorization'] = v
    i.headers = o
  }
  return i
}

function _isOwnApi(url) {
  if (typeof url !== 'string') return false
  const u = url.indexOf(location.origin) === 0 ? url.slice(location.origin.length) : url
  return u.indexOf('/api/') === 0 && !/^\/api\/auth\/(refresh|local-session|login|register|logout)/.test(u)
}

if (_rawFetch && !window.__hunterStrategiesAuthPatched) {
  window.__hunterStrategiesAuthPatched = true
  window.fetch = async function (input, init) {
    if (!_isOwnApi(input)) return _rawFetch(input, init)
    // ① 发之前:快过期就先续
    const cur = getToken()
    if (cur && tokenStale(cur)) {
      const fresh = await refreshAuth()
      if (fresh) init = withAuth(init, fresh)
    }
    const r = await _rawFetch(input, init)
    // ② 发之后:还是 401 就续一次、重发一次(只重发一次,避免死循环)
    if (r.status === 401) {
      const fresh = await refreshAuth()
      if (fresh) return _rawFetch(input, withAuth(init, fresh))
    }
    return r
  }
  // 页面一打开就检查一次:过期就立刻续,别等第一个请求去撞
  try { const t0 = getToken(); if (t0 && tokenStale(t0)) refreshAuth() } catch (e) { /* ignore */ }
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
      // 判据只用 isDefaultParams,**不要**再加 `hasParams(k) &&`:参数定义还没
      // 从后端拉到时 hasParams 恒 false,那道与会把用户存着的自定义参数整个吞掉 ——
      // 存下来的策略明天打开就变回默认口径,而界面上没有任何提示。
      // isDefaultParams 自己处理了"定义没拉到"这一支(见它的注释),
      // factorsPayload 走的也是同一条判据,两处保持一致。
      if (!isDefaultParams(k, record.params)) f.params = paramsOf(k, record.params)
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
// 因子参数 · 由后端下发,前端不再手抄
// ═════════════════════════════════════════════════════════════════
//
// 产品经理:「这些因子大部分需要进一步设置参数,比如 RSI 超买卖多少
// 才算超,让客户自己设置。现在这种只拖动滚动条调节没有意义。」
//
// **这里原来是一份手抄的副本,一个提交之后就过期了。**
// 2026-09-09 加 7 个纯日线因子(量比 / 52 周高点 / 换手 / Amihud /
// 贝塔 / 偏度)时后端登记了参数,前端这份表没跟着改 —— 于是用户在
// 工作台上看到「52 周高点距离」连一个「参数」入口都没有,而后端
// 明明支持。功能做了但没人能发现,等于没做。
//
// 现在改成从 `/api/quant/factors` 拉(那个接口本来就返回同一份表),
// **前端一份都不存**:后端加参数,界面自动就有。
// 拉不到就在参数区显示"参数没加载出来"并给个重试,**不用旧副本兜底** ——
// 拿一份可能过期的范围去画输入框,用户会以为自己调的是真的。
//
// 字段名两边不同(后端 `default`,界面历史上用 `def`),
// `normalizeParamSpec` 统一成 `def`;类型 `type` 缺省按默认值推断,
// 支持 int / float / select(select 用 `options: [{value,label}]`)。
let FACTOR_PARAM_SPECS = null      // null = 还没拉到 · {} = 拉到了但没有任何可调参数
let FACTOR_PARAM_ERROR = ''

function normalizeParamSpec(list) {
  return (list || []).map(p => ({
    key: p.key,
    label: p.label || p.key,
    def: p.default !== undefined ? p.default : p.def,
    type: p.type || (Number.isInteger(p.default !== undefined ? p.default : p.def) ? 'int' : 'float'),
    min: p.min, max: p.max, step: p.step,
    options: p.options || null,
    unit: p.unit || '',
    hint: p.hint || '',
  }))
}

/** 拉一次因子清单,把可调参数收进 FACTOR_PARAM_SPECS · 返回是否成功 */
async function loadFactorParams() {
  try {
    const d = await fetch('/api/quant/factors', { cache: 'no-store' }).then(r => r.json())
    const out = {}
    for (const f of (d.factors || [])) {
      if (f.params && f.params.length) out[f.key] = normalizeParamSpec(f.params)
    }
    FACTOR_PARAM_SPECS = out
    FACTOR_PARAM_ERROR = ''
    return true
  } catch (e) {
    FACTOR_PARAM_SPECS = null
    FACTOR_PARAM_ERROR = (e && e.message) || String(e)
    return false
  }
}

/** 某因子的参数定义 · 还没拉到就是空数组(界面据此显示"加载中") */
function paramSpecOf(key) { return (FACTOR_PARAM_SPECS || {})[key] || [] }

/** 参数定义拉到了吗(没拉到时界面不能说"这个因子没有参数") */
function paramSpecsReady() { return FACTOR_PARAM_SPECS !== null }

/** 这个因子有没有可调参数 */
function hasParams(key) { return paramSpecOf(key).length > 0 }

/** 把一个值收进它自己的合法范围 · select 认选项,数值夹 [min,max] 并对齐 step */
function clampParam(p, raw) {
  if (p.type === 'select') {
    const ok = (p.options || []).some(o => o.value === raw)
    return ok ? raw : p.def
  }
  let v = Number(raw)
  if (!Number.isFinite(v)) return p.def
  if (p.step) v = Math.round(v / p.step) * p.step
  v = Math.max(p.min, Math.min(p.max, v))
  // step 是 0.5 这类小数时会算出 7.000000000000001,显示出来很难看
  return p.type === 'int' ? Math.round(v) : Math.round(v * 1e6) / 1e6
}

/** 某因子当前生效的参数 = 默认值 覆盖上 draft 里存的 */
function paramsOf(key, draftParams) {
  const spec = paramSpecOf(key)
  const saved = (draftParams || {})[key] || {}
  // 参数定义还没拉到时,原样返回存着的那份 —— 这时不知道范围,
  // 但**绝不能返回空**:下面 factorsPayload 要靠它把用户调过的参数发给后端,
  // 返回空等于用户存好的参数在打分时悄悄变回默认值
  if (!spec.length) return { ...saved }
  const out = {}
  spec.forEach(p => {
    out[p.key] = (saved[p.key] === undefined || saved[p.key] === null)
      ? p.def : clampParam(p, saved[p.key])
  })
  return out
}

/** 是不是全都还是默认值(是的话不必往后端传,省一次实时计算) */
function isDefaultParams(key, draftParams) {
  const spec = paramSpecOf(key)
  if (!spec.length) {
    // 定义还没拉到:只要 draft 里存了东西,就当成"用户调过",照发后端。
    // 后端会夹取越界值、丢掉没登记的键,发多了不会算脏,发少了才会。
    const saved = (draftParams || {})[key] || {}
    return Object.keys(saved).length === 0
  }
  const cur = paramsOf(key, draftParams)
  return spec.every(p => cur[p.key] === p.def)
}

/** 参数摘要 · 折叠状态下显示 「RSI 周期 14 · 超卖线 30」这样一行 */
function paramSummary(key, draftParams) {
  const spec = paramSpecOf(key)
  if (!spec.length) {
    // 定义没拉到但 draft 里存着值:直接把键值列出来。
    // 显示 `near_pct 5` 不好看,但比什么都不显示强 —— 用户至少知道
    // 这次算的确实带了自定义参数,而不是以为回测走的是默认口径
    const saved = (draftParams || {})[key] || {}
    return Object.keys(saved).map(kk => `${kk} ${saved[kk]}`).join(' · ')
  }
  const cur = paramsOf(key, draftParams)
  const label = p => {
    if (p.type === 'select') {
      const o = (p.options || []).find(x => x.value === cur[p.key])
      return `${p.label} ${o ? o.label : cur[p.key]}`
    }
    return `${p.label} ${cur[p.key]}${p.unit || ''}`
  }
  const head = spec.slice(0, 2).map(label).join(' · ')
  return spec.length > 2 ? `${head} 等 ${spec.length} 项` : head
}

/** 组装发给后端的 factors 数组 · 只有改过参数的才带 params */
function factorsPayload(draft) {
  return (draft.factors || [])
    .map(k => {
      const f = { key: k, weight_pct: draft.weights[k] || 0 }
      if (!isDefaultParams(k, draft.params)) f.params = paramsOf(k, draft.params)
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
  // ⚠️ 键名必须是 LS_RESULT('hunter_backtest_result')。原来写的
  // 'quant_backtest_result' **没有任何地方写过** —— workbench 存的是 LS_RESULT。
  // 于是 real 恒为 null:用户刚跑完回测点「问 Hunter」,得到的是
  // "还没有回测结果,先去跑一次"。清假数据那轮把兜底 mock 去掉之后,
  // 这条死键就从"悄悄用假数据"变成了"功能直接不可用",更明显但同样是坏的。
  let real = null
  try { real = JSON.parse(localStorage.getItem(LS_RESULT) || 'null') } catch {}
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
      <!-- 小鹿智能体(2026-09-09)。自迭代量化原型:每日自动收数据 / 回测 / 复盘 / 改规则。
           放在工作台与数据之间 —— 工作台是「人调策略」,它是「策略自己调自己」,
           两者是同一件事的手动挡与自动挡,挨着放才看得出关系。 -->
      <a href="/strategies/agent.html"    class="tab-h ${activeTab==='agent'?'active':''}">小鹿智能体</a>
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


// ═════════════════════════════════════════════════════════════════
// 悬停日K —— 鼠标停在代码上弹出近一年日线 · 滚轮缩放 · 十字星读数
// ═════════════════════════════════════════════════════════════════
// 2026-09-12 从 screener.html 搬到这里变成公共模块:智能体的历史交易记录
// 也要用它,再抄一份迟早会漂移(前科:hermes 和 huntercode 两份 uzi_mcp.py)。
// 搬进 app.js 而不是新建文件 —— render_check 只跑内联 script 和 app.js,
// 新文件它不加载,里面的语法错就永远抓不到。
//
// 数据走现成的 GET /api/kline/{code}?period=daily&limit=250。
// 那个端点内部按代码形态分派(A 股走 finance-data,港美股走免费通道)。
//
// 四个体验点,少一个都不像样:
//   1. 悬停 180ms 才请求 —— 否则鼠标扫过一列会连打几十个请求
//   2. 移出 260ms 才隐藏 —— 这段时间够鼠标从代码移进弹层
//   3. 弹层自己 mouseenter 时取消隐藏 —— 否则滚轮还没碰到图它就没了
//   4. 同一只票只请求一次,回头再看是瞬开的
//
// ⚠️ 用 opacity 而不是 display:none 来隐藏:display:none 的容器尺寸为 0,
// echarts 在里面 init 会画出一张 0×0 的图,再 resize 也回不来。
//
// 标记(2026-09-12 加):元素上挂 data-kmark='{"buy":["2026-08-11"],"sell":[…],"scan":[…]}'
//   scan  扫描筛选命中当天 —— 半透明蓝色竖线(markArea,占满整根K线的宽度)
//   buy   规则买入当天 —— 绿色 Buy 标签
//   sell  规则卖出当天 —— 红色 Sell 标签
// 标记画在 K 线序列上,所以缩放平移时跟着走。
const KC = { el: null, chart: null, cache: new Map(),
             seq: 0, showT: null, hideT: null, cur: null, bound: false,
             asOf: null }          // asOf: 回溯时只画到这天(screener 用)
const KC_LIMIT = 250          // 一年大约 250 个交易日
// 中式红涨绿跌,与站内其它页面一致(标的可能是美股,但全站配色统一)
const KC_UP = '#a4332b', KC_DN = '#3f6b40'
// 买卖标记反过来用国际习惯的绿买红卖 —— 它标的是「我的动作」不是「涨跌」,
// 和 K 线的红绿不是一回事,用户 2026-09-12 明确指定了颜色
const KC_BUY = '#1f9254', KC_SELL = '#c0392b'
const KC_SCAN = 'rgba(46,134,222,.16)'    // 扫描命中:半透明蓝

function kcEsc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;')
}
function kcNum(v, d) {
  return Number.isFinite(v) ? v.toFixed(d == null ? 2 : d) : '—'
}
function kcVol(v) {
  if (!Number.isFinite(v)) return '—'
  if (v >= 1e8) return (v / 1e8).toFixed(2) + '亿'
  if (v >= 1e4) return (v / 1e4).toFixed(1) + '万'
  return String(Math.round(v))
}

function kcEl() {
  if (KC.el) return KC.el
  const d = document.createElement('div')
  d.className = 'kc-pop'
  d.style.left = '-9999px'
  d.style.top = '0px'
  d.innerHTML =
    '<div class="kc-hd"><span class="sy" id="kc-sy"></span>' +
    '<span class="nm" id="kc-nm"></span><span class="px" id="kc-px"></span></div>' +
    '<div class="kc-box" id="kc-box"></div>' +
    '<div class="kc-ft"><span id="kc-lg">滚轮缩放 · 十字星读数</span><span class="r" id="kc-rg"></span></div>'
  document.body.appendChild(d)
  // 鼠标进了弹层就别关 —— 用户要在里面滚轮缩放
  d.addEventListener('mouseenter', function () { clearTimeout(KC.hideT) })
  d.addEventListener('mouseleave', kcHide)
  KC.el = d
  return d
}

function kcPlace(rect) {
  const el = kcEl()
  const W = el.offsetWidth || 560
  const H = el.offsetHeight || 320
  let x = rect.right + 14
  if (x + W > window.innerWidth - 8) x = rect.left - W - 14   // 右边放不下就翻到左边
  if (x < 8) x = 8
  let y = rect.top + rect.height / 2 - H / 2
  y = Math.max(8, Math.min(y, window.innerHeight - H - 8))     // 上下都夹住
  el.style.left = Math.round(x) + 'px'
  el.style.top = Math.round(y) + 'px'
}

function kcHide() {
  clearTimeout(KC.showT)
  KC.hideT = setTimeout(function () {
    if (KC.el) KC.el.classList.remove('on')
    KC.cur = null
  }, 60)
}

function kcMsg(html) {
  const box = document.getElementById('kc-box')
  if (box) box.innerHTML = '<div class="kc-msg">' + html + '</div>'
}

// 把标记里的日期对到 K 线的下标上。日线只有交易日,而标记的日子一定是交易日
// (它们本来就来自成交与扫描记录),对不上的直接丢掉 —— 硬凑到最近一根
// 会把标记画在没发生那件事的那天上。
function kcIndexOf(rows, want) {
  const idx = {}
  for (let i = 0; i < rows.length; i++) {
    idx[String(rows[i].ts || rows[i].date || '').slice(0, 10)] = i
  }
  const out = []
  for (const d of (want || [])) {
    const i = idx[String(d).slice(0, 10)]
    if (i != null) out.push(i)
  }
  return out
}

function kcMarkSeries(rows, mark) {
  const out = []
  if (!mark) return out
  const scan = kcIndexOf(rows, mark.scan)
  const buy = kcIndexOf(rows, mark.buy)
  const sell = kcIndexOf(rows, mark.sell)
  // 扫描命中:一根 K 线宽的竖向色带。用 markArea 而不是 markLine ——
  // 竖线只有 1px,几十个命中日挤在一起看不出哪根是哪根
  if (scan.length) {
    out.push({
      type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: [], silent: true,
      markArea: {
        itemStyle: { color: KC_SCAN },
        data: scan.map(function (i) { return [{ xAxis: i - 0.5 }, { xAxis: i + 0.5 }] }),
      },
    })
  }
  const tag = function (list, color, text, pos) {
    if (!list.length) return
    out.push({
      type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: [], silent: true,
      markPoint: {
        symbol: 'pin', symbolSize: 26, symbolOffset: [0, pos === 'top' ? -2 : 2],
        itemStyle: { color: color },
        label: { color: '#fff', fontSize: 9, fontWeight: 600, formatter: text },
        data: list.map(function (i) {
          const r = rows[i]
          return { xAxis: i, yAxis: pos === 'top' ? r.high : r.low, name: text }
        }),
      },
    })
  }
  tag(buy, KC_BUY, 'Buy', 'bottom')     // 买在低处标 —— 视觉上贴着 K 线的下沿
  tag(sell, KC_SELL, 'Sell', 'top')
  return out
}

function kcOption(rows, mark) {
  const dates = rows.map(function (r) { return String(r.ts || r.date || '').slice(5) })
  const ohlc = rows.map(function (r) { return [r.open, r.close, r.low, r.high] })
  const vols = rows.map(function (r) { return r.volume })
  const axisLbl = { fontSize: 10, color: '#777970' }
  return {
    animation: false,
    // 两个 grid:主图 + 成交量。link 让两边的十字线一起动
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: [{ left: 54, right: 12, top: 8, height: 158 },
           { left: 54, right: 12, top: 182, height: 42 }],
    xAxis: [
      { type: 'category', data: dates, gridIndex: 0, boundaryGap: true,
        axisLabel: { show: false }, axisTick: { show: false },
        axisLine: { lineStyle: { color: '#deddd5' } },
        axisPointer: { label: { show: false } } },
      { type: 'category', data: dates, gridIndex: 1, boundaryGap: true,
        axisLabel: axisLbl, axisTick: { show: false },
        axisLine: { lineStyle: { color: '#deddd5' } } },
    ],
    yAxis: [
      { scale: true, gridIndex: 0, axisLabel: axisLbl,
        splitLine: { lineStyle: { color: '#f2f0ea' } },
        axisLine: { show: false }, axisTick: { show: false } },
      { gridIndex: 1, splitNumber: 2, axisLabel: { show: false },
        splitLine: { show: false }, axisLine: { show: false }, axisTick: { show: false } },
    ],
    dataZoom: [{
      type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100,
      zoomOnMouseWheel: true,
      // ⚠️ 这两个必须关掉。开着的话鼠标一动图就平移,十字星根本读不了数 ——
      // 而「十字星读数」正是这个弹层存在的理由。
      moveOnMouseMove: false, moveOnMouseWheel: false,
      minValueSpan: 8,        // 最多放大到 8 根,再放大就看不出形态了
    }],
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross', lineStyle: { color: '#b56b2d', width: 1, type: 'dashed' },
                     crossStyle: { color: '#b56b2d', width: 1, type: 'dashed' },
                     label: { backgroundColor: '#8f4f1d', fontSize: 10.5 } },
      backgroundColor: 'rgba(255,255,255,.97)',
      borderColor: '#deddd5', borderWidth: 1, padding: [7, 9],
      textStyle: { color: '#1f211f', fontSize: 11.5 },
      formatter: function (ps) {
        if (!ps || !ps.length) return ''
        const i = ps[0].dataIndex
        const r = rows[i]
        if (!r) return ''
        const prev = i > 0 ? rows[i - 1].close : r.open
        const chg = (Number.isFinite(prev) && prev) ? (r.close - prev) / prev * 100 : null
        const cls = chg == null ? '' : (chg >= 0 ? KC_UP : KC_DN)
        const day = String(r.ts || r.date || '').slice(0, 10)
        // 那天发生过什么,直接写进读数里 —— 光有色块还得对着图例猜
        const evt = []
        if (mark && (mark.scan || []).some(function (d) { return String(d).slice(0, 10) === day })) evt.push('扫描命中')
        if (mark && (mark.buy || []).some(function (d) { return String(d).slice(0, 10) === day })) evt.push('<b style="color:' + KC_BUY + '">买入</b>')
        if (mark && (mark.sell || []).some(function (d) { return String(d).slice(0, 10) === day })) evt.push('<b style="color:' + KC_SELL + '">卖出</b>')
        return '<b>' + kcEsc(String(r.ts || r.date || '')) + '</b>' +
          (evt.length ? '　' + evt.join(' · ') : '') + '<br>' +
          '开 ' + kcNum(r.open) + '　高 ' + kcNum(r.high) + '<br>' +
          '低 ' + kcNum(r.low) + '　收 <b>' + kcNum(r.close) + '</b><br>' +
          '涨跌 <b style="color:' + cls + '">' +
            (chg == null ? '—' : (chg >= 0 ? '+' : '') + chg.toFixed(2) + '%') + '</b><br>' +
          '量 ' + kcVol(r.volume)
      },
    },
    series: [
      { type: 'candlestick', data: ohlc, xAxisIndex: 0, yAxisIndex: 0,
        itemStyle: { color: KC_UP, color0: KC_DN,
                     borderColor: KC_UP, borderColor0: KC_DN, borderWidth: 1 } },
      { type: 'bar', data: vols, xAxisIndex: 1, yAxisIndex: 1,
        itemStyle: { color: function (p) {
          const r = rows[p.dataIndex]
          return (r && r.close >= r.open) ? 'rgba(164,51,43,.45)' : 'rgba(63,107,64,.45)'
        } } },
    ].concat(kcMarkSeries(rows, mark)),
  }
}

function kcLegend(rows, mark) {
  const lg = document.getElementById('kc-lg')
  if (!lg) return
  if (!mark) { lg.textContent = '滚轮缩放 · 十字星读数'; return }
  const n = function (a) { return kcIndexOf(rows, a).length }
  const parts = []
  if (n(mark.scan)) parts.push('<i style="background:' + KC_SCAN + '"></i>扫描命中 ' + n(mark.scan) + ' 天')
  if (n(mark.buy)) parts.push('<i style="background:' + KC_BUY + '"></i>买入 ' + n(mark.buy))
  if (n(mark.sell)) parts.push('<i style="background:' + KC_SELL + '"></i>卖出 ' + n(mark.sell))
  lg.innerHTML = parts.length ? parts.join('　') : '滚轮缩放 · 十字星读数'
}

function kcRender(code, name, payload, mark) {
  const el = kcEl()
  const sy = document.getElementById('kc-sy')
  const nm = document.getElementById('kc-nm')
  const px = document.getElementById('kc-px')
  const rg = document.getElementById('kc-rg')
  if (sy) sy.textContent = code
  if (nm) nm.textContent = name || ''

  let rows = (payload && payload.rows) || []
  if (KC.asOf) {
    // 回溯时只画到那天:图上露出之后的走势,等于把答案写在题目旁边
    rows = rows.filter(function (r) { return String(r.ts || r.date || '').slice(0, 10) <= KC.asOf })
  }
  if (!rows.length) {
    // 拿不到就明说,不要一直转圈 —— 与 routers/kline.py 那条注释同一个道理:
    // 用户分不清「这只票没数据」和「还在加载」,只会一直等下去。
    if (px) { px.textContent = ''; px.className = 'px' }
    if (rg) rg.textContent = ''
    kcMsg(kcEsc((payload && payload.error) || '暂无日线数据'))
    if (KC.chart) { KC.chart.dispose(); KC.chart = null }
    return
  }

  const last = rows[rows.length - 1]
  const prev = rows.length > 1 ? rows[rows.length - 2].close : last.open
  const chg = (Number.isFinite(prev) && prev) ? (last.close - prev) / prev * 100 : null
  if (px) {
    px.textContent = kcNum(last.close) + (chg == null ? '' :
      '　' + (chg >= 0 ? '+' : '') + chg.toFixed(2) + '%')
    px.className = 'px' + (chg == null ? '' : (chg >= 0 ? ' up' : ' dn'))
  }
  if (rg) rg.textContent = String(rows[0].ts || '').slice(0, 10) + ' → ' +
                           String(last.ts || '').slice(0, 10) + ' · ' + rows.length + ' 根'
  kcLegend(rows, mark)

  const box = document.getElementById('kc-box')
  if (!box) return
  box.innerHTML = ''
  if (KC.chart) { KC.chart.dispose(); KC.chart = null }
  if (!window.echarts) { kcMsg('图表库没加载出来'); return }
  KC.chart = window.echarts.init(box)
  KC.chart.setOption(kcOption(rows, mark))
  kcPlace(el._rect || { right: 0, left: 0, top: 0, height: 0 })
}

async function kcFetch(code) {
  if (KC.cache.has(code)) return KC.cache.get(code)
  let out
  try {
    const r = await fetch('/api/kline/' + encodeURIComponent(code) +
                          '?period=daily&limit=' + KC_LIMIT,
                          { headers: apiHeaders(), cache: 'no-store' })
    const j = await r.json()
    out = Array.isArray(j)
      ? { rows: j, error: null }
      : { rows: [], error: (j && (j.message || j.error)) || ('HTTP ' + r.status) }
  } catch (e) {
    out = { rows: [], error: '请求失败:' + ((e && e.message) || e) }
  }
  // 失败也缓存 —— 否则鼠标每划过一次就重打一次必然失败的请求。
  // 代价是这次会话里不会自动重试,用户刷新页面即可。
  KC.cache.set(code, out)
  return out
}

function kcMarkOf(td) {
  const raw = td.dataset.kmark
  if (!raw) return null
  try { return JSON.parse(raw) } catch (e) { return null }
}

function kcShow(td) {
  const code = td.dataset.kchart
  // 同一只票、同一组标记才算「没变」—— 历史记录里一只票可能有两笔,
  // 标记不同,只比代码的话第二笔会沿用第一笔的标记
  const key = code + '|' + (td.dataset.kmark || '')
  if (!code || key === KC.cur) { clearTimeout(KC.hideT); return }
  clearTimeout(KC.hideT)
  clearTimeout(KC.showT)
  KC.showT = setTimeout(async function () {
    const el = kcEl()
    KC.cur = key
    const seq = ++KC.seq
    el._rect = td.getBoundingClientRect()
    const sy = document.getElementById('kc-sy')
    const nm = document.getElementById('kc-nm')
    if (sy) sy.textContent = code
    if (nm) nm.textContent = td.dataset.kname || ''
    if (!KC.cache.has(code)) kcMsg('加载中…')
    el.classList.add('on')
    kcPlace(el._rect)
    const payload = await kcFetch(code)
    // 竞态:鼠标已经划到别的票上了,这次的响应直接丢掉
    if (seq !== KC.seq) return
    kcRender(code, td.dataset.kname, payload, kcMarkOf(td))
  }, 180)
}

function bindKChart() {
  if (KC.bound) return           // 表格每次都重渲染,所以委托绑在 document 上,只绑一次
  KC.bound = true
  document.addEventListener('mouseover', function (e) {
    const td = e.target && e.target.closest ? e.target.closest('[data-kchart]') : null
    if (td) kcShow(td)
  })
  document.addEventListener('mouseout', function (e) {
    const td = e.target && e.target.closest ? e.target.closest('[data-kchart]') : null
    if (!td) return
    const to = e.relatedTarget
    if (to && KC.el && KC.el.contains(to)) return    // 移进弹层了,别关
    clearTimeout(KC.showT)
    kcHide()
  })
  // 页面滚动 / 窗口变化时位置会失效,直接收起比错位好
  window.addEventListener('scroll', function () {
    if (KC.el && KC.el.classList.contains('on')) { KC.cur = null; KC.el.classList.remove('on') }
  }, true)
}
