'use client'
import { useEffect, useState, useCallback, useRef, KeyboardEvent } from 'react'
import { useRouter } from 'next/navigation'
import Sidebar from '../components/Sidebar'
import Link from 'next/link'
import { RefreshCw, Send, Sparkles } from 'lucide-react'
import { HUNTER } from '../lib/hunter-theme'

const API = process.env.NEXT_PUBLIC_API_URL || ''
const AUTO_REFRESH_MS = 30 * 60 * 1000  // 30 分钟

interface Quote {
  code: string; name: string; price: number | null
  change_pct: number; change_amt: number
  volume: number; amount: number; high: number; low: number
  open: number; prev_close: number; market: string; ts: string
}

function ChatHero() {
  const router = useRouter()
  const [text, setText] = useState('')
  const taRef = useRef<HTMLTextAreaElement>(null)

  const autosize = () => {
    const ta = taRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = Math.min(ta.scrollHeight, 140) + 'px'
  }

  const handleSubmit = () => {
    const trimmed = text.trim()
    if (trimmed) {
      router.push(`/chat?q=${encodeURIComponent(trimmed)}`)
    } else {
      router.push('/chat')
    }
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      handleSubmit()
    }
  }

  const suggestions = ['茅台走势预测', '000001 半年报要点', '半导体政策影响', '为 600519 起草论点']

  return (
    <div
      style={{
        background: 'linear-gradient(135deg, #252815 0%, #353A1A 55%, #282C14 100%)',
        borderRadius: 20,
        padding: '28px 32px 26px',
        marginBottom: 24,
        color: '#fff',
        boxShadow: '0 8px 32px rgba(37,40,21,0.18)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 10 }}>
        <span style={{ fontSize: 30 }}>🦌</span>
        <div>
          <div
            style={{
              fontFamily: HUNTER.SERIF,
              fontSize: 22,
              fontWeight: 700,
              color: HUNTER.COPPER2,
              lineHeight: 1.2,
            }}
          >
            猎鹿人 · Hunter
          </div>
          <div
            style={{
              fontFamily: HUNTER.SERIF,
              fontSize: 13,
              color: 'rgba(255,255,255,0.75)',
              marginTop: 3,
            }}
          >
            别人给你答案 · 我们记住你的判断
          </div>
        </div>
      </div>

      <div
        style={{
          background: '#fff',
          borderRadius: 14,
          padding: '12px 14px',
          boxShadow: '0 4px 20px rgba(0,0,0,0.15)',
        }}
      >
        <textarea
          ref={taRef}
          value={text}
          onChange={(e) => {
            setText(e.target.value)
            autosize()
          }}
          onKeyDown={handleKeyDown}
          placeholder="有什么想问 Hunter?"
          rows={1}
          style={{
            width: '100%',
            border: 'none',
            outline: 'none',
            resize: 'none',
            fontFamily: 'inherit',
            fontSize: 15,
            lineHeight: 1.5,
            color: HUNTER.INK,
            background: 'transparent',
            padding: '4px 0 8px',
            maxHeight: 140,
            overflowY: 'auto',
          }}
        />
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginTop: 4,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11.5, color: HUNTER.INK_F }}>
            <Sparkles size={12} style={{ color: HUNTER.THEME }} />
            AI 对话 · 探索型多智能体
          </div>
          <button
            type="button"
            onClick={handleSubmit}
            style={{
              padding: '7px 18px',
              background: HUNTER.THEME,
              color: '#fff',
              border: 'none',
              borderRadius: 20,
              fontSize: 13,
              fontWeight: 600,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              transition: 'background 0.15s',
              fontFamily: 'inherit',
            }}
            onMouseEnter={(e) => (e.currentTarget.style.background = HUNTER.COPPER2)}
            onMouseLeave={(e) => (e.currentTarget.style.background = HUNTER.THEME)}
          >
            <Send size={12} /> {text.trim() ? '开始对话' : '进入 AI 对话'}
          </button>
        </div>
      </div>

      <div style={{ marginTop: 12, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 11, color: 'rgba(255,255,255,0.5)' }}>💡 试试:</span>
        {suggestions.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => router.push(`/chat?q=${encodeURIComponent(s)}`)}
            style={{
              padding: '3px 10px',
              background: 'rgba(255,255,255,0.08)',
              border: '1px solid rgba(255,255,255,0.15)',
              borderRadius: 12,
              fontSize: 11,
              color: 'rgba(255,255,255,0.85)',
              cursor: 'pointer',
              fontFamily: 'inherit',
              transition: 'all 0.15s',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = 'rgba(255,255,255,0.15)'
              e.currentTarget.style.borderColor = HUNTER.COPPER2
              e.currentTarget.style.color = HUNTER.COPPER2
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = 'rgba(255,255,255,0.08)'
              e.currentTarget.style.borderColor = 'rgba(255,255,255,0.15)'
              e.currentTarget.style.color = 'rgba(255,255,255,0.85)'
            }}
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  )
}

function QuoteCard({ q }: { q: Quote }) {
  const up = (q.change_pct ?? 0) > 0
  const down = (q.change_pct ?? 0) < 0
  const color = up ? 'var(--red)' : down ? 'var(--green)' : 'var(--text-muted)'
  const hasData = q.price != null

  return (
    <Link href={`/stock/${q.code}`} className="block rounded-xl p-5 transition-all hover:shadow-md"
      style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
      <div className="flex justify-between items-start mb-4">
        <div>
          <div className="font-bold text-base" style={{ color: 'var(--text)' }}>{q.name}</div>
          <div className="text-sm mt-0.5" style={{ color: 'var(--text-muted)' }}>
            {q.code} · {q.market === 'HK' ? '港股' : 'A股'}
          </div>
        </div>
        <div className="text-right">
          <div className="text-2xl font-bold tabular-nums" style={{ color: hasData ? color : 'var(--text-muted)' }}>
            {hasData ? q.price : '--'}
          </div>
          <div className="text-sm font-semibold mt-0.5" style={{ color: hasData ? color : 'var(--text-muted)' }}>
            {hasData ? `${(q.change_pct ?? 0) >= 0 ? '+' : ''}${q.change_pct?.toFixed(2)}%` : '--'}
          </div>
        </div>
      </div>
      <div className="grid grid-cols-3 gap-y-2 gap-x-3">
        {[
          ['开盘', hasData && q.open ? q.open.toFixed(2) : '--'],
          ['最高', hasData && q.high ? q.high.toFixed(2) : '--'],
          ['最低', hasData && q.low ? q.low.toFixed(2) : '--'],
          ['昨收', hasData && q.prev_close ? q.prev_close.toFixed(2) : '--'],
          ['成交额', hasData && q.amount ? (q.amount / 1e8).toFixed(2) + '亿' : '--'],
          ['成交量', hasData && q.volume ? (q.volume / 1e4).toFixed(0) + '万' : '--'],
        ].map(([l, v]) => (
          <div key={String(l)}>
            <div className="text-xs" style={{ color: 'var(--text-muted)' }}>{l}</div>
            <div className="text-sm font-medium mt-0.5" style={{ color: 'var(--text)' }}>{v}</div>
          </div>
        ))}
      </div>
    </Link>
  )
}

export default function HomePage() {
  const [quotes, setQuotes] = useState<Quote[]>([])
  const [lastUpdate, setLastUpdate] = useState('')
  const [loading, setLoading] = useState(false)

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const token = typeof window !== 'undefined' ? localStorage.getItem('hunter_token') : ''
      const headers: Record<string, string> = {}
      if (token) headers['Authorization'] = `Bearer ${token}`
      const r = await fetch(`${API}/api/quotes`, { headers })
      const d = await r.json()
      setQuotes(d.quotes || [])
      setLastUpdate(new Date().toLocaleTimeString('zh-CN', { timeZone: 'Asia/Shanghai' }))
    } catch {}
    setLoading(false)
  }, [])

  useEffect(() => {
    fetchData()
    const t = setInterval(fetchData, AUTO_REFRESH_MS)
    return () => clearInterval(t)
  }, [fetchData])

  const up = quotes.filter(q => (q.change_pct ?? 0) > 0 && q.price != null).length
  const down = quotes.filter(q => (q.change_pct ?? 0) < 0 && q.price != null).length

  return (
    <div className="flex h-screen overflow-hidden" style={{ background: 'var(--bg)' }}>
      <Sidebar />
      <main className="flex-1 overflow-y-auto p-6 ml-52">
        {/* 顶部 hero · AI 对话入口 · 引流 /chat */}
        <ChatHero />

        <div className="flex justify-between items-center mb-5">
          <div>
            <h1 className="text-xl font-bold" style={{ color: 'var(--text)' }}>自选股总览</h1>
            <div className="text-sm mt-1" style={{ color: 'var(--text-muted)' }}>
              <span style={{ color: 'var(--red)' }}>涨 {up}</span>
              <span className="mx-2">·</span>
              <span style={{ color: 'var(--green)' }}>跌 {down}</span>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {lastUpdate && (
              <span className="text-sm" style={{ color: 'var(--text-muted)' }}>
                上次更新 {lastUpdate}
              </span>
            )}
            <button
              onClick={fetchData}
              disabled={loading}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-all"
              style={{
                background: 'var(--blue)', color: '#fff',
                opacity: loading ? 0.6 : 1,
              }}
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
              刷新
            </button>
          </div>
        </div>
        <div className="grid grid-cols-2 xl:grid-cols-3 gap-4">
          {quotes.length === 0
            ? [...Array(6)].map((_, i) => (
                <div key={i} className="rounded-xl h-44 animate-pulse"
                  style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }} />
              ))
            : quotes.map(q => <QuoteCard key={q.code} q={q} />)
          }
        </div>
      </main>
    </div>
  )
}
