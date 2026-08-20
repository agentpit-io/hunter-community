'use client'
import { useEffect, useState } from 'react'
import { Trash2, Loader2, Sparkles, CalendarDays, Hash, Wallet, Info, Plus } from 'lucide-react'
import Sidebar from '../components/Sidebar'
import AddStockModal, { emitWatchlistChanged } from '../components/AddStockModal'

const API_BASE = process.env.NEXT_PUBLIC_API_URL || ''

function authFetch(url: string, options: RequestInit = {}) {
  const token = typeof window !== 'undefined' ? localStorage.getItem('hunter_token') : ''
  return fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
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

function tagOf(it: Item): { label: string; bg: string; fg: string } {
  if (it.asset_type === 'fund') return { label: '基金', bg: 'rgba(168,85,247,0.12)', fg: '#a855f7' }
  if (it.asset_type === 'etf')  return { label: 'ETF',  bg: 'rgba(20,184,166,0.12)', fg: '#0d9488' }
  if (it.market === 'HK')       return { label: 'HK',   bg: 'rgba(217,119,6,0.12)',  fg: 'var(--yellow)' }
  if (it.market === 'US')       return { label: 'US',   bg: 'rgba(22,163,74,0.12)',  fg: '#16a34a' }
  return { label: 'A股', bg: 'rgba(176,106,50,0.12)', fg: 'var(--blue)' }
}

// Sprint 2 · 分组展示 · 优先按 asset_type=fund 分独立"基金"组,其余按 market 分
// ETF (asset_type=etf, market=A) 归入 A 股组 · 币种为 A 股同一大类
type GroupKey = 'A' | 'HK' | 'US' | 'FUND'
const GROUP_ORDER: Record<GroupKey, number> = { A: 1, HK: 2, US: 3, FUND: 4 }
const GROUP_META: Record<GroupKey, { label: string; bg: string; fg: string }> = {
  A:    { label: 'A 股', bg: 'rgba(176,106,50,0.12)', fg: 'var(--blue)' },
  HK:   { label: '港股', bg: 'rgba(217,119,6,0.12)',  fg: 'var(--yellow)' },
  US:   { label: '美股', bg: 'rgba(22,163,74,0.12)',  fg: '#16a34a' },
  FUND: { label: '基金', bg: 'rgba(168,85,247,0.12)', fg: '#a855f7' },
}

function groupKeyOf(it: Item): GroupKey {
  if (it.asset_type === 'fund') return 'FUND'
  const m = (it.market || 'A').toUpperCase()
  if (m === 'HK' || m === 'US' || m === 'FUND') return m as GroupKey
  return 'A'
}

function groupByMarket(items: Item[]): [GroupKey, Item[]][] {
  const groups = {} as Record<GroupKey, Item[]>
  for (const it of items) {
    const k = groupKeyOf(it)
    ;(groups[k] ||= []).push(it)
  }
  return (Object.entries(groups) as [GroupKey, Item[]][])
    .sort(([a], [b]) => GROUP_ORDER[a] - GROUP_ORDER[b])
}

export default function WatchlistManagePage() {
  const [items, setItems] = useState<Item[]>([])
  const [loading, setLoading] = useState(true)
  const [confirmDel, setConfirmDel] = useState<Item | null>(null)
  const [showAdd, setShowAdd] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const r = await authFetch(`${API_BASE}/api/watchlist/manage`)
      const d = await r.json()
      setItems(d.items || [])
    } catch {}
    setLoading(false)
  }
  useEffect(() => { load() }, [])

  // 跨组件事件:侧栏 modal / 本页 modal / hard-delete 都会 dispatch
  // 'watchlist:changed',本页 reload 保持数据同步。
  useEffect(() => {
    const h = () => load()
    window.addEventListener('watchlist:changed', h)
    return () => window.removeEventListener('watchlist:changed', h)
  }, [])

  const handleHardDelete = async () => {
    if (!confirmDel) return
    await authFetch(`${API_BASE}/api/watchlist/${confirmDel.code}/hard`, { method: 'DELETE' })
    setConfirmDel(null)
    // 广播 · 侧栏(/api/watchlist)同步刷新
    emitWatchlistChanged()
    load()
  }

  return (
    <div className="flex min-h-screen" style={{ background: 'var(--bg)' }}>
      {/* 侧栏含 "+ 添加自选股" 按钮 · 页面下方两处引导文案指向它,
          之前忘渲染 Sidebar,用户看到 ml-52 空白 + "点左侧菜单"却找不到菜单。 */}
      <Sidebar />
      <div className="flex-1 ml-52">
      <div className="px-10 py-8 border-b" style={{ borderColor: 'var(--border)' }}>
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold" style={{ color: 'var(--text)' }}>自选股管理</h1>
            <p className="text-sm mt-1.5" style={{ color: 'var(--text-muted)' }}>
              管理你的自选股列表，价格提醒请在个股 K 线或分时页面设置
            </p>
          </div>
          <button onClick={() => setShowAdd(true)}
            className="shrink-0 inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium transition-colors"
            style={{ background: 'var(--blue)', color: 'white' }}
            title="添加自选股">
            <Plus className="w-4 h-4" />
            <span>添加自选股</span>
          </button>
        </div>
        <div className="mt-4 inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs"
          style={{ background: 'rgba(176,106,50,0.06)', color: 'var(--text-muted)' }}>
          <Info className="w-3.5 h-3.5" style={{ color: 'var(--blue)' }} />
          点击右上角 <span style={{ color: 'var(--blue)', fontWeight: 500 }}>"+ 添加自选股"</span> 或左侧菜单同名按钮均可添加，添加后自动出现在这里与侧栏。
        </div>
      </div>

      <div className="px-10 py-8">
        {loading ? (
          <div className="text-center py-20" style={{ color: 'var(--text-muted)' }}>
            <Loader2 className="w-6 h-6 mx-auto animate-spin mb-3" />加载中...
          </div>
        ) : items.length === 0 ? (
          <EmptyState />
        ) : (
          <>
            <SummaryHeader items={items} />
            {groupByMarket(items).map(([mkt, list]) => (
              <section key={mkt} id={`section-${mkt}`} className="mb-10 scroll-mt-6">
                <div className="flex items-center gap-3 mb-4">
                  <span className="text-xs px-2 py-1 rounded font-medium"
                    style={{ background: GROUP_META[mkt].bg, color: GROUP_META[mkt].fg }}>
                    {GROUP_META[mkt].label}
                  </span>
                  <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
                    {list.length} 只
                  </span>
                </div>
                <div className="grid gap-5" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(380px, 1fr))' }}>
                  {list.map(it => (
                    <StockCard key={it.code} item={it} onDelete={() => setConfirmDel(it)} />
                  ))}
                </div>
              </section>
            ))}
          </>
        )}
      </div>

      {confirmDel && (
        <ConfirmModal
          title="删除自选股？"
          message={`删除 ${confirmDel.name} (${confirmDel.code}) 后将不再追踪行情，此操作不可撤销。`}
          danger="删除"
          onCancel={() => setConfirmDel(null)}
          onConfirm={handleHardDelete} />
      )}
      {showAdd && <AddStockModal onClose={() => setShowAdd(false)} />}
      </div>
    </div>
  )
}

function StockCard({ item, onDelete }: { item: Item; onDelete: () => void }) {
  const tag = tagOf(item)
  return (
    <div className="rounded-2xl p-6 transition hover:shadow-lg"
      style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}>

      <div className="flex items-start justify-between mb-4">
        <div className="flex items-center gap-3">
          <span className="text-xs px-2 py-1 rounded font-medium"
            style={{ background: tag.bg, color: tag.fg }}>
            {tag.label}
          </span>
          <div>
            <div className="text-base font-semibold" style={{ color: 'var(--text)' }}>{item.name}</div>
            <div className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>{item.code}</div>
          </div>
        </div>
        <button onClick={onDelete}
          className="px-3 py-2 rounded-lg text-sm transition"
          style={{ color: 'var(--red)', border: '1px solid var(--border)' }}
          title="删除自选股">
          <Trash2 className="w-3.5 h-3.5" />
        </button>
      </div>

      {(item.shares || item.cost_price || item.buy_date) && (
        <div className="flex flex-wrap gap-4 text-xs" style={{ color: 'var(--text-muted)' }}>
          {item.shares != null && (
            <span className="flex items-center gap-1"><Hash className="w-3 h-3" />持仓 {item.shares} 股</span>
          )}
          {item.cost_price != null && (
            <span className="flex items-center gap-1"><Wallet className="w-3 h-3" />均价 ¥{item.cost_price}</span>
          )}
          {item.buy_date && (
            <span className="flex items-center gap-1"><CalendarDays className="w-3 h-3" />{item.buy_date}</span>
          )}
        </div>
      )}
    </div>
  )
}

function SummaryHeader({ items }: { items: Item[] }) {
  const groups = groupByMarket(items)
  return (
    <div className="mb-8 flex flex-wrap items-center gap-3">
      <div className="text-sm font-medium" style={{ color: 'var(--text)' }}>
        合计 {items.length} 只
      </div>
      <div className="flex flex-wrap gap-2">
        {groups.map(([mkt, list]) => {
          const meta = GROUP_META[mkt]
          return (
            <a key={mkt} href={`#section-${mkt}`}
              className="text-xs px-2.5 py-1 rounded-full font-medium transition-opacity hover:opacity-80"
              style={{ background: meta.bg, color: meta.fg }}>
              {meta.label} {list.length}
            </a>
          )
        })}
      </div>
    </div>
  )
}

function EmptyState() {
  return (
    <div className="text-center py-24">
      <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl mb-4"
        style={{ background: 'rgba(176,106,50,0.08)' }}>
        <Sparkles className="w-7 h-7" style={{ color: 'var(--blue)' }} />
      </div>
      <div className="text-base font-semibold mb-2" style={{ color: 'var(--text)' }}>还没有自选股</div>
      <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
        点右上角 <span style={{ color: 'var(--blue)', fontWeight: 500 }}>"+ 添加自选股"</span>（或左侧菜单同名按钮）加几只股票/ETF/基金
      </p>
    </div>
  )
}

function ConfirmModal({ title, message, danger, onConfirm, onCancel }:
  { title: string; message: string; danger?: string; onConfirm: () => void; onCancel: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={e => { if (e.target === e.currentTarget) onCancel() }}>
      <div className="rounded-2xl p-6 w-full max-w-sm shadow-2xl"
        style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
        <div className="font-semibold text-base mb-3" style={{ color: 'var(--text)' }}>{title}</div>
        <p className="text-sm mb-6 leading-relaxed" style={{ color: 'var(--text-muted)' }}>{message}</p>
        <div className="flex gap-3">
          <button onClick={onCancel}
            className="flex-1 py-2 rounded-lg text-sm"
            style={{ color: 'var(--text)', border: '1px solid var(--border)' }}>取消</button>
          <button onClick={onConfirm}
            className="flex-1 py-2 rounded-lg text-sm font-medium text-white"
            style={{ background: 'var(--red)' }}>{danger || '确认'}</button>
        </div>
      </div>
    </div>
  )
}
