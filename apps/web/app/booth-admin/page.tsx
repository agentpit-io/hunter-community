'use client'

/**
 * AdventureX 展位后台（仅 ADMIN / 白名单邮箱）
 * - 统计看板：注册 / 一级完成 / 二级完成 / 已核销
 * - 输码核销：工作人员输入用户手机上的核销码（AX-XXXX）
 * - 参与用户列表：按邮箱 / 昵称 / 核销码搜索
 */
import { useCallback, useEffect, useState } from 'react'
import Sidebar from '../components/Sidebar'
import { Gift, Search, RefreshCw, CheckCircle2, Ticket } from 'lucide-react'

function authFetch(url: string, options: RequestInit = {}) {
  const token = typeof window !== 'undefined' ? localStorage.getItem('hunter_token') : ''
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) headers['Authorization'] = `Bearer ${token}`
  return fetch(url, { ...options, headers: { ...headers, ...(options.headers as Record<string, string> || {}) } })
}

interface Stats {
  total: number; registered: number
  level1_done: number; level2_done: number
  level1_redeemed: number; level2_redeemed: number
  conversion_pct: number
}
interface Row {
  openid: string; user_id: string; email: string; nickname: string
  registered_at: string | null
  level1_stock_name: string; level1_done_at: string | null
  level1_code: string; level1_redeemed_at: string | null; level1_redeemed_by: string
  level2_positions: { code: string; name: string; cost_price: number }[]
  level2_done_at: string | null
  level2_code: string; level2_redeemed_at: string | null; level2_redeemed_by: string
  member_months: number
}
interface RedeemResult { ok: boolean; level?: number; already?: boolean; row?: Row; error?: string }

export default function BoothAdminPage() {
  const [stats, setStats] = useState<Stats | null>(null)
  const [rows, setRows] = useState<Row[]>([])
  const [q, setQ] = useState('')
  const [code, setCode] = useState('')
  const [redeemMsg, setRedeemMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [redeemRow, setRedeemRow] = useState<Row | null>(null)
  const [busy, setBusy] = useState(false)
  const [denied, setDenied] = useState(false)

  const load = useCallback(async (query = '') => {
    const [sr, lr] = await Promise.all([
      authFetch('/api/ax/admin/stats'),
      authFetch(`/api/ax/admin/list?q=${encodeURIComponent(query)}`),
    ])
    if (sr.status === 403 || lr.status === 403) { setDenied(true); return }
    const sd = await sr.json().catch(() => null)
    const ld = await lr.json().catch(() => null)
    if (sd?.stats) setStats(sd.stats)
    if (ld?.items) setRows(ld.items)
  }, [])

  useEffect(() => { load() }, [load])

  const doRedeem = async () => {
    if (!code.trim()) return
    setBusy(true); setRedeemMsg(null); setRedeemRow(null)
    try {
      const r = await authFetch('/api/ax/admin/redeem', { method: 'POST', body: JSON.stringify({ code: code.trim() }) })
      const d: RedeemResult = await r.json()
      if (!d.ok) { setRedeemMsg({ ok: false, text: d.error || '核销失败' }); return }
      const levelName = d.level === 1 ? '🎫 体验礼（铜钱钥匙链 + 3 个月 pro 会员）' : '💎 股民礼（高级伴手礼 + 3 个月会员）'
      setRedeemMsg(d.already
        ? { ok: false, text: `⚠️ 该码已核销过 — ${levelName}，请勿重复发放` }
        : { ok: true, text: `✅ 核销成功 — ${levelName}，请发放实物` })
      if (d.row) setRedeemRow(d.row)
      setCode('')
      load(q)
    } finally { setBusy(false) }
  }

  const fmtT = (t: string | null) => t ? t.slice(5, 16) : '—'

  const StatCard = ({ label, value, sub }: { label: string; value: number | string; sub?: string }) => (
    <div className="rounded-xl border px-4 py-3 flex-1 min-w-[120px]"
      style={{ borderColor: 'var(--border)', background: 'var(--bg-card)' }}>
      <div className="text-xs" style={{ color: 'var(--text-muted)' }}>{label}</div>
      <div className="text-2xl font-bold mt-1" style={{ color: 'var(--text)' }}>{value}</div>
      {sub && <div className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>{sub}</div>}
    </div>
  )

  if (denied) return (
    <div className="flex min-h-screen" style={{ background: 'var(--bg)' }}>
      <Sidebar />
      <div className="flex-1 ml-52 flex items-center justify-center">
        <div className="text-sm" style={{ color: 'var(--text-muted)' }}>无展位后台权限（仅管理员可用）</div>
      </div>
    </div>
  )

  return (
    <div className="flex min-h-screen" style={{ background: 'var(--bg)' }}>
      <Sidebar />
      <div className="flex-1 ml-52">
        <div className="px-6 pt-5 pb-3 flex flex-wrap items-center gap-2"
          style={{ borderBottom: '1px solid var(--border)' }}>
          <Gift className="w-5 h-5" style={{ color: 'var(--blue)' }} />
          <h1 className="text-base font-bold mr-2" style={{ color: 'var(--text)' }}>AdventureX 展位后台</h1>
          <button onClick={() => load(q)}
            className="ml-auto flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg border"
            style={{ borderColor: 'var(--border)', color: 'var(--text)' }}>
            <RefreshCw className="w-3.5 h-3.5" /> 刷新
          </button>
        </div>

        <div className="p-6 space-y-5">
          {/* 统计看板 */}
          {stats && (
            <div className="flex flex-wrap gap-3">
              <StatCard label="注册人数" value={stats.registered} />
              <StatCard label="第一级完成（体验礼）" value={stats.level1_done} sub={`已核销 ${stats.level1_redeemed}`} />
              <StatCard label="第二级完成（股民礼）" value={stats.level2_done} sub={`已核销 ${stats.level2_redeemed}`} />
              <StatCard label="一级→二级转化率" value={`${stats.conversion_pct}%`} sub="目标 25–35%" />
            </div>
          )}

          {/* 输码核销 */}
          <div className="rounded-xl border p-5" style={{ borderColor: 'var(--border)', background: 'var(--bg-card)' }}>
            <div className="flex items-center gap-2 mb-3">
              <Ticket className="w-4 h-4" style={{ color: 'var(--blue)' }} />
              <span className="text-sm font-bold" style={{ color: 'var(--text)' }}>输码核销</span>
              <span className="text-xs" style={{ color: 'var(--text-muted)' }}>让用户展示手机上的核销码，输入后回车</span>
            </div>
            <div className="flex gap-2">
              <input value={code} onChange={(e) => setCode(e.target.value.toUpperCase())}
                onKeyDown={(e) => e.key === 'Enter' && doRedeem()}
                placeholder="AX-XXXX"
                className="flex-1 max-w-xs px-4 py-2.5 rounded-lg border font-mono text-lg tracking-widest"
                style={{ borderColor: 'var(--border)', background: 'var(--bg)', color: 'var(--text)' }} />
              <button onClick={doRedeem} disabled={busy || !code.trim()}
                className="px-5 py-2.5 rounded-lg text-sm font-medium text-white disabled:opacity-50"
                style={{ background: 'var(--blue)' }}>
                {busy ? '核销中...' : '核销'}
              </button>
            </div>
            {redeemMsg && (
              <div className="mt-3 text-sm font-medium" style={{ color: redeemMsg.ok ? '#16a34a' : '#dc2626' }}>
                {redeemMsg.text}
              </div>
            )}
            {redeemRow && (
              <div className="mt-2 text-xs leading-relaxed" style={{ color: 'var(--text-muted)' }}>
                用户：{redeemRow.email}{redeemRow.nickname ? `（${redeemRow.nickname}）` : ''}
                {redeemRow.level2_positions?.length > 0 &&
                  ` · 导入持仓：${redeemRow.level2_positions.map(p => p.name).join('、')}`}
                {redeemRow.level1_stock_name && ` · 体检股票：${redeemRow.level1_stock_name}`}
              </div>
            )}
          </div>

          {/* 参与用户列表 */}
          <div className="rounded-xl border" style={{ borderColor: 'var(--border)', background: 'var(--bg-card)' }}>
            <div className="p-4 flex items-center gap-2" style={{ borderBottom: '1px solid var(--border)' }}>
              <Search className="w-4 h-4" style={{ color: 'var(--text-muted)' }} />
              <input value={q} onChange={(e) => setQ(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && load(q)}
                placeholder="按邮箱 / 昵称 / 核销码搜索，回车"
                className="flex-1 bg-transparent outline-none text-sm"
                style={{ color: 'var(--text)' }} />
              <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{rows.length} 人</span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr style={{ color: 'var(--text-muted)' }} className="text-xs text-left">
                    <th className="px-4 py-2.5 font-medium">用户</th>
                    <th className="px-4 py-2.5 font-medium">注册时间</th>
                    <th className="px-4 py-2.5 font-medium">🎫 体验礼</th>
                    <th className="px-4 py-2.5 font-medium">💎 股民礼</th>
                    <th className="px-4 py-2.5 font-medium">持仓</th>
                    <th className="px-4 py-2.5 font-medium">会员</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.openid} style={{ borderTop: '1px solid var(--border)', color: 'var(--text)' }}>
                      <td className="px-4 py-2.5">
                        <div className="font-medium">{r.email || '—'}</div>
                        {r.nickname && <div className="text-xs" style={{ color: 'var(--text-muted)' }}>{r.nickname}</div>}
                      </td>
                      <td className="px-4 py-2.5 text-xs" style={{ color: 'var(--text-muted)' }}>{fmtT(r.registered_at)}</td>
                      <td className="px-4 py-2.5">
                        {r.level1_code ? (
                          <div className="flex items-center gap-1.5">
                            <span className="font-mono text-xs">{r.level1_code}</span>
                            {r.level1_redeemed_at
                              ? <span className="text-xs flex items-center gap-0.5" style={{ color: '#16a34a' }}><CheckCircle2 className="w-3 h-3" />已核销</span>
                              : <span className="text-xs" style={{ color: '#ca8a04' }}>待核销</span>}
                          </div>
                        ) : <span className="text-xs" style={{ color: 'var(--text-muted)' }}>未完成</span>}
                        {r.level1_stock_name && <div className="text-xs" style={{ color: 'var(--text-muted)' }}>{r.level1_stock_name}</div>}
                      </td>
                      <td className="px-4 py-2.5">
                        {r.level2_code ? (
                          <div className="flex items-center gap-1.5">
                            <span className="font-mono text-xs">{r.level2_code}</span>
                            {r.level2_redeemed_at
                              ? <span className="text-xs flex items-center gap-0.5" style={{ color: '#16a34a' }}><CheckCircle2 className="w-3 h-3" />已核销</span>
                              : <span className="text-xs" style={{ color: '#ca8a04' }}>待核销</span>}
                          </div>
                        ) : <span className="text-xs" style={{ color: 'var(--text-muted)' }}>未完成</span>}
                      </td>
                      <td className="px-4 py-2.5 text-xs" style={{ color: 'var(--text-muted)' }}>
                        {r.level2_positions?.length ? r.level2_positions.map(p => p.name).join('、') : '—'}
                      </td>
                      <td className="px-4 py-2.5 text-xs">{r.member_months ? `${r.member_months} 个月` : '—'}</td>
                    </tr>
                  ))}
                  {!rows.length && (
                    <tr><td colSpan={6} className="px-4 py-10 text-center text-sm" style={{ color: 'var(--text-muted)' }}>暂无参与用户</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
