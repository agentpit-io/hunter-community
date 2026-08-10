'use client'
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { HUNTER, decisionStyle, DecisionType } from '../../lib/hunter-theme'
import { HunterHeader, HunterCard } from '../../lib/hunter-ui'

const API_BASE = process.env.NEXT_PUBLIC_API_URL || ''

function authHeaders(): Record<string, string> {
  const token = typeof window !== 'undefined' ? localStorage.getItem('hunter_token') || '' : ''
  return token ? { Authorization: `Bearer ${token}` } : {}
}

type ReportSummary = {
  id: number
  stock_code: string
  stock_name: string
  thesis_status: DecisionType
  confidence: number | null
  created_at: string
  duration_ms: number
}

export default function MobileHistoryPage() {
  const [items,      setItems]      = useState<ReportSummary[]>([])
  const [loading,    setLoading]    = useState(true)
  const [filterCode, setFilterCode] = useState('')

  useEffect(() => {
    const url = `${API_BASE}/api/online-analysis/reports?limit=50`
      + (filterCode ? `&stock_code=${encodeURIComponent(filterCode)}` : '')
    setLoading(true)
    fetch(url, { headers: authHeaders() })
      .then(r => r.json())
      .then(d => setItems(d.items || []))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [filterCode])

  return (
    <div style={{ minHeight: '100vh', background: HUNTER.BG, fontFamily: HUNTER.SANS }}>
      <HunterHeader
        sub="历史研判记录"
        right={
          <Link href="/online-analysis" style={{
            display: 'inline-flex', alignItems: 'center', gap: 5, padding: '7px 12px',
            borderRadius: 8, fontSize: 12, color: HUNTER.COPPER2,
            border: `1px solid ${HUNTER.COPPER2}`, textDecoration: 'none',
            fontFamily: HUNTER.SERIF, fontWeight: 600,
          }}>
            🛡 新建
          </Link>
        } />

      <div style={{ padding: '14px 14px 40px', maxWidth: 520, margin: '0 auto' }}>
        <input
          value={filterCode}
          onChange={e => setFilterCode(e.target.value)}
          placeholder="按股票代码过滤（如 600519，留空看全部）"
          style={{
            width: '100%', padding: '10px 13px', borderRadius: HUNTER.R_MD, fontSize: 13,
            border: `1.5px solid ${HUNTER.LINE}`, background: '#fff', color: HUNTER.INK,
            boxSizing: 'border-box', marginBottom: 14, outline: 'none',
          }} />

        {loading ? (
          <div style={{ padding: '48px 0', textAlign: 'center', color: HUNTER.INK_F, fontSize: 13 }}>
            加载中…
          </div>
        ) : items.length === 0 ? (
          <HunterCard style={{ padding: '36px 20px', textAlign: 'center' }}>
            <div style={{ fontSize: 28, marginBottom: 8 }}>📜</div>
            <div style={{ fontSize: 14, color: HUNTER.INK_S }}>
              还没有研判记录。
              <Link href="/online-analysis" style={{ color: HUNTER.THEME, marginLeft: 4, fontWeight: 600 }}>
                立即创建第一份 →
              </Link>
            </div>
          </HunterCard>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {items.map(r => {
              const cfg = decisionStyle(r.thesis_status || 'HOLD')
              const pct = r.confidence !== null ? Math.round(r.confidence * 100) : null
              return (
                <Link key={r.id} href={`/online-analysis/report/${r.id}`}
                  style={{
                    display: 'block', padding: '13px 15px', borderRadius: HUNTER.R_LG,
                    background: HUNTER.PAPER, border: `1px solid ${HUNTER.LINE}`,
                    borderLeft: `4px solid ${cfg.color}`, textDecoration: 'none',
                  }}>
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 5, flexWrap: 'wrap' }}>
                    <span style={{ fontFamily: HUNTER.SERIF, fontWeight: 700, fontSize: 15, color: HUNTER.INK }}>
                      {r.stock_name}
                    </span>
                    <span style={{ fontSize: 12, color: HUNTER.INK_F, fontFamily: 'monospace' }}>
                      {r.stock_code}
                    </span>
                    <span style={{
                      padding: '2px 10px', borderRadius: 5, fontSize: 12, fontWeight: 600,
                      background: cfg.bg, border: `1px solid ${cfg.border}`, color: cfg.color,
                    }}>
                      {cfg.label}{pct !== null && ` · ${pct}%`}
                    </span>
                  </div>
                  <div style={{ fontSize: 11, color: HUNTER.INK_F, display: 'flex', gap: 12 }}>
                    <span>{new Date(r.created_at).toLocaleString('zh-CN')}</span>
                    <span>耗时 {(r.duration_ms / 1000).toFixed(1)}s</span>
                  </div>
                </Link>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
