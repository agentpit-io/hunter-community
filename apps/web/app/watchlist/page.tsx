'use client'
// /watchlist · Claude 风改造 · 与 /(chat) 主页一致
// 顶部 TopNav + HUNTER 铜色主题 + 卡片网格 · 去除旧 Sidebar 依赖
// "+ 添加自选股" 内联进本页,不再靠侧栏
import { useCallback, useEffect, useState } from 'react'
import { Trash2, Loader2, Sparkles, CalendarDays, Hash, Wallet, Plus, X } from 'lucide-react'
import TopNav from '../components/TopNav'
import { HUNTER } from '../lib/hunter-theme'

const API_BASE = process.env.NEXT_PUBLIC_API_URL || ''

function authFetch(url: string, options: RequestInit = {}) {
  const token = typeof window !== 'undefined' ? localStorage.getItem('hunter_token') : ''
  return fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers || {}),
    },
  })
}

type AssetType = 'stock' | 'etf' | 'fund'
type Item = {
  code: string
  name: string
  market: string
  exchange: string
  asset_type: AssetType | string
  shares: number | null
  cost_price: number | null
  buy_date: string | null
  status?: string
}

type Candidate = {
  code: string; name: string; market: string; exchange: string
  asset_type: string; type_name?: string
}

/** 铜色主题下的标签配色 · A股用品牌铜色 · 其他类型走对比色但降饱和 */
function tagOf(it: Pick<Item, 'asset_type' | 'market'>): { label: string; bg: string; fg: string } {
  if (it.asset_type === 'fund') return { label: '基金', bg: '#F1EAF6', fg: '#6B3B93' }
  if (it.asset_type === 'etf') return { label: 'ETF', bg: '#E7F1EE', fg: '#20614D' }
  if (it.market === 'HK') return { label: 'HK', bg: '#F6EEE0', fg: '#8A5A18' }
  if (it.market === 'US') return { label: 'US', bg: '#EAF1E7', fg: '#2E6B38' }
  return { label: 'A股', bg: HUNTER.BRAND_PALE, fg: HUNTER.COPPER3 }
}

export default function WatchlistManagePage() {
  const [items, setItems] = useState<Item[]>([])
  const [loading, setLoading] = useState(true)
  const [confirmDel, setConfirmDel] = useState<Item | null>(null)
  const [showAdd, setShowAdd] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await authFetch(`${API_BASE}/api/watchlist/manage`)
      const d = await r.json()
      setItems(d.items || [])
    } catch {}
    setLoading(false)
  }, [])
  useEffect(() => { load() }, [load])

  const handleHardDelete = async () => {
    if (!confirmDel) return
    await authFetch(`${API_BASE}/api/watchlist/${confirmDel.code}/hard`, { method: 'DELETE' })
    setConfirmDel(null)
    load()
  }

  return (
    <div style={{ minHeight: '100vh', background: HUNTER.PAPER, fontFamily: HUNTER.SANS }}>
      <TopNav active="watchlist" />

      <div style={{ maxWidth: 1200, margin: '0 auto', padding: '32px 40px 48px' }}>
        {/* 标题条 · 铜色下划线 + 描述 + 右侧 "+ 添加自选股" CTA */}
        <div style={{
          display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between',
          gap: 16, paddingBottom: 20, borderBottom: `1px solid ${HUNTER.LINE}`,
        }}>
          <div>
            <h1 style={{
              fontFamily: HUNTER.SERIF, fontSize: 28, fontWeight: 700,
              letterSpacing: '.02em', color: HUNTER.INK, margin: 0,
            }}>
              自选股管理
            </h1>
            <p style={{ margin: '8px 0 0', fontSize: 13, color: HUNTER.INK_F }}>
              管理你的自选股列表 · 价格提醒请在个股 K 线或分时页面设置
            </p>
          </div>
          <button
            type="button"
            onClick={() => setShowAdd(true)}
            style={{
              display: 'inline-flex', alignItems: 'center', gap: 6,
              height: 36, padding: '0 16px',
              background: HUNTER.THEME, color: '#fff',
              border: 'none', borderRadius: HUNTER.R_MD,
              fontSize: 13, fontWeight: 600, cursor: 'pointer',
              fontFamily: 'inherit', boxShadow: HUNTER.SHADOW_BRAND,
              transition: 'transform .08s',
            }}
            onMouseDown={(e) => (e.currentTarget.style.transform = 'scale(0.98)')}
            onMouseUp={(e) => (e.currentTarget.style.transform = 'scale(1)')}
            onMouseLeave={(e) => (e.currentTarget.style.transform = 'scale(1)')}
          >
            <Plus size={14} /> 添加自选股
          </button>
        </div>

        {/* 内容区 */}
        <div style={{ marginTop: 28 }}>
          {loading ? (
            <div style={{
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
              padding: '80px 0', color: HUNTER.INK_F, fontSize: 13,
            }}>
              <Loader2 size={16} className="animate-spin" /> 加载中...
            </div>
          ) : items.length === 0 ? (
            <EmptyState onAdd={() => setShowAdd(true)} />
          ) : (
            <div style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))',
              gap: 16,
            }}>
              {items.map((it) => (
                <StockCard key={`${it.market}-${it.code}`} item={it} onDelete={() => setConfirmDel(it)} />
              ))}
            </div>
          )}
        </div>
      </div>

      {showAdd && <AddStockModal onClose={() => setShowAdd(false)} onAdded={load} />}

      {confirmDel && (
        <ConfirmModal
          title="删除自选股?"
          message={`删除 ${confirmDel.name} (${confirmDel.code}) 后将不再追踪行情,此操作不可撤销。`}
          danger="删除"
          onCancel={() => setConfirmDel(null)}
          onConfirm={handleHardDelete}
        />
      )}
    </div>
  )
}

function StockCard({ item, onDelete }: { item: Item; onDelete: () => void }) {
  const tag = tagOf(item)
  const [hover, setHover] = useState(false)
  return (
    <div
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        background: '#fff',
        border: `1px solid ${hover ? HUNTER.LINE_STRONG : HUNTER.LINE}`,
        borderRadius: HUNTER.R_LG,
        padding: 20,
        boxShadow: hover ? HUNTER.SHADOW : '0 1px 2px rgba(38,31,25,.03)',
        transition: 'box-shadow .15s, border-color .15s',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
          <span style={{
            display: 'inline-flex', padding: '3px 8px', borderRadius: 6,
            fontSize: 11, fontWeight: 600,
            background: tag.bg, color: tag.fg,
          }}>
            {tag.label}
          </span>
          <div style={{ minWidth: 0 }}>
            <div style={{
              fontSize: 15, fontWeight: 600, color: HUNTER.INK,
              whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
            }}>
              {item.name}
            </div>
            <div style={{ fontSize: 11.5, color: HUNTER.INK_F, marginTop: 2 }}>{item.code}</div>
          </div>
        </div>
        <button
          type="button"
          onClick={onDelete}
          title="删除自选股"
          style={{
            width: 30, height: 30, flexShrink: 0,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            background: 'transparent', border: `1px solid ${HUNTER.LINE}`,
            borderRadius: 8, cursor: 'pointer', color: HUNTER.INK_F,
            transition: 'background .1s, color .1s, border-color .1s',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = '#FBEDEA'
            e.currentTarget.style.color = HUNTER.UP
            e.currentTarget.style.borderColor = '#E6C0BA'
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = 'transparent'
            e.currentTarget.style.color = HUNTER.INK_F
            e.currentTarget.style.borderColor = HUNTER.LINE
          }}
        >
          <Trash2 size={13} />
        </button>
      </div>

      {(item.shares || item.cost_price || item.buy_date) && (
        <div style={{
          display: 'flex', flexWrap: 'wrap', gap: 14,
          fontSize: 12, color: HUNTER.INK_F,
          paddingTop: 12, borderTop: `1px dashed ${HUNTER.LINE}`,
        }}>
          {item.shares != null && (
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
              <Hash size={12} /> 持仓 {item.shares} 股
            </span>
          )}
          {item.cost_price != null && (
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
              <Wallet size={12} /> 均价 ¥{item.cost_price}
            </span>
          )}
          {item.buy_date && (
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
              <CalendarDays size={12} /> {item.buy_date}
            </span>
          )}
        </div>
      )}
    </div>
  )
}

function EmptyState({ onAdd }: { onAdd: () => void }) {
  return (
    <div style={{ textAlign: 'center', padding: '80px 0' }}>
      <div style={{
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        width: 64, height: 64, borderRadius: 20, marginBottom: 18,
        background: HUNTER.BRAND_PALE, color: HUNTER.THEME,
      }}>
        <Sparkles size={26} />
      </div>
      <div style={{
        fontFamily: HUNTER.SERIF, fontSize: 18, fontWeight: 700,
        color: HUNTER.INK, marginBottom: 8,
      }}>
        还没有自选股
      </div>
      <p style={{ fontSize: 13, color: HUNTER.INK_F, margin: '0 0 20px' }}>
        点右上角 <span style={{ color: HUNTER.COPPER3, fontWeight: 600 }}>"+ 添加自选股"</span> 加几只股票 / ETF / 基金开始追踪
      </p>
      <button
        type="button"
        onClick={onAdd}
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 6,
          height: 36, padding: '0 18px',
          background: HUNTER.THEME, color: '#fff',
          border: 'none', borderRadius: HUNTER.R_MD,
          fontSize: 13, fontWeight: 600, cursor: 'pointer',
          fontFamily: 'inherit', boxShadow: HUNTER.SHADOW_BRAND,
        }}
      >
        <Plus size={14} /> 添加自选股
      </button>
    </div>
  )
}

/** 搜索式添加弹窗 · 复用 /api/watchlist/search + POST /api/watchlist
 *  逻辑同旧 Sidebar 里的 modal · 抽到本页减少 Sidebar 依赖 */
function AddStockModal({ onClose, onAdded }: { onClose: () => void; onAdded: () => void }) {
  const [query, setQuery] = useState('')
  const [candidates, setCandidates] = useState<Candidate[]>([])
  const [searching, setSearching] = useState(false)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    const q = query.trim()
    if (!q) { setCandidates([]); setSearching(false); return }
    setSearching(true)
    const t = setTimeout(async () => {
      try {
        const r = await authFetch(`${API_BASE}/api/watchlist/search?q=${encodeURIComponent(q)}&limit=8`)
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
      const r = await authFetch(`${API_BASE}/api/watchlist`, {
        method: 'POST',
        body: JSON.stringify({
          code: it.code, name: it.name,
          market: it.market, exchange: it.exchange,
          asset_type: it.asset_type,
        }),
      })
      if (!r.ok) { const d = await r.json(); throw new Error(d.detail || '添加失败') }
      onAdded()
      onClose()
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : '添加失败')
    } finally { setSaving(false) }
  }

  return (
    <div
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
      style={{
        position: 'fixed', inset: 0, zIndex: 60,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: 'rgba(0,0,0,0.4)', padding: 16,
      }}
    >
      <div style={{
        width: '100%', maxWidth: 420,
        background: '#fff', border: `1px solid ${HUNTER.LINE}`,
        borderRadius: HUNTER.R_LG, boxShadow: '0 20px 60px rgba(30,20,10,.24)',
        padding: 20, fontFamily: HUNTER.SANS,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
          <h2 style={{
            fontFamily: HUNTER.SERIF, fontSize: 16, fontWeight: 700,
            color: HUNTER.INK, margin: 0,
          }}>
            添加自选股
          </h2>
          <button
            type="button"
            onClick={onClose}
            style={{
              width: 26, height: 26, background: 'transparent', border: 'none',
              cursor: 'pointer', color: HUNTER.INK_F, borderRadius: 6,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}
            onMouseEnter={(e) => (e.currentTarget.style.background = HUNTER.PANEL_2)}
            onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
          >
            <X size={14} />
          </button>
        </div>

        <input
          autoFocus
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="股票名称或代码,如 平安银行 / 000001 / AAPL"
          style={{
            width: '100%', padding: '9px 12px',
            background: HUNTER.PAPER, color: HUNTER.INK,
            border: `1px solid ${HUNTER.LINE}`, borderRadius: HUNTER.R_MD,
            fontSize: 13, outline: 'none', fontFamily: 'inherit',
          }}
        />

        <div style={{ marginTop: 8, maxHeight: 320, overflowY: 'auto' }}>
          {searching && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '12px 4px', fontSize: 12, color: HUNTER.INK_F }}>
              <Loader2 size={13} className="animate-spin" /> 搜索中...
            </div>
          )}
          {!searching && query.trim() && candidates.length === 0 && (
            <p style={{ padding: '12px 4px', fontSize: 12, color: HUNTER.INK_F, margin: 0 }}>
              没找到匹配 &quot;{query.trim()}&quot;,试试完整名字或代码
            </p>
          )}
          {!query.trim() && (
            <p style={{ padding: '12px 4px', fontSize: 12, color: HUNTER.INK_F, margin: 0 }}>
              输入名字或代码,自动搜索并添加
            </p>
          )}
          {candidates.map((it) => {
            const tag = tagOf(it)
            return (
              <button
                key={`${it.market}-${it.code}`}
                type="button"
                onClick={() => handlePick(it)}
                disabled={saving}
                style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  width: '100%', padding: '9px 12px', marginBottom: 6,
                  background: 'transparent', border: `1px solid ${HUNTER.LINE}`,
                  borderRadius: HUNTER.R_MD, textAlign: 'left', cursor: saving ? 'wait' : 'pointer',
                  opacity: saving ? 0.4 : 1, fontFamily: 'inherit',
                  transition: 'background .1s, border-color .1s',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = HUNTER.BRAND_PALE
                  e.currentTarget.style.borderColor = HUNTER.LINE_STRONG
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = 'transparent'
                  e.currentTarget.style.borderColor = HUNTER.LINE
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
                  <span style={{
                    display: 'inline-flex', padding: '2px 6px', borderRadius: 5,
                    fontSize: 10.5, fontWeight: 600,
                    background: tag.bg, color: tag.fg,
                  }}>
                    {tag.label}
                  </span>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 13, color: HUNTER.INK, fontWeight: 500 }}>{it.name}</div>
                    <div style={{ fontSize: 11, color: HUNTER.INK_F, marginTop: 1 }}>{it.code} · {it.exchange || it.market}</div>
                  </div>
                </div>
              </button>
            )
          })}
        </div>

        {err && (
          <div style={{
            marginTop: 8, padding: '8px 10px', borderRadius: HUNTER.R_SM,
            background: '#FBEDEA', color: HUNTER.UP, fontSize: 12,
            border: '1px solid #E6C0BA',
          }}>{err}</div>
        )}
      </div>
    </div>
  )
}

function ConfirmModal({ title, message, danger, onConfirm, onCancel }: {
  title: string; message: string; danger?: string; onConfirm: () => void; onCancel: () => void
}) {
  return (
    <div
      onClick={(e) => { if (e.target === e.currentTarget) onCancel() }}
      style={{
        position: 'fixed', inset: 0, zIndex: 60,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: 'rgba(0,0,0,0.4)', padding: 16,
      }}
    >
      <div style={{
        width: '100%', maxWidth: 360,
        background: '#fff', border: `1px solid ${HUNTER.LINE}`,
        borderRadius: HUNTER.R_LG, boxShadow: '0 20px 60px rgba(30,20,10,.24)',
        padding: 22, fontFamily: HUNTER.SANS,
      }}>
        <div style={{
          fontFamily: HUNTER.SERIF, fontSize: 16, fontWeight: 700,
          color: HUNTER.INK, marginBottom: 10,
        }}>{title}</div>
        <p style={{ fontSize: 13, color: HUNTER.INK_F, lineHeight: 1.6, margin: '0 0 20px' }}>{message}</p>
        <div style={{ display: 'flex', gap: 10 }}>
          <button
            type="button"
            onClick={onCancel}
            style={{
              flex: 1, height: 36, background: '#fff',
              color: HUNTER.INK, border: `1px solid ${HUNTER.LINE}`,
              borderRadius: HUNTER.R_MD, fontSize: 13, cursor: 'pointer',
              fontFamily: 'inherit',
            }}
          >取消</button>
          <button
            type="button"
            onClick={onConfirm}
            style={{
              flex: 1, height: 36, background: HUNTER.UP,
              color: '#fff', border: 'none',
              borderRadius: HUNTER.R_MD, fontSize: 13, fontWeight: 600, cursor: 'pointer',
              fontFamily: 'inherit',
            }}
          >{danger || '确认'}</button>
        </div>
      </div>
    </div>
  )
}
