'use client'

import React, { useEffect, useState } from 'react'
import TopNav from '../components/TopNav'
import { BrokerConnectorPanel } from '../settings/BrokerConnectorPanel'

type WorkbenchTab = 'market' | 'watchlist' | 'stock' | 'quant' | 'portfolio_review'

interface PageItem {
  page_no?: number
  page_number?: number
  page_title?: string
  content_md?: string
  text?: string
  citation_tag?: string
}

interface AnnouncementItem {
  announcement_id: string
  symbol?: string
  name?: string
  title: string
  category?: string
  published_at?: string
  provider?: string
  page_count?: number
  citation?: string
  pages?: PageItem[]
}

function fmtVal(v: unknown, suffix = '', digits = 2): string {
  if (v === null || v === undefined || v === '') return '—'
  if (typeof v === 'number') {
    if (Number.isNaN(v)) return '—'
    return `${v.toFixed(digits)}${suffix}`
  }
  return `${String(v)}${suffix}`
}

export default function BrokerDailyWorkbenchPage() {
  const [activeTab, setActiveTab] = useState<WorkbenchTab>('market')
  const [loading, setLoading] = useState<boolean>(false)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  // Catalog
  const [catalog, setCatalog] = useState<any>(null)

  // 1. 市场工作台状态
  const [marketData, setMarketData] = useState<any>(null)
  const [activeWorkflowView, setActiveWorkflowView] = useState<'pre_market' | 'after_close'>('pre_market')

  // 2. 自选股工作台状态
  const [watchlistSymbolsInput, setWatchlistSymbolsInput] = useState<string>('600036.SH,600519.SH,600000.SH,600898.SH')
  const [newSymbolInput, setNewSymbolInput] = useState<string>('')
  const [watchlistData, setWatchlistData] = useState<any>(null)
  const [intradayResult, setIntradayResult] = useState<any>(null)
  const [drawerOpen, setDrawerOpen] = useState<boolean>(false)
  const [selectedAnn, setSelectedAnn] = useState<AnnouncementItem | null>(null)
  const [selectedPageIdx, setSelectedPageIdx] = useState<number>(0)

  // 3. 个股研究工作台状态
  const [stockSymbol, setStockSymbol] = useState<string>('600036.SH')
  const [adjustment, setAdjustment] = useState<string>('前复权')
  const [restated, setRestated] = useState<boolean>(true)
  const [stockData, setStockData] = useState<any>(null)
  const [selectedTechCategory, setSelectedTechCategory] = useState<string>('全部')
  const [selectedFundDimension, setSelectedFundDimension] = useState<string>('全部')

  // 4. 量化研究工作台状态 (M3)
  const [quantData, setQuantData] = useState<any>(null)
  const [etfSymbol, setEtfSymbol] = useState<string>('510300.SH')
  const [periodicAmount, setPeriodicAmount] = useState<number>(2000)
  const [dcaFrequency, setDcaFrequency] = useState<string>('monthly')
  const [investDay, setInvestDay] = useState<number>(15)
  const [dcaStartDate, setDcaStartDate] = useState<string>('2023-11-01')
  const [dcaEndDate, setDcaEndDate] = useState<string>('2026-11-13')
  const [subFeePct, setSubFeePct] = useState<number>(0.10)
  const [dividendReinvest, setDividendReinvest] = useState<boolean>(true)
  const [targetAmount, setTargetAmount] = useState<number>(100000)
  const [targetYears, setTargetYears] = useState<number>(3)
  const [assumedReturnPct, setAssumedReturnPct] = useState<number>(6.0)
  const [selectedFactorId, setSelectedFactorId] = useState<string>('low_pe_high_div')
  const [usePitFundamentals, setUsePitFundamentals] = useState<boolean>(true)
  const [includeDelisted, setIncludeDelisted] = useState<boolean>(true)
  const [filterLimitUpDown, setFilterLimitUpDown] = useState<boolean>(true)
  const [filterHalted, setFilterHalted] = useState<boolean>(true)
  const [screenerQueryInput, setScreenerQueryInput] = useState<string>(
    '筛选沪深300成分股中市盈率低于15倍、股息率大于3%、ROE大于10%且非ST正常交易的股票'
  )

  // 5. 组合复盘工作台状态 (M3)
  const [portfolioReviewData, setPortfolioReviewData] = useState<any>(null)
  const [thesisSymbolFilter, setThesisSymbolFilter] = useState<string>('')
  const [customThesisInput, setCustomThesisInput] = useState<string>('')
  const [customInvalidationInput, setCustomInvalidationInput] = useState<string>('')

  useEffect(() => {
    void fetchCatalog()
    void loadMarketWorkbench()
    void loadWatchlistWorkbench('600036.SH,600519.SH,600000.SH,600898.SH')
    void loadStockWorkbench('600036.SH', '前复权', true)
    void loadQuantWorkbench()
    void loadPortfolioReviewWorkbench()
  }, [])

  async function fetchCatalog() {
    try {
      const r = await fetch('/api/workflows/catalog')
      if (r.ok) {
        const d = await r.json()
        setCatalog(d)
      }
    } catch {
      // ignore
    }
  }

  async function loadMarketWorkbench() {
    setLoading(true)
    setErrorMsg(null)
    try {
      const r = await fetch('/api/workflows/workbench/market?market=CN_A&trade_date=2026-11-13')
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json()
      setMarketData(d)
    } catch (e: any) {
      setErrorMsg(`加载市场工作台失败：${e?.message || String(e)}`)
    } finally {
      setLoading(false)
    }
  }

  async function triggerPreMarketWorkflow() {
    setLoading(true)
    setErrorMsg(null)
    try {
      const r = await fetch('/api/workflows/pre-market', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          market: 'CN_A',
          trade_date: '2026-11-13',
          watchlist_symbols: watchlistSymbolsInput.split(',').map((s) => s.trim()).filter(Boolean),
        }),
      })
      const d = await r.json()
      setMarketData((prev: any) => ({ ...(prev || {}), pre_market_card: d }))
      setActiveWorkflowView('pre_market')
    } catch (e: any) {
      setErrorMsg(`触发盘前工作流失败：${e?.message || String(e)}`)
    } finally {
      setLoading(false)
    }
  }

  async function triggerAfterCloseWorkflow() {
    setLoading(true)
    setErrorMsg(null)
    try {
      const r = await fetch('/api/workflows/after-close', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          market: 'CN_A',
          trade_date: '2026-11-13',
          watchlist_symbols: watchlistSymbolsInput.split(',').map((s) => s.trim()).filter(Boolean),
        }),
      })
      const d = await r.json()
      setMarketData((prev: any) => ({ ...(prev || {}), after_close_card: d }))
      setActiveWorkflowView('after_close')
    } catch (e: any) {
      setErrorMsg(`触发盘后复盘工作流失败：${e?.message || String(e)}`)
    } finally {
      setLoading(false)
    }
  }

  async function loadWatchlistWorkbench(symbolsStr?: string) {
    const targetSyms = symbolsStr ?? watchlistSymbolsInput
    setLoading(true)
    setErrorMsg(null)
    try {
      const r = await fetch(`/api/workflows/workbench/watchlist?symbols=${encodeURIComponent(targetSyms)}&market=CN_A`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json()
      setWatchlistData(d)
    } catch (e: any) {
      setErrorMsg(`加载自选股工作台失败：${e?.message || String(e)}`)
    } finally {
      setLoading(false)
    }
  }

  async function handleAddWatchlistSymbol() {
    const trimmed = newSymbolInput.trim().toUpperCase()
    if (!trimmed) return
    const currentList = watchlistSymbolsInput.split(',').map((s) => s.trim()).filter(Boolean)
    if (!currentList.includes(trimmed)) {
      currentList.push(trimmed)
    }
    const nextStr = currentList.join(',')
    setWatchlistSymbolsInput(nextStr)
    setNewSymbolInput('')
    await loadWatchlistWorkbench(nextStr)
  }

  async function triggerIntradayAnomaly(symbol: string, simulateDisconnect = false) {
    setLoading(true)
    try {
      const r = await fetch('/api/workflows/intraday-anomaly', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol,
          market: 'CN_A',
          event_type: 'price_volume_spike',
          cooldown_seconds: 300,
          max_alerts_per_window: 2,
          simulate_disconnect: simulateDisconnect,
        }),
      })
      const d = await r.json()
      setIntradayResult(d)
    } catch (e: any) {
      setErrorMsg(`盘中异动归因请求失败：${e?.message || String(e)}`)
    } finally {
      setLoading(false)
    }
  }

  function openAnnouncementDrawer(ann: AnnouncementItem, pageIndex = 0) {
    setSelectedAnn(ann)
    setSelectedPageIdx(pageIndex)
    setDrawerOpen(true)
  }

  async function loadStockWorkbench(sym?: string, adj?: string, rest?: boolean) {
    const s = sym ?? stockSymbol
    const a = adj ?? adjustment
    const rs = rest ?? restated
    setLoading(true)
    setErrorMsg(null)
    try {
      const r = await fetch(
        `/api/workflows/workbench/stock?symbol=${encodeURIComponent(s)}&adjustment=${encodeURIComponent(a)}&restated=${rs}`
      )
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json()
      setStockData(d)
    } catch (e: any) {
      setErrorMsg(`加载个股研究工作台失败：${e?.message || String(e)}`)
    } finally {
      setLoading(false)
    }
  }

  async function loadQuantWorkbench() {
    setLoading(true)
    setErrorMsg(null)
    try {
      const r = await fetch(
        `/api/workflows/workbench/quant?etf_symbol=${encodeURIComponent(etfSymbol)}&periodic_amount=${periodicAmount}&frequency=${dcaFrequency}&invest_day=${investDay}&start_date=${dcaStartDate}&end_date=${dcaEndDate}&subscription_fee_pct=${subFeePct}&dividend_reinvest=${dividendReinvest}&target_amount=${targetAmount}&target_years=${targetYears}&assumed_annual_return_pct=${assumedReturnPct}&factor_id=${selectedFactorId}`
      )
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json()
      setQuantData(d)
    } catch (e: any) {
      setErrorMsg(`加载量化研究工作台失败：${e?.message || String(e)}`)
    } finally {
      setLoading(false)
    }
  }

  async function runEtfDcaCalculation() {
    setLoading(true)
    setErrorMsg(null)
    try {
      const r = await fetch('/api/workflows/etf-dca/calculate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: etfSymbol,
          periodic_amount: Number(periodicAmount),
          frequency: dcaFrequency,
          invest_day: Number(investDay),
          start_date: dcaStartDate,
          end_date: dcaEndDate,
          subscription_fee_pct: Number(subFeePct),
          dividend_reinvest: dividendReinvest,
          target_amount: Number(targetAmount),
          target_years: Number(targetYears),
          assumed_annual_return_pct: Number(assumedReturnPct),
        }),
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json()
      setQuantData((prev: any) => ({ ...(prev || {}), etf_dca: d }))
    } catch (e: any) {
      setErrorMsg(`ETF 定投计算失败：${e?.message || String(e)}`)
    } finally {
      setLoading(false)
    }
  }

  async function runFactorAnalysis(fId?: string) {
    const targetFactor = fId ?? selectedFactorId
    setSelectedFactorId(targetFactor)
    setLoading(true)
    try {
      const r = await fetch('/api/workflows/factor/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          factor_id: targetFactor,
          universe: '000300.SH',
          neutralize_industry: true,
        }),
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json()
      setQuantData((prev: any) => ({ ...(prev || {}), factor_research: d }))
    } catch (e: any) {
      setErrorMsg(`因子分析失败：${e?.message || String(e)}`)
    } finally {
      setLoading(false)
    }
  }

  async function runBacktestAudit(pit?: boolean, delisted?: boolean, limitFilter?: boolean, haltFilter?: boolean) {
    const uPit = pit ?? usePitFundamentals
    const uDel = delisted ?? includeDelisted
    const uLim = limitFilter ?? filterLimitUpDown
    const uHalt = haltFilter ?? filterHalted
    setUsePitFundamentals(uPit)
    setIncludeDelisted(uDel)
    setFilterLimitUpDown(uLim)
    setFilterHalted(uHalt)
    setLoading(true)
    try {
      const r = await fetch('/api/workflows/backtest/audit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          strategy_name: '低市盈率+高股息月度轮动策略',
          use_pit_fundamentals: uPit,
          include_delisted_stocks: uDel,
          adjustment_type: '前复权',
          commission_bps: 3.0,
          stamp_duty_bps: 5.0,
          slippage_bps: 10.0,
          filter_limit_up_down: uLim,
          filter_halted_stocks: uHalt,
        }),
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json()
      setQuantData((prev: any) => ({ ...(prev || {}), backtest_audit: d }))
    } catch (e: any) {
      setErrorMsg(`回测质量审计失败：${e?.message || String(e)}`)
    } finally {
      setLoading(false)
    }
  }

  async function runNaturalLanguageScreener(customQuery?: string) {
    const q = customQuery ?? screenerQueryInput
    if (customQuery !== undefined) setScreenerQueryInput(customQuery)
    setLoading(true)
    try {
      const r = await fetch('/api/workflows/screener/build', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ natural_query: q }),
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json()
      setQuantData((prev: any) => ({ ...(prev || {}), stock_screener: d }))
    } catch (e: any) {
      setErrorMsg(`自然语言选股器执行失败：${e?.message || String(e)}`)
    } finally {
      setLoading(false)
    }
  }

  async function loadPortfolioReviewWorkbench(sym?: string) {
    const s = sym ?? thesisSymbolFilter
    setLoading(true)
    setErrorMsg(null)
    try {
      const qStr = s ? `?symbol=${encodeURIComponent(s)}` : ''
      const r = await fetch(`/api/workflows/workbench/portfolio-review${qStr}`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json()
      setPortfolioReviewData(d)
    } catch (e: any) {
      setErrorMsg(`加载组合复盘工作台失败：${e?.message || String(e)}`)
    } finally {
      setLoading(false)
    }
  }

  async function runThesisTrackerCheck() {
    setLoading(true)
    try {
      const r = await fetch('/api/workflows/thesis/check', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: thesisSymbolFilter || null,
          custom_thesis: customThesisInput || null,
          invalidation_rule: customInvalidationInput || null,
        }),
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json()
      setPortfolioReviewData((prev: any) => ({ ...(prev || {}), thesis_tracker: d }))
    } catch (e: any) {
      setErrorMsg(`投资论点核查失败：${e?.message || String(e)}`)
    } finally {
      setLoading(false)
    }
  }

  const preCard = marketData?.pre_market_card
  const afterCard = marketData?.after_close_card
  const activeMarketCard = activeWorkflowView === 'pre_market' ? preCard : afterCard

  const tech56 = stockData?.technical_dashboard_56
  const fund90 = stockData?.fundamental_scorecard_90

  const allTechItems: any[] = tech56?.indicators ? Object.values(tech56.indicators) : []
  const filteredTechItems =
    selectedTechCategory === '全部'
      ? allTechItems
      : allTechItems.filter((item) => item.category === selectedTechCategory)

  const allFundItems: any[] = fund90?.metrics ? Object.values(fund90.metrics) : []
  const filteredFundItems =
    selectedFundDimension === '全部'
      ? allFundItems
      : allFundItems.filter((item) => item.dimension === selectedFundDimension)

  return (
    <div className="min-h-screen flex flex-col" style={{ background: '#f8fafc', color: '#0f172a' }}>
      <TopNav active="workbench" />

      <main className="flex-1 max-w-7xl w-full mx-auto px-4 py-6">
        {/* 顶部标题与工作流状态栏 */}
        <div className="bg-white rounded-xl border border-slate-200 p-5 mb-6 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <div className="flex items-center gap-2.5">
                <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-amber-100 text-amber-900">
                  HunterCode v3.4.0 · v3.3.0 · 券商数据信封认证
                </span>
                <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-100 text-emerald-800">
                  正则数字回读 100% 校验 · AES-256-GCM 只读授权
                </span>
                <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-indigo-100 text-indigo-900">
                  8 大券商 MCP + 22 金融 SKILL 全量上线
                </span>
              </div>
              <h1 className="text-2xl font-bold mt-2 text-slate-900">
                股民日常投研 & 量化/组合复盘五大工作台
              </h1>
              <p className="text-sm text-slate-600 mt-1">
                覆盖 8 大 P0 日常高频技能 + 7 大 P1 进阶量化研究技能 + 7 大升级技能 · ETF 定投计算器 · 56 技术指标 · 90 基本面评分卡
              </p>
            </div>

            {/* 五大工作台切换按钮 */}
            <div className="flex flex-wrap items-center gap-1.5 bg-slate-100 p-1.5 rounded-xl" role="tablist">
              <button
                data-testid="tab-market-workbench"
                onClick={() => setActiveTab('market')}
                className={`px-3.5 py-2 rounded-lg text-xs font-semibold transition-all ${
                  activeTab === 'market'
                    ? 'bg-amber-700 text-white shadow-sm'
                    : 'text-slate-700 hover:bg-white'
                }`}
              >
                🌅 市场工作台（盘前/盘后）
              </button>
              <button
                data-testid="tab-watchlist-workbench"
                onClick={() => setActiveTab('watchlist')}
                className={`px-3.5 py-2 rounded-lg text-xs font-semibold transition-all ${
                  activeTab === 'watchlist'
                    ? 'bg-amber-700 text-white shadow-sm'
                    : 'text-slate-700 hover:bg-white'
                }`}
              >
                🛡️ 自选股工作台（排雷/PDF）
              </button>
              <button
                data-testid="tab-stock-workbench"
                onClick={() => setActiveTab('stock')}
                className={`px-3.5 py-2 rounded-lg text-xs font-semibold transition-all ${
                  activeTab === 'stock'
                    ? 'bg-amber-700 text-white shadow-sm'
                    : 'text-slate-700 hover:bg-white'
                }`}
              >
                🔬 个股研究（56技术/90基本面）
              </button>
              <button
                data-testid="tab-quant-workbench"
                onClick={() => setActiveTab('quant')}
                className={`px-3.5 py-2 rounded-lg text-xs font-semibold transition-all ${
                  activeTab === 'quant'
                    ? 'bg-amber-700 text-white shadow-sm'
                    : 'text-slate-700 hover:bg-white'
                }`}
              >
                📐 量化研究工作台（ETF定投/因子/审计）
              </button>
              <button
                data-testid="tab-portfolio-review-workbench"
                onClick={() => setActiveTab('portfolio_review')}
                className={`px-3.5 py-2 rounded-lg text-xs font-semibold transition-all ${
                  activeTab === 'portfolio_review'
                    ? 'bg-amber-700 text-white shadow-sm'
                    : 'text-slate-700 hover:bg-white'
                }`}
              >
                📓 组合复盘工作台（论点/日志/去重）
              </button>
            </div>
          </div>

          {/* 8 大 P0 + 7 大 P1 技能速览条 */}
          {(catalog?.p0_daily_skills || catalog?.p1_advanced_skills) && (
            <div className="mt-4 pt-4 border-t border-slate-100 flex flex-wrap gap-2">
              {(catalog?.p0_daily_skills || []).map((sk: any) => (
                <span
                  key={sk.name}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs bg-slate-50 border border-slate-200 text-slate-700"
                >
                  <strong className="text-amber-800">{sk.display_name}</strong>
                  <span className="text-slate-400">({sk.name})</span>
                  <span className="px-1.5 py-0.2 rounded bg-amber-50 text-amber-700">{sk.stage}</span>
                </span>
              ))}
              {(catalog?.p1_advanced_skills || []).map((sk: any) => (
                <span
                  key={sk.name}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs bg-indigo-50/60 border border-indigo-200 text-slate-800"
                >
                  <strong className="text-indigo-900">{sk.display_name}</strong>
                  <span className="text-slate-500">({sk.name})</span>
                  <span className="px-1.5 py-0.2 rounded bg-indigo-100 text-indigo-800">P1 · {sk.stage}</span>
                </span>
              ))}
            </div>
          )}
        </div>

        {errorMsg && (
          <div className="mb-4 p-4 rounded-xl bg-red-50 border border-red-200 text-red-800 text-sm">
            {errorMsg}
          </div>
        )}

        {/* ==================== 1. 市场工作台 ==================== */}
        {activeTab === 'market' && (
          <div data-testid="panel-market-workbench" className="space-y-6">
            {/* 操作控制栏 */}
            <div className="bg-white rounded-xl border border-slate-200 p-4 flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <button
                  data-testid="btn-generate-pre-market"
                  onClick={triggerPreMarketWorkflow}
                  disabled={loading}
                  className="px-4 py-2 rounded-lg text-sm font-semibold bg-amber-700 text-white hover:bg-amber-800 disabled:opacity-50"
                >
                  ☀️ 生成今日市场晨报（08:30 盘前观察卡）
                </button>
                <button
                  data-testid="btn-generate-after-close"
                  onClick={triggerAfterCloseWorkflow}
                  disabled={loading}
                  className="px-4 py-2 rounded-lg text-sm font-semibold bg-slate-800 text-white hover:bg-slate-900 disabled:opacity-50"
                >
                  🌙 执行 15:30 盘后复盘（沉淀 fin_memory）
                </button>
              </div>

              {activeMarketCard?.envelope_meta && (
                <div className="text-xs text-slate-600 flex flex-wrap items-center gap-3" data-testid="market-envelope-meta">
                  <span>
                    数据源：<strong className="text-slate-900">{activeMarketCard.envelope_meta.provider}</strong>
                  </span>
                  <span>
                    行情业务时间 (as_of)：<strong className="text-slate-900">{activeMarketCard.envelope_meta.as_of}</strong>
                  </span>
                  <span>
                    抓取时间 (retrieved_at)：<strong className="text-slate-900">{activeMarketCard.envelope_meta.retrieved_at}</strong>
                  </span>
                  {activeMarketCard.number_verification?.ok && (
                    <span className="px-2 py-0.5 rounded bg-emerald-100 text-emerald-800 font-semibold">
                      ✅ 数字回读校验通过 ({activeMarketCard.number_verification.matched_count} 项真值)
                    </span>
                  )}
                </div>
              )}
            </div>

            {/* 核心指数与行业涨跌卡片 */}
            {preCard?.market_overview && (
              <div className="grid grid-cols-1 md:grid-cols-4 gap-4" data-testid="market-indices-grid">
                {(preCard.market_overview.indices || []).map((idx: any) => {
                  const pct = idx.pct_chg ?? idx.pct_change ?? 0
                  const isUp = Number(pct) >= 0
                  return (
                    <div key={idx.symbol} className="bg-white rounded-xl border border-slate-200 p-4 shadow-sm">
                      <div className="flex items-center justify-between">
                        <span className="font-bold text-slate-900">{idx.name}</span>
                        <span className="text-xs text-slate-500">{idx.symbol}</span>
                      </div>
                      <div className="text-2xl font-bold mt-2 text-slate-900">
                        {fmtVal(idx.close ?? idx.last_price)}
                      </div>
                      <div className="flex items-center justify-between mt-1 text-xs">
                        <span className={isUp ? 'text-red-600 font-semibold' : 'text-emerald-600 font-semibold'}>
                          {isUp ? '+' : ''}{fmtVal(pct, '%')}
                        </span>
                        <span className="text-slate-500">成交 {fmtVal(idx.turnover_billion, '亿', 1)}</span>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}

            {/* 领涨与领跌板块 + 市场广度 */}
            {preCard?.market_overview && (
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div className="bg-white rounded-xl border border-slate-200 p-4">
                  <h3 className="text-sm font-bold text-slate-900 mb-2">📊 市场涨跌家数与交易日历状态</h3>
                  <div className="text-xs text-slate-600 space-y-1.5">
                    <div>交易日历状态：<strong className="text-amber-800">{preCard.calendar_note}</strong></div>
                    <div>两市总成交额：<strong>{fmtVal(preCard.market_overview.total_turnover_billion, ' 亿元', 1)}</strong></div>
                    <div>
                      上涨 <strong className="text-red-600">{preCard.market_overview.advancing_count}</strong> 家 ·
                      下跌 <strong className="text-emerald-600">{preCard.market_overview.declining_count}</strong> 家 ·
                      平盘 <strong>{preCard.market_overview.flat_count}</strong> 家
                    </div>
                  </div>
                </div>

                <div className="bg-white rounded-xl border border-slate-200 p-4">
                  <h3 className="text-sm font-bold text-slate-900 mb-2">🔥 领涨行业板块</h3>
                  <div className="space-y-1.5">
                    {(preCard.market_overview.leading_sectors || []).map((s: any) => (
                      <div key={s.sector} className="flex items-center justify-between text-xs">
                        <span className="font-medium text-slate-800">{s.sector}</span>
                        <span className="text-red-600 font-semibold">+{fmtVal(s.pct_chg, '%')}</span>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="bg-white rounded-xl border border-slate-200 p-4">
                  <h3 className="text-sm font-bold text-slate-900 mb-2">🧊 领跌行业板块</h3>
                  <div className="space-y-1.5">
                    {(preCard.market_overview.lagging_sectors || []).map((s: any) => (
                      <div key={s.sector} className="flex items-center justify-between text-xs">
                        <span className="font-medium text-slate-800">{s.sector}</span>
                        <span className="text-emerald-600 font-semibold">{fmtVal(s.pct_chg, '%')}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}

            {/* 三栏证据格式展示卡（【已核实事实】【逻辑推断】【待核实问题】） */}
            {activeMarketCard?.three_column_evidence && (
              <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm" data-testid="market-three-columns">
                <div className="flex items-center justify-between mb-4">
                  <h2 className="text-lg font-bold text-slate-900">
                    {activeWorkflowView === 'pre_market'
                      ? '📋 《今日盘前观察卡》 · 三栏证据拆解'
                      : '📝 《15:30 盘后复盘与经验沉淀报告》 · 三栏证据拆解'}
                  </h2>
                  {activeWorkflowView === 'after_close' && afterCard?.fin_memory_receipt && (
                    <span className="px-3 py-1 rounded-full text-xs font-semibold bg-indigo-100 text-indigo-800">
                      🧠 已沉淀至 fin_memory：{afterCard.fin_memory_receipt.memory_id}
                    </span>
                  )}
                </div>

                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                  <div className="p-4 rounded-xl bg-emerald-50/60 border border-emerald-200">
                    <h3 className="text-sm font-bold text-emerald-900 mb-2">【已核实事实】（券商信封真值）</h3>
                    <ul className="text-xs text-slate-700 space-y-2 list-disc pl-4">
                      {(activeMarketCard.three_column_evidence.verified_facts || []).map((f: string, i: number) => (
                        <li key={i}>{f}</li>
                      ))}
                    </ul>
                  </div>

                  <div className="p-4 rounded-xl bg-amber-50/60 border border-amber-200">
                    <h3 className="text-sm font-bold text-amber-900 mb-2">【逻辑推断】（传导链与多空推演）</h3>
                    <ul className="text-xs text-slate-700 space-y-2 list-disc pl-4">
                      {(activeMarketCard.three_column_evidence.logical_inferences || []).map((f: string, i: number) => (
                        <li key={i}>{f}</li>
                      ))}
                    </ul>
                  </div>

                  <div className="p-4 rounded-xl bg-slate-50 border border-slate-200">
                    <h3 className="text-sm font-bold text-slate-900 mb-2">【待核实问题】（缺失项与观察开关）</h3>
                    <ul className="text-xs text-slate-700 space-y-2 list-disc pl-4">
                      {(activeMarketCard.three_column_evidence.pending_questions || []).map((f: string, i: number) => (
                        <li key={i}>{f}</li>
                      ))}
                    </ul>
                  </div>
                </div>

                {/* 盘后复盘：早盘预期 vs 收盘现实核对表 */}
                {activeWorkflowView === 'after_close' && afterCard?.review_comparison && (
                  <div className="mt-5 pt-4 border-t border-slate-200">
                    <h3 className="text-sm font-bold text-slate-900 mb-3">🔍 早盘观察条件 vs 15:00 收盘结果对照表</h3>
                    <div className="overflow-x-auto">
                      <table className="w-full text-xs text-left border-collapse">
                        <thead>
                          <tr className="bg-slate-100 text-slate-700">
                            <th className="p-2.5 border border-slate-200">观察编号</th>
                            <th className="p-2.5 border border-slate-200">08:30 早盘观察条件</th>
                            <th className="p-2.5 border border-slate-200">15:00 收盘实际表现</th>
                            <th className="p-2.5 border border-slate-200">兑现状态</th>
                            <th className="p-2.5 border border-slate-200">原投资论点影响</th>
                          </tr>
                        </thead>
                        <tbody>
                          {afterCard.review_comparison.map((rc: any) => (
                            <tr key={rc.condition_id} className="hover:bg-slate-50">
                              <td className="p-2.5 border border-slate-200 font-semibold">{rc.condition_id}</td>
                              <td className="p-2.5 border border-slate-200">{rc.morning_condition}</td>
                              <td className="p-2.5 border border-slate-200">{rc.closing_reality}</td>
                              <td className="p-2.5 border border-slate-200 font-semibold text-emerald-700">{rc.status}</td>
                              <td className="p-2.5 border border-slate-200">{rc.thesis_impact}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* ==================== 2. 自选股工作台 ==================== */}
        {activeTab === 'watchlist' && (
          <div data-testid="panel-watchlist-workbench" className="space-y-6">
            {/* 添加自选股与盘中异动触发栏 */}
            <div className="bg-white rounded-xl border border-slate-200 p-4 flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <input
                  data-testid="input-add-watchlist-symbol"
                  type="text"
                  value={newSymbolInput}
                  onChange={(e) => setNewSymbolInput(e.target.value)}
                  placeholder="输入股票代码，如 000001.SZ"
                  className="px-3 py-1.5 rounded-lg border border-slate-300 text-sm focus:outline-none focus:border-amber-600"
                />
                <button
                  data-testid="btn-add-watchlist-symbol"
                  onClick={handleAddWatchlistSymbol}
                  className="px-3.5 py-1.5 rounded-lg text-sm font-semibold bg-amber-700 text-white hover:bg-amber-800"
                >
                  + 添加自选股并刷新排雷
                </button>
              </div>

              <div className="flex items-center gap-2">
                <button
                  data-testid="btn-trigger-intraday-anomaly"
                  onClick={() => triggerIntradayAnomaly('600036.SH', false)}
                  className="px-3.5 py-1.5 rounded-lg text-xs font-semibold bg-indigo-700 text-white hover:bg-indigo-800"
                >
                  ⚡ 盘中异动解释流 (600036.SH)
                </button>
                <button
                  data-testid="btn-trigger-disconnect-drill"
                  onClick={() => triggerIntradayAnomaly('600036.SH', true)}
                  className="px-3.5 py-1.5 rounded-lg text-xs font-semibold bg-slate-200 text-slate-800 hover:bg-slate-300"
                >
                  🔌 演练数据源断连诚实告警
                </button>
              </div>
            </div>

            {/* 盘中异动解释流结果卡片 */}
            {intradayResult && (
              <div
                data-testid="intraday-anomaly-card"
                className={`rounded-xl border p-4 ${
                  !intradayResult.ok
                    ? 'bg-red-50 border-red-300 text-red-900'
                    : intradayResult.throttled
                    ? 'bg-amber-50 border-amber-300 text-amber-900'
                    : 'bg-indigo-50/70 border-indigo-200 text-slate-900'
                }`}
              >
                {!intradayResult.ok ? (
                  <div className="text-sm font-semibold">{intradayResult.honest_alert}</div>
                ) : intradayResult.throttled ? (
                  <div className="text-sm font-semibold">{intradayResult.throttle_message}</div>
                ) : (
                  <div>
                    <div className="flex items-center justify-between mb-2">
                      <span className="font-bold text-sm">
                        ⚡ 盘中异动客观归因：{intradayResult.symbol}（{intradayResult.name}） · 原投资论点状态：【{intradayResult.thesis_check?.status_label}】
                      </span>
                      <span className="text-xs text-slate-600">
                        窗口推送计数：{intradayResult.alert_count_in_window} / 冷却 {intradayResult.cooldown_seconds}s
                      </span>
                    </div>
                    <ul className="text-xs space-y-1 list-disc pl-4">
                      {(intradayResult.three_column_evidence?.verified_facts || []).map((f: string, idx: number) => (
                        <li key={idx}>{f}</li>
                      ))}
                      {(intradayResult.three_column_evidence?.logical_inferences || []).map((f: string, idx: number) => (
                        <li key={`inf-${idx}`}>{f}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}

            {/* 自选股行情与基本面全景表格 */}
            <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
              <h2 className="text-base font-bold text-slate-900 mb-3">
                📋 自选股行情、基本面与风险状态表（停牌/缺失字段严格显示 `—`，严禁假 0）
              </h2>
              <div className="overflow-x-auto">
                <table className="w-full text-xs text-left border-collapse" data-testid="watchlist-table">
                  <thead>
                    <tr className="bg-slate-100 text-slate-700">
                      <th className="p-2.5 border border-slate-200">代码</th>
                      <th className="p-2.5 border border-slate-200">简称</th>
                      <th className="p-2.5 border border-slate-200">行业</th>
                      <th className="p-2.5 border border-slate-200">交易状态</th>
                      <th className="p-2.5 border border-slate-200">最新价</th>
                      <th className="p-2.5 border border-slate-200">涨跌幅</th>
                      <th className="p-2.5 border border-slate-200">PE-TTM</th>
                      <th className="p-2.5 border border-slate-200">PB-MRQ</th>
                      <th className="p-2.5 border border-slate-200">加权ROE(2026Q3)</th>
                      <th className="p-2.5 border border-slate-200">数据时间 (as_of)</th>
                      <th className="p-2.5 border border-slate-200">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(watchlistData?.watchlist_rows || []).map((row: any) => (
                      <tr key={row.symbol} className="hover:bg-slate-50">
                        <td className="p-2.5 border border-slate-200 font-mono font-semibold">{row.symbol}</td>
                        <td className="p-2.5 border border-slate-200 font-semibold">{row.name}</td>
                        <td className="p-2.5 border border-slate-200">{row.industry}</td>
                        <td className="p-2.5 border border-slate-200">
                          <span
                            className={`px-2 py-0.5 rounded font-semibold ${
                              row.trading_status === '停牌'
                                ? 'bg-amber-100 text-amber-800'
                                : 'bg-emerald-50 text-emerald-700'
                            }`}
                          >
                            {row.trading_status}
                          </span>
                        </td>
                        <td className="p-2.5 border border-slate-200 font-mono">{fmtVal(row.last_price)}</td>
                        <td className="p-2.5 border border-slate-200 font-mono">{fmtVal(row.pct_change, '%')}</td>
                        <td className="p-2.5 border border-slate-200 font-mono">{fmtVal(row.pe_ttm)}</td>
                        <td className="p-2.5 border border-slate-200 font-mono">{fmtVal(row.pb_mrq)}</td>
                        <td className="p-2.5 border border-slate-200 font-mono">{fmtVal(row.roe_pct, '%')}</td>
                        <td className="p-2.5 border border-slate-200 text-slate-500">{row.as_of}</td>
                        <td className="p-2.5 border border-slate-200">
                          <button
                            onClick={() => {
                              setStockSymbol(row.symbol)
                              setActiveTab('stock')
                              void loadStockWorkbench(row.symbol, adjustment, restated)
                            }}
                            className="px-2 py-1 rounded bg-amber-50 text-amber-800 hover:bg-amber-100 font-medium"
                          >
                            深度体检 →
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* 自选股公告列表（点击打开右侧 PDF 原文页码抽屉） + 组合风险巡检 */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
                <h2 className="text-base font-bold text-slate-900 mb-3">
                  📄 自选股最新公告解读（点击任意公告打开右侧 PDF 原文页码抽屉）
                </h2>
                <div className="space-y-3" data-testid="watchlist-announcements-list">
                  {(watchlistData?.announcements || []).map((ann: AnnouncementItem, idx: number) => (
                    <div
                      key={`${ann.announcement_id}-${idx}`}
                      className="p-3.5 rounded-xl border border-slate-200 hover:border-amber-600 transition-all bg-slate-50/50"
                    >
                      <div className="flex items-center justify-between text-xs text-slate-500 mb-1">
                        <span className="font-semibold text-amber-800">
                          {ann.symbol} · {ann.category || '上市公司公告'}
                        </span>
                        <span>披露时间：{ann.published_at}</span>
                      </div>
                      <div className="font-bold text-sm text-slate-900 mb-2">{ann.title}</div>
                      <div className="flex items-center justify-between">
                        <span className="text-xs font-mono text-emerald-800 bg-emerald-50 px-2 py-0.5 rounded">
                          {ann.citation}
                        </span>
                        <button
                          data-testid={`btn-open-pdf-drawer-${idx}`}
                          onClick={() => openAnnouncementDrawer(ann, 0)}
                          className="px-3 py-1 rounded-lg text-xs font-semibold bg-amber-700 text-white hover:bg-amber-800"
                        >
                          📖 打开 PDF 原文并高亮页码
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* 组合风险巡检卡片 (portfolio_risk_check) */}
              {watchlistData?.portfolio_risk && (
                <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm" data-testid="portfolio-risk-card">
                  <h2 className="text-base font-bold text-slate-900 mb-3">
                    🛡️ 组合风险暴露与压力测试巡检（`portfolio_risk_check`）
                  </h2>
                  <div className="grid grid-cols-2 gap-3 mb-4 text-xs">
                    <div className="p-3 rounded-lg bg-slate-50 border border-slate-200">
                      <div className="text-slate-500">单票最高持仓占比</div>
                      <div className="text-lg font-bold text-slate-900 mt-0.5">
                        {watchlistData.portfolio_risk.max_single_symbol} ({fmtVal(watchlistData.portfolio_risk.max_single_weight_pct, '%')})
                      </div>
                      <div className="text-slate-500 mt-0.5">
                        单票上限约束：{fmtVal(watchlistData.portfolio_risk.single_stock_limit_pct, '%')}
                      </div>
                    </div>
                    <div className="p-3 rounded-lg bg-slate-50 border border-slate-200">
                      <div className="text-slate-500">前三大行业集中度</div>
                      <div className="text-lg font-bold text-slate-900 mt-0.5">
                        {fmtVal(watchlistData.portfolio_risk.top3_industry_weight_pct, '%')}
                      </div>
                      <div className="text-slate-500 mt-0.5">
                        第一重仓行业：{watchlistData.portfolio_risk.top1_industry_name} ({fmtVal(watchlistData.portfolio_risk.top1_industry_weight_pct, '%')})
                      </div>
                    </div>
                  </div>

                  {/* 风险预警列表 */}
                  {(watchlistData.portfolio_risk.alerts || []).length > 0 && (
                    <div className="mb-4 p-3 rounded-lg bg-amber-50 border border-amber-200 text-xs text-amber-900 space-y-1">
                      {watchlistData.portfolio_risk.alerts.map((al: string, i: number) => (
                        <div key={i}>⚠️ {al}</div>
                      ))}
                    </div>
                  )}

                  {/* 压力测试情景 */}
                  <div className="text-xs space-y-2">
                    <div className="font-semibold text-slate-800">极端情景压力测试：</div>
                    {(watchlistData.portfolio_risk.stress_scenarios || []).map((sc: any, i: number) => (
                      <div key={i} className="flex items-center justify-between p-2 rounded bg-slate-50 border border-slate-200">
                        <span>{sc.scenario}</span>
                        <span className="font-mono font-bold text-emerald-700">
                          预计组合净值冲击：{fmtVal(sc.estimated_portfolio_impact_pct, '%')}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* ==================== 3. 个股研究工作台 ==================== */}
        {activeTab === 'stock' && (
          <div data-testid="panel-stock-workbench" className="space-y-6">
            {/* 股票与复权控制栏 */}
            <div className="bg-white rounded-xl border border-slate-200 p-4 flex flex-wrap items-center justify-between gap-4">
              <div className="flex flex-wrap items-center gap-3">
                <span className="text-sm font-bold text-slate-800">研究标的：</span>
                {['600036.SH', '600519.SH', '600000.SH', '000001.SZ'].map((sym) => (
                  <button
                    key={sym}
                    data-testid={`btn-select-stock-${sym}`}
                    onClick={() => {
                      setStockSymbol(sym)
                      void loadStockWorkbench(sym, adjustment, restated)
                    }}
                    className={`px-3 py-1.5 rounded-lg text-xs font-semibold ${
                      stockSymbol === sym
                        ? 'bg-amber-700 text-white'
                        : 'bg-slate-100 text-slate-700 hover:bg-slate-200'
                    }`}
                  >
                    {sym}
                  </button>
                ))}

                <span className="text-sm font-bold text-slate-800 ml-2">复权方式：</span>
                {['前复权', '后复权', '不复权'].map((adj) => (
                  <button
                    key={adj}
                    onClick={() => {
                      setAdjustment(adj)
                      void loadStockWorkbench(stockSymbol, adj, restated)
                    }}
                    className={`px-2.5 py-1 rounded text-xs font-medium ${
                      adjustment === adj
                        ? 'bg-slate-800 text-white'
                        : 'bg-slate-100 text-slate-700'
                    }`}
                  >
                    {adj}
                  </button>
                ))}
              </div>

              {stockData?.quote && (
                <div className="flex items-center gap-4 text-sm">
                  <span className="font-bold text-slate-900">
                    {stockData.name} ({stockData.symbol})
                  </span>
                  <span className="font-mono font-bold text-lg text-amber-800">
                    {fmtVal(stockData.quote.last_price, ' 元')}
                  </span>
                  <span className="text-xs text-slate-500">
                    复权：{stockData.adjustment} · 数据源：{stockData.envelope_meta?.provider}
                  </span>
                </div>
              )}
            </div>

            {/* 三栏证据摘要卡 */}
            {stockData?.three_column_evidence && (
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div className="bg-white rounded-xl border border-emerald-200 p-4">
                  <h3 className="text-xs font-bold text-emerald-900 mb-2">【已核实事实】（56技术 + 90基本面真值）</h3>
                  <ul className="text-xs text-slate-700 space-y-1.5 list-disc pl-4">
                    {stockData.three_column_evidence.verified_facts.map((f: string, i: number) => (
                      <li key={i}>{f}</li>
                    ))}
                  </ul>
                </div>
                <div className="bg-white rounded-xl border border-amber-200 p-4">
                  <h3 className="text-xs font-bold text-amber-900 mb-2">【逻辑推断】（指标冲突与现金流质量）</h3>
                  <ul className="text-xs text-slate-700 space-y-1.5 list-disc pl-4">
                    {stockData.three_column_evidence.logical_inferences.map((f: string, i: number) => (
                      <li key={i}>{f}</li>
                    ))}
                  </ul>
                </div>
                <div className="bg-white rounded-xl border border-slate-200 p-4">
                  <h3 className="text-xs font-bold text-slate-900 mb-2">【待核实问题】（后续财报跟踪项）</h3>
                  <ul className="text-xs text-slate-700 space-y-1.5 list-disc pl-4">
                    {stockData.three_column_evidence.pending_questions.map((f: string, i: number) => (
                      <li key={i}>{f}</li>
                    ))}
                  </ul>
                </div>
              </div>
            )}

            {/* 7 大类 56 项技术指标确定性看板 */}
            {tech56 && (
              <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm" data-testid="technical-dashboard-56">
                <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
                  <div>
                    <h2 className="text-base font-bold text-slate-900">
                      📈 7 大类 56 项技术指标确定性看板（`technical_dashboard` · 已计算 {tech56.computed_indicators}/{tech56.total_indicators} 项）
                    </h2>
                    <p className="text-xs text-slate-500 mt-0.5">
                      纯 Python 确定性引擎计算（误差 &lt; 1e-6） · 严禁大模型心算指标 · 复权口径：{tech56.adjustment}
                    </p>
                  </div>

                  <div className="flex flex-wrap gap-1.5">
                    {['全部', '趋势型', '超买超卖型', '能量型', '成交量型', '通道路径型', '波动率型', '市场广度型'].map((cat) => (
                      <button
                        key={cat}
                        onClick={() => setSelectedTechCategory(cat)}
                        className={`px-2.5 py-1 rounded text-xs font-medium ${
                          selectedTechCategory === cat
                            ? 'bg-amber-700 text-white'
                            : 'bg-slate-100 text-slate-700 hover:bg-slate-200'
                        }`}
                      >
                        {cat}
                      </button>
                    ))}
                  </div>
                </div>

                {/* 多指标信号冲突告警 */}
                {(tech56.signal_conflicts || []).length > 0 && (
                  <div className="mb-4 p-3 rounded-lg bg-amber-50 border border-amber-200 text-xs text-amber-900">
                    <strong>⚠️ 多指标客观冲突提示：</strong>
                    {tech56.signal_conflicts.map((c: any, i: number) => (
                      <span key={i} className="ml-2">{c.summary || c.type}</span>
                    ))}
                  </div>
                )}

                <div className="overflow-x-auto max-h-96 overflow-y-auto">
                  <table className="w-full text-xs text-left border-collapse">
                    <thead className="sticky top-0 bg-slate-100 text-slate-700">
                      <tr>
                        <th className="p-2 border border-slate-200">指标代码</th>
                        <th className="p-2 border border-slate-200">指标名称</th>
                        <th className="p-2 border border-slate-200">所属大类</th>
                        <th className="p-2 border border-slate-200">当前真值</th>
                        <th className="p-2 border border-slate-200">近20日分位数</th>
                        <th className="p-2 border border-slate-200">客观信号状态</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredTechItems.map((ind: any) => (
                        <tr key={ind.code} className="hover:bg-slate-50">
                          <td className="p-2 border border-slate-200 font-mono font-semibold">{ind.code}</td>
                          <td className="p-2 border border-slate-200">{ind.name}</td>
                          <td className="p-2 border border-slate-200">{ind.category}</td>
                          <td className="p-2 border border-slate-200 font-mono">{fmtVal(ind.value, '', 4)}</td>
                          <td className="p-2 border border-slate-200 font-mono">{fmtVal(ind.percentile_20d, '%', 1)}</td>
                          <td className="p-2 border border-slate-200">
                            <span className="px-2 py-0.5 rounded bg-slate-100 text-slate-800 font-medium">
                              {ind.signal_state}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* 6 大维度 90 项基本面体检评分卡 */}
            {fund90 && (
              <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm" data-testid="fundamental-scorecard-90">
                <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
                  <div>
                    <h2 className="text-base font-bold text-slate-900">
                      🏛️ 6 大维度 90 项基本面体检评分卡（`fundamental_scorecard` · 已计算 {fund90.computed_metrics}/{fund90.total_metrics} 项）
                    </h2>
                    <p className="text-xs text-slate-500 mt-0.5">
                      报告期：<strong>{fund90.report_period}</strong> · 披露时间：<strong>{fund90.published_at}</strong> ·
                      重述口径：<strong>{fund90.basis_label}</strong> · 六维综合得分：
                      <strong className="text-amber-800 text-sm ml-1">{fmtVal(fund90.scorecard?.overall_score, ' 分')}</strong>
                    </p>
                  </div>

                  <div className="flex flex-wrap gap-1.5">
                    {['全部', '盈利能力', '成长能力', '偿债与安全性', '现金流质量', '估值水平', '公司治理与股东'].map((dim) => (
                      <button
                        key={dim}
                        onClick={() => setSelectedFundDimension(dim)}
                        className={`px-2.5 py-1 rounded text-xs font-medium ${
                          selectedFundDimension === dim
                            ? 'bg-amber-700 text-white'
                            : 'bg-slate-100 text-slate-700 hover:bg-slate-200'
                        }`}
                      >
                        {dim}
                      </button>
                    ))}
                  </div>
                </div>

                {/* 六维评分雷达/进度卡 */}
                {fund90.scorecard?.dimensions && (
                  <div className="grid grid-cols-2 md:grid-cols-6 gap-3 mb-5">
                    {Object.entries(fund90.scorecard.dimensions).map(([dimName, dimInfo]: [string, any]) => (
                      <div key={dimName} className="p-3 rounded-xl bg-slate-50 border border-slate-200">
                        <div className="text-xs font-semibold text-slate-700">{dimName}</div>
                        <div className="flex items-baseline justify-between mt-1">
                          <span className="text-xl font-bold text-slate-900">{fmtVal(dimInfo.score, '', 1)}</span>
                          <span className="text-xs font-bold px-1.5 py-0.5 rounded bg-amber-100 text-amber-900">
                            {dimInfo.grade}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                {/* 会计准则重述（调整前 vs 调整后）对照表（对应 TC-CALC-02） */}
                {fund90.restatement_comparison && (
                  <div className="mb-5 p-3.5 rounded-xl bg-indigo-50/50 border border-indigo-200" data-testid="restatement-comparison-box">
                    <div className="text-xs font-bold text-indigo-950 mb-2">
                      🔄 会计准则调整重述对照表（报告期 {fund90.restatement_comparison.report_period} · 调整前 vs 调整后）
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs">
                      {Object.entries(fund90.restatement_comparison.items || {}).map(([k, v]: [string, any]) => (
                        <div key={k} className="bg-white p-2.5 rounded-lg border border-indigo-100">
                          <div className="font-semibold text-slate-800">{v.label || k}</div>
                          <div className="text-slate-600 mt-1">
                            调整前：<strong className="font-mono">{fmtVal(v['调整前'])}</strong> →
                            调整后：<strong className="font-mono text-indigo-900">{fmtVal(v['调整后'])}</strong>
                            （差异：<span className="font-mono">{fmtVal(v['差异'])}</span>）
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                <div className="overflow-x-auto max-h-96 overflow-y-auto">
                  <table className="w-full text-xs text-left border-collapse">
                    <thead className="sticky top-0 bg-slate-100 text-slate-700">
                      <tr>
                        <th className="p-2 border border-slate-200">指标代码</th>
                        <th className="p-2 border border-slate-200">指标名称</th>
                        <th className="p-2 border border-slate-200">所属维度</th>
                        <th className="p-2 border border-slate-200">当前真值</th>
                        <th className="p-2 border border-slate-200">单位</th>
                        <th className="p-2 border border-slate-200">报告期</th>
                        <th className="p-2 border border-slate-200">体检评价</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredFundItems.map((m: any) => (
                        <tr key={m.code} className="hover:bg-slate-50">
                          <td className="p-2 border border-slate-200 font-mono font-semibold">{m.code}</td>
                          <td className="p-2 border border-slate-200">{m.name}</td>
                          <td className="p-2 border border-slate-200">{m.dimension}</td>
                          <td className="p-2 border border-slate-200 font-mono">{fmtVal(m.value)}</td>
                          <td className="p-2 border border-slate-200">{m.unit || '—'}</td>
                          <td className="p-2 border border-slate-200 font-mono">{m.report_period}</td>
                          <td className="p-2 border border-slate-200">
                            <span className="px-2 py-0.5 rounded bg-slate-100 text-slate-800">
                              {m.evaluation || '正常'}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        )}

        {/* ==================== 4. 量化研究工作台 (M3) ==================== */}
        {activeTab === 'quant' && (
          <div data-testid="panel-quant-workbench" className="space-y-6">
            {/* 4.1 ETF 定投计算器交互面板 (`etf_dca_planner`) */}
            <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm" data-testid="etf-dca-calculator-card">
              <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="px-2 py-0.5 rounded text-xs font-semibold bg-amber-100 text-amber-900">
                      P1 核心工具 · etf_dca_planner + broker_funds
                    </span>
                    <span className="text-xs text-slate-500">
                      100% 确定性现金流引擎（严禁心算 IRR / 份额 / 均价）
                    </span>
                  </div>
                  <h2 className="text-lg font-bold text-slate-900 mt-1">
                    💰 ETF 定投计算器（正向历史净值回测 · 目标金额反推 · 定投 vs 一次性买入对比）
                  </h2>
                </div>
                {quantData?.etf_dca?.etf_quote && (
                  <div className="text-xs bg-slate-50 px-3 py-2 rounded-lg border border-slate-200 flex items-center gap-3">
                    <span>
                      标的：<strong className="text-slate-900">{quantData.etf_dca.etf_quote.name} ({quantData.etf_dca.symbol})</strong>
                    </span>
                    <span>
                      最新价：<strong className="font-mono">{fmtVal(quantData.etf_dca.etf_quote.price, '', 3)}</strong>
                    </span>
                    <span>
                      IOPV：<strong className="font-mono">{fmtVal(quantData.etf_dca.etf_quote.iopv, '', 4)}</strong>
                    </span>
                    <span>
                      折溢价率：<strong className="font-mono text-amber-800">{fmtVal(quantData.etf_dca.etf_quote.premium_discount_rate_pct, '%', 2)}</strong>
                    </span>
                  </div>
                )}
              </div>

              {/* 参数表单栏 */}
              <div className="grid grid-cols-2 md:grid-cols-6 gap-3 p-4 rounded-xl bg-slate-50 border border-slate-200 mb-5">
                <div>
                  <label className="block text-xs font-semibold text-slate-700 mb-1">ETF 标的</label>
                  <select
                    data-testid="select-etf-symbol"
                    value={etfSymbol}
                    onChange={(e) => setEtfSymbol(e.target.value)}
                    className="w-full px-2.5 py-1.5 rounded-lg border border-slate-300 text-xs bg-white"
                  >
                    <option value="510300.SH">510300.SH 华泰柏瑞沪深300ETF</option>
                    <option value="510050.SH">510050.SH 华夏上证50ETF</option>
                    <option value="159915.SZ">159915.SZ 易方达创业板ETF</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-semibold text-slate-700 mb-1">每期定投金额 (元)</label>
                  <input
                    data-testid="input-dca-periodic-amount"
                    type="number"
                    value={periodicAmount}
                    onChange={(e) => setPeriodicAmount(Number(e.target.value))}
                    className="w-full px-2.5 py-1.5 rounded-lg border border-slate-300 text-xs bg-white font-mono"
                  />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-slate-700 mb-1">定投频率</label>
                  <select
                    data-testid="select-dca-frequency"
                    value={dcaFrequency}
                    onChange={(e) => setDcaFrequency(e.target.value)}
                    className="w-full px-2.5 py-1.5 rounded-lg border border-slate-300 text-xs bg-white"
                  >
                    <option value="monthly">按月定投 (Monthly)</option>
                    <option value="biweekly">双周定投 (Bi-weekly)</option>
                    <option value="weekly">按周定投 (Weekly)</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-semibold text-slate-700 mb-1">申购费率 (%)</label>
                  <input
                    data-testid="input-dca-fee-pct"
                    type="number"
                    step="0.01"
                    value={subFeePct}
                    onChange={(e) => setSubFeePct(Number(e.target.value))}
                    className="w-full px-2.5 py-1.5 rounded-lg border border-slate-300 text-xs bg-white font-mono"
                  />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-slate-700 mb-1">目标反推金额 (元)</label>
                  <input
                    data-testid="input-dca-target-amount"
                    type="number"
                    value={targetAmount}
                    onChange={(e) => setTargetAmount(Number(e.target.value))}
                    className="w-full px-2.5 py-1.5 rounded-lg border border-slate-300 text-xs bg-white font-mono"
                  />
                </div>
                <div className="flex items-end">
                  <button
                    data-testid="btn-run-etf-dca"
                    onClick={runEtfDcaCalculation}
                    disabled={loading}
                    className="w-full px-3 py-1.5 rounded-lg text-xs font-semibold bg-amber-700 text-white hover:bg-amber-800 disabled:opacity-50"
                  >
                    🧮 重新测算定投方案
                  </button>
                </div>
              </div>

              {/* 核心测算结果指标卡 */}
              {quantData?.etf_dca?.backtest?.summary && (
                <div className="grid grid-cols-2 md:grid-cols-6 gap-3 mb-5" data-testid="dca-summary-metrics">
                  <div className="p-3 rounded-xl bg-slate-50 border border-slate-200">
                    <div className="text-xs text-slate-500">累计定投期数 / 总投入本金</div>
                    <div className="text-lg font-bold font-mono text-slate-900 mt-1">
                      {quantData.etf_dca.backtest.summary.total_periods} 期 / {fmtVal(quantData.etf_dca.backtest.summary.total_invested, ' 元')}
                    </div>
                  </div>
                  <div className="p-3 rounded-xl bg-slate-50 border border-slate-200">
                    <div className="text-xs text-slate-500">累计持有份额 (含分红再投)</div>
                    <div className="text-lg font-bold font-mono text-slate-900 mt-1">
                      {fmtVal(quantData.etf_dca.backtest.summary.total_shares, ' 份', 2)}
                    </div>
                  </div>
                  <div className="p-3 rounded-xl bg-slate-50 border border-slate-200">
                    <div className="text-xs text-slate-500">持仓均价 vs 期末净值</div>
                    <div className="text-lg font-bold font-mono text-slate-900 mt-1">
                      {fmtVal(quantData.etf_dca.backtest.summary.average_holding_cost, '', 4)} / {fmtVal(quantData.etf_dca.backtest.summary.final_nav, '', 4)}
                    </div>
                  </div>
                  <div className="p-3 rounded-xl bg-amber-50/70 border border-amber-200">
                    <div className="text-xs text-amber-900">期末总市值 (含累计收益)</div>
                    <div className="text-lg font-bold font-mono text-amber-950 mt-1">
                      {fmtVal(quantData.etf_dca.backtest.summary.final_market_value, ' 元')}
                    </div>
                    <div className="text-xs text-emerald-700 font-semibold">
                      净收益 +{fmtVal(quantData.etf_dca.backtest.summary.total_profit, ' 元')} ({fmtVal(quantData.etf_dca.backtest.summary.total_return_pct, '%')})
                    </div>
                  </div>
                  <div className="p-3 rounded-xl bg-emerald-50/70 border border-emerald-200">
                    <div className="text-xs text-emerald-900">年化内部收益率 (XIRR)</div>
                    <div className="text-xl font-bold font-mono text-emerald-900 mt-1" data-testid="dca-irr-value">
                      {fmtVal(quantData.etf_dca.backtest.summary.irr_annualized_pct, '%')}
                    </div>
                  </div>
                  <div className="p-3 rounded-xl bg-rose-50/70 border border-rose-200">
                    <div className="text-xs text-rose-900">回测区间最大回撤</div>
                    <div className="text-xl font-bold font-mono text-rose-900 mt-1">
                      -{fmtVal(quantData.etf_dca.backtest.summary.max_drawdown_pct, '%')}
                    </div>
                  </div>
                </div>
              )}

              {/* 定投 vs 一次性买入同区间对比 + 目标金额反推情景矩阵 */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
                {quantData?.etf_dca?.dca_vs_lump_sum?.lump_sum_summary && (
                  <div className="p-4 rounded-xl bg-slate-50 border border-slate-200" data-testid="dca-vs-lump-sum-box">
                    <h3 className="text-sm font-bold text-slate-900 mb-2">
                      ⚖️ 定投 (DCA) vs 期初一次性买入 (Lump Sum) 同区间对比
                    </h3>
                    <p className="text-xs text-slate-700 leading-relaxed mb-3">
                      {quantData.etf_dca.dca_vs_lump_sum.comparison_insights?.objective_interpretation}
                    </p>
                    <div className="grid grid-cols-2 gap-2 text-xs">
                      <div className="bg-white p-2.5 rounded border border-slate-200">
                        <div className="font-semibold text-amber-900">定期定额投资 (DCA)</div>
                        <div>期末市值：<strong className="font-mono">{fmtVal(quantData.etf_dca.backtest.summary.final_market_value, ' 元')}</strong></div>
                        <div>年化 XIRR：<strong className="font-mono">{fmtVal(quantData.etf_dca.backtest.summary.irr_annualized_pct, '%')}</strong></div>
                        <div>最大回撤：<strong className="font-mono">-{fmtVal(quantData.etf_dca.backtest.summary.max_drawdown_pct, '%')}</strong></div>
                      </div>
                      <div className="bg-white p-2.5 rounded border border-slate-200">
                        <div className="font-semibold text-slate-800">期初一次性投入同等本金</div>
                        <div>期末市值：<strong className="font-mono">{fmtVal(quantData.etf_dca.dca_vs_lump_sum.lump_sum_summary.final_market_value, ' 元')}</strong></div>
                        <div>年化 CAGR：<strong className="font-mono">{fmtVal(quantData.etf_dca.dca_vs_lump_sum.lump_sum_summary.annualized_cagr_pct, '%')}</strong></div>
                        <div>最大回撤：<strong className="font-mono">-{fmtVal(quantData.etf_dca.dca_vs_lump_sum.lump_sum_summary.max_drawdown_pct, '%')}</strong></div>
                      </div>
                    </div>
                  </div>
                )}

                {quantData?.etf_dca?.target_reverse_plan && (
                  <div className="p-4 rounded-xl bg-slate-50 border border-slate-200" data-testid="dca-target-reverse-box">
                    <h3 className="text-sm font-bold text-slate-900 mb-2">
                      🎯 目标金额反推情景矩阵（目标 {fmtVal(quantData.etf_dca.target_reverse_plan.target_amount, ' 元', 0)} · {quantData.etf_dca.target_reverse_plan.years} 年）
                    </h3>
                    <table className="w-full text-xs text-left border-collapse bg-white">
                      <thead>
                        <tr className="bg-slate-100 text-slate-700">
                          <th className="p-2 border border-slate-200">假设年化收益</th>
                          <th className="p-2 border border-slate-200">每期需定投</th>
                          <th className="p-2 border border-slate-200">需累计本金</th>
                          <th className="p-2 border border-slate-200">复利增值贡献</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(quantData.etf_dca.target_reverse_plan.scenario_matrix || []).map((sc: any, idx: number) => (
                          <tr key={idx}>
                            <td className="p-2 border border-slate-200 font-mono font-semibold">{fmtVal(sc.assumed_annual_return_pct, '%')}</td>
                            <td className="p-2 border border-slate-200 font-mono font-bold text-amber-900">{fmtVal(sc.required_periodic_amount, ' 元')}</td>
                            <td className="p-2 border border-slate-200 font-mono">{fmtVal(sc.total_principal_needed, ' 元')}</td>
                            <td className="p-2 border border-slate-200 font-mono text-emerald-700">+{fmtVal(sc.estimated_compound_gain, ' 元')}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              {/* 强制历史假设免责声明 */}
              <div className="p-3 rounded-lg bg-amber-50 border border-amber-200 text-xs text-amber-950 font-medium" data-testid="dca-mandatory-disclaimer">
                {quantData?.etf_dca?.disclaimer}
              </div>
            </div>

            {/* 4.2 量化因子有效性研究 (`factor_research`) + 4.3 回测质量防坑审计 (`backtest_quality_auditor`) */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {/* 因子有效性 IC/Rank IC 与 5 层分层回测卡片 */}
              {quantData?.factor_research && (
                <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm" data-testid="factor-research-card">
                  <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
                    <div>
                      <span className="px-2 py-0.5 rounded text-xs font-semibold bg-indigo-100 text-indigo-900">
                        P1 · factor_research
                      </span>
                      <h3 className="text-base font-bold text-slate-900 mt-1">
                        📈 因子有效性检验（IC / Rank IC / 5层分层单调性 / 拥挤度）
                      </h3>
                    </div>
                    <select
                      data-testid="select-factor-id"
                      value={selectedFactorId}
                      onChange={(e) => runFactorAnalysis(e.target.value)}
                      className="px-2.5 py-1.5 rounded-lg border border-slate-300 text-xs bg-white"
                    >
                      <option value="low_pe_high_div">低市盈率+高股息复合因子 (EP + Div)</option>
                      <option value="roe_quality_growth">高ROE+经营现金流质量因子</option>
                      <option value="low_vol_momentum">特质低波+20日量价动量因子</option>
                    </select>
                  </div>

                  <div className="text-xs text-slate-600 mb-3 font-mono bg-slate-50 p-2.5 rounded border border-slate-200">
                    公式：{quantData.factor_research.formula} · 股票池：{quantData.factor_research.universe}
                  </div>

                  <div className="grid grid-cols-3 gap-2.5 mb-4 text-xs">
                    <div className="p-2.5 rounded-lg bg-slate-50 border border-slate-200">
                      <div className="text-slate-500">IC 均值 / Rank IC 均值</div>
                      <div className="font-mono font-bold text-sm text-slate-900 mt-0.5">
                        {fmtVal(quantData.factor_research.metrics.ic_mean, '', 3)} / {fmtVal(quantData.factor_research.metrics.rank_ic_mean, '', 3)}
                      </div>
                    </div>
                    <div className="p-2.5 rounded-lg bg-slate-50 border border-slate-200">
                      <div className="text-slate-500">IR / Rank ICIR (t={fmtVal(quantData.factor_research.metrics.t_stat)})</div>
                      <div className="font-mono font-bold text-sm text-emerald-800 mt-0.5">
                        {fmtVal(quantData.factor_research.metrics.ir)} / {fmtVal(quantData.factor_research.metrics.rank_ic_ir)}
                      </div>
                    </div>
                    <div className="p-2.5 rounded-lg bg-slate-50 border border-slate-200">
                      <div className="text-slate-500">近1年拥挤度分位</div>
                      <div className="font-mono font-bold text-sm text-amber-800 mt-0.5">
                        {fmtVal(quantData.factor_research.metrics.crowding_percentile_1y, '%', 1)} ({quantData.factor_research.metrics.crowding_status})
                      </div>
                    </div>
                  </div>

                  <h4 className="text-xs font-bold text-slate-800 mb-2">
                    5 层分层回测年化收益单调性检验（单调性得分：{fmtVal(quantData.factor_research.metrics.monotonicity_score, '', 1)} · 多空年化：{fmtVal(quantData.factor_research.metrics.long_short_annualized_pct, '%')}）
                  </h4>
                  <table className="w-full text-xs text-left border-collapse mb-3">
                    <thead>
                      <tr className="bg-slate-100 text-slate-700">
                        <th className="p-2 border border-slate-200">分层</th>
                        <th className="p-2 border border-slate-200">年化收益率</th>
                        <th className="p-2 border border-slate-200">超额收益</th>
                        <th className="p-2 border border-slate-200">最大回撤</th>
                        <th className="p-2 border border-slate-200">夏普比率</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(quantData.factor_research.layer_backtest || []).map((ly: any) => (
                        <tr key={ly.layer}>
                          <td className="p-2 border border-slate-200 font-semibold">{ly.layer}</td>
                          <td className="p-2 border border-slate-200 font-mono font-bold text-slate-900">{fmtVal(ly.annualized_return_pct, '%')}</td>
                          <td className={`p-2 border border-slate-200 font-mono ${ly.excess_return_pct >= 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                            {ly.excess_return_pct >= 0 ? '+' : ''}{fmtVal(ly.excess_return_pct, '%')}
                          </td>
                          <td className="p-2 border border-slate-200 font-mono">-{fmtVal(ly.max_drawdown_pct, '%')}</td>
                          <td className="p-2 border border-slate-200 font-mono">{fmtVal(ly.sharpe)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* 回测质量 6 维防坑审计卡片 (`backtest_quality_auditor`) */}
              {quantData?.backtest_audit && (
                <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm" data-testid="backtest-quality-audit-card">
                  <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
                    <div>
                      <span className="px-2 py-0.5 rounded text-xs font-semibold bg-emerald-100 text-emerald-900">
                        P1 · backtest_quality_auditor
                      </span>
                      <h3 className="text-base font-bold text-slate-900 mt-1">
                        🛡️ 量化回测质量 6 维防坑审计卡
                      </h3>
                    </div>
                    <div className="text-right">
                      <span
                        data-testid="backtest-audit-score-badge"
                        className={`px-3 py-1 rounded-full text-xs font-bold ${
                          quantData.backtest_audit.audit_score >= 85
                            ? 'bg-emerald-100 text-emerald-900'
                            : 'bg-rose-100 text-rose-900'
                        }`}
                      >
                        审计得分：{quantData.backtest_audit.audit_score} / 100 · {quantData.backtest_audit.verdict}
                      </span>
                    </div>
                  </div>

                  {/* 交互模拟开关：测试未来函数与幸存者偏差拦截 */}
                  <div className="flex flex-wrap items-center gap-2 mb-3 p-2.5 rounded-lg bg-slate-50 border border-slate-200 text-xs">
                    <span className="font-semibold text-slate-700">审计场景演练：</span>
                    <button
                      data-testid="btn-audit-standard-pass"
                      onClick={() => runBacktestAudit(true, true, true, true)}
                      className="px-2.5 py-1 rounded bg-emerald-700 text-white font-semibold hover:bg-emerald-800"
                    >
                      ✅ 标准合规回测（PIT财报+历史成分股+扣费）
                    </button>
                    <button
                      data-testid="btn-audit-simulate-pit-fail"
                      onClick={() => runBacktestAudit(false, false, false, true)}
                      className="px-2.5 py-1 rounded bg-rose-700 text-white font-semibold hover:bg-rose-800"
                    >
                      ⚠️ 模拟未来函数与幸存者偏差（触发拦截）
                    </button>
                  </div>

                  <div className="space-y-2">
                    {(quantData.backtest_audit.checks || []).map((chk: any) => (
                      <div
                        key={chk.check_id}
                        className={`p-2.5 rounded-lg border text-xs ${
                          chk.status === 'PASS'
                            ? 'bg-emerald-50/40 border-emerald-200'
                            : 'bg-rose-50/60 border-rose-300'
                        }`}
                      >
                        <div className="flex items-center justify-between font-bold">
                          <span className="text-slate-900">{chk.dimension}</span>
                          <span
                            className={`px-2 py-0.5 rounded text-xs ${
                              chk.status === 'PASS'
                                ? 'bg-emerald-100 text-emerald-900'
                                : 'bg-rose-200 text-rose-950'
                            }`}
                          >
                            {chk.status}
                          </span>
                        </div>
                        <div className="text-slate-700 mt-1">{chk.detail}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* 4.4 自然语言结构化选股器 (`stock_screener_builder`) */}
            {quantData?.stock_screener && (
              <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm" data-testid="stock-screener-card">
                <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
                  <div>
                    <span className="px-2 py-0.5 rounded text-xs font-semibold bg-amber-100 text-amber-900">
                      P1 · stock_screener_builder
                    </span>
                    <h3 className="text-base font-bold text-slate-900 mt-1">
                      🔍 自然语言结构化选股器（含字段可用性诚实校验）
                    </h3>
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      data-testid="btn-screener-test-unsupported"
                      onClick={() =>
                        runNaturalLanguageScreener('筛选沪深300低PE高股息且包含主力资金净流入预测的股票')
                      }
                      className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-200 text-slate-800 hover:bg-slate-300"
                    >
                      测试含未授权字段诚实提示
                    </button>
                  </div>
                </div>

                <div className="flex gap-2 mb-4">
                  <input
                    data-testid="input-screener-query"
                    type="text"
                    value={screenerQueryInput}
                    onChange={(e) => setScreenerQueryInput(e.target.value)}
                    className="flex-1 px-3 py-2 rounded-lg border border-slate-300 text-xs"
                  />
                  <button
                    data-testid="btn-run-screener"
                    onClick={() => runNaturalLanguageScreener()}
                    className="px-4 py-2 rounded-lg text-xs font-semibold bg-amber-700 text-white hover:bg-amber-800"
                  >
                    解析条件并选股
                  </button>
                </div>

                {/* 结构化条件标签 */}
                <div className="flex flex-wrap gap-2 mb-3">
                  {(quantData.stock_screener.structured_filters || []).map((f: any, idx: number) => (
                    <span key={idx} className="px-2.5 py-1 rounded-md text-xs bg-emerald-50 border border-emerald-200 text-emerald-950 font-mono">
                      ✅ {f.label} {f.operator} {String(f.value)} ({f.unit})
                    </span>
                  ))}
                  {(quantData.stock_screener.unsupported_fields || []).map((u: any, idx: number) => (
                    <span key={idx} className="px-2.5 py-1 rounded-md text-xs bg-rose-50 border border-rose-300 text-rose-900 font-semibold" data-testid="screener-unsupported-badge">
                      ⚠️ {u.requested_term}：{u.reason}
                    </span>
                  ))}
                </div>

                <table className="w-full text-xs text-left border-collapse">
                  <thead>
                    <tr className="bg-slate-100 text-slate-700">
                      <th className="p-2 border border-slate-200">证券代码</th>
                      <th className="p-2 border border-slate-200">名称</th>
                      <th className="p-2 border border-slate-200">申万行业</th>
                      <th className="p-2 border border-slate-200">最新价</th>
                      <th className="p-2 border border-slate-200">PE(TTM)</th>
                      <th className="p-2 border border-slate-200">PB(MRQ)</th>
                      <th className="p-2 border border-slate-200">股息率(TTM)</th>
                      <th className="p-2 border border-slate-200">ROE(TTM)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(quantData.stock_screener.matched_stocks || []).map((st: any) => (
                      <tr key={st.symbol}>
                        <td className="p-2 border border-slate-200 font-mono font-semibold">{st.symbol}</td>
                        <td className="p-2 border border-slate-200 font-semibold">{st.name}</td>
                        <td className="p-2 border border-slate-200">{st.industry_sw}</td>
                        <td className="p-2 border border-slate-200 font-mono">{fmtVal(st.price)}</td>
                        <td className="p-2 border border-slate-200 font-mono">{fmtVal(st.pe_ttm, '倍')}</td>
                        <td className="p-2 border border-slate-200 font-mono">{fmtVal(st.pb_mrq, '倍')}</td>
                        <td className="p-2 border border-slate-200 font-mono text-emerald-700 font-bold">{fmtVal(st.dividend_yield_pct, '%')}</td>
                        <td className="p-2 border border-slate-200 font-mono">{fmtVal(st.roe_ttm_pct, '%')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* ==================== 5. 组合复盘工作台 (M3) ==================== */}
        {activeTab === 'portfolio_review' && (
          <div data-testid="panel-portfolio-review-workbench" className="space-y-6">
            {/* 5.1 投资论点与反证条件看板 (`investment_thesis_tracker`) */}
            {portfolioReviewData?.thesis_tracker && (
              <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm" data-testid="investment-thesis-board">
                <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
                  <div>
                    <span className="px-2 py-0.5 rounded text-xs font-semibold bg-amber-100 text-amber-900">
                      P1 · investment_thesis_tracker
                    </span>
                    <h2 className="text-lg font-bold text-slate-900 mt-1">
                      🧭 投资论点看板（买入理由 · 催化剂 · 反证失效条件 · 最新公告自动核查）
                    </h2>
                  </div>
                  <div className="flex items-center gap-2 text-xs">
                    <span className="px-2.5 py-1 rounded bg-emerald-100 text-emerald-900 font-semibold">
                      稳健成立：{portfolioReviewData.thesis_tracker.intact_count}
                    </span>
                    <span className="px-2.5 py-1 rounded bg-amber-100 text-amber-900 font-semibold">
                      核心观察：{portfolioReviewData.thesis_tracker.watch_count}
                    </span>
                    <span className="px-2.5 py-1 rounded bg-rose-100 text-rose-900 font-semibold">
                      反证触发/停牌预警：{portfolioReviewData.thesis_tracker.broken_or_halted_count}
                    </span>
                  </div>
                </div>

                {/* 新增或更新论点核查 */}
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3 p-3.5 rounded-xl bg-slate-50 border border-slate-200 mb-4">
                  <input
                    data-testid="input-custom-thesis"
                    type="text"
                    placeholder="输入更新后的买入逻辑（如：零售护城河稳固+分红率35%以上）"
                    value={customThesisInput}
                    onChange={(e) => setCustomThesisInput(e.target.value)}
                    className="px-3 py-1.5 rounded-lg border border-slate-300 text-xs bg-white"
                  />
                  <input
                    data-testid="input-custom-invalidation"
                    type="text"
                    placeholder="输入新增反证失效条件（如：不良率突破1.25%则无条件退出）"
                    value={customInvalidationInput}
                    onChange={(e) => setCustomInvalidationInput(e.target.value)}
                    className="px-3 py-1.5 rounded-lg border border-slate-300 text-xs bg-white"
                  />
                  <button
                    data-testid="btn-check-thesis"
                    onClick={runThesisTrackerCheck}
                    className="px-4 py-1.5 rounded-lg text-xs font-semibold bg-amber-700 text-white hover:bg-amber-800"
                  >
                    🔄 对照最新公告核查反证条件
                  </button>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                  {(portfolioReviewData.thesis_tracker.theses || []).map((th: any) => {
                    const isBroken = th.status === 'BROKEN_OR_HALTED'
                    const isIntact = th.status === 'INTACT'
                    return (
                      <div
                        key={th.thesis_id}
                        className={`p-4 rounded-xl border ${
                          isBroken
                            ? 'bg-rose-50/50 border-rose-300'
                            : isIntact
                            ? 'bg-emerald-50/30 border-emerald-200'
                            : 'bg-amber-50/40 border-amber-200'
                        }`}
                      >
                        <div className="flex items-center justify-between mb-2">
                          <span className="font-bold text-sm text-slate-900">
                            {th.name} ({th.symbol})
                          </span>
                          <span
                            className={`px-2 py-0.5 rounded text-xs font-bold ${
                              isBroken
                                ? 'bg-rose-200 text-rose-950'
                                : isIntact
                                ? 'bg-emerald-100 text-emerald-900'
                                : 'bg-amber-100 text-amber-900'
                            }`}
                          >
                            {th.status_label}
                          </span>
                        </div>
                        <div className="text-xs text-slate-700 mb-2">
                          <strong>核心买入理由：</strong>{th.core_rationale}
                        </div>
                        <div className="text-xs text-slate-700 mb-2">
                          <strong>预设反证条件：</strong>
                          <ul className="list-disc list-inside mt-0.5 space-y-0.5">
                            {(th.invalidation_conditions || []).map((c: string, i: number) => (
                              <li key={i}>{c}</li>
                            ))}
                          </ul>
                        </div>
                        {th.latest_announcement_check && (
                          <div className="mt-2 pt-2 border-t border-slate-200 text-xs">
                            <div className="font-semibold text-slate-800">
                              最新公告核查：{th.latest_announcement_check.latest_announcement_title}{' '}
                              <span className="font-mono text-amber-900">{th.latest_announcement_check.citation}</span>
                            </div>
                            <div className="text-slate-700 mt-1">
                              {th.latest_announcement_check.verification_conclusion}
                            </div>
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            )}

            {/* 5.2 交易与研究日志闭环 (`trading_journal`) + 5.3 多源新闻水位线去重摘要 (`news_signal_digest`) */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {portfolioReviewData?.trading_journal && (
                <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm" data-testid="trading-journal-card">
                  <div className="flex items-center justify-between mb-3">
                    <div>
                      <span className="px-2 py-0.5 rounded text-xs font-semibold bg-indigo-100 text-indigo-900">
                        P1 · trading_journal
                      </span>
                      <h3 className="text-base font-bold text-slate-900 mt-1">
                        📓 交易与研究日志闭环（计划 vs 执行 vs 纪律评分）
                      </h3>
                    </div>
                    <span className="px-3 py-1 rounded-full text-xs font-bold bg-emerald-100 text-emerald-900">
                      平均纪律得分：{fmtVal(portfolioReviewData.trading_journal.average_discipline_score, ' 分', 1)}
                    </span>
                  </div>

                  <div className="text-xs text-amber-900 bg-amber-50 p-2 rounded border border-amber-200 mb-3">
                    {portfolioReviewData.trading_journal.paper_mode_notice}
                  </div>

                  <div className="space-y-3">
                    {(portfolioReviewData.trading_journal.entries || []).map((en: any) => (
                      <div key={en.journal_id} className="p-3 rounded-xl bg-slate-50 border border-slate-200 text-xs space-y-1.5">
                        <div className="flex items-center justify-between font-bold text-slate-900">
                          <span>{en.date} · {en.name} ({en.symbol})</span>
                          <span className="px-2 py-0.5 rounded bg-emerald-100 text-emerald-900">
                            纪律评分 {en.post_trade_review?.discipline_score} 分
                          </span>
                        </div>
                        <div><strong>盘前计划：</strong>{en.pre_trade_plan?.planned_condition}</div>
                        <div><strong>实际执行：</strong>{en.actual_execution?.notes}</div>
                        <div className="text-emerald-900"><strong>复盘沉淀：</strong>{en.post_trade_review?.lesson_learned}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {portfolioReviewData?.news_signal_digest && (
                <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm" data-testid="news-signal-digest-card">
                  <div className="flex items-center justify-between mb-3">
                    <div>
                      <span className="px-2 py-0.5 rounded text-xs font-semibold bg-emerald-100 text-emerald-900">
                        P1 · news_signal_digest
                      </span>
                      <h3 className="text-base font-bold text-slate-900 mt-1">
                        📰 多源新闻水位线去重与跨源事件聚类摘要
                      </h3>
                    </div>
                    <div className="text-xs text-slate-600 font-mono">
                      原始 {portfolioReviewData.news_signal_digest.raw_count} 条 → 唯一事件 {portfolioReviewData.news_signal_digest.unique_events_count} 个
                    </div>
                  </div>

                  <div className="grid grid-cols-3 gap-2 mb-3 text-xs">
                    <div className="p-2 rounded bg-slate-50 border border-slate-200">
                      水位线过滤旧闻：<strong>{portfolioReviewData.news_signal_digest.dropped_by_watermark} 条</strong>
                    </div>
                    <div className="p-2 rounded bg-slate-50 border border-slate-200">
                      跨源转载合并：<strong>{portfolioReviewData.news_signal_digest.dropped_by_cluster_dedup} 条</strong>
                    </div>
                    <div className="p-2 rounded bg-slate-50 border border-slate-200 font-mono">
                      新水位：{portfolioReviewData.news_signal_digest.watermark_out?.slice(0, 16)}
                    </div>
                  </div>

                  <div className="space-y-3">
                    {(portfolioReviewData.news_signal_digest.events || []).map((ev: any) => (
                      <div key={ev.news_id} className="p-3 rounded-xl bg-slate-50 border border-slate-200 text-xs space-y-1">
                        <div className="flex items-center justify-between">
                          <span className="font-bold text-slate-900">{ev.title}</span>
                          <span className="px-2 py-0.5 rounded bg-amber-100 text-amber-900 font-mono">
                            指纹 #{ev.event_fingerprint?.slice(0, 6)} · 合并 {ev.duplicate_count} 源
                          </span>
                        </div>
                        <div className="text-slate-700">{ev.summary}</div>
                        <div className="flex items-center justify-between text-slate-500 pt-1">
                          <span>影响标的：{(ev.impact_symbols || []).join(', ')}</span>
                          <span>情绪评估：{ev.sentiment}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* 5.4 M4 合作券商数据源与只读授权管理 & 交割单手工导入面板 */}
            <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm" data-testid="workbench-broker-connector-section">
              <BrokerConnectorPanel />
            </div>
          </div>
        )}
      </main>

      {/* ==================== 右侧公告 PDF 原文页码定位抽屉 ==================== */}
      {drawerOpen && selectedAnn && (
        <div className="fixed inset-0 z-50 flex justify-end bg-black/40" data-testid="announcement-pdf-drawer">
          <div className="w-full max-w-2xl bg-white h-full shadow-2xl flex flex-col overflow-hidden">
            <div className="p-4 border-b border-slate-200 flex items-center justify-between bg-slate-50">
              <div>
                <span className="text-xs font-semibold px-2 py-0.5 rounded bg-amber-100 text-amber-900">
                  公告 PDF 原文页码溯源 · 共 {selectedAnn.page_count || (selectedAnn.pages || []).length} 页
                </span>
                <h3 className="text-base font-bold text-slate-900 mt-1">{selectedAnn.title}</h3>
              </div>
              <button
                data-testid="btn-close-pdf-drawer"
                onClick={() => setDrawerOpen(false)}
                className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-200 text-slate-800 hover:bg-slate-300"
              >
                ✕ 关闭抽屉
              </button>
            </div>

            {/* 页码切换标签 */}
            <div className="px-4 py-2.5 border-b border-slate-200 flex items-center gap-2 bg-white">
              <span className="text-xs text-slate-500 font-medium">定位页码：</span>
              {(selectedAnn.pages || []).map((p: PageItem, i: number) => {
                const pNo = p.page_no ?? p.page_number ?? i + 1
                return (
                  <button
                    key={i}
                    data-testid={`btn-pdf-page-${pNo}`}
                    onClick={() => setSelectedPageIdx(i)}
                    className={`px-3 py-1 rounded text-xs font-semibold ${
                      selectedPageIdx === i
                        ? 'bg-amber-700 text-white'
                        : 'bg-slate-100 text-slate-700 hover:bg-slate-200'
                    }`}
                  >
                    第 {pNo} 页
                  </button>
                )
              })}
            </div>

            {/* 当前高亮页码原文内容 */}
            <div className="flex-1 p-5 overflow-y-auto space-y-4">
              {selectedAnn.pages && selectedAnn.pages[selectedPageIdx] ? (
                <div className="p-4 rounded-xl border-2 border-amber-500 bg-amber-50/30">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-bold text-amber-900">
                      📌 当前高亮页码：第 {selectedAnn.pages[selectedPageIdx].page_no ?? selectedAnn.pages[selectedPageIdx].page_number ?? selectedPageIdx + 1} 页
                      {selectedAnn.pages[selectedPageIdx].page_title ? ` · ${selectedAnn.pages[selectedPageIdx].page_title}` : ''}
                    </span>
                    <span className="text-xs font-mono bg-amber-100 text-amber-900 px-2 py-0.5 rounded" data-testid="drawer-page-citation">
                      {selectedAnn.pages[selectedPageIdx].citation_tag ||
                        `[来源：${selectedAnn.title} · 第${selectedAnn.pages[selectedPageIdx].page_no ?? selectedPageIdx + 1}页]`}
                    </span>
                  </div>
                  <pre className="whitespace-pre-wrap text-xs text-slate-800 font-sans leading-relaxed bg-white p-3.5 rounded-lg border border-slate-200">
                    {selectedAnn.pages[selectedPageIdx].content_md || selectedAnn.pages[selectedPageIdx].text}
                  </pre>
                </div>
              ) : (
                <div className="text-xs text-slate-500">该页暂无文本内容</div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
