'use client'
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { Briefcase, Plus, X, Loader2, RefreshCw, Edit2 } from 'lucide-react'

type Position = {
  code: string; name: string; shares: number; cost_price: number
  current_price: number | null; change_pct: number | null
  market_value: number | null; profit_loss: number | null
  profit_loss_pct: number | null; today_profit: number | null
  buy_date: string | null; has_price: boolean
}
type Summary = {
  total_market_value: number; total_cost: number
  total_profit_loss: number; total_profit_pct: number
  total_today_profit: number
}
type NoCost = { code: string; name: string }
type Data = { summary: Summary; positions: Position[]; no_cost_stocks: NoCost[] }
type FormState = { code: string; name: string; shares: string; cost_price: string; buy_date: string }

function getToken() {
  if (typeof window === 'undefined') return ''
  return localStorage.getItem('hunter_token') || ''
}
function authH(): Record<string, string> {
  const t = getToken()
  return t ? { Authorization: `Bearer ${t}`, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' }
}

function fmt(v: number | null, prefix = '') {
  if (v === null || v === undefined) return '--'
  const sign = v >= 0 ? '+' : ''
  return `${prefix}${sign}${v.toFixed(2)}`
}
function fmtMoney(v: number | null) {
  if (v === null || v === undefined) return '--'
  const sign = v >= 0 ? '+' : '-'
  return `${sign}¥${Math.abs(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}
function fmtVal(v: number | null) {
  if (v === null || v === undefined) return '--'
  return `¥${v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}
function pnlColor(v: number | null) {
  if (v === null || v === undefined) return 'var(--text-muted)'
  return v > 0 ? '#22c55e' : v < 0 ? '#ef4444' : 'var(--text-muted)'
}

export default function PortfolioPage() {
  const router = useRouter()
  const [data, setData] = useState<Data | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [modal, setModal] = useState<{ open: boolean; code: string; name: string } | null>(null)
  const [form, setForm] = useState<FormState>({ code: '', name: '', shares: '', cost_price: '', buy_date: '' })
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')

  async function load(quiet = false) {
    if (!quiet) setLoading(true)
    else setRefreshing(true)
    try {
      const r = await fetch('/api/portfolio/summary', { headers: authH() })
      if (r.status === 401) { router.push('/login'); return }
      if (!r.ok) throw new Error('加载失败')
      setData(await r.json())
    } catch {}
    finally { setLoading(false); setRefreshing(false) }
  }

  useEffect(() => { load() }, [])

  function openModal(code: string, name: string, pos?: Position) {
    setErr('')
    setForm({
      code, name,
      shares:     pos?.shares?.toString() ?? '',
      cost_price: pos?.cost_price?.toString() ?? '',
      buy_date:   pos?.buy_date ?? '',
    })
    setModal({ open: true, code, name })
  }

  async function save() {
    if (!form.cost_price || !form.shares) { setErr('买入价格和持股数量必填'); return }
    const cost = parseFloat(form.cost_price)
    const shs  = parseInt(form.shares)
    if (isNaN(cost) || cost <= 0) { setErr('买入价格格式不正确'); return }
    if (isNaN(shs) || shs <= 0)   { setErr('持股数量必须是正整数'); return }

    setSaving(true); setErr('')
    try {
      const r = await fetch(`/api/portfolio/${form.code}/position`, {
        method: 'PUT',
        headers: authH(),
        body: JSON.stringify({ shares: shs, cost_price: cost, buy_date: form.buy_date || null }),
      })
      if (!r.ok) { const d = await r.json(); throw new Error(d.detail || '保存失败') }
      setModal(null)
      load(true)
    } catch (e: any) { setErr(e.message) }
    finally { setSaving(false) }
  }

  async function clearPosition(code: string) {
    if (!confirm('确认清除该股票的持仓数据？买入理由不会删除。')) return
    await fetch(`/api/portfolio/${code}/position`, { method: 'DELETE', headers: authH() })
    load(true)
  }

  if (loading) return (
    <div className="flex-1 flex items-center justify-center" style={{ minHeight: '60vh' }}>
      <Loader2 className="w-6 h-6 animate-spin" style={{ color: 'var(--text-muted)' }} />
    </div>
  )

  const summary = data?.summary
  const positions = data?.positions ?? []
  const noCost = data?.no_cost_stocks ?? []

  return (
    <div className="flex-1 p-6 max-w-5xl mx-auto">
      {/* 页头 */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-2.5">
          <Briefcase className="w-5 h-5" style={{ color: 'var(--blue)' }} />
          <h1 className="text-lg font-bold" style={{ color: 'var(--text)' }}>持仓报告</h1>
          <span className="text-xs px-2 py-0.5 rounded" style={{ background: 'rgba(37,99,235,0.1)', color: 'var(--blue)' }}>
            实时盈亏
          </span>
        </div>
        <button onClick={() => load(true)} disabled={refreshing}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm transition-colors"
          style={{ color: 'var(--text-muted)', border: '1px solid var(--border)' }}>
          <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin' : ''}`} />
          刷新
        </button>
      </div>

      {/* 总览卡片 */}
      {summary && (
        <div className="grid grid-cols-3 gap-4 mb-6">
          <div className="rounded-xl p-4" style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
            <div className="text-xs mb-1" style={{ color: 'var(--text-muted)' }}>总持仓市值</div>
            <div className="text-xl font-bold" style={{ color: 'var(--text)' }}>
              {fmtVal(summary.total_market_value)}
            </div>
            <div className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>
              成本 {fmtVal(summary.total_cost)}
            </div>
          </div>
          <div className="rounded-xl p-4" style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
            <div className="text-xs mb-1" style={{ color: 'var(--text-muted)' }}>累计盈亏</div>
            <div className="text-xl font-bold" style={{ color: pnlColor(summary.total_profit_loss) }}>
              {fmtMoney(summary.total_profit_loss)}
            </div>
            <div className="text-xs mt-1" style={{ color: pnlColor(summary.total_profit_pct) }}>
              {fmt(summary.total_profit_pct, '')}%
            </div>
          </div>
          <div className="rounded-xl p-4" style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
            <div className="text-xs mb-1" style={{ color: 'var(--text-muted)' }}>今日盈亏</div>
            <div className="text-xl font-bold" style={{ color: pnlColor(summary.total_today_profit) }}>
              {fmtMoney(summary.total_today_profit)}
            </div>
            <div className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>
              {positions.length} 只持仓
            </div>
          </div>
        </div>
      )}

      {/* 持仓列表 */}
      {positions.length > 0 && (
        <div className="rounded-xl overflow-hidden mb-6" style={{ border: '1px solid var(--border)' }}>
          <div className="px-4 py-3 flex items-center justify-between" style={{ background: 'var(--bg-card)', borderBottom: '1px solid var(--border)' }}>
            <span className="text-sm font-semibold" style={{ color: 'var(--text)' }}>持仓明细</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr style={{ background: 'var(--bg)', borderBottom: '1px solid var(--border)' }}>
                  {['股票', '成本价', '现价', '今日涨跌', '持股数', '持仓市值', '盈亏金额', '盈亏%', '操作'].map(h => (
                    <th key={h} className="px-4 py-2.5 text-left text-xs font-medium whitespace-nowrap"
                      style={{ color: 'var(--text-muted)' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {positions.map((p, i) => (
                  <tr key={p.code} style={{
                    borderBottom: i < positions.length - 1 ? '1px solid var(--border)' : undefined,
                    background: 'var(--bg-card)'
                  }}>
                    <td className="px-4 py-3">
                      <div className="font-medium" style={{ color: 'var(--text)' }}>{p.name}</div>
                      <div className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>{p.code}</div>
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap" style={{ color: 'var(--text)' }}>
                      ¥{p.cost_price.toFixed(2)}
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap" style={{ color: 'var(--text)' }}>
                      {p.current_price !== null ? `¥${p.current_price.toFixed(2)}` : '--'}
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap font-medium"
                      style={{ color: pnlColor(p.change_pct) }}>
                      {fmt(p.change_pct)}%
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap" style={{ color: 'var(--text)' }}>
                      {p.shares?.toLocaleString() ?? '--'}
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap" style={{ color: 'var(--text)' }}>
                      {fmtVal(p.market_value)}
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap font-medium"
                      style={{ color: pnlColor(p.profit_loss) }}>
                      {fmtMoney(p.profit_loss)}
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap font-medium"
                      style={{ color: pnlColor(p.profit_loss_pct) }}>
                      {fmt(p.profit_loss_pct)}%
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap">
                      <div className="flex items-center gap-2">
                        <button onClick={() => openModal(p.code, p.name, p)}
                          className="p-1 rounded transition-colors"
                          style={{ color: 'var(--blue)' }} title="编辑持仓">
                          <Edit2 className="w-3.5 h-3.5" />
                        </button>
                        <button onClick={() => clearPosition(p.code)}
                          className="p-1 rounded transition-colors"
                          style={{ color: '#ef4444' }} title="清除持仓">
                          <X className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 未录入持仓的自选股 */}
      {noCost.length > 0 && (
        <div className="rounded-xl overflow-hidden" style={{ border: '1px solid var(--border)' }}>
          <div className="px-4 py-3" style={{ background: 'var(--bg-card)', borderBottom: '1px solid var(--border)' }}>
            <span className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
              未录入持仓
            </span>
            <span className="ml-2 text-xs" style={{ color: 'var(--text-muted)' }}>
              点击录入成本价后可计算盈亏
            </span>
          </div>
          <div className="p-4 flex flex-wrap gap-2" style={{ background: 'var(--bg-card)' }}>
            {noCost.map(s => (
              <button key={s.code} onClick={() => openModal(s.code, s.name)}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm transition-colors"
                style={{ border: '1px dashed var(--border)', color: 'var(--text-muted)' }}>
                <Plus className="w-3.5 h-3.5" />
                {s.name}
                <span className="text-xs">{s.code}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {positions.length === 0 && noCost.length === 0 && (
        <div className="text-center py-20" style={{ color: 'var(--text-muted)' }}>
          <Briefcase className="w-10 h-10 mx-auto mb-3 opacity-30" />
          <p className="text-sm">暂无自选股，请先添加自选股后录入持仓</p>
        </div>
      )}

      {/* 录入/编辑弹窗 */}
      {modal?.open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
          onClick={e => { if (e.target === e.currentTarget) setModal(null) }}>
          <div className="rounded-xl p-5 w-80 shadow-xl" style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
            <div className="flex items-center justify-between mb-4">
              <div>
                <h2 className="font-semibold text-sm" style={{ color: 'var(--text)' }}>录入持仓</h2>
                <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
                  {modal.name}（{modal.code}）
                </p>
              </div>
              <button onClick={() => setModal(null)} style={{ color: 'var(--text-muted)' }}>
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="space-y-3">
              <div>
                <label className="text-xs font-medium block mb-1" style={{ color: 'var(--text-muted)' }}>
                  买入均价（元）<span style={{ color: '#ef4444' }}>*</span>
                </label>
                <input
                  type="number" step="0.01" placeholder="如 38.50"
                  value={form.cost_price}
                  onChange={e => setForm(f => ({ ...f, cost_price: e.target.value }))}
                  className="w-full px-3 py-2 rounded-lg text-sm border"
                  style={{ background: 'var(--bg)', color: 'var(--text)', borderColor: 'var(--border)' }} />
              </div>
              <div>
                <label className="text-xs font-medium block mb-1" style={{ color: 'var(--text-muted)' }}>
                  持股数量（股）<span style={{ color: '#ef4444' }}>*</span>
                </label>
                <input
                  type="number" step="1" placeholder="如 100"
                  value={form.shares}
                  onChange={e => setForm(f => ({ ...f, shares: e.target.value }))}
                  className="w-full px-3 py-2 rounded-lg text-sm border"
                  style={{ background: 'var(--bg)', color: 'var(--text)', borderColor: 'var(--border)' }} />
              </div>
              <div>
                <label className="text-xs font-medium block mb-1" style={{ color: 'var(--text-muted)' }}>
                  买入日期（可选）
                </label>
                <input
                  type="date"
                  value={form.buy_date}
                  onChange={e => setForm(f => ({ ...f, buy_date: e.target.value }))}
                  className="w-full px-3 py-2 rounded-lg text-sm border"
                  style={{ background: 'var(--bg)', color: 'var(--text)', borderColor: 'var(--border)' }} />
              </div>
              {err && <p className="text-xs" style={{ color: '#ef4444' }}>{err}</p>}
              <button onClick={save} disabled={saving}
                className="w-full py-2 rounded-lg text-sm font-medium flex items-center justify-center gap-1.5 mt-1"
                style={{ background: 'var(--blue)', color: '#fff', opacity: saving ? 0.7 : 1 }}>
                {saving && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                {saving ? '保存中...' : '保存'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
