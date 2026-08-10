'use client'

// 用户洞察后台(仅 admin)—— 看用户群体的偏好分布,不是监视个人。
//
// ⚠️ 隐私边界(硬性,改代码前先读):
//   能看  画像设置项 / 浓缩后的记忆 / 会话数量与活跃时间
//   不能看 对话原文 / 具体问了哪只股票的哪句话
//   后端 /api/user-insight/* 也不返回任何 message 内容,前后端一致。

import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Sidebar from '../components/Sidebar'

interface Dist { value: string; label?: string; n: number }
interface Overview {
  profiled_users: number
  users_with_memory: number
  chat_users: number
  risk_style: Dist[]
  horizon: Dist[]
  verbosity: Dist[]
  sectors: Dist[]
  markets: Dist[]
}
interface UserRow {
  user_id: string
  email: string
  nickname: string
  risk_style: string
  risk_label: string
  horizon: string
  horizon_label: string
  sectors: string[]
  markets: string[]
  profile_filled: number
  profile_total: number
  profile_updated: string | null
  condensed_sessions: number
  chat_sessions: number
  last_active: string | null
  top_symbols: string[]
  top_topics: string[]
}

function authHeaders(): Record<string, string> {
  const t = typeof window !== 'undefined' ? localStorage.getItem('hunter_token') || '' : ''
  return t ? { Authorization: `Bearer ${t}` } : {}
}

const fmtTime = (s: string | null) => (s ? s.slice(0, 16).replace('T', ' ') : '—')

export default function UserInsightPage() {
  const router = useRouter()
  const [ov, setOv] = useState<Overview | null>(null)
  const [users, setUsers] = useState<UserRow[]>([])
  const [detail, setDetail] = useState<any>(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    try {
      const [a, b] = await Promise.all([
        fetch('/api/user-insight/overview', { headers: authHeaders(), cache: 'no-store' }),
        fetch('/api/user-insight/users?limit=100', { headers: authHeaders(), cache: 'no-store' }),
      ])
      if (a.status === 401) { router.push('/login'); return }
      if (a.status === 403) { setErr('仅管理员可访问'); return }
      if (!a.ok) throw new Error(`HTTP ${a.status}`)
      setOv(await a.json())
      setUsers((await b.json())?.items ?? [])
    } catch (e: any) {
      setErr(e?.message || '加载失败')
    } finally {
      setLoading(false)
    }
  }, [router])

  useEffect(() => { void load() }, [load])

  const openDetail = async (uid: string) => {
    setDetail({ loading: true, user_id: uid })
    try {
      const r = await fetch(`/api/user-insight/users/${encodeURIComponent(uid)}`, {
        headers: authHeaders(), cache: 'no-store',
      })
      setDetail(r.ok ? await r.json() : { error: '加载失败', user_id: uid })
    } catch {
      setDetail({ error: '网络错误', user_id: uid })
    }
  }

  const Bar = ({ items, title }: { items: Dist[]; title: string }) => {
    const max = Math.max(1, ...items.map((x) => x.n))
    return (
      <div className="rounded-xl border p-4" style={{ borderColor: 'var(--border)' }}>
        <div className="text-sm font-semibold mb-3">{title}</div>
        {items.length === 0 ? (
          <div className="text-xs text-[var(--text-muted)]">暂无数据</div>
        ) : (
          items.map((x) => (
            <div key={x.value} className="flex items-center gap-2 py-1">
              <span className="text-xs w-20 shrink-0 truncate">{x.label || x.value}</span>
              <div className="flex-1 h-2 rounded bg-[var(--bg-subtle)] overflow-hidden">
                <div className="h-full rounded" style={{ width: `${(x.n / max) * 100}%`, background: '#B06A32' }} />
              </div>
              <span className="text-xs w-8 text-right text-[var(--text-muted)]">{x.n}</span>
            </div>
          ))
        )}
      </div>
    )
  }

  return (
    // Sidebar 是 fixed left-0 w-52,内容必须 ml-52,否则整个左侧会被压在侧栏底下
    <div className="min-h-screen">
      <Sidebar />
      <main className="ml-52 p-6">
        <h1 className="text-xl font-bold mb-1">用户画像洞察</h1>
        <p className="text-xs text-[var(--text-muted)] mb-5">
          仅展示偏好设置与系统浓缩的记忆,<b>不含任何对话原文</b> —— 后台用于理解用户群体,不用于查看个人对话。
        </p>

        {err && <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700 mb-4">{err}</div>}
        {loading && <div className="text-sm text-[var(--text-muted)]">加载中…</div>}

        {ov && (
          <>
            <div className="grid grid-cols-3 gap-3 mb-5 max-w-3xl">
              {[
                { l: '已填画像', v: ov.profiled_users },
                { l: '有记忆积累', v: ov.users_with_memory },
                { l: '用过对话', v: ov.chat_users },
              ].map((x) => (
                <div key={x.l} className="rounded-xl border p-4" style={{ borderColor: 'var(--border)' }}>
                  <div className="text-xs text-[var(--text-muted)]">{x.l}</div>
                  <div className="text-2xl font-bold mt-1">{x.v}</div>
                </div>
              ))}
            </div>

            <div className="grid grid-cols-2 gap-3 mb-5 max-w-4xl">
              <Bar title="风险偏好分布" items={ov.risk_style} />
              <Bar title="持有周期分布" items={ov.horizon} />
              <Bar title="关注市场" items={ov.markets} />
              <Bar title="关注行业 Top10" items={ov.sectors} />
            </div>

            <div className="rounded-xl border overflow-hidden" style={{ borderColor: 'var(--border)' }}>
              <div className="px-4 py-3 text-sm font-semibold border-b" style={{ borderColor: 'var(--border)' }}>
                用户列表({users.length})
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead className="text-[var(--text-muted)]">
                    <tr>
                      {['用户', '画像', '风险偏好', '持有周期', '关注行业', '常看股票', '常问', '对话', '最后活跃', ''].map((h) => (
                        <th key={h} className="text-left font-normal px-3 py-2 whitespace-nowrap">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {users.map((u) => (
                      <tr key={u.user_id} className="border-t hover:bg-[var(--bg-subtle)]" style={{ borderColor: 'var(--border)' }}>
                        <td className="px-3 py-2">
                          {/* 显示"是谁" —— cuid 对人没有意义,优先邮箱/昵称 */}
                          <div className="font-medium">{u.email || u.nickname || '(未绑定邮箱)'}</div>
                          <div className="font-mono text-[10px] text-[var(--text-muted)]">{u.user_id.slice(0, 12)}…</div>
                        </td>
                        <td className="px-3 py-2 whitespace-nowrap">
                          {u.profile_filled === 0 ? (
                            <span className="text-[var(--text-muted)]">全跳过</span>
                          ) : (
                            <span style={{ color: '#B06A32' }}>{u.profile_filled}/{u.profile_total}</span>
                          )}
                        </td>
                        <td className="px-3 py-2 whitespace-nowrap">{u.risk_label || '—'}</td>
                        <td className="px-3 py-2 whitespace-nowrap">{u.horizon_label || '—'}</td>
                        <td className="px-3 py-2 max-w-[160px] truncate">{u.sectors.join('、') || '—'}</td>
                        <td className="px-3 py-2 max-w-[160px] truncate">{u.top_symbols.join('、') || '—'}</td>
                        <td className="px-3 py-2 max-w-[120px] truncate">{u.top_topics.join('、') || '—'}</td>
                        <td className="px-3 py-2 whitespace-nowrap">
                          {u.chat_sessions}
                          {u.condensed_sessions > 0 && (
                            <span className="text-[var(--text-muted)]"> · 浓缩{u.condensed_sessions}</span>
                          )}
                        </td>
                        <td className="px-3 py-2 whitespace-nowrap">{fmtTime(u.last_active)}</td>
                        <td className="px-3 py-2">
                          <button onClick={() => openDetail(u.user_id)} className="text-[#B06A32] hover:underline whitespace-nowrap">详情</button>
                        </td>
                      </tr>
                    ))}
                    {users.length === 0 && (
                      <tr><td colSpan={10} className="px-4 py-6 text-center text-[var(--text-muted)]">暂无用户数据</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        )}

        {detail && (
          <div onClick={() => setDetail(null)} className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(20,15,8,.45)' }}>
            <div onClick={(e) => e.stopPropagation()} className="w-full max-w-2xl max-h-[86vh] overflow-y-auto rounded-xl bg-[var(--bg)] border p-5" style={{ borderColor: 'var(--border)' }}>
              <div className="flex items-start mb-4">
                <div className="flex-1">
                  <div className="text-base font-semibold">{detail.email || detail.nickname || '(未绑定邮箱)'}</div>
                  <div className="font-mono text-[11px] text-[var(--text-muted)] mt-0.5">{detail.user_id}</div>
                </div>
                <button onClick={() => setDetail(null)} className="text-xl leading-none text-[var(--text-muted)]">×</button>
              </div>

              {detail.loading && <div className="text-sm text-[var(--text-muted)] py-6 text-center">加载中…</div>}
              {detail.error && <div className="text-sm text-red-600">{detail.error}</div>}

              {detail.profile && <UserDetailBody d={detail} />}
            </div>
          </div>
        )}
      </main>
    </div>
  )
}

function UserDetailBody({ d }: { d: any }) {
  const p = d.profile || {}
  const mem = d.memory || {}
  const syms = mem.mentioned_symbols || []
  const topics = mem.recurring_topics || []
  const positions = mem.stated_positions || []

  const Row = ({ k, v }: { k: string; v: React.ReactNode }) => (
    <div className="flex gap-3 py-1.5 border-b text-xs" style={{ borderColor: 'var(--border)' }}>
      <span className="w-20 shrink-0 text-[var(--text-muted)]">{k}</span>
      <span className="flex-1">{v}</span>
    </div>
  )
  const none = <span className="text-[var(--text-muted)]">未设置</span>

  return (
    <>
      <div className="grid grid-cols-3 gap-2 mb-4">
        {[
          { l: '对话数', v: d.chat_sessions ?? 0 },
          { l: '已浓缩会话', v: d.session_count ?? 0 },
          { l: '最后活跃', v: (d.last_active || '—').slice(5, 16).replace('T', ' ') },
        ].map((x) => (
          <div key={x.l} className="rounded-lg border p-2.5" style={{ borderColor: 'var(--border)' }}>
            <div className="text-[10px] text-[var(--text-muted)]">{x.l}</div>
            <div className="text-sm font-semibold mt-0.5">{x.v}</div>
          </div>
        ))}
      </div>

      <div className="text-xs font-semibold mb-1.5">画像设置</div>
      <div className="mb-4">
        <Row k="风险偏好" v={p.risk_style ? `${p.risk_style}${p.max_drawdown ? ` · 可接受回撤 ${p.max_drawdown}%` : ''}` : none} />
        <Row k="持有周期" v={p.horizon || none} />
        <Row k="关注市场" v={p.markets?.length ? p.markets.join('、') : none} />
        <Row k="关注行业" v={p.sectors?.length ? p.sectors.join('、') : none} />
        <Row k="市值偏好" v={p.cap_pref || none} />
        <Row k="看重次序" v={p.weight_order?.length ? p.weight_order.join(' > ') : none} />
        <Row k="回答详略" v={p.verbosity || none} />
        <Row k="回避" v={p.taboos?.length ? p.taboos.join('、') : none} />
      </div>

      <div className="text-xs font-semibold mb-1.5">系统浓缩的记忆</div>
      <div className="mb-4">
        <Row
          k="常看股票"
          v={syms.length ? (
            <span>
              {syms.slice(0, 10).map((s: any) => (
                <span key={s.code} className="inline-block mr-2">
                  {s.name || s.code}
                  <span className="text-[var(--text-muted)]">
                    ({s.count}次 · 权重{s.weight}{(s.weight ?? 0) >= 3 ? ' ✓已生效' : ' 未达门控'})
                  </span>
                </span>
              ))}
            </span>
          ) : none}
        />
        <Row k="常问话题" v={topics.length ? topics.map((t: any) => `${t.topic}(${t.count})`).join('、') : none} />
        <Row
          k="自述持仓"
          v={positions.length ? (
            <span>
              {positions.slice(0, 8).map((x: any) => x.name || x.symbol).join('、')}
              <span className="text-[var(--text-muted)]"> (用户自述)</span>
            </span>
          ) : none}
        />
      </div>

      {/* 所见即所得:后台显示的就是实际注入给模型的那段 */}
      <div className="text-xs font-semibold mb-1.5">实际注入模型的内容</div>
      {d.system_prompt ? (
        <pre className="text-[11px] whitespace-pre-wrap bg-[var(--bg-subtle)] rounded-lg p-3">{d.system_prompt}</pre>
      ) : (
        <div className="text-xs text-[var(--text-muted)] bg-[var(--bg-subtle)] rounded-lg p-3">
          空 —— 该用户未设置画像,且记忆权重未达门控(≥3),对话不会有任何个性化
        </div>
      )}

      <div className="text-[11px] text-[var(--text-muted)] mt-3">{d.note}</div>
    </>
  )
}
