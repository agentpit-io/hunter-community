'use client'
import { useEffect, useState, useRef, useCallback } from 'react'
import { useParams } from 'next/navigation'
import Sidebar from '../../components/Sidebar'
import ReactECharts from 'echarts-for-react'
import Link from 'next/link'
import { Bell, Trash2, Plus, X } from 'lucide-react'
// Bell/Trash2/Plus/X used by AlertPanel

const API = process.env.NEXT_PUBLIC_API_URL || ''

interface Quote {
  code: string; name: string; price: number | null
  change_pct: number; change_amt: number
  volume: number; amount: number; high: number; low: number
  open: number; prev_close: number; market: string
  asset_type?: string  // stock / etf / fund
  bid1: number; bid1v: number; bid2: number; bid2v: number
  bid3: number; bid3v: number; bid4: number; bid4v: number
  bid5: number; bid5v: number
  ask1: number; ask1v: number; ask2: number; ask2v: number
  ask3: number; ask3v: number; ask4: number; ask4v: number
  ask5: number; ask5v: number
  ts: string
}

function marketLabel(q: Quote): string {
  if (q.asset_type === 'fund') return '场外基金'
  if (q.asset_type === 'etf')  return q.market === 'A' ? 'A股 ETF' : 'ETF'
  if (q.market === 'HK') return '港股'
  if (q.market === 'US') return '美股'
  return 'A股'
}

interface Kline {
  ts: string; open: number; high: number; low: number; close: number; volume: number
}

interface FundFlow {
  main_net: number; big_net: number; large_net: number; mid_net: number; small_net: number
  date: string
}

interface NewsItem {
  id: number; code: string; title: string; source: string; url: string
  sentiment: string; published_at: string
}

const TABS_STOCK = ['行情', 'K线', '分时', '资金', '新闻']
const TABS_FUND  = ['行情', 'K线', '新闻']  // 公募无分时（盘中无价）/无资金流向

function fmt(v: number | null | undefined, dec = 2) {
  if (v == null || isNaN(Number(v))) return '--'
  return Number(v).toFixed(dec)
}

function fmtVol(v: number) {
  if (!v) return '--'
  if (v >= 1e8) return (v / 1e8).toFixed(2) + '亿'
  if (v >= 1e4) return (v / 1e4).toFixed(0) + '万'
  return String(v)
}

function OrderBook({ q }: { q: Quote }) {
  const asks = [
    { price: q.ask5, vol: q.ask5v, label: '卖五' },
    { price: q.ask4, vol: q.ask4v, label: '卖四' },
    { price: q.ask3, vol: q.ask3v, label: '卖三' },
    { price: q.ask2, vol: q.ask2v, label: '卖二' },
    { price: q.ask1, vol: q.ask1v, label: '卖一' },
  ]
  const bids = [
    { price: q.bid1, vol: q.bid1v, label: '买一' },
    { price: q.bid2, vol: q.bid2v, label: '买二' },
    { price: q.bid3, vol: q.bid3v, label: '买三' },
    { price: q.bid4, vol: q.bid4v, label: '买四' },
    { price: q.bid5, vol: q.bid5v, label: '买五' },
  ]
  return (
    <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }} className="rounded-xl p-4">
      <div className="text-sm font-medium mb-3" style={{ color: 'var(--text-muted)' }}>盘口</div>
      <div className="space-y-1.5">
        {asks.map((a, i) => (
          <div key={i} className="flex justify-between text-sm">
            <span className="w-8" style={{ color: 'var(--text-muted)' }}>{a.label}</span>
            <span style={{ color: 'var(--green)' }}>{fmt(a.price)}</span>
            <span style={{ color: 'var(--text-muted)' }}>{fmtVol(a.vol)}</span>
          </div>
        ))}
        <div className="my-2 border-t" style={{ borderColor: 'var(--border)' }} />
        {bids.map((b, i) => (
          <div key={i} className="flex justify-between text-sm">
            <span className="w-8" style={{ color: 'var(--text-muted)' }}>{b.label}</span>
            <span style={{ color: 'var(--red)' }}>{fmt(b.price)}</span>
            <span style={{ color: 'var(--text-muted)' }}>{fmtVol(b.vol)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function StatRow({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex justify-between py-2 border-b text-sm" style={{ borderColor: 'var(--border)' }}>
      <span style={{ color: 'var(--text-muted)' }}>{label}</span>
      <span style={{ color: color || 'var(--text)' }}>{value}</span>
    </div>
  )
}

function FundFlowBar({ label, value, total }: { label: string; value: number; total: number }) {
  const pct = total ? Math.abs(value / total) * 100 : 0
  const positive = value >= 0
  return (
    <div className="mb-3">
      <div className="flex justify-between text-xs mb-1">
        <span className="text-slate-400">{label}</span>
        <span style={{ color: positive ? '#ef4444' : '#22c55e' }}>
          {positive ? '+' : ''}{(value / 1e8).toFixed(2)}亿
        </span>
      </div>
      <div className="h-1.5 bg-slate-800 rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all"
          style={{
            width: pct + '%',
            background: positive ? '#ef4444' : '#22c55e',
          }}
        />
      </div>
    </div>
  )
}

interface PriceAlert {
  id: number
  label: string
  condition_type: string
  threshold: number
  threshold2: number | null
  cooldown_minutes: number
  last_triggered_at: string | null
}

const CONDITION_LABELS: Record<string, string> = {
  price_below: '价格跌到 ≤',
  price_above: '价格涨到 ≥',
  change_pct_below: '跌幅超过',
  volatility_above: '日内振幅超过',
}

const CONDITION_UNIT: Record<string, string> = {
  price_below: '',
  price_above: '',
  change_pct_below: '%',
  volatility_above: '%',
}

function authHeaders(): HeadersInit {
  const token = typeof window !== 'undefined' ? localStorage.getItem('hunter_token') : ''
  const h: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) h['Authorization'] = `Bearer ${token}`
  return h
}

export default function StockPage() {
  const params = useParams()
  const code = params.code as string
  const [tab, setTab] = useState(0)
  const [quote, setQuote] = useState<Quote | null>(null)
  const [klines, setKlines] = useState<Kline[]>([])
  // 拿不到的原因。空字符串 = 还在加载 —— 这两种状态必须分开,
  // 混在一起就是测试人员报的"K线永远转圈"
  const [klineErr, setKlineErr] = useState('')
  const [timeshare, setTimeshare] = useState<Kline[]>([])
  const [fundflow, setFundflow] = useState<FundFlow | null>(null)
  const [news, setNews] = useState<NewsItem[]>([])
  const [timeshareLoading, setTimeshareLoading] = useState(false)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const timeshareTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // 价格提醒
  const [alerts, setAlerts] = useState<PriceAlert[]>([])

  const loadAlerts = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/alerts/${code}`, { headers: authHeaders() })
      const d = await r.json()
      setAlerts(d.alerts || [])
    } catch {}
  }, [code])

  const createAlert = async (condType: string, threshold: number, threshold2: number | null, label: string, cooldown: number) => {
    await fetch(`${API}/api/alerts/${code}`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ condition_type: condType, threshold, threshold2, label, cooldown_minutes: cooldown }),
    })
    await loadAlerts()
  }

  const deleteAlert = async (id: number) => {
    await fetch(`${API}/api/alerts/${id}`, { method: 'DELETE', headers: authHeaders() })
    await loadAlerts()
  }


  const fetchQuote = useCallback(async () => {
    try {
      const r = await fetch(API + '/api/quote/' + code)
      if (r.ok) setQuote(await r.json())
    } catch {}
  }, [code])

  useEffect(() => {
    fetchQuote()
    loadAlerts()
    timerRef.current = setInterval(fetchQuote, 5000)
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [fetchQuote, loadAlerts])

  useEffect(() => {
    if (tab === 1) {
      setKlineErr('')
      fetch(API + '/api/kline/' + code + '?period=day&limit=120')
        .then(r => r.json())
        .then(d => {
          // 接口拿不到数据时返回的是 {error, message},不是数组。
          // 直接 setKlines(d) 会让下面的 klines.map 报
          // "klines.map is not a function" —— 白屏,比"加载中"更糟。
          if (Array.isArray(d)) { setKlines(d); return }
          setKlines([])
          setKlineErr(d?.message || '暂无数据')
        })
        .catch(() => { setKlines([]); setKlineErr('行情接口没响应') })
    }
    if (tab === 2) {
      const fetchTS = () => {
        setTimeshareLoading(true)
        fetch(API + '/api/timeshare/' + code)
          .then(r => r.json())
          // 同 K线:拿不到时返回的是 {error, message} 而不是数组,
          // 直接塞进 state 会让 timeshare.map 崩掉
          .then(d => { setTimeshare(Array.isArray(d) ? d : []); setTimeshareLoading(false) })
          .catch(() => { setTimeshare([]); setTimeshareLoading(false) })
      }
      fetchTS()
      timeshareTimerRef.current = setInterval(fetchTS, 60000)
    } else {
      if (timeshareTimerRef.current) {
        clearInterval(timeshareTimerRef.current)
        timeshareTimerRef.current = null
      }
    }
    if (tab === 3) {
      fetch(API + '/api/fundflow/' + code)
        .then(r => r.json()).then(setFundflow).catch(() => {})
    }
    if (tab === 4) {
      fetch(API + '/api/news/' + code + '?limit=30')
        .then(r => r.json()).then(setNews).catch(() => {})
    }
    return () => {
      if (timeshareTimerRef.current) {
        clearInterval(timeshareTimerRef.current)
        timeshareTimerRef.current = null
      }
    }
  }, [tab, code])

  const up = (quote?.change_pct ?? 0) > 0
  const down = (quote?.change_pct ?? 0) < 0
  const priceColor = up ? '#ef4444' : down ? '#22c55e' : '#94a3b8'

  const isFund = quote?.asset_type === 'fund'

  // 股票/ETF 用蜡烛图 + 成交量柱
  const klineOption = {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    legend: { data: ['K线', 'MA5', 'MA10', 'MA20'], textStyle: { color: '#64748b' }, top: 4 },
    grid: [{ left: 64, right: 16, top: 40, bottom: 100 }, { left: 64, right: 16, top: '72%', bottom: 20 }],
    xAxis: [
      { type: 'category', data: klines.map(k => k.ts.slice(0, 10)), axisLabel: { color: '#64748b', fontSize: 11 }, axisLine: { lineStyle: { color: '#e2e8f0' } }, gridIndex: 0 },
      { type: 'category', data: klines.map(k => k.ts.slice(0, 10)), axisLabel: { show: false }, axisLine: { lineStyle: { color: '#e2e8f0' } }, gridIndex: 1 },
    ],
    yAxis: [
      { scale: true, splitLine: { lineStyle: { color: '#e2e8f0' } }, axisLabel: { color: '#64748b', fontSize: 11 }, gridIndex: 0 },
      { scale: true, splitLine: { lineStyle: { color: '#e2e8f0' } }, axisLabel: { color: '#64748b', fontSize: 11 }, gridIndex: 1 },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 60, end: 100 },
      { xAxisIndex: [0, 1], start: 60, end: 100, bottom: 4, height: 18, textStyle: { color: '#64748b' }, borderColor: '#e2e8f0' },
    ],
    series: [
      {
        name: 'K线', type: 'candlestick', xAxisIndex: 0, yAxisIndex: 0,
        data: klines.map(k => [k.open, k.close, k.low, k.high]),
        itemStyle: { color: '#dc2626', color0: '#16a34a', borderColor: '#dc2626', borderColor0: '#16a34a' },
      },
      {
        name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1,
        data: klines.map(k => ({ value: k.volume, itemStyle: { color: k.close >= k.open ? '#dc2626' : '#16a34a' } })),
      },
    ],
  }

  // 公募基金用折线图（净值走势，无成交量、无蜡烛）
  const fundNavOption = {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', valueFormatter: (v: any) => Number(v).toFixed(4) },
    grid: { left: 64, right: 16, top: 30, bottom: 60 },
    xAxis: {
      type: 'category', data: klines.map(k => k.ts.slice(0, 10)),
      axisLabel: { color: '#64748b', fontSize: 11 }, axisLine: { lineStyle: { color: '#e2e8f0' } },
    },
    yAxis: {
      scale: true, splitLine: { lineStyle: { color: '#e2e8f0' } },
      axisLabel: { color: '#64748b', fontSize: 11, formatter: (v: number) => v.toFixed(3) },
    },
    dataZoom: [
      { type: 'inside', start: 60, end: 100 },
      { start: 60, end: 100, bottom: 4, height: 18, textStyle: { color: '#64748b' }, borderColor: '#e2e8f0' },
    ],
    series: [{
      name: '单位净值', type: 'line', smooth: true, symbol: 'none',
      data: klines.map(k => k.close),
      lineStyle: { color: '#7c3aed', width: 2 },
      areaStyle: { color: 'rgba(124,58,237,0.08)' },
    }],
  }

  const timeshareOption = {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    grid: [{ left: 64, right: 16, top: 20, bottom: 80 }, { left: 64, right: 16, top: '72%', bottom: 20 }],
    xAxis: [
      { type: 'category', data: timeshare.map(t => t.ts.slice(11, 16)), axisLabel: { color: '#64748b', fontSize: 11 }, axisLine: { lineStyle: { color: '#e2e8f0' } }, gridIndex: 0 },
      { type: 'category', data: timeshare.map(t => t.ts.slice(11, 16)), axisLabel: { show: false }, axisLine: { lineStyle: { color: '#e2e8f0' } }, gridIndex: 1 },
    ],
    yAxis: [
      { scale: true, splitLine: { lineStyle: { color: '#e2e8f0' } }, axisLabel: { color: '#64748b', fontSize: 11 }, gridIndex: 0 },
      { scale: true, splitLine: { lineStyle: { color: '#e2e8f0' } }, axisLabel: { color: '#64748b', fontSize: 11 }, gridIndex: 1 },
    ],
    series: [
      {
        type: 'line', data: timeshare.map(t => t.close), smooth: false, symbol: 'none',
        lineStyle: { color: '#2563eb', width: 2 }, areaStyle: { color: 'rgba(176,106,50,0.08)' },
        xAxisIndex: 0, yAxisIndex: 0,
      },
      {
        type: 'bar', data: timeshare.map(t => t.volume),
        xAxisIndex: 1, yAxisIndex: 1, itemStyle: { color: '#93c5fd' },
      },
    ],
  }

  return (
    <div className="flex min-h-screen" style={{ background: 'var(--bg)', color: 'var(--text)' }}>
      <Sidebar />
      <div className="flex-1 flex flex-col ml-52">
        {/* Header */}
        <div className="px-6 py-4 border-b" style={{ borderColor: 'var(--border)', background: 'var(--bg-card)' }}>
          <div className="flex items-center gap-3 mb-1">
            <Link href="/" className="text-slate-500 text-sm hover:text-slate-300">← 总览</Link>
            <span className="text-slate-700">/</span>
            <span className="text-sm text-slate-300">{quote?.name || code}</span>
          </div>
          <div className="flex items-end gap-4">
            <div>
              <div className="text-2xl font-bold" style={{ color: priceColor }}>
                {fmt(quote?.price)}
              </div>
              <div className="text-sm mt-0.5" style={{ color: priceColor }}>
                {(quote?.change_pct ?? 0) > 0 ? '+' : ''}{fmt(quote?.change_pct)}%
                &nbsp;
                {(quote?.change_amt ?? 0) >= 0 ? '+' : ''}{fmt(quote?.change_amt)}
              </div>
            </div>
            <div className="flex gap-5 text-sm pb-1" style={{ color: 'var(--text-muted)' }}>
              {quote?.asset_type === 'fund' ? (
                <span>昨日净值 <span style={{ color: 'var(--text)' }}>{fmt(quote?.prev_close, 4)}</span></span>
              ) : (
                <>
                  <span>今开 <span style={{ color: 'var(--text)' }}>{fmt(quote?.open)}</span></span>
                  <span>昨收 <span style={{ color: 'var(--text)' }}>{fmt(quote?.prev_close)}</span></span>
                  <span>最高 <span style={{ color: 'var(--red)' }}>{fmt(quote?.high)}</span></span>
                  <span>最低 <span style={{ color: 'var(--green)' }}>{fmt(quote?.low)}</span></span>
                  <span>成交额 <span style={{ color: 'var(--text)' }}>{fmtVol(quote?.amount || 0)}</span></span>
                </>
              )}
              {quote?.ts && (
                <span style={{ color: 'var(--text-muted)' }}>
                  更新 {new Date(quote.ts).toLocaleTimeString('zh-CN', {
                    timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', second: '2-digit',
                  })}
                </span>
              )}
            </div>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex gap-0 border-b" style={{ borderColor: 'var(--border)', background: 'var(--bg-card)' }}>
          {(isFund ? TABS_FUND : TABS_STOCK).map((t, i) => (
            <button
              key={t}
              onClick={() => setTab(isFund ? (t === '行情' ? 0 : t === 'K线' ? 1 : 4) : i)}
              className="px-5 py-2.5 text-sm transition-colors"
              style={{
                color: tab === (isFund ? (t === '行情' ? 0 : t === 'K线' ? 1 : 4) : i) ? '#3b82f6' : '#64748b',
                borderBottom: tab === (isFund ? (t === '行情' ? 0 : t === 'K线' ? 1 : 4) : i) ? '2px solid #3b82f6' : '2px solid transparent',
              }}
            >
              {t}
            </button>
          ))}
        </div>

        {/* Tab Content */}
        <div className="flex-1 p-6">
          {/* 行情 Tab */}
          {tab === 0 && quote && (() => {
            const isFund = quote.asset_type === 'fund'  // 场外公募：无成交、无盘口
            return (
              <div className={`grid gap-6 ${isFund ? 'grid-cols-1 max-w-2xl' : 'grid-cols-3'}`}>
                <div className={isFund ? '' : 'col-span-2'} style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
                  <div className="rounded-xl p-4">
                    <div className="text-xs text-slate-500 mb-3 font-medium">{isFund ? '基金净值数据' : '行情数据'}</div>
                    <StatRow label={isFund ? '单位净值' : '现价'} value={fmt(quote.price, isFund ? 4 : 2)} color={priceColor} />
                    <StatRow label="涨跌幅" value={(quote.change_pct > 0 ? '+' : '') + fmt(quote.change_pct) + '%'} color={priceColor} />
                    {!isFund && <StatRow label="今开" value={fmt(quote.open)} />}
                    <StatRow label={isFund ? '前一日净值' : '昨收'} value={fmt(quote.prev_close, isFund ? 4 : 2)} />
                    {!isFund && <StatRow label="最高" value={fmt(quote.high)} color="#ef4444" />}
                    {!isFund && <StatRow label="最低" value={fmt(quote.low)} color="#22c55e" />}
                    {!isFund && <StatRow label="成交量" value={fmtVol(quote.volume)} />}
                    {!isFund && <StatRow label="成交额" value={fmtVol(quote.amount)} />}
                    <StatRow label="市场" value={marketLabel(quote)} />
                  </div>
                </div>
                {!isFund && <OrderBook q={quote} />}
              </div>
            )
          })()}
          {tab === 0 && !quote && (
            <div className="text-slate-600 text-sm">加载行情中...</div>
          )}

          {/* K线 Tab */}
          {tab === 1 && (
            <div className="space-y-4">
              <div className="rounded-xl p-4" style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
                <div className="text-xs text-slate-500 mb-3 font-medium">{isFund ? '净值走势' : '日K线'}</div>
                {klines.length > 0 ? (
                  <ReactECharts option={isFund ? fundNavOption : klineOption} style={{ height: isFund ? 400 : 500 }} />
                ) : klineErr ? (
                  <div className="h-60 flex flex-col items-center justify-center text-slate-500 gap-2">
                    <div className="text-sm">暂无 K 线数据</div>
                    <div className="text-xs text-slate-400">{klineErr}</div>
                  </div>
                ) : (
                  <div className="h-60 flex items-center justify-center text-slate-600">加载中...</div>
                )}
              </div>
              <AlertPanel
                allAlerts={alerts}
                rows={[
                  { type: 'price_below', label: '价格跌到 ≤', unit: '元', placeholder: quote ? String(Math.floor((quote.price||0)*0.95)) : '如：1200' },
                  { type: 'price_above', label: '价格涨到 ≥', unit: '元', placeholder: quote ? String(Math.ceil((quote.price||0)*1.05)) : '如：1400' },
                ]}
                title="价格提醒"
                hint="当价格到达设定值时推送飞书"
                onAdd={createAlert}
                onDelete={deleteAlert}
              />
            </div>
          )}

          {/* 分时 Tab */}
          {tab === 2 && (
            <div className="space-y-4">
              <div className="rounded-xl p-4" style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
                <div className="text-sm font-medium mb-3" style={{ color: 'var(--text-muted)' }}>
                  分时{timeshare.length > 0 ? `（${timeshare[0].ts.slice(0, 10)}）` : ''}
                </div>
                {timeshareLoading ? (
                  <div className="h-60 flex items-center justify-center text-sm" style={{ color: 'var(--text-muted)' }}>加载中...</div>
                ) : timeshare.length > 0 ? (
                  <ReactECharts option={timeshareOption} style={{ height: 400 }} />
                ) : (
                  <div className="h-60 flex items-center justify-center text-sm" style={{ color: 'var(--text-muted)' }}>暂无分时数据</div>
                )}
              </div>
              <AlertPanel
                allAlerts={alerts}
                rows={[
                  { type: 'change_pct_below', label: '跌幅超过', unit: '%', placeholder: '如：5' },
                  { type: 'volatility_above', label: '振幅超过', unit: '%', placeholder: '如：3', hasThreshold2: true },
                ]}
                title="波动提醒"
                hint="当跌幅或振幅超过设定值时推送飞书"
                onAdd={createAlert}
                onDelete={deleteAlert}
              />
            </div>
          )}

          {/* 资金 Tab */}
          {tab === 3 && (
            <div className="rounded-xl p-4 max-w-lg" style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
              <div className="text-sm font-medium mb-4" style={{ color: 'var(--text-muted)' }}>资金流向</div>
              {fundflow ? (
                <>
                  <FundFlowBar label="主力净流入" value={fundflow.main_net} total={Math.abs(fundflow.main_net) + Math.abs(fundflow.small_net)} />
                  <FundFlowBar label="大单净流入" value={fundflow.big_net} total={Math.abs(fundflow.big_net) + Math.abs(fundflow.small_net)} />
                  <FundFlowBar label="中单净流入" value={fundflow.mid_net} total={Math.abs(fundflow.main_net) + Math.abs(fundflow.small_net)} />
                  <FundFlowBar label="散户净流入" value={fundflow.small_net} total={Math.abs(fundflow.main_net) + Math.abs(fundflow.small_net)} />
                </>
              ) : (
                <div className="text-slate-600 text-sm">暂无资金流向数据</div>
              )}
            </div>
          )}

          {/* 新闻 Tab */}
          {tab === 4 && (
            <div className="rounded-xl p-4" style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
              <div className="text-xs text-slate-500 mb-4 font-medium">相关新闻</div>
              {news.length > 0 ? (
                <div className="space-y-3">
                  {news.map(n => (
                    <a key={n.id} href={n.url} target="_blank" rel="noreferrer"
                      className="block p-3 rounded-lg hover:opacity-80 transition-opacity"
                      style={{ background: 'var(--bg)', border: '1px solid var(--border)' }}>
                      <div className="text-sm text-slate-800 leading-snug mb-1">{n.title}</div>
                      <div className="flex gap-3 text-[11px] text-slate-500">
                        <span>{n.source}</span>
                        <span>{n.published_at?.slice(0, 16)}</span>
                        {n.sentiment && (
                          <span style={{ color: n.sentiment === 'positive' ? '#ef4444' : n.sentiment === 'negative' ? '#22c55e' : '#64748b' }}>
                            {n.sentiment === 'positive' ? '利好' : n.sentiment === 'negative' ? '利空' : '中性'}
                          </span>
                        )}
                      </div>
                    </a>
                  ))}
                </div>
              ) : (
                <div className="text-slate-600 text-sm">暂无新闻数据</div>
              )}
            </div>
          )}
        </div>
      </div>

    </div>
  )
}

function AlertRow({
  condType, label, unit, placeholder, hasThreshold2, existingAlerts, onAdd, onDelete,
}: {
  condType: string; label: string; unit: string; placeholder: string
  hasThreshold2?: boolean
  existingAlerts: PriceAlert[]
  onAdd: (condType: string, threshold: number, threshold2: number | null, label: string, cooldown: number) => Promise<void>
  onDelete: (id: number) => void
}) {
  const [threshold, setThreshold] = useState('')
  const [threshold2, setThreshold2] = useState('')
  const [note, setNote] = useState('')
  const [cooldown, setCooldown] = useState(60)
  const [saving, setSaving] = useState(false)

  const handleAdd = async () => {
    if (!threshold) return
    setSaving(true)
    await onAdd(condType, parseFloat(threshold), threshold2 ? parseFloat(threshold2) : null, note, cooldown)
    setThreshold(''); setThreshold2(''); setNote('')
    setSaving(false)
  }

  return (
    <div className="py-2.5 border-b last:border-0" style={{ borderColor: 'var(--border)' }}>
      {/* 条件输入行 */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium w-24 flex-shrink-0" style={{ color: 'var(--text)' }}>{label}</span>
        <div className="flex items-center gap-1">
          <input type="number" step="0.01" value={threshold} onChange={e => setThreshold(e.target.value)}
            placeholder={placeholder}
            className="w-28 px-2.5 py-1.5 rounded-lg text-sm outline-none"
            style={{ background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text)' }} />
          <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{unit}</span>
        </div>
        {hasThreshold2 && (
          <div className="flex items-center gap-1">
            <span className="text-xs" style={{ color: 'var(--text-muted)' }}>且价格≤</span>
            <input type="number" step="0.01" value={threshold2} onChange={e => setThreshold2(e.target.value)}
              placeholder="选填"
              className="w-24 px-2.5 py-1.5 rounded-lg text-sm outline-none"
              style={{ background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text)' }} />
          </div>
        )}
        <input type="text" value={note} onChange={e => setNote(e.target.value)}
          placeholder="备注（选填）"
          className="w-24 px-2.5 py-1.5 rounded-lg text-sm outline-none"
          style={{ background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text)' }} />
        <select value={cooldown} onChange={e => setCooldown(Number(e.target.value))}
          className="px-2 py-1.5 rounded-lg text-xs outline-none"
          style={{ background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text)' }}>
          <option value={30}>冷却30分钟</option>
          <option value={60}>冷却1小时</option>
          <option value={120}>冷却2小时</option>
          <option value={1440}>今天仅一次</option>
        </select>
        <button onClick={handleAdd} disabled={saving || !threshold}
          className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium flex-shrink-0"
          style={{ background: '#d97706', color: '#fff', opacity: !threshold ? 0.45 : 1 }}>
          <Plus className="w-3 h-3" />
          {saving ? '...' : '添加'}
        </button>
      </div>
      {/* 已设置的该类提醒标签 */}
      {existingAlerts.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mt-2 pl-26">
          {existingAlerts.map(a => (
            <span key={a.id} className="flex items-center gap-1 px-2 py-0.5 rounded-full text-xs"
              style={{ background: 'rgba(245,158,11,0.12)', border: '1px solid rgba(245,158,11,0.3)', color: 'var(--text)' }}>
              {a.label && <span style={{ color: '#d97706' }}>{a.label} · </span>}
              <strong>{a.threshold}{unit}</strong>
              {a.threshold2 != null && <span style={{ color: 'var(--text-muted)' }}> 且≤{a.threshold2}</span>}
              {a.last_triggered_at && <span style={{ color: 'var(--text-muted)' }}> · {new Date(a.last_triggered_at).toLocaleDateString('zh-CN')}</span>}
              <button onClick={() => onDelete(a.id)} style={{ color: '#ef4444' }}><X className="w-3 h-3" /></button>
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

function AlertPanel({ allAlerts, rows, title, hint, onAdd, onDelete }: {
  allAlerts: PriceAlert[]
  rows: { type: string; label: string; unit: string; placeholder: string; hasThreshold2?: boolean }[]
  title: string
  hint: string
  onAdd: (condType: string, threshold: number, threshold2: number | null, label: string, cooldown: number) => Promise<void>
  onDelete: (id: number) => void
}) {
  return (
    <div className="rounded-xl px-4 pt-3 pb-1" style={{ background: 'var(--bg-card)', border: '1px solid rgba(245,158,11,0.25)' }}>
      <div className="flex items-center gap-2 mb-2">
        <Bell className="w-3.5 h-3.5" style={{ color: '#d97706' }} />
        <span className="text-xs font-semibold" style={{ color: '#d97706' }}>{title}</span>
        <span className="text-xs" style={{ color: 'var(--text-muted)' }}>— {hint}</span>
      </div>
      {rows.map(row => (
        <AlertRow
          key={row.type}
          condType={row.type}
          label={row.label}
          unit={row.unit}
          placeholder={row.placeholder}
          hasThreshold2={row.hasThreshold2}
          existingAlerts={allAlerts.filter(a => a.condition_type === row.type)}
          onAdd={onAdd}
          onDelete={onDelete}
        />
      ))}
    </div>
  )
}
