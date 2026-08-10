'use client'
import { useEffect, useState } from 'react'

type Signal = {
  signal_type: string
  label: string
  icon: string
  desc: string
  scenario: string | null
  triggered_at: string | null
  data: Record<string, any> | null
  color: 'red' | 'green' | 'yellow' | 'gray'
  active: boolean
}

type HistoryItem = {
  id: number
  signal_type: string
  label: string
  icon: string
  scenario: string
  triggered_at: string
  color: string
}

type Subscriptions = Record<string, boolean>

const COLOR: Record<string, { bg: string; text: string; dot: string }> = {
  red:    { bg: 'rgba(239,68,68,0.1)',    text: '#ef4444', dot: '#ef4444' },
  green:  { bg: 'rgba(34,197,94,0.1)',    text: '#22c55e', dot: '#22c55e' },
  yellow: { bg: 'rgba(234,179,8,0.1)',    text: '#eab308', dot: '#eab308' },
  gray:   { bg: 'rgba(148,163,184,0.08)', text: 'var(--text-muted)', dot: '#94a3b8' },
}

const SCENARIO_ZH: Record<string, string> = {
  HAWKISH_SHOCK: '通胀超预期 · 加息叙事',
  DOVISH_RELIEF: '通胀降温 · 风险偏好修复',
  IN_LINE:       '符合预期 · 影响有限',
  OIL_SPIKE:     '油价急涨',
  OIL_DROP:      '油价急跌',
}

function fmtTime(iso: string | null) {
  if (!iso) return '--'
  const d = new Date(iso)
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function authHeaders(): Record<string, string> {
  const token = typeof window !== 'undefined' ? localStorage.getItem('hunter_token') || '' : ''
  return token ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
               : { 'Content-Type': 'application/json' }
}

function SignalCard({ sig, subscribed, onToggle }: {
  sig: Signal
  subscribed: boolean
  onToggle: (type: string, val: boolean) => void
}) {
  const cs = COLOR[sig.color]
  const scenarioZh = sig.scenario ? (SCENARIO_ZH[sig.scenario] || sig.scenario) : '暂无触发记录'

  return (
    <div className="rounded-xl p-4 border" style={{ background: 'var(--bg-card)', borderColor: 'var(--border)', opacity: subscribed ? 1 : 0.5 }}>
      <div className="flex items-start justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-xl">{sig.icon}</span>
          <div>
            <div className="font-semibold text-sm" style={{ color: 'var(--text)' }}>{sig.label}</div>
            <div className="text-xs" style={{ color: 'var(--text-muted)' }}>{sig.desc}</div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1 px-2 py-1 rounded-full text-xs"
            style={{ background: cs.bg, color: cs.text }}>
            <span className="w-1.5 h-1.5 rounded-full" style={{ background: cs.dot }} />
            {sig.active ? '已触发' : '监控中'}
          </div>
          {/* 订阅开关 */}
          <button
            onClick={() => onToggle(sig.signal_type, !subscribed)}
            className="relative w-9 h-5 rounded-full transition-colors"
            style={{ background: subscribed ? 'var(--blue)' : '#94a3b8' }}
            title={subscribed ? '点击关闭监控' : '点击开启监控'}
          >
            <span className="absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all"
              style={{ left: subscribed ? '18px' : '2px' }} />
          </button>
        </div>
      </div>

      <div className="mt-3 text-sm font-medium" style={{ color: sig.active ? cs.text : 'var(--text-muted)' }}>
        {scenarioZh}
      </div>

      {sig.active && sig.data && (
        <div className="mt-2 text-xs rounded-lg px-3 py-2"
          style={{ background: 'var(--bg)', color: 'var(--text-muted)' }}>
          {sig.signal_type === 'cpi' && <>同比 {sig.data.yoy}%  ·  环比 {sig.data.mom}%</>}
          {sig.signal_type === 'oil' && <>当前 ${sig.data.price}  ·  24H {sig.data.change_24h > 0 ? '+' : ''}{sig.data.change_24h}%</>}
        </div>
      )}

      {sig.triggered_at && (
        <div className="mt-2 text-xs" style={{ color: 'var(--text-muted)' }}>
          触发时间：{fmtTime(sig.triggered_at)}
        </div>
      )}
    </div>
  )
}

export default function SignalsPage() {
  const [current, setCurrent] = useState<Signal[]>([])
  const [history, setHistory] = useState<HistoryItem[]>([])
  const [subs, setSubs] = useState<Subscriptions>({})
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const h = authHeaders()
    Promise.all([
      fetch('/api/signals/current', { headers: h }).then(r => r.json()),
      fetch('/api/signals/history?limit=20', { headers: h }).then(r => r.json()),
      fetch('/api/signal-settings', { headers: h }).then(r => r.ok ? r.json() : {}),
    ]).then(([cur, hist, sub]) => {
      setCurrent(Array.isArray(cur) ? cur : [])
      setHistory(Array.isArray(hist) ? hist : [])
      setSubs(typeof sub === 'object' && sub !== null ? (sub as Subscriptions) : {})
    }).finally(() => setLoading(false))
  }, [])

  const handleToggle = async (signalType: string, enabled: boolean) => {
    setSubs(prev => ({ ...prev, [signalType]: enabled }))
    await fetch('/api/signal-settings', {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ signal_type: signalType, enabled }),
    })
  }

  return (
    <main className="flex-1 ml-52 p-6 min-h-screen" style={{ background: 'var(--bg)' }}>
      <div className="max-w-5xl mx-auto">
        <div className="mb-6">
          <h1 className="text-xl font-bold" style={{ color: 'var(--text)' }}>🌐 市场信号看板</h1>
          <p className="text-sm mt-1" style={{ color: 'var(--text-muted)' }}>
            触发时分析你的持仓影响，推送一条聚合消息到微信。右上角开关可关闭单个信号。
          </p>
        </div>

        {loading ? (
          <div className="text-sm" style={{ color: 'var(--text-muted)' }}>加载中...</div>
        ) : (
          <>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 mb-8">
              {current.map(sig => (
                <SignalCard
                  key={sig.signal_type}
                  sig={sig}
                  subscribed={subs[sig.signal_type] !== false}
                  onToggle={handleToggle}
                />
              ))}
            </div>

            <div>
              <h2 className="text-sm font-semibold mb-3" style={{ color: 'var(--text)' }}>历史触发记录</h2>
              {history.length === 0 ? (
                <div className="text-sm rounded-xl p-6 text-center border"
                  style={{ color: 'var(--text-muted)', borderColor: 'var(--border)', background: 'var(--bg-card)' }}>
                  暂无触发记录
                </div>
              ) : (
                <div className="rounded-xl border overflow-hidden" style={{ borderColor: 'var(--border)' }}>
                  <table className="w-full text-sm">
                    <thead>
                      <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg-card)' }}>
                        <th className="text-left px-4 py-2.5 text-xs font-semibold" style={{ color: 'var(--text-muted)' }}>信号</th>
                        <th className="text-left px-4 py-2.5 text-xs font-semibold" style={{ color: 'var(--text-muted)' }}>场景</th>
                        <th className="text-left px-4 py-2.5 text-xs font-semibold" style={{ color: 'var(--text-muted)' }}>触发时间</th>
                      </tr>
                    </thead>
                    <tbody>
                      {history.map(item => {
                        const cs = COLOR[item.color] || COLOR.gray
                        return (
                          <tr key={item.id} style={{ borderBottom: '1px solid var(--border)' }}>
                            <td className="px-4 py-3">
                              <span className="mr-1.5">{item.icon}</span>
                              <span style={{ color: 'var(--text)' }}>{item.label}</span>
                            </td>
                            <td className="px-4 py-3">
                              <span className="text-xs px-2 py-0.5 rounded-full"
                                style={{ background: cs.bg, color: cs.text }}>
                                {SCENARIO_ZH[item.scenario] || item.scenario}
                              </span>
                            </td>
                            <td className="px-4 py-3 text-xs" style={{ color: 'var(--text-muted)' }}>
                              {fmtTime(item.triggered_at)}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </main>
  )
}
