'use client'
import { useState, useRef, useEffect } from 'react'
import Link from 'next/link'
import ReactECharts from 'echarts-for-react'
import { TrendingUp, Search, Loader2, AlertCircle, Info, X, Activity, ExternalLink } from 'lucide-react'
import ComplianceWatermark from '../components/ComplianceWatermark'

type Bar = { date: string; open: number; high: number; low: number; close: number; volume: number }
type PredResult = {
  symbol: string
  name: string
  last_date: string
  last_close: number
  history: Bar[]
  predictions: Bar[]
}
type ProInfo = {
  composite_score: number
  factor_return_pct: number
  adj_return_pct: number
  kronos_raw_return_pct: number
  rating: string
  confidence: string
  conflict_level: string
  sigma_daily_pct: number
  factors: {
    key: string
    label: string
    score: number
    weight: number
    contribution: number
  }[]
}
type ProResult = PredResult & { pro: ProInfo }
type StockHit = { code: string; name: string; market?: string }

function getToken() {
  if (typeof window === 'undefined') return ''
  return localStorage.getItem('hunter_token') || ''
}

function authHeaders(): Record<string, string> {
  const t = getToken()
  return t ? { Authorization: `Bearer ${t}`, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' }
}

function buildCandleOption(data: PredResult) {
  const hist = data.history.slice(-60) // 显示最近60根历史
  const pred = data.predictions

  const allDates = [...hist.map(b => b.date), ...pred.map(b => b.date)]
  const histData = hist.map(b => [b.open, b.close, b.low, b.high])
  const predData = pred.map(b => [b.open, b.close, b.low, b.high])
  const predFill = new Array(hist.length).fill('-').concat(predData)

  const upColor = '#ef4444'
  const downColor = '#22c55e'

  return {
    backgroundColor: 'transparent',
    animation: false,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      backgroundColor: 'rgba(15,23,42,0.95)',
      borderColor: '#1e293b',
      textStyle: { color: '#e2e8f0', fontSize: 12 },
      formatter(params: any[]) {
        const p = params[0]
        if (!p || !p.value || p.value === '-') return ''
        const [o, c, l, h] = p.value
        const isPred = p.seriesIndex === 1
        const chg = ((c - data.last_close) / data.last_close * 100).toFixed(2)
        return `
          <div style="font-size:12px;line-height:1.8">
            <b>${p.name}${isPred ? ' <span style="color:#f59e0b">预测</span>' : ''}</b><br/>
            开: ${o?.toFixed(2)}&nbsp;&nbsp;收: <b style="color:${c >= o ? upColor : downColor}">${c?.toFixed(2)}</b><br/>
            高: ${h?.toFixed(2)}&nbsp;&nbsp;低: ${l?.toFixed(2)}<br/>
            ${isPred ? `较当前: <b style="color:${Number(chg) >= 0 ? upColor : downColor}">${Number(chg) >= 0 ? '+' : ''}${chg}%</b>` : ''}
          </div>`
      },
    },
    grid: { top: 16, bottom: 40, left: 56, right: 16 },
    xAxis: {
      type: 'category',
      data: allDates,
      axisLine: { lineStyle: { color: '#334155' } },
      axisLabel: {
        color: '#64748b', fontSize: 11,
        formatter: (v: string) => v.slice(5),
      },
      splitLine: { show: false },
    },
    yAxis: {
      scale: true,
      axisLine: { lineStyle: { color: '#334155' } },
      axisLabel: { color: '#64748b', fontSize: 11 },
      splitLine: { lineStyle: { color: '#1e293b' } },
    },
    series: [
      {
        name: '历史',
        type: 'candlestick',
        data: histData,
        itemStyle: {
          color: upColor, color0: downColor,
          borderColor: upColor, borderColor0: downColor,
        },
      },
      {
        name: '预测',
        type: 'candlestick',
        data: predFill,
        itemStyle: {
          color: 'rgba(251,191,36,0.85)',
          color0: 'rgba(251,191,36,0.5)',
          borderColor: '#fbbf24',
          borderColor0: '#f59e0b',
          opacity: 0.9,
        },
      },
      {
        name: '预测区域',
        type: 'line',
        data: new Array(hist.length - 1).fill(null).concat(
          [data.last_close, ...pred.map(b => b.close)]
        ),
        lineStyle: { color: '#f59e0b', type: 'dashed', width: 1.5, opacity: 0.6 },
        symbol: 'none',
        z: 0,
      },
    ],
  }
}

// ---------------------------------------------------------------------------
// Pro 因子条形图组件
// ---------------------------------------------------------------------------

function ScoreBar({ score }: { score: number }) {
  const pct = Math.round(Math.abs(score) * 50) // 0-50% half bar
  const isBull = score >= 0
  return (
    <div className="flex items-center gap-2 w-full">
      {/* 左侧（看空/绿色） */}
      <div className="flex-1 flex justify-end h-3 overflow-hidden rounded-l-full" style={{ background: 'rgba(255,255,255,0.05)' }}>
        {!isBull && (
          <div
            className="h-full rounded-l-full transition-all"
            style={{ width: `${pct * 2}%`, background: '#22c55e' }}
          />
        )}
      </div>
      {/* 中心刻度 */}
      <div className="w-px h-4 shrink-0" style={{ background: 'var(--border)' }} />
      {/* 右侧（看多/红色） */}
      <div className="flex-1 h-3 overflow-hidden rounded-r-full" style={{ background: 'rgba(255,255,255,0.05)' }}>
        {isBull && (
          <div
            className="h-full rounded-r-full transition-all"
            style={{ width: `${pct * 2}%`, background: '#ef4444' }}
          />
        )}
      </div>
    </div>
  )
}

function RatingBadge({ rating }: { rating: string }) {
  const cfg: Record<string, { bg: string; color: string }> = {
    '强烈看多': { bg: 'rgba(239,68,68,0.15)',   color: '#ef4444' },
    '偏多':    { bg: 'rgba(239,68,68,0.10)',   color: '#f87171' },
    '中性观望': { bg: 'rgba(100,116,139,0.15)', color: '#94a3b8' },
    '偏空':    { bg: 'rgba(34,197,94,0.10)',   color: '#4ade80' },
    '强烈看空': { bg: 'rgba(34,197,94,0.15)',   color: '#22c55e' },
  }
  const s = cfg[rating] ?? { bg: 'rgba(100,116,139,0.15)', color: '#94a3b8' }
  return (
    <span
      className="px-2 py-0.5 rounded-full text-xs font-semibold"
      style={{ background: s.bg, color: s.color }}
    >
      {rating}
    </span>
  )
}

function ProPanel({ pro, lastClose, days }: { pro: ProInfo; lastClose: number; days: number }) {
  const scoreColor = pro.composite_score > 0.15
    ? '#ef4444'
    : pro.composite_score < -0.15
      ? '#22c55e'
      : '#94a3b8'

  return (
    <div className="border-t" style={{ borderColor: 'var(--border)' }}>
      {/* ---- 综合评分卡 ---- */}
      <div className="px-5 py-4">
        <p className="text-xs font-semibold mb-3" style={{ color: 'var(--text-muted)' }}>综合评分</p>
        <div className="flex flex-wrap items-center gap-4">
          {/* 评分圆圈 */}
          <div
            className="w-16 h-16 rounded-full flex flex-col items-center justify-center shrink-0 border-2"
            style={{ borderColor: scoreColor }}
          >
            <span className="text-lg font-bold leading-none" style={{ color: scoreColor }}>
              {pro.composite_score > 0 ? '+' : ''}{(pro.composite_score * 100).toFixed(0)}
            </span>
            <span className="text-[10px] mt-0.5" style={{ color: 'var(--text-muted)' }}>/ 100</span>
          </div>

          {/* 评级 + 置信度 + 信号冲突 */}
          <div className="flex flex-col gap-1.5">
            <div className="flex items-center gap-2">
              <RatingBadge rating={pro.rating} />
            </div>
            <div className="flex items-center gap-3 text-xs" style={{ color: 'var(--text-muted)' }}>
              <span>置信度 <b style={{ color: 'var(--text)' }}>{pro.confidence}</b></span>
              <span>信号分歧
                <b style={{
                  color: pro.conflict_level === '低' ? '#22c55e'
                    : pro.conflict_level === '中' ? '#f59e0b'
                      : '#ef4444',
                  marginLeft: 4,
                }}>
                  {pro.conflict_level}
                </b>
              </span>
              <span>日波动 <b style={{ color: 'var(--text)' }}>{pro.sigma_daily_pct}%</b></span>
            </div>
          </div>

          {/* 收益预期 */}
          <div className="ml-auto flex flex-col items-end gap-1 text-xs">
            <div style={{ color: 'var(--text-muted)' }}>
              因子预期收益
              <span
                className="ml-2 text-sm font-bold font-mono"
                style={{ color: pro.factor_return_pct >= 0 ? '#ef4444' : '#22c55e' }}
              >
                {pro.factor_return_pct >= 0 ? '+' : ''}{pro.factor_return_pct}%
              </span>
            </div>
            <div style={{ color: 'var(--text-muted)' }}>
              调整后预测
              <span
                className="ml-2 font-mono font-semibold"
                style={{ color: pro.adj_return_pct >= 0 ? '#ef4444' : '#22c55e' }}
              >
                {pro.adj_return_pct >= 0 ? '+' : ''}{pro.adj_return_pct}%
              </span>
            </div>
            <div style={{ color: 'var(--text-muted)' }}>
              Kronos 原始
              <span className="ml-2 font-mono" style={{ color: '#f59e0b' }}>
                {pro.kronos_raw_return_pct >= 0 ? '+' : ''}{pro.kronos_raw_return_pct}%
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* ---- 因子明细 ---- */}
      <div className="px-5 pb-4 border-t" style={{ borderColor: 'var(--border)' }}>
        <p className="text-xs font-semibold mt-3 mb-3" style={{ color: 'var(--text-muted)' }}>因子明细</p>

        {/* 表头 */}
        <div className="grid gap-x-3 mb-1" style={{ gridTemplateColumns: '7rem 1fr 3.5rem 3.5rem 4rem' }}>
          {['因子', '方向 (←空 | 多→)', '评分', '权重', '贡献'].map(h => (
            <span key={h} className="text-[11px]" style={{ color: 'var(--text-muted)' }}>{h}</span>
          ))}
        </div>

        {pro.factors.map(f => (
          <div
            key={f.key}
            className="grid items-center gap-x-3 py-1.5 border-t"
            style={{ gridTemplateColumns: '7rem 1fr 3.5rem 3.5rem 4rem', borderColor: 'var(--border)' }}
          >
            <span className="text-xs truncate" style={{ color: 'var(--text)' }}>{f.label}</span>
            <ScoreBar score={f.score} />
            <span
              className="text-xs font-mono text-right"
              style={{ color: f.score > 0.05 ? '#ef4444' : f.score < -0.05 ? '#22c55e' : '#94a3b8' }}
            >
              {f.score > 0 ? '+' : ''}{f.score.toFixed(2)}
            </span>
            <span className="text-xs font-mono text-right" style={{ color: 'var(--text-muted)' }}>
              {(f.weight * 100).toFixed(0)}%
            </span>
            <span
              className="text-xs font-mono text-right font-semibold"
              style={{ color: f.contribution > 0.01 ? '#ef4444' : f.contribution < -0.01 ? '#22c55e' : '#94a3b8' }}
            >
              {f.contribution > 0 ? '+' : ''}{f.contribution.toFixed(3)}
            </span>
          </div>
        ))}

        <p className="text-[11px] mt-3 leading-relaxed" style={{ color: 'var(--text-muted)' }}>
          预测 K 线已根据多因子综合评分对 Kronos 原始幅度进行调整（40% Kronos + 60% 因子混合），形态保持不变。
        </p>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 主页面
// ---------------------------------------------------------------------------

export default function KPredPage() {
  const [input, setInput] = useState('')
  const [selectedCode, setSelectedCode] = useState('')
  const [selectedName, setSelectedName] = useState('')
  const [hits, setHits] = useState<StockHit[]>([])
  const [searching, setSearching] = useState(false)
  const [dropOpen, setDropOpen] = useState(false)
  const [days, setDays] = useState(10)
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<PredResult | null>(null)
  const [error, setError] = useState('')
  const [mode, setMode] = useState<'basic' | 'pro'>('basic')
  const searchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const isComposingRef = useRef(false)

  // 点外部关闭下拉
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setDropOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  // 输入变化时 debounce 搜索
  const handleInputChange = (v: string, fromCompositionEnd = false) => {
    setInput(v)
    setSelectedCode(''); setSelectedName('')
    setResult(null); setError('')
    if (isComposingRef.current && !fromCompositionEnd) return
    if (searchTimerRef.current) clearTimeout(searchTimerRef.current)
    if (!v.trim()) { setHits([]); setDropOpen(false); return }
    searchTimerRef.current = setTimeout(async () => {
      setSearching(true)
      try {
        const r = await fetch(
          `/api/online-analysis/search-stock?q=${encodeURIComponent(v)}&limit=8`,
          { headers: authHeaders() }
        )
        const d = await r.json()
        const items: StockHit[] = d.items || []
        setHits(items)
        setDropOpen(items.length > 0)
      } catch { }
      finally { setSearching(false) }
    }, 300)
  }

  const confirmStock = (code: string, name: string) => {
    setSelectedCode(code); setSelectedName(name)
    setInput(`${name}（${code}）`)
    setDropOpen(false); setHits([])
  }

  const clearStock = () => {
    setInput(''); setSelectedCode(''); setSelectedName('')
    setHits([]); setDropOpen(false); setResult(null); setError('')
  }

  const run = async (codeOverride?: string, dOverride?: number, modeOverride?: 'basic' | 'pro') => {
    // Allow direct 6-digit code input even without dropdown selection
    const rawInput = input.trim()
    const directCode = /^\d{6}$/.test(rawInput) ? rawInput : ''
    const c = codeOverride ?? selectedCode ?? directCode
    const d = dOverride ?? days
    const m = modeOverride ?? mode
    if (!c) return
    setLoading(true); setError(''); setResult(null)
    try {
      const endpoint = m === 'pro'
        ? `/api/kpred/${c}/pro?days=${d}`
        : `/api/kpred/${c}?days=${d}`
      const r = await fetch(endpoint, { headers: authHeaders() })
      const data = await r.json()
      if (!r.ok) throw new Error(data.detail || '请求失败')
      setResult(data)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const lastPred = result?.predictions.at(-1)
  const chgPct = lastPred && result
    ? ((lastPred.close - result.last_close) / result.last_close * 100)
    : null

  const proResult = result && 'pro' in result ? (result as ProResult) : null

  return (
    <main className="flex-1 min-h-screen p-6" style={{ background: 'var(--bg)' }}>
      <div className="max-w-4xl mx-auto">

        {/* 标题 */}
        <div className="flex items-center gap-3 mb-4">
          <div className="w-9 h-9 rounded-xl flex items-center justify-center"
            style={{ background: 'rgba(251,191,36,0.12)' }}>
            <TrendingUp className="w-5 h-5" style={{ color: '#fbbf24' }} />
          </div>
          <div>
            <h1 className="text-lg font-bold" style={{ color: 'var(--text)' }}>K线预测</h1>
            <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
              {mode === 'pro'
                ? 'Pro 多因子模型 · Kronos + 技术/资金/估值 8因子 · 仅供参考'
                : 'Kronos 基础模型 · 纯形态预测 · 仅供参考'}
            </p>
          </div>
        </div>

        {/* 模式切换 */}
        <div className="flex items-center gap-2 mb-5">
          {(['basic', 'pro'] as const).map(m => (
            <button
              key={m}
              onClick={() => {
                setMode(m)
                setResult(null)
                setError('')
              }}
              className="px-4 py-1.5 rounded-full text-xs font-semibold border transition-all"
              style={{
                background: mode === m
                  ? m === 'pro' ? 'rgba(239,68,68,0.15)' : 'rgba(251,191,36,0.15)'
                  : 'transparent',
                color: mode === m
                  ? m === 'pro' ? '#ef4444' : '#f59e0b'
                  : 'var(--text-muted)',
                borderColor: mode === m
                  ? m === 'pro' ? 'rgba(239,68,68,0.4)' : 'rgba(251,191,36,0.4)'
                  : 'var(--border)',
              }}
            >
              {m === 'basic' ? '基础版 Kronos' : '⚡ Pro 多因子'}
            </button>
          ))}
        </div>

        {/* 输入区 */}
        <div className="rounded-xl p-4 mb-4 flex flex-wrap items-end gap-3"
          style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
          <div className="flex-1 min-w-52 relative" ref={wrapRef}>
            <label className="text-xs mb-1 block" style={{ color: 'var(--text-muted)' }}>股票名称或代码</label>
            <div className="relative flex items-center">
              <input
                value={input}
                onChange={e => handleInputChange(e.target.value)}
                onCompositionStart={() => { isComposingRef.current = true }}
                onCompositionEnd={e => {
                  isComposingRef.current = false
                  handleInputChange((e.target as HTMLInputElement).value, true)
                }}
                onKeyDown={e => e.key === 'Enter' && selectedCode && run()}
                placeholder="输入名称或代码，如 贵州茅台 / 600519"
                className="w-full pl-3 pr-8 py-2 rounded-lg text-sm border"
                style={{ background: 'var(--bg)', color: 'var(--text)', borderColor: 'var(--border)' }}
              />
              {input && (
                <button onClick={clearStock} className="absolute right-2.5"
                  style={{ color: 'var(--text-muted)' }}>
                  <X className="w-3.5 h-3.5" />
                </button>
              )}
              {searching && (
                <Loader2 className="absolute right-2.5 w-3.5 h-3.5 animate-spin" style={{ color: 'var(--text-muted)' }} />
              )}
            </div>
            {/* 搜索下拉 */}
            {dropOpen && hits.length > 0 && (
              <div className="absolute left-0 right-0 top-full mt-1 z-50 rounded-xl shadow-xl overflow-hidden"
                style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
                {hits.map(h => (
                  <button key={h.code} onClick={() => confirmStock(h.code, h.name)}
                    className="flex items-center justify-between w-full px-3 py-2.5 text-sm hover:bg-white/5 transition-colors">
                    <span style={{ color: 'var(--text)' }}>{h.name}</span>
                    <span className="font-mono text-xs" style={{ color: 'var(--text-muted)' }}>{h.code}</span>
                  </button>
                ))}
              </div>
            )}
          </div>

          <div>
            <label className="text-xs mb-1 block" style={{ color: 'var(--text-muted)' }}>预测天数</label>
            <div className="flex gap-1.5">
              {[5, 10, 20].map(d => (
                <button key={d} onClick={() => { setDays(d); if (result && selectedCode) run(selectedCode, d) }}
                  className="px-3 py-2 rounded-lg text-xs font-medium border transition-colors"
                  style={{
                    background: days === d ? '#f59e0b' : 'transparent',
                    color: days === d ? '#000' : 'var(--text)',
                    borderColor: days === d ? '#f59e0b' : 'var(--border)',
                  }}>
                  {d}日
                </button>
              ))}
            </div>
          </div>

          <button onClick={() => run()} disabled={loading || (!selectedCode && !/^\d{6}$/.test(input.trim()))}
            className="flex items-center gap-2 px-5 py-2 rounded-lg text-sm font-medium transition-colors"
            style={{ background: '#f59e0b', color: '#000', opacity: loading || !selectedCode ? 0.5 : 1 }}>
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
            {loading ? (mode === 'pro' ? '分析中...' : '推理中...') : '预测'}
          </button>
        </div>

        {/* 免责声明 */}
        <div className="flex items-start gap-2 rounded-lg px-3 py-2.5 mb-4"
          style={{ background: 'rgba(251,191,36,0.06)', border: '1px solid rgba(251,191,36,0.2)' }}>
          <Info className="w-3.5 h-3.5 mt-0.5 shrink-0" style={{ color: '#f59e0b' }} />
          <p className="text-xs leading-relaxed" style={{ color: 'var(--text-muted)' }}>
            {mode === 'pro'
              ? <>Pro 模式综合 Kronos 形态、技术指标、主力资金流向和 PE 估值等 8 个因子，结果仍存在较大不确定性，<b>不构成投资建议</b>。</>
              : <>Kronos 为纯 K 线形态预测模型，不含基本面/消息面信息，预测结果存在较大不确定性，<b>不构成投资建议</b>。</>
            }
          </p>
        </div>

        {/* 错误 */}
        {error && (
          <div className="flex items-center gap-2 rounded-lg px-4 py-3 mb-4"
            style={{ background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)' }}>
            <AlertCircle className="w-4 h-4 shrink-0" style={{ color: '#ef4444' }} />
            <span className="text-sm" style={{ color: '#ef4444' }}>{error}</span>
          </div>
        )}

        {/* 结果 */}
        {result && (
          <div className="rounded-xl overflow-hidden relative" style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
            <ComplianceWatermark />
            {/* 头部信息 */}
            <div className="flex items-center justify-between px-5 py-3 border-b"
              style={{ borderColor: 'var(--border)' }}>
              <div className="flex items-center gap-2">
                <span className="font-semibold text-sm mr-2" style={{ color: 'var(--text)' }}>{result.name}</span>
                <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{result.symbol}</span>
                {proResult && (
                  <RatingBadge rating={proResult.pro.rating} />
                )}
              </div>
              <div className="flex items-center gap-4 text-sm">
                <div>
                  <span className="text-xs mr-1" style={{ color: 'var(--text-muted)' }}>当前</span>
                  <span className="font-mono font-medium" style={{ color: 'var(--text)' }}>{result.last_close.toFixed(2)}</span>
                </div>
                {lastPred && (
                  <div>
                    <span className="text-xs mr-1" style={{ color: 'var(--text-muted)' }}>{days}日后预测</span>
                    <span className="font-mono font-bold" style={{ color: lastPred.close >= result.last_close ? '#ef4444' : '#22c55e' }}>
                      {lastPred.close.toFixed(2)}
                    </span>
                    {chgPct !== null && (
                      <span className="ml-1 text-xs font-medium"
                        style={{ color: chgPct >= 0 ? '#ef4444' : '#22c55e' }}>
                        {chgPct >= 0 ? '+' : ''}{chgPct.toFixed(2)}%
                      </span>
                    )}
                  </div>
                )}
              </div>
            </div>

            {/* K线图 */}
            <div className="p-4">
              <ReactECharts
                option={buildCandleOption(result)}
                style={{ height: 380 }}
                notMerge
              />
              <div className="flex items-center gap-4 mt-2 px-1">
                <div className="flex items-center gap-1.5">
                  <span className="w-3 h-2 rounded-sm inline-block" style={{ background: '#ef4444' }} />
                  <span className="text-xs" style={{ color: 'var(--text-muted)' }}>历史 K 线</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="w-3 h-2 rounded-sm inline-block" style={{ background: '#fbbf24' }} />
                  <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
                    预测 K 线（金色）{proResult ? ' · 已多因子调整' : ''}
                  </span>
                </div>
              </div>
            </div>

            {/* Pro 因子面板（仅 Pro 模式） */}
            {proResult && <ProPanel pro={proResult.pro} lastClose={result.last_close} days={days} />}

            {/* 预测不确定性 · 区间条 + 三类概率环(复赛 §3.C · 仅 Pro 模式) */}
            {proResult && (proResult.pro as any).distribution && (
              <UncertaintyCard distribution={(proResult.pro as any).distribution} />
            )}

            {/* 该股·该模型·近 90d 表现(复赛 §3.A.2.2) */}
            <HistoricalPerformanceCard symbol={result.symbol} />


            {/* 预测数据表 */}
            <div className="border-t px-5 py-4" style={{ borderColor: 'var(--border)' }}>
              <p className="text-xs font-semibold mb-3" style={{ color: 'var(--text-muted)' }}>逐日预测明细</p>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr style={{ color: 'var(--text-muted)' }}>
                      {['日期', '开盘', '最高', '最低', '收盘', '涨跌'].map(h => (
                        <th key={h} className="text-right pb-2 font-medium first:text-left">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {result.predictions.map((b, i) => {
                      const chg = (b.close - result.last_close) / result.last_close * 100
                      const up = b.close >= b.open
                      return (
                        <tr key={i} className="border-t" style={{ borderColor: 'var(--border)' }}>
                          <td className="py-1.5 font-mono" style={{ color: 'var(--text-muted)' }}>{b.date}</td>
                          <td className="text-right py-1.5 font-mono" style={{ color: 'var(--text)' }}>{b.open.toFixed(2)}</td>
                          <td className="text-right py-1.5 font-mono" style={{ color: '#ef4444' }}>{b.high.toFixed(2)}</td>
                          <td className="text-right py-1.5 font-mono" style={{ color: '#22c55e' }}>{b.low.toFixed(2)}</td>
                          <td className="text-right py-1.5 font-mono font-semibold"
                            style={{ color: up ? '#ef4444' : '#22c55e' }}>{b.close.toFixed(2)}</td>
                          <td className="text-right py-1.5 font-mono font-semibold"
                            style={{ color: chg >= 0 ? '#ef4444' : '#22c55e' }}>
                            {chg >= 0 ? '+' : ''}{chg.toFixed(2)}%
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}
      </div>
    </main>
  )
}


// ─── 预测不确定性卡(§3.C 复赛验证) ──────────────────────────────
// 目的:在点估计之外 · 显示 80/95 预测区间 + 三类概率环
// 数据源:pro.distribution(来自 compute_pro_prediction · 已包含 calibration 计算)
// 合规:任何字段 None 都不给假数字 · 只显"样本不足"

type Distribution = {
  point_pct: number
  interval_80: [number, number] | null
  interval_95: [number, number] | null
  residual_std: number | null
  prob: { up: number; flat: number; down: number } | null
  prob_bucket: string | null
  prob_sample_in_bucket: number | null
  sample: number
  note: string | null
}

function UncertaintyCard({ distribution: d }: { distribution: Distribution }) {
  const hasInterval = !!(d.interval_80 && d.interval_95)
  const hasProb = !!d.prob
  const point = d.point_pct

  return (
    <div className="border-t px-5 py-4" style={{ borderColor: 'var(--border)' }}>
      <div className="flex items-center gap-2 mb-3">
        <Activity className="w-3.5 h-3.5" style={{ color: '#f59e0b' }} />
        <p className="text-xs font-semibold" style={{ color: 'var(--text-muted)' }}>
          预测不确定性 · 区间与概率(§3.C · 复赛新增)
        </p>
      </div>

      {!hasInterval && !hasProb ? (
        <p className="text-xs py-2" style={{ color: 'var(--text-muted)' }}>
          样本量不足({d.sample} 条 · 需 ≥ 30)· 无法给出稳定的区间和概率
        </p>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* 区间条 */}
          <div>
            <div className="text-[10px] mb-2" style={{ color: 'var(--text-muted)' }}>
              预测区间 · 基于近 90 日残差分位
            </div>
            {hasInterval ? (
              <IntervalBar point={point} p80={d.interval_80!} p95={d.interval_95!}
                           residualStd={d.residual_std ?? 0} />
            ) : (
              <div className="text-xs py-2" style={{ color: 'var(--text-muted)' }}>
                区间数据不足
              </div>
            )}
          </div>

          {/* 概率环 */}
          <div>
            <div className="text-[10px] mb-2" style={{ color: 'var(--text-muted)' }}>
              三类概率 · 基于近 180 日经验频率
            </div>
            {hasProb ? (
              <ProbDonut prob={d.prob!} point={point} bucket={d.prob_bucket ?? ''}
                         nInBucket={d.prob_sample_in_bucket ?? 0} />
            ) : (
              <div className="text-xs py-2" style={{ color: 'var(--text-muted)' }}>
                概率数据不足
              </div>
            )}
          </div>
        </div>
      )}

      <div className="mt-3 text-[10px] opacity-60" style={{ color: 'var(--text-muted)' }}>
        仅供研究 · 非投资建议 · 区间与概率均从历史统计推断 · 不保证覆盖未来实际收益
      </div>
    </div>
  )
}

/** 区间条 · 深色 marker 是点估计 · 两层色带为 80/95 区间 */
function IntervalBar({ point, p80, p95, residualStd }: {
  point: number; p80: [number, number]; p95: [number, number]; residualStd: number
}) {
  const lo = p95[0] + point; const hi = p95[1] + point
  const lo80 = p80[0] + point; const hi80 = p80[1] + point
  // 归一化到 0-100% · 用 p95 range 撑满
  const range = Math.max(hi - lo, 0.001)
  const posPoint = ((point - lo) / range) * 100
  const posLo80 = ((lo80 - lo) / range) * 100
  const posHi80 = ((hi80 - lo) / range) * 100
  const up = point >= 0

  return (
    <div>
      <div className="relative h-8 rounded overflow-hidden"
           style={{ background: 'rgba(148,163,184,0.15)' }}>
        {/* 95 区间(浅色底 · 已经是背景) */}
        {/* 80 区间(深一档) */}
        <div className="absolute top-0 h-full"
             style={{
               left: `${posLo80}%`, width: `${posHi80 - posLo80}%`,
               background: 'rgba(251,191,36,0.28)',
             }} />
        {/* 点估计 marker */}
        <div className="absolute top-0 h-full w-[3px]"
             style={{
               left: `${posPoint}%`, transform: 'translateX(-50%)',
               background: up ? '#ef4444' : '#22c55e',
             }} />
      </div>
      <div className="flex justify-between mt-1.5 text-[10px] font-mono"
           style={{ color: 'var(--text-muted)' }}>
        <span>{lo.toFixed(2)}%</span>
        <span style={{ color: up ? '#ef4444' : '#22c55e' }}>
          {up ? '+' : ''}{point.toFixed(2)}%
        </span>
        <span>{hi.toFixed(2)}%</span>
      </div>
      <div className="mt-1 text-[10px]" style={{ color: 'var(--text-muted)' }}>
        深色区 80% 区间 [{lo80.toFixed(2)}%, {hi80.toFixed(2)}%] · 全带 95% ·
        残差 std {residualStd.toFixed(2)}%
      </div>
    </div>
  )
}

/** 概率环 · SVG 三段圆环 · 中央显示点估计 */
function ProbDonut({ prob, point, bucket, nInBucket }: {
  prob: { up: number; flat: number; down: number }
  point: number; bucket: string; nInBucket: number
}) {
  // 圆环参数
  const size = 120, stroke = 14
  const r = (size - stroke) / 2
  const cx = size / 2, cy = size / 2
  const circ = 2 * Math.PI * r
  const upLen = prob.up * circ
  const flatLen = prob.flat * circ
  const downLen = prob.down * circ
  const up = point >= 0

  return (
    <div className="flex items-center gap-3">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="flex-shrink-0">
        {/* down 段 · 红 · 从 12 点开始逆时针 */}
        <circle cx={cx} cy={cy} r={r} fill="none" stroke="#ef4444" strokeWidth={stroke}
                strokeDasharray={`${downLen} ${circ}`}
                transform={`rotate(-90 ${cx} ${cy})`} />
        {/* flat 段 · 灰 */}
        <circle cx={cx} cy={cy} r={r} fill="none" stroke="#94a3b8" strokeWidth={stroke}
                strokeDasharray={`${flatLen} ${circ}`}
                strokeDashoffset={-downLen}
                transform={`rotate(-90 ${cx} ${cy})`} />
        {/* up 段 · 绿(A 股上涨用红 · 这里 up=看涨概率用绿避免视觉冲突 · 或反过来?
             ← 复赛面向评委 · 用国际通用 up=绿 down=红 · 便于理解) */}
        <circle cx={cx} cy={cy} r={r} fill="none" stroke="#22c55e" strokeWidth={stroke}
                strokeDasharray={`${upLen} ${circ}`}
                strokeDashoffset={-(downLen + flatLen)}
                transform={`rotate(-90 ${cx} ${cy})`} />
        {/* 中心文字 · 点估计 */}
        <text x={cx} y={cy - 4} textAnchor="middle"
              style={{ fontSize: 11, fill: 'var(--text-muted)' }}>点估计</text>
        <text x={cx} y={cy + 12} textAnchor="middle"
              style={{
                fontSize: 16, fontWeight: 700, fontFamily: 'monospace',
                fill: up ? '#ef4444' : '#22c55e',
              }}>
          {up ? '+' : ''}{point.toFixed(2)}%
        </text>
      </svg>
      <div className="flex-1 min-w-0 text-xs space-y-1">
        <ProbRow color="#22c55e" label="上涨" val={prob.up} />
        <ProbRow color="#94a3b8" label="持平" val={prob.flat} />
        <ProbRow color="#ef4444" label="下跌" val={prob.down} />
        <div className="text-[10px] pt-1" style={{ color: 'var(--text-muted)' }}>
          bucket {bucket} · 桶内 {nInBucket} 条
        </div>
      </div>
    </div>
  )
}

function ProbRow({ color, label, val }: { color: string; label: string; val: number }) {
  return (
    <div className="flex items-center gap-2">
      <span className="w-2.5 h-2.5 rounded-sm inline-block" style={{ background: color }} />
      <span className="opacity-80">{label}</span>
      <span className="font-mono font-bold ml-auto">{(val * 100).toFixed(0)}%</span>
    </div>
  )
}


// ─── 该股·该模型·近 90d 表现卡(§3.A.2.2 复赛验证) ──────────────
// 目的:每张预测报告底部显示"该模型对这只股票近 90 天的历史命中率"
// 数据源:/api/backtest/accuracy?days=90&symbol=XXX
// 合规:样本量 < 30 显示"数据不足" · 严禁 mock 兜底

type AccuracySummary = {
  sample: number
  hit_rate: number | null
  amt_hit_rate: number | null
  mae: number | null
  window_days: number
}

function HistoricalPerformanceCard({ symbol }: { symbol: string }) {
  const [data, setData] = useState<AccuracySummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true); setError(false); setData(null)
    fetch(`/api/backtest/accuracy?days=90&symbol=${encodeURIComponent(symbol)}`, { headers: authHeaders() })
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(d => { if (!cancelled) { if (d?.detail) setError(true); else setData(d) } })
      .catch(() => { if (!cancelled) setError(true) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [symbol])

  return (
    <div className="border-t px-5 py-4" style={{ borderColor: 'var(--border)' }}>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Activity className="w-3.5 h-3.5" style={{ color: 'var(--text-muted)' }} />
          <p className="text-xs font-semibold" style={{ color: 'var(--text-muted)' }}>
            该股 · 该模型 · 近 90 日表现
          </p>
        </div>
        <Link href={`/evaluation?symbol=${encodeURIComponent(symbol)}`}
              className="text-xs flex items-center gap-1 hover:opacity-70 transition-opacity"
              style={{ color: '#f59e0b' }}>
          查看完整评估看板 <ExternalLink className="w-3 h-3" />
        </Link>
      </div>

      {loading ? (
        <div className="grid grid-cols-4 gap-2">
          {[0, 1, 2, 3].map(i => (
            <div key={i} className="h-14 rounded animate-pulse"
                 style={{ background: 'var(--bg)' }} />
          ))}
        </div>
      ) : error ? (
        <p className="text-xs py-2" style={{ color: 'var(--text-muted)' }}>
          历史表现数据暂不可用 · 需完成每日预测流水线积累样本
        </p>
      ) : data && data.sample >= 30 ? (
        <div className="grid grid-cols-4 gap-2">
          <PerfCell label="样本量" value={data.sample.toLocaleString()} />
          <PerfCell label="方向命中率"
                    value={data.hit_rate !== null ? `${data.hit_rate.toFixed(1)}%` : '—'}
                    highlight={data.hit_rate !== null && data.hit_rate >= 55}
                    color={data.hit_rate !== null && data.hit_rate >= 55 ? '#22c55e' : '#f59e0b'} />
          <PerfCell label="幅度命中率"
                    value={data.amt_hit_rate !== null ? `${data.amt_hit_rate.toFixed(1)}%` : '—'}
                    hint="误差 < 1%" />
          <PerfCell label="平均绝对误差"
                    value={data.mae !== null ? `${data.mae.toFixed(2)}%` : '—'}
                    hint="MAE" />
        </div>
      ) : (
        <p className="text-xs py-2" style={{ color: 'var(--text-muted)' }}>
          样本量不足(仅 {data?.sample ?? 0} 条 · 需 ≥ 30)· 无法给出稳定的历史评估
        </p>
      )}
    </div>
  )
}

function PerfCell({ label, value, hint, highlight, color }:
  { label: string; value: string; hint?: string; highlight?: boolean; color?: string }) {
  return (
    <div className="rounded-lg px-3 py-2"
         style={{ background: 'var(--bg)', border: `1px solid ${highlight ? (color || 'var(--border)') : 'var(--border)'}` }}>
      <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>{label}</div>
      <div className="font-mono font-bold text-sm mt-0.5"
           style={{ color: color || 'var(--text)' }}>{value}</div>
      {hint && <div className="text-[10px] opacity-60 mt-0.5" style={{ color: 'var(--text-muted)' }}>{hint}</div>}
    </div>
  )
}
