'use client'
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { Shield, History, ChevronRight, Loader2, TrendingUp, TrendingDown, Minus } from 'lucide-react'

const API_BASE = process.env.NEXT_PUBLIC_API_URL || ''

function authHeaders(): Record<string, string> {
  const token = typeof window !== 'undefined' ? localStorage.getItem('hunter_token') || '' : ''
  return token ? { Authorization: `Bearer ${token}` } : {}
}

type ReportSummary = {
  id: number
  stock_code: string
  stock_name: string
  thesis_status: string   // BUY / HOLD / SELL
  confidence: number | null
  created_at: string
  duration_ms: number
}

const T = {
  bg: '#f8fafc', card: '#ffffff', border: '#e2e8f0',
  text: '#0f172a', mute: '#64748b', dim: '#334155',
  blue: '#2563eb', green: '#16a34a', red: '#dc2626', gold: '#b45309',
}

const DECISION_MAP: Record<string, { label: string; color: string; bg: string; Icon: any }> = {
  BUY:  { label: '买入', color: T.green, bg: '#f0fdf4', Icon: TrendingUp   },
  HOLD: { label: '持仓', color: T.gold,  bg: '#fffbeb', Icon: Minus        },
  SELL: { label: '卖出', color: T.red,   bg: '#fef2f2', Icon: TrendingDown },
}

export default function DesktopHistoryPage() {
  const [items,      setItems]      = useState<ReportSummary[]>([])
  const [loading,    setLoading]    = useState(true)
  const [filterCode, setFilterCode] = useState('')

  useEffect(() => {
    const url = `${API_BASE}/api/online-analysis/reports?limit=50` +
                (filterCode ? `&stock_code=${encodeURIComponent(filterCode)}` : '')
    setLoading(true)
    fetch(url, { headers: authHeaders() })
      .then(r => r.json())
      .then(d => setItems(d.items || []))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [filterCode])

  return (
    <div style={{ minHeight: '100vh', background: T.bg, padding: '32px 20px' }}>
      <div style={{ maxWidth: 760, margin: '0 auto' }}>

        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <History size={22} color={T.blue} />
            <span style={{ fontSize: 20, fontWeight: 700, color: T.text }}>分析历史</span>
          </div>
          <Link href="/online-analysis"
            style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '8px 16px',
                     borderRadius: 8, background: T.blue, color: '#fff', fontSize: 13,
                     textDecoration: 'none' }}>
            <Shield size={14} />新建分析
          </Link>
        </div>

        <input value={filterCode} onChange={e => setFilterCode(e.target.value)}
          placeholder="按股票代码过滤（如 600519.SH，留空看全部）"
          style={{ width: '100%', padding: '10px 14px', borderRadius: 8, fontSize: 13,
                   border: `1px solid ${T.border}`, background: T.card, color: T.text,
                   boxSizing: 'border-box', marginBottom: 16, outline: 'none' }} />

        {loading ? (
          <div style={{ display: 'flex', justifyContent: 'center', padding: '60px 0' }}>
            <Loader2 size={24} color={T.blue} style={{ animation: 'spin 1s linear infinite' }} />
          </div>
        ) : items.length === 0 ? (
          <div style={{ padding: '48px 0', textAlign: 'center', background: T.card,
                        borderRadius: 12, border: `1px solid ${T.border}`, color: T.mute }}>
            还没有分析记录。
            <Link href="/online-analysis" style={{ color: T.blue, marginLeft: 4 }}>立即创建第一份</Link>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {items.map(r => {
              const d = DECISION_MAP[r.thesis_status] || DECISION_MAP.HOLD
              const Icon = d.Icon
              const pct = r.confidence !== null ? Math.round(r.confidence * 100) : null
              return (
                <Link key={r.id} href={`/online-analysis/report/${r.id}`}
                  style={{ display: 'block', padding: '14px 18px', borderRadius: 10,
                           background: T.card, border: `1px solid ${T.border}`,
                           textDecoration: 'none' }}
                  onMouseEnter={e => (e.currentTarget.style.boxShadow = '0 2px 8px rgba(0,0,0,0.07)')}
                  onMouseLeave={e => (e.currentTarget.style.boxShadow = 'none')}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 5 }}>
                        <span style={{ fontWeight: 600, fontSize: 15, color: T.text }}>{r.stock_name}</span>
                        <span style={{ fontSize: 12, color: T.mute, fontFamily: 'monospace' }}>{r.stock_code}</span>
                        <span style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '2px 10px',
                                       borderRadius: 5, background: d.bg, fontSize: 12,
                                       color: d.color, fontWeight: 600 }}>
                          <Icon size={11} />{d.label}
                          {pct !== null && ` · ${pct}%`}
                        </span>
                      </div>
                      <div style={{ fontSize: 12, color: T.mute, display: 'flex', gap: 12 }}>
                        <span>{new Date(r.created_at).toLocaleString('zh-CN')}</span>
                        <span>耗时 {(r.duration_ms / 1000).toFixed(1)}s</span>
                      </div>
                    </div>
                    <ChevronRight size={16} color={T.mute} />
                  </div>
                </Link>
              )
            })}
          </div>
        )}

        <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      </div>
    </div>
  )
}
