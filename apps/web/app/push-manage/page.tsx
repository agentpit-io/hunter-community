'use client'
import { useEffect, useState, useCallback } from 'react'
import Sidebar from '../components/Sidebar'
import { Send, RefreshCw, FileText, ChevronRight, PlayCircle, Zap, ExternalLink } from 'lucide-react'

const API = process.env.NEXT_PUBLIC_API_URL || ''

function authFetch(url: string, options: RequestInit = {}) {
  const token = typeof window !== 'undefined' ? localStorage.getItem('hunter_token') : ''
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) headers['Authorization'] = `Bearer ${token}`
  return fetch(url, { ...options, headers: { ...headers, ...(options.headers as Record<string, string> || {}) } })
}

interface PushLog {
  id: number; task_name: string; push_type: string; push_date: string
  executed_at: string; status: string; written_count: number; error_msg: string; content: string
}

interface SignalReport { report_id: number; user_id: string }
interface SignalFire {
  sig_id: number; signal_type: string; label: string; icon: string
  scenario: string; color: string; triggered_at: string | null
  reports: SignalReport[]
}

const CONTENT_LABEL: Record<string, string> = {
  watchlist_morning: '自选股早报',
  close_review: '盘后复盘',
  news_today: '今日新闻速递',
  fundflow_topn: '资金流向 TopN',
  weekly_summary: '周度持仓汇总',
  custom: '自定义推送',
}

const SIGNAL_META: Record<string, { label: string; icon: string; scenarios: string[] }> = {
  cpi:        { label: '美国 CPI',   icon: '📊', scenarios: ['HAWKISH_SHOCK', 'DOVISH_RELIEF', 'IN_LINE'] },
  oil:        { label: '布伦特油价', icon: '🛢️', scenarios: ['OIL_SPIKE', 'OIL_DROP'] },
  fomc:       { label: 'FOMC 决议',  icon: '🏦', scenarios: ['HAWKISH_SHOCK', 'DOVISH_RELIEF'] },
  spacex:     { label: 'SpaceX IPO', icon: '🚀', scenarios: ['STRONG_OPEN', 'BROKEN_IPO'] },
  northbound: { label: '北向资金',   icon: '🧲', scenarios: ['INFLOW', 'OUTFLOW'] },
}

const SCENARIO_COLOR: Record<string, string> = {
  HAWKISH_SHOCK: '#dc2626', DOVISH_RELIEF: '#16a34a', IN_LINE: '#ca8a04',
  OIL_SPIKE: '#dc2626', OIL_DROP: '#16a34a',
  STRONG_OPEN: '#16a34a', BROKEN_IPO: '#dc2626',
  OUTFLOW: '#dc2626', INFLOW: '#16a34a',
}

function fmtTime(iso: string) {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    const y = d.getFullYear()
    const mo = String(d.getMonth() + 1).padStart(2, '0')
    const day = String(d.getDate()).padStart(2, '0')
    const h = String(d.getHours()).padStart(2, '0')
    const mi = String(d.getMinutes()).padStart(2, '0')
    const s = String(d.getSeconds()).padStart(2, '0')
    return `${y}-${mo}-${day} ${h}:${mi}:${s}`
  } catch { return iso }
}

function isAuto(taskName: string) {
  return !taskName.includes('手工') && !taskName.includes('测试')
}

function reportBaseUrl() {
  if (typeof window === 'undefined') return ''
  const host = window.location.hostname
  if (host.includes('localhost') || host.includes('127.0.0.1')) {
    return `${window.location.protocol}//${host}:8000`
  }
  return 'https://hunter.agentpit.io'
}

export default function PushManagePage() {
  const [logs, setLogs] = useState<PushLog[]>([])
  const [signalFires, setSignalFires] = useState<SignalFire[]>([])
  const [loading, setLoading] = useState(false)
  const [broadcastType, setBroadcastType] = useState('watchlist_morning')
  const [operating, setOperating] = useState<number | string | null>(null)
  const [toast, setToast] = useState('')
  // signal test fire state
  const [fireType, setFireType] = useState('oil')
  const [fireScenario, setFireScenario] = useState('OIL_SPIKE')
  const [firing, setFiring] = useState(false)

  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(''), 3500) }

  const loadLogs = useCallback(async () => {
    setLoading(true)
    try {
      const [logsData, sigData] = await Promise.all([
        authFetch(`${API}/api/push/logs`).then(r => r.json()).catch(() => []),
        authFetch(`${API}/api/signals/reports`).then(r => r.json()).catch(() => []),
      ])
      setLogs(Array.isArray(logsData) ? logsData : [])
      setSignalFires(Array.isArray(sigData) ? sigData : [])
    } finally { setLoading(false) }
  }, [])

  useEffect(() => { loadLogs() }, [loadLogs])

  // when signal type changes, reset scenario to first available
  useEffect(() => {
    const scenarios = SIGNAL_META[fireType]?.scenarios ?? []
    if (scenarios.length > 0) setFireScenario(scenarios[0])
  }, [fireType])

  const handleBroadcast = async (contentType: string, adminOnly: boolean, opKey: number | string) => {
    setOperating(opKey)
    try {
      const r = await authFetch(`${API}/api/push/broadcast-now`, {
        method: 'POST',
        body: JSON.stringify({ content_type: contentType, admin_only: adminOnly }),
      }).then(x => x.json())
      showToast(r.ok ? `✅ ${r.note}` : '❌ ' + (r.detail || r.error || '失败'))
      if (r.ok) setTimeout(loadLogs, 1500)
    } finally { setOperating(null) }
  }

  const handleFireSignal = async () => {
    setFiring(true)
    try {
      const r = await authFetch(`${API}/api/signals/fire`, {
        method: 'POST',
        body: JSON.stringify({ signal_type: fireType, scenario: fireScenario, test_only: true }),
      }).then(x => x.json())
      showToast(r.ok ? `✅ ${r.message}` : '❌ ' + (r.detail || r.error || '触发失败'))
      if (r.ok) setTimeout(loadLogs, 4000)
    } finally { setFiring(false) }
  }

  const detailUrl = (id: number) =>
    `${typeof window !== 'undefined' ? window.location.origin : ''}/push-detail/${id}`

  return (
    <div className="flex min-h-screen" style={{ background: 'var(--bg)' }}>
      <Sidebar />
      <div className="flex-1 ml-52">
        {/* 顶部操作栏 */}
        <div className="px-6 pt-5 pb-3 flex flex-wrap items-center gap-2"
          style={{ borderBottom: '1px solid var(--border)' }}>
          <h1 className="text-base font-bold mr-2" style={{ color: 'var(--text)' }}>推送管理</h1>

          <button onClick={loadLogs} disabled={loading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium"
            style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', color: 'var(--text)' }}>
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            刷新
          </button>

          {/* 手工立即执行（当前账号） */}
          <div className="flex items-center gap-0 rounded-lg overflow-hidden"
            style={{ border: '1px solid var(--blue)' }}>
            <select value={broadcastType} onChange={e => setBroadcastType(e.target.value)}
              className="px-2 py-1.5 text-sm outline-none border-r"
              style={{ background: 'var(--bg-card)', color: 'var(--text)', borderColor: 'var(--blue)' }}>
              {Object.entries(CONTENT_LABEL).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
            <button
              onClick={() => handleBroadcast(broadcastType, false, 'broadcast')}
              disabled={operating === 'broadcast'}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium"
              style={{ background: 'var(--blue)', color: '#fff' }}>
              <Send className="w-3.5 h-3.5" />
              {operating === 'broadcast' ? '推送中...' : '立即执行'}
            </button>
          </div>

          <button
            onClick={() => handleBroadcast(broadcastType, true, 'admin')}
            disabled={operating === 'admin'}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium"
            style={{ background: 'rgba(37,99,235,0.1)', color: 'var(--blue)', border: '1px solid rgba(37,99,235,0.3)' }}>
            <Send className="w-3.5 h-3.5" />
            {operating === 'admin' ? '推送中...' : '仅发给本账号（测试）'}
          </button>
        </div>

        <div className="p-6 space-y-8">

          {/* ── 推送日志表 ── */}
          {logs.length === 0 && !loading ? (
            <div className="rounded-xl py-12 text-center text-sm"
              style={{ background: 'var(--bg-card)', border: '1px dashed var(--border)', color: 'var(--text-muted)' }}>
              暂无推送记录。定时任务执行后会自动回写到此处。
            </div>
          ) : (
            <div className="rounded-xl overflow-x-auto" style={{ border: '1px solid var(--border)' }}>
              <table className="w-full text-sm min-w-[900px]">
                <thead>
                  <tr style={{ background: 'var(--bg-card)', borderBottom: '2px solid var(--border)' }}>
                    {[
                      '任务名称', '计划执行时间', '是否自动任务',
                      '总数/异常', '任务执行状态',
                      '任务开始时间', '任务结束时间',
                      '日志信息', '详情', '手工执行',
                    ].map(h => (
                      <th key={h} className="text-left px-3 py-3 text-xs font-semibold whitespace-nowrap"
                        style={{ color: 'var(--text-muted)' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {logs.map((log, i) => {
                    const auto = isAuto(log.task_name)
                    const ok = log.status === 'ok'
                    const isOp = operating === log.id
                    return (
                      <tr key={log.id}
                        style={{
                          background: i % 2 === 0 ? 'var(--bg-card)' : 'var(--bg)',
                          borderBottom: '1px solid var(--border)',
                        }}>
                        <td className="px-3 py-3 font-medium max-w-[180px]" style={{ color: 'var(--text)' }}>
                          <div className="truncate" title={log.task_name}>{log.task_name || '—'}</div>
                          <div className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
                            {CONTENT_LABEL[log.push_type] || log.push_type}
                          </div>
                        </td>
                        <td className="px-3 py-3 text-xs whitespace-nowrap" style={{ color: 'var(--text-muted)' }}>
                          {fmtTime(log.executed_at)}
                        </td>
                        <td className="px-3 py-3">
                          <span className="text-xs px-2 py-0.5 rounded-full"
                            style={{
                              background: auto ? 'rgba(37,99,235,0.1)' : 'rgba(107,114,128,0.1)',
                              color: auto ? 'var(--blue)' : 'var(--text-muted)',
                            }}>
                            {auto ? '自动执行' : '手工执行'}
                          </span>
                        </td>
                        <td className="px-3 py-3 text-sm font-mono" style={{ color: 'var(--text)' }}>
                          <span style={{ color: ok ? 'inherit' : 'rgb(220,38,38)' }}>
                            {log.written_count}/{ok ? 0 : 1}
                          </span>
                        </td>
                        <td className="px-3 py-3">
                          <span className="text-xs px-2 py-0.5 rounded"
                            style={{
                              background: ok ? 'rgba(34,197,94,0.1)' : 'rgba(220,38,38,0.1)',
                              color: ok ? 'rgb(22,163,74)' : 'rgb(220,38,38)',
                            }}>
                            {ok ? '执行成功' : '执行失败'}
                          </span>
                        </td>
                        <td className="px-3 py-3 text-xs whitespace-nowrap" style={{ color: 'var(--text-muted)' }}>
                          {fmtTime(log.executed_at)}
                        </td>
                        <td className="px-3 py-3 text-xs whitespace-nowrap" style={{ color: 'var(--text-muted)' }}>
                          {fmtTime(log.executed_at)}
                        </td>
                        <td className="px-3 py-3 max-w-[180px]">
                          <div className="text-xs" style={{ color: ok ? 'var(--text-muted)' : 'rgb(220,38,38)' }}>
                            {ok
                              ? `推送成功，写入 ${log.written_count} 条`
                              : (log.error_msg || '执行失败')}
                          </div>
                        </td>
                        <td className="px-3 py-3">
                          <a href={detailUrl(log.id)} target="_blank" rel="noreferrer"
                            className="flex items-center gap-1 text-xs px-2.5 py-1.5 rounded-lg w-fit whitespace-nowrap"
                            style={{ background: 'rgba(37,99,235,0.08)', color: 'var(--blue)' }}>
                            <FileText className="w-3 h-3" />
                            详情
                            <ChevronRight className="w-3 h-3" />
                          </a>
                        </td>
                        <td className="px-3 py-3">
                          <button
                            onClick={() => handleBroadcast(log.push_type, false, log.id)}
                            disabled={isOp}
                            className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap"
                            style={{ background: 'var(--blue)', color: '#fff', opacity: isOp ? 0.6 : 1 }}>
                            <PlayCircle className="w-3.5 h-3.5" />
                            {isOp ? '推送中...' : '立即推送'}
                          </button>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}

          {/* ── 信号报告区 ── */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-bold flex items-center gap-2" style={{ color: 'var(--text)' }}>
                <Zap className="w-4 h-4" style={{ color: 'var(--blue)' }} />
                信号报告
              </h2>

              {/* 测试触发面板 */}
              <div className="flex items-center gap-2">
                <span className="text-xs" style={{ color: 'var(--text-muted)' }}>测试触发（仅推给 admin）：</span>
                <div className="flex items-center gap-0 rounded-lg overflow-hidden"
                  style={{ border: '1px solid var(--blue)' }}>
                  <select value={fireType} onChange={e => setFireType(e.target.value)}
                    className="px-2 py-1.5 text-xs outline-none border-r"
                    style={{ background: 'var(--bg-card)', color: 'var(--text)', borderColor: 'var(--blue)' }}>
                    {Object.entries(SIGNAL_META).map(([k, v]) => (
                      <option key={k} value={k}>{v.icon} {v.label}</option>
                    ))}
                  </select>
                  <select value={fireScenario} onChange={e => setFireScenario(e.target.value)}
                    className="px-2 py-1.5 text-xs outline-none border-r"
                    style={{ background: 'var(--bg-card)', color: 'var(--text)', borderColor: 'var(--blue)' }}>
                    {(SIGNAL_META[fireType]?.scenarios ?? []).map(s => (
                      <option key={s} value={s} style={{ color: SCENARIO_COLOR[s] ?? 'inherit' }}>{s}</option>
                    ))}
                  </select>
                  <button
                    onClick={handleFireSignal}
                    disabled={firing}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium"
                    style={{ background: 'var(--blue)', color: '#fff', opacity: firing ? 0.6 : 1 }}>
                    <Zap className="w-3 h-3" />
                    {firing ? '触发中...' : '触发'}
                  </button>
                </div>
              </div>
            </div>

            {signalFires.length === 0 ? (
              <div className="rounded-xl py-10 text-center text-sm"
                style={{ background: 'var(--bg-card)', border: '1px dashed var(--border)', color: 'var(--text-muted)' }}>
                暂无信号触发记录。
              </div>
            ) : (
              <div className="rounded-xl overflow-x-auto" style={{ border: '1px solid var(--border)' }}>
                <table className="w-full text-sm min-w-[700px]">
                  <thead>
                    <tr style={{ background: 'var(--bg-card)', borderBottom: '2px solid var(--border)' }}>
                      {['信号类型', '场景', '触发时间', '已生成报告'].map(h => (
                        <th key={h} className="text-left px-3 py-3 text-xs font-semibold whitespace-nowrap"
                          style={{ color: 'var(--text-muted)' }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {signalFires.map((fire, i) => (
                      <tr key={fire.sig_id}
                        style={{
                          background: i % 2 === 0 ? 'var(--bg-card)' : 'var(--bg)',
                          borderBottom: '1px solid var(--border)',
                        }}>
                        {/* 信号类型 */}
                        <td className="px-3 py-3 font-medium" style={{ color: 'var(--text)' }}>
                          <span className="mr-1">{fire.icon}</span>{fire.label}
                          <div className="text-xs mt-0.5 font-mono" style={{ color: 'var(--text-muted)' }}>
                            #{fire.sig_id}
                          </div>
                        </td>

                        {/* 场景 */}
                        <td className="px-3 py-3">
                          <span className="text-xs px-2 py-0.5 rounded font-mono font-semibold"
                            style={{
                              background: (SCENARIO_COLOR[fire.scenario] ?? '#6b7280') + '22',
                              color: SCENARIO_COLOR[fire.scenario] ?? 'var(--text-muted)',
                            }}>
                            {fire.scenario}
                          </span>
                        </td>

                        {/* 触发时间 */}
                        <td className="px-3 py-3 text-xs whitespace-nowrap" style={{ color: 'var(--text-muted)' }}>
                          {fire.triggered_at ? fmtTime(fire.triggered_at) : '—'}
                        </td>

                        {/* 已生成报告 */}
                        <td className="px-3 py-3">
                          {fire.reports.length === 0 ? (
                            <span className="text-xs" style={{ color: 'var(--text-muted)' }}>无报告</span>
                          ) : (
                            <div className="flex flex-wrap gap-1.5">
                              {fire.reports.map(rpt => (
                                <a
                                  key={rpt.report_id}
                                  href={`${reportBaseUrl()}/api/v1/signal/report/${rpt.report_id}`}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="flex items-center gap-1 text-xs px-2 py-0.5 rounded"
                                  style={{ background: 'rgba(37,99,235,0.1)', color: 'var(--blue)' }}>
                                  <ExternalLink className="w-3 h-3" />
                                  报告#{rpt.report_id}
                                </a>
                              ))}
                            </div>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

        </div>
      </div>

      {toast && (
        <div className="fixed bottom-6 right-6 px-4 py-2 rounded-lg text-sm shadow-lg z-50"
          style={{ background: 'var(--text)', color: 'var(--bg)' }}>
          {toast}
        </div>
      )}
    </div>
  )
}
