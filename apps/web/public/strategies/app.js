// 策略中心 · 前端交互 + mock 数据
// 后端接入前 · 所有数据硬编码；接入时替换 loadFactors / loadOfficial 等函数

// ═════════════════════════════════════════════════════════════════
// 常量：20 因子
// ═════════════════════════════════════════════════════════════════
const FACTORS = [
  // 价值 4
  { key: 'pe_inv',        cat: '价值', name: '市盈率倒数',    icon: '💰', desc: '1 / TTM PE · 低估值分数高', ic: 0.062, ann: 0.085, vol: 0.22 },
  { key: 'pb_inv',        cat: '价值', name: '市净率倒数',    icon: '💰', desc: '1 / PB · 破净股偏防御',    ic: 0.048, ann: 0.062, vol: 0.24 },
  { key: 'dividend_yield',cat: '价值', name: '股息率',        icon: '💰', desc: '近 12 月现金分红 / 市值', ic: 0.056, ann: 0.078, vol: 0.18 },
  { key: 'ev_ebitda_inv', cat: '价值', name: 'EV/EBITDA 倒数',icon: '💰', desc: '企业价值 / 息税折旧摊销前利润 倒数', ic: 0.041, ann: 0.055, vol: 0.21 },
  // 质量 4
  { key: 'roe',           cat: '质量', name: 'ROE',           icon: '🏆', desc: 'TTM 净利润 / 平均归母权益 · 巴菲特最爱', ic: 0.071, ann: 0.092, vol: 0.19 },
  { key: 'roa',           cat: '质量', name: 'ROA',           icon: '🏆', desc: '剔除杠杆的经营效率',      ic: 0.052, ann: 0.071, vol: 0.18 },
  { key: 'gross_margin',  cat: '质量', name: '毛利率',        icon: '🏆', desc: '(营收 - 成本) / 营收 · 护城河', ic: 0.043, ann: 0.059, vol: 0.17 },
  { key: 'debt_ratio_inv',cat: '质量', name: '1/负债率',      icon: '🏆', desc: '低杠杆抗风险',            ic: 0.032, ann: 0.043, vol: 0.16 },
  // 成长 2
  { key: 'revenue_growth_yoy',  cat: '成长', name: '营收同比', icon: '🚀', desc: '最近季营收 vs 去年', ic: 0.058, ann: 0.080, vol: 0.27 },
  { key: 'earnings_growth_yoy', cat: '成长', name: '净利同比', icon: '🚀', desc: '最近季归母净利 vs 去年', ic: 0.049, ann: 0.068, vol: 0.29 },
  // 动量 3
  { key: 'momentum_1m',     cat: '动量', name: '1 月动量',    icon: '📈', desc: '近 20 日涨幅 · 短反转', ic: -0.031, ann: 0.042, vol: 0.24, note: '反向 IC' },
  { key: 'momentum_6m',     cat: '动量', name: '6 月动量',    icon: '📈', desc: '近 120 日涨幅', ic: 0.053, ann: 0.072, vol: 0.26 },
  { key: 'momentum_12m_1m', cat: '动量', name: '12M-1M 动量', icon: '📈', desc: '剔除最近 1 月的 11 月涨幅 · 学术经典', ic: 0.068, ann: 0.089, vol: 0.24 },
  // 技术/资金 5
  { key: 'kronos',    cat: 'ML',   name: 'Kronos 技术', icon: '🧠', desc: '清华 Kronos 时序大模型输出',  ic: 0.089, ann: 0.115, vol: 0.31 },
  { key: 'ma_align',  cat: '技术', name: '均线趋势',    icon: '📉', desc: 'MA5/10/20/60 多头排列打分',   ic: 0.037, ann: 0.051, vol: 0.28 },
  { key: 'macd',      cat: '技术', name: 'MACD 动量',   icon: '📉', desc: 'MACD_bar / ATR14',            ic: 0.028, ann: 0.036, vol: 0.30 },
  { key: 'main_flow', cat: '资金', name: '主力净流入',  icon: '💵', desc: '5 日主力资金净流入占比',       ic: 0.061, ann: 0.083, vol: 0.29 },
  { key: 'rsi',       cat: '技术', name: 'RSI 超买卖',  icon: '📉', desc: 'RSI14 分段映射',              ic: 0.021, ann: 0.027, vol: 0.27 },
  // 低波 & 其他 2
  { key: 'vol_20d_inv',cat: '波动', name: '低波动',     icon: '🛡', desc: '低波异象 · 稳定跑赢波动',      ic: 0.044, ann: 0.065, vol: 0.15 },
  { key: 'candle_5d',  cat: '技术', name: '近5日 K 线', icon: '📉', desc: '近 5 日阳线比例',              ic: 0.019, ann: 0.023, vol: 0.28 },
]

const CAT_ORDER = ['价值', '质量', '成长', '动量', 'ML', '技术', '资金', '波动']
const CAT_ICON = { '价值': '💰', '质量': '🏆', '成长': '🚀', '动量': '📈', 'ML': '🧠', '技术': '📉', '资金': '💵', '波动': '🛡' }

// ═════════════════════════════════════════════════════════════════
// 常量：6 官方精选策略
// ═════════════════════════════════════════════════════════════════
const OFFICIAL_STRATEGIES = [
  {
    id: 'official_deep_value', icon: '💎', name: '深度价值',
    desc: '低估值 + 高分红 · 抗跌型长期持仓',
    factors: [ { key: 'pe_inv', w: 40 }, { key: 'pb_inv', w: 30 }, { key: 'dividend_yield', w: 30 } ],
    config: { universe: 'hs300', top_n: 20, rebalance: 'Q', cost_bps: 15, benchmark: '000300' },
    metrics: { ann_ret: 0.152, sharpe: 1.28, max_dd: -0.118 }
  },
  {
    id: 'official_high_div', icon: '🌾', name: '高股息防御',
    desc: '收租型 · 熊市抗跌 · 极稳组合',
    factors: [ { key: 'dividend_yield', w: 50 }, { key: 'vol_20d_inv', w: 30 }, { key: 'debt_ratio_inv', w: 20 } ],
    config: { universe: 'a_all', top_n: 20, rebalance: 'H', cost_bps: 15, benchmark: '000300' },
    metrics: { ann_ret: 0.135, sharpe: 1.55, max_dd: -0.082 }
  },
  {
    id: 'official_quality_momo', icon: '🎯', name: '质量+动量',
    desc: '主流经典 · Barra 简化版 · 均衡型',
    factors: [ { key: 'roe', w: 35 }, { key: 'gross_margin', w: 15 }, { key: 'momentum_12m_1m', w: 35 }, { key: 'momentum_6m', w: 15 } ],
    config: { universe: 'hs300', top_n: 20, rebalance: 'M', cost_bps: 15, benchmark: '000300' },
    metrics: { ann_ret: 0.184, sharpe: 1.42, max_dd: -0.143 }
  },
  {
    id: 'official_small_growth', icon: '🌱', name: '小盘成长',
    desc: '中证 500 · 高弹性 · 牛市攻击型',
    factors: [ { key: 'revenue_growth_yoy', w: 40 }, { key: 'momentum_6m', w: 30 }, { key: 'roe', w: 30 } ],
    config: { universe: 'zz500', top_n: 30, rebalance: 'M', cost_bps: 15, benchmark: '000905' },
    metrics: { ann_ret: 0.221, sharpe: 1.15, max_dd: -0.263 }
  },
  {
    id: 'official_hs300_enhance', icon: '📊', name: '沪深 300 增强',
    desc: '基准增强 · 稳定跑赢 · 机构风格',
    factors: [ { key: 'pe_inv', w: 20 }, { key: 'roe', w: 30 }, { key: 'momentum_12m_1m', w: 30 }, { key: 'vol_20d_inv', w: 20 } ],
    config: { universe: 'hs300', top_n: 30, rebalance: 'M', cost_bps: 15, benchmark: '000300' },
    metrics: { ann_ret: 0.168, sharpe: 1.35, max_dd: -0.128 }
  },
  {
    id: 'official_hk_high_div', icon: '🇭🇰', name: '港股高息',
    desc: '港股通 · 收租型 · 汇率对冲',
    factors: [ { key: 'dividend_yield', w: 60 }, { key: 'pe_inv', w: 20 }, { key: 'roe', w: 20 } ],
    config: { universe: 'hk_all', top_n: 20, rebalance: 'H', cost_bps: 25, benchmark: 'HSI' },
    metrics: { ann_ret: 0.098, sharpe: 1.18, max_dd: -0.153 }
  },
]

// ═════════════════════════════════════════════════════════════════
// 常量：Top 20 持仓（硬编码 · 后端接入后由 scan API 返回）
// ═════════════════════════════════════════════════════════════════
const TOP_HOLDINGS = [
  { rank: 1, code: '600519', mkt: 'SH', name: '贵州茅台', score: 2.34 },
  { rank: 2, code: '000651', mkt: 'SZ', name: '格力电器', score: 2.18 },
  { rank: 3, code: '000858', mkt: 'SZ', name: '五粮液',   score: 2.05 },
  { rank: 4, code: '600036', mkt: 'SH', name: '招商银行', score: 1.97 },
  { rank: 5, code: '300750', mkt: 'SZ', name: '宁德时代', score: 1.88 },
  { rank: 6, code: '601318', mkt: 'SH', name: '中国平安', score: 1.76 },
  { rank: 7, code: '000333', mkt: 'SZ', name: '美的集团', score: 1.68 },
  { rank: 8, code: '002594', mkt: 'SZ', name: '比亚迪',   score: 1.55 },
  { rank: 9, code: '600887', mkt: 'SH', name: '伊利股份', score: 1.47 },
  { rank: 10,code: '600900', mkt: 'SH', name: '长江电力', score: 1.42 },
  { rank: 11,code: '300760', mkt: 'SZ', name: '迈瑞医疗', score: 1.35 },
  { rank: 12,code: '601166', mkt: 'SH', name: '兴业银行', score: 1.29 },
  { rank: 13,code: '600690', mkt: 'SH', name: '海尔智家', score: 1.24 },
  { rank: 14,code: '601288', mkt: 'SH', name: '农业银行', score: 1.18 },
  { rank: 15,code: '601225', mkt: 'SH', name: '陕西煤业', score: 1.12 },
  { rank: 16,code: '601088', mkt: 'SH', name: '中国神华', score: 1.07 },
  { rank: 17,code: '600809', mkt: 'SH', name: '山西汾酒', score: 1.02 },
  { rank: 18,code: '002415', mkt: 'SZ', name: '海康威视', score: 0.96 },
  { rank: 19,code: '002714', mkt: 'SZ', name: '牧原股份', score: 0.91 },
  { rank: 20,code: '600436', mkt: 'SH', name: '片仔癀',   score: 0.87 },
]

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
function saveDraft(d) {
  d.updated_at = Date.now()
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
    factors: (record.factors || []).map(k => ({
      key: k,
      weight_pct: (record.weights || {})[k] || 0,
    })),
    config: record.config || {},
  }
}
function strategyFromApi(row) {
  const factors = []
  const weights = {}
  for (const f of (row.factors || [])) {
    factors.push(f.key)
    weights[f.key] = f.weight_pct
  }
  return {
    id: row.id,             // 后端整数 id · 前端 detect 用 (typeof id === 'number' → 后端)
    name: row.name,
    description: row.description || '',
    factors,
    weights,
    config: row.config || {},
    metrics_mock: { ann_ret: 0.184, sharpe: 1.42, max_dd: -0.143 },  // Phase C4 上真数据
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
  const m = d._metrics || { ann_ret: 0.184, sharpe: 1.42, max_dd: -0.143, win_rate: 0.62 }
  const prompt = [
    `我用【${draftFactorNames(d)}】策略回测 3 年结果:`,
    `· 年化收益 ${fmtPct(m.ann_ret)} (基准 +6.2%)`,
    `· 夏普 ${m.sharpe.toFixed(2)}`,
    `· 最大回撤 ${fmtPct(m.max_dd)} (2024-Q4)`,
    `· 月度胜率 ${(m.win_rate*100).toFixed(0)}%`,
    '',
    '这些指标合理吗? 在什么市场环境下会失效? 我该注意什么?'
  ].join('\n')
  location.href = '/chat?q=' + encodeURIComponent(prompt)
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
  <nav class="railbar">
    <a href="/chat" class="rail-icon brand" title="猎鹿人">🦌</a>
    <a href="/chat" class="rail-icon" title="对话">💬<span class="lbl">对话</span></a>
    <a href="/watchlist" class="rail-icon" title="自选">★<span class="lbl">自选</span></a>
    <a href="/strategies/index.html" class="rail-icon active" title="策略中心"><span style="font-size:15px">📊</span><span class="lbl">策略中心</span></a>
    <a href="/push" class="rail-icon" title="推送">🔔<span class="lbl">推送</span></a>
    <div class="rail-spacer"></div>
    <a href="/chat" class="rail-icon" title="账户">A<span class="lbl">账户</span></a>
  </nav>
  <div class="main">
    <div class="topbar">
      <a href="/strategies/index.html" style="color:var(--text)"><div class="title">策略中心</div></a>
      <div class="sub">· ${subTitle}</div>
      <div class="actions">${actions || ''}</div>
    </div>
    <div class="tabs-h">
      <a href="/strategies/index.html"     class="tab-h ${activeTab==='marketplace'?'active':''}">策略广场</a>
      <a href="/strategies/factors.html"   class="tab-h ${activeTab==='factors'?'active':''}">因子广场</a>
      <a href="/strategies/workbench.html" class="tab-h ${activeTab==='workbench'?'active':''}">策略工作台</a>
      <a href="/strategies/backtest.html"  class="tab-h ${activeTab==='backtest'?'active':''}">回测结果</a>
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
    <div class="modal-hd">💾 保存为我的策略 <span class="close" data-close>×</span></div>
    <div class="modal-body">
      <div class="field" style="margin-bottom:12px">
        <div class="lbl">策略名称</div>
        <input id="save-name" class="input" placeholder="给策略起个好记的名字" value="${defaultName.replace(/"/g,'&quot;')}" maxlength="30" />
      </div>
      <div style="color:var(--muted);font-size:12px;line-height:1.7">
        · 保存后可在 <b style="color:var(--text)">策略广场 · 我的策略</b> 找到<br>
        · 支持后续订阅每日 top 20 推送
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
      metrics_mock: { ann_ret: 0.184, sharpe: 1.42, max_dd: -0.143 },
      created_at: Date.now(),
    }
    // C3 · 先试 API · 失败 fallback localStorage
    let apiOk = false
    if (getToken()) {
      try {
        const r = await createStrategyApi(record)
        record.id = r.id           // 用后端整数 id
        record._from_api = true
        apiOk = true
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
    <div class="modal-hd">🔔 订阅每日推送 <span class="close" data-close>×</span></div>
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
