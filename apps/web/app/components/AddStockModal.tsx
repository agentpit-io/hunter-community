'use client'
import { useEffect, useState } from 'react'
import { Loader2, X } from 'lucide-react'

// 添加自选股共享 modal · Sidebar 和 /watchlist 页面都用它。
// 成功后广播 window CustomEvent('watchlist:changed'),两处组件各自监听刷新。
// 抽出来是因为原来 Sidebar 独占,主页要加按钮时会导致代码 + 状态双份。

type Candidate = {
  code: string
  name: string
  market: string
  exchange: string
  asset_type: string
  type_name?: string
}

function getToken(): string {
  if (typeof window === 'undefined') return ''
  return localStorage.getItem('hunter_token') || ''
}

function authHeaders(): Record<string, string> {
  const token = getToken()
  return token
    ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
    : { 'Content-Type': 'application/json' }
}

export function emitWatchlistChanged() {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent('watchlist:changed'))
}

export default function AddStockModal({ onClose }: { onClose: () => void }) {
  const [query, setQuery] = useState('')
  const [candidates, setCandidates] = useState<Candidate[]>([])
  const [searching, setSearching] = useState(false)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')

  // 搜索:debounce 300ms · 空 query 不调 API · GET /api/watchlist/search
  useEffect(() => {
    const q = query.trim()
    if (!q) { setCandidates([]); setSearching(false); return }
    setSearching(true)
    const t = setTimeout(async () => {
      try {
        const r = await fetch(
          `/api/watchlist/search?q=${encodeURIComponent(q)}&limit=8`,
          { headers: authHeaders() },
        )
        if (!r.ok) throw new Error('搜索失败')
        const d = await r.json()
        setCandidates(Array.isArray(d.items) ? d.items : [])
      } catch { setCandidates([]) }
      finally { setSearching(false) }
    }, 300)
    return () => clearTimeout(t)
  }, [query])

  const handlePick = async (it: Candidate) => {
    if (saving) return
    setSaving(true); setErr('')
    try {
      const r = await fetch('/api/watchlist', {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({
          code: it.code, name: it.name,
          market: it.market, exchange: it.exchange,
          asset_type: it.asset_type,
        }),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d?.detail || '添加失败')
      }
      emitWatchlistChanged()
      onClose()
    } catch (e: any) { setErr(e?.message || '添加失败') }
    finally { setSaving(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="rounded-xl p-5 w-96 shadow-xl"
        style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-semibold text-sm" style={{ color: 'var(--text)' }}>添加自选股</h2>
          <button onClick={onClose} style={{ color: 'var(--text-muted)' }}>
            <X className="w-4 h-4" />
          </button>
        </div>

        <input
          autoFocus
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="股票名称或代码,如 平安银行 / 000001 / AAPL"
          className="w-full px-3 py-2 rounded-lg text-sm border"
          style={{ background: 'var(--bg)', color: 'var(--text)', borderColor: 'var(--border)' }} />

        <div className="mt-2 max-h-72 overflow-y-auto">
          {searching && (
            <div className="flex items-center gap-2 py-3 text-xs" style={{ color: 'var(--text-muted)' }}>
              <Loader2 className="w-3.5 h-3.5 animate-spin" />搜索中...
            </div>
          )}
          {!searching && query.trim() && candidates.length === 0 && (
            <p className="py-3 text-xs" style={{ color: 'var(--text-muted)' }}>
              没找到匹配 "{query.trim()}",试试完整名字或代码
            </p>
          )}
          {!query.trim() && (
            <p className="py-3 text-xs" style={{ color: 'var(--text-muted)' }}>
              输入名字或代码,自动搜索并添加
            </p>
          )}
          {candidates.map(it => {
            const tag = it.asset_type === 'etf' ? { label: 'ETF', bg: 'rgba(20,184,166,0.12)', fg: '#0d9488' }
                      : it.asset_type === 'fund' ? { label: '基金', bg: 'rgba(168,85,247,0.12)', fg: '#a855f7' }
                      : it.market === 'HK' ? { label: 'HK', bg: 'rgba(217,119,6,0.12)', fg: 'var(--yellow)' }
                      : it.market === 'US' ? { label: 'US', bg: 'rgba(22,163,74,0.12)', fg: '#16a34a' }
                      : { label: 'A股', bg: 'rgba(176,106,50,0.12)', fg: 'var(--blue)' }
            return (
              <button
                key={`${it.market}-${it.code}`}
                onClick={() => handlePick(it)}
                disabled={saving}
                className="w-full flex items-center justify-between px-3 py-2 rounded-lg text-left transition-colors mb-1"
                style={{ background: 'transparent', border: '1px solid var(--border)',
                         opacity: saving ? 0.4 : 1, cursor: saving ? 'wait' : 'pointer' }}
                onMouseEnter={e => (e.currentTarget.style.background = 'rgba(176,106,50,0.06)')}
                onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                <div>
                  <div className="text-sm font-medium" style={{ color: 'var(--text)' }}>{it.name}</div>
                  <div className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
                    {it.code}{it.type_name ? ` · ${it.type_name}` : ''}
                  </div>
                </div>
                <span className="text-xs px-2 py-1 rounded font-medium"
                  style={{ background: tag.bg, color: tag.fg }}>
                  {tag.label}
                </span>
              </button>
            )
          })}
        </div>

        {err && (
          <p className="mt-3 text-xs" style={{ color: 'var(--red, #ef4444)' }}>{err}</p>
        )}
      </div>
    </div>
  )
}
