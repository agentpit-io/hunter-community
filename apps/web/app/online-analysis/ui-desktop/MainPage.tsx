'use client'
import { useEffect, useState, useRef } from 'react'
import Link from 'next/link'
import {
  Shield, Loader2, TrendingUp, TrendingDown, Minus,
  AlertTriangle, CheckCircle2, RotateCcw, History,
  Search, Activity, BarChart2, Scale, XCircle,
  ChevronDown, ChevronUp, Sparkles, Plus, Trash2, Edit3,
} from 'lucide-react'

const API_BASE = process.env.NEXT_PUBLIC_API_URL || ''

function authHeaders(): Record<string, string> {
  const token = typeof window !== 'undefined' ? localStorage.getItem('hunter_token') || '' : ''
  return token ? { Authorization: `Bearer ${token}` } : {}
}

type Stock = { code: string; name: string; market: string; exchange: string }
type AgentResult = {
  decision: 'BUY' | 'HOLD' | 'SELL'
  confidence: number
  key_reason: string
  bull_summary: string
  bear_summary: string
  sentinel_summary: string
  market_report: string
  stop_loss: number | null
  investment_plan: string
  sentinel_conflicts: boolean
  report_id?: number
}

const T = {
  bg: '#f8fafc', card: '#ffffff', panel: '#f1f5f9',
  border: '#e2e8f0', borderSm: '#f0f4f8',
  text: '#0f172a', mute: '#64748b', dim: '#334155',
  red: '#dc2626', green: '#16a34a', gold: '#b45309',
  blue: '#2563eb', purple: '#7c3aed', cyan: '#0891b2', amber: '#d97706',
  violet: '#7c3aed',
}

const DECISION_CFG = {
  BUY:  { label: '买入', color: T.green, bg: '#f0fdf4', border: '#86efac', Icon: TrendingUp   },
  HOLD: { label: '持仓', color: T.gold,  bg: '#fffbeb', border: '#fcd34d', Icon: Minus        },
  SELL: { label: '卖出', color: T.red,   bg: '#fef2f2', border: '#fca5a5', Icon: TrendingDown },
}

function CollapseText({ text, maxLines = 6 }: { text: string; maxLines?: number }) {
  const [open, setOpen] = useState(false)
  const lines = text.split('\n').filter(Boolean)
  const visible = open ? lines : lines.slice(0, maxLines)
  return (
    <div>
      <p style={{ color: T.dim, fontSize: 13, lineHeight: 1.75, whiteSpace: 'pre-wrap', margin: 0 }}>
        {visible.join('\n')}
      </p>
      {lines.length > maxLines && (
        <button onClick={() => setOpen(o => !o)} style={{
          marginTop: 8, color: T.blue, fontSize: 12, background: 'none',
          border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4, padding: 0,
        }}>
          {open ? <><ChevronUp size={13} />收起</> : <><ChevronDown size={13} />展开全文</>}
        </button>
      )}
    </div>
  )
}

function Card({ title, icon, children, accent }: {
  title: string; icon: React.ReactNode; children: React.ReactNode; accent?: string
}) {
  return (
    <div style={{
      background: T.card, border: `1px solid ${T.border}`, borderRadius: 12,
      padding: 20, borderTop: accent ? `3px solid ${accent}` : undefined,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 14,
                    color: T.mute, fontSize: 12, fontWeight: 600, textTransform: 'uppercase',
                    letterSpacing: '0.05em' }}>
        {icon}{title}
      </div>
      {children}
    </div>
  )
}

const phases = [
  { key: 'init',   label: '引擎初始化' },
  { key: 'phase1', label: 'Phase 1 · 技术面 + Sentinel 并行' },
  { key: 'phase2', label: 'Phase 2 · Bull / Bear 辩论' },
  { key: 'phase3', label: 'Phase 3 · 综合裁判' },
]

export default function DesktopMainPage() {
  const [stocks,      setStocks]      = useState<Stock[]>([])
  const [scCode,      setScCode]      = useState('')
  const [scName,      setScName]      = useState('')
  const [searchInput, setSearchInput] = useState('')
  const [searchHits,  setSearchHits]  = useState<any[]>([])
  const [searchOpen,  setSearchOpen]  = useState(false)
  const [detecting,   setDetecting]   = useState(false)

  // 看好理由 + 卖出红线
  const [genLoading,      setGenLoading]      = useState(false)
  const [thesis,          setThesis]          = useState('')
  const [killConditions,  setKillConditions]  = useState<string[]>([])
  const [editingKillIdx,  setEditingKillIdx]  = useState<number | null>(null)
  const [newKillText,     setNewKillText]     = useState('')
  const [addingKill,      setAddingKill]      = useState(false)

  const [running,  setRunning]  = useState(false)
  const [loadStep, setLoadStep] = useState('init')
  const [logs,     setLogs]     = useState<string[]>([])
  const [result,   setResult]   = useState<AgentResult | null>(null)
  const [startErr, setStartErr] = useState('')
  const esRef = useRef<EventSource | null>(null)
  const resultRef = useRef<HTMLDivElement>(null)
  const [isMobile, setIsMobile] = useState(false)

  // URL query 预填：见下方 confirmStock 定义之后的 useEffect（避免 TDZ）

  useEffect(() => {
    const check = () => setIsMobile(window.innerWidth < 640)
    check()
    window.addEventListener('resize', check)
    return () => window.removeEventListener('resize', check)
  }, [])

  useEffect(() => {
    fetch(`${API_BASE}/api/watchlist`, { headers: authHeaders() })
      .then(r => r.json())
      .then(d => setStocks(Array.isArray(d) ? d : []))
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (result && resultRef.current) {
      resultRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }, [result])

  // 股票确认后自动生成看好理由 + 卖出红线
  const autoGenThesis = async (code: string, name: string) => {
    setGenLoading(true)
    setThesis('')
    setKillConditions([])
    try {
      const r = await fetch(`${API_BASE}/api/online-analysis/generate-thesis-kills`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ stock_code: code, stock_name: name }),
      })
      const d = await r.json()
      setThesis(d.thesis || '')
      setKillConditions(Array.isArray(d.kill_conditions) ? d.kill_conditions : [])
    } catch {
      setThesis('')
      setKillConditions([])
    } finally {
      setGenLoading(false)
    }
  }

  const confirmStock = (code: string, name: string) => {
    setScCode(code); setScName(name)
    setSearchInput(''); setSearchHits([]); setSearchOpen(false)
    setResult(null); setLogs([])
    autoGenThesis(code, name)
  }

  // URL query 预填（从助手/持仓研判 sub-tab 跳过来带 symbol=xxx&name=xxx）
  // 声明在 confirmStock 之后避免 TDZ；仅首次 mount 触发一次，走完整 confirmStock 触发 autoGenThesis
  useEffect(() => {
    try {
      const usp = new URLSearchParams(window.location.search)
      const sym = usp.get('symbol'), nm = usp.get('name')
      if (sym) confirmStock(sym, nm || sym)
    } catch {}
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleDetect = async () => {
    if (!searchInput.trim()) return
    setDetecting(true); setSearchHits([])
    const r = await fetch(
      `${API_BASE}/api/online-analysis/search-stock?q=${encodeURIComponent(searchInput)}&limit=8`,
      { headers: authHeaders() }
    )
    const d = await r.json()
    const hits = d.items || []
    setDetecting(false)
    if (hits.length === 1) {
      confirmStock(hits[0].code, hits[0].name)
    } else if (hits.length > 1) {
      setSearchHits(hits); setSearchOpen(true)
    } else {
      setStartErr('未找到该股票，请尝试其他关键词')
    }
  }

  const handleStart = async () => {
    if (!scCode || !scName) { setStartErr('请先选择股票'); return }
    esRef.current?.close()
    setStartErr(''); setRunning(true); setResult(null); setLogs([]); setLoadStep('init')
    try {
      const r = await fetch(`${API_BASE}/api/online-analysis/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({
          stock_code: scCode, stock_name: scName,
          thesis: thesis.trim(),
          kill_conditions: killConditions.filter(k => k.trim()),
        }),
      })
      const { task_id } = await r.json()
      const es = new EventSource(`${API_BASE}/api/online-analysis/stream/${task_id}`)
      esRef.current = es
      es.addEventListener('progress', (e: MessageEvent) => {
        const d = JSON.parse(e.data)
        setLoadStep(d.phase || 'phase1')
        if (d.text) setLogs(prev => [...prev, d.text])
      })
      es.addEventListener('complete', (e: MessageEvent) => {
        es.close()
        setResult(JSON.parse(e.data) as AgentResult)
        setRunning(false)
      })
      es.addEventListener('error', () => {
        es.close(); setRunning(false); setStartErr('分析失败，请重试')
      })
      es.onerror = () => {
        if (es.readyState === EventSource.CLOSED) {
          setRunning(false); setStartErr('连接中断，请重试')
        }
      }
    } catch {
      setRunning(false); setStartErr('请求失败，请重试')
    }
  }

  const handleReset = () => {
    esRef.current?.close()
    setResult(null); setRunning(false); setLogs([]); setStartErr('')
  }

  const inputStyle: React.CSSProperties = {
    color: T.text, background: T.card, border: `1px solid ${T.border}`,
    borderRadius: 8, padding: '10px 14px', fontSize: 14, width: '100%',
    outline: 'none', boxSizing: 'border-box',
  }

  const phaseIdx = phases.findIndex(p => loadStep.includes(p.key))
  const showThesisPanel = !!scCode && !running && !result

  return (
    <div style={{ minHeight: '100vh', background: T.bg, padding: '28px 20px' }}>
      <div style={{ maxWidth: 760, margin: '0 auto' }}>

        {/* ── Header ── */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 22 }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
              <Shield size={20} color={T.blue} />
              <span style={{ color: T.text, fontSize: 20, fontWeight: 700 }}>持仓研判</span>
              <span style={{ padding: '2px 8px', background: '#eff6ff', borderRadius: 4,
                             fontSize: 11, color: T.blue, fontWeight: 600 }}>多空辩论</span>
            </div>
            <p style={{ margin: '3px 0 0 29px', color: T.mute, fontSize: 12 }}>
              原「AI 持仓管家」· 帮你判断已持有股票该继续拿还是撤 · Bull vs Bear 辩论
            </p>
          </div>
          <Link href="/online-analysis/history"
            style={{ display: 'flex', alignItems: 'center', gap: 5, padding: '7px 14px',
                     borderRadius: 8, fontSize: 13, color: T.mute, background: T.card,
                     border: `1px solid ${T.border}`, textDecoration: 'none' }}>
            <History size={13} />历史
          </Link>
        </div>

        {/* ── 股票选择 ── */}
        <div style={{ background: T.card, border: `1px solid ${T.border}`, borderRadius: 12,
                      padding: 20, marginBottom: 14,
                      opacity: running ? 0.7 : 1, pointerEvents: running ? 'none' : 'auto' }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: T.mute, textTransform: 'uppercase',
                        letterSpacing: '0.05em', marginBottom: 12 }}>
            选择股票
          </div>

          {stocks.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7, marginBottom: 12 }}>
              {stocks.slice(0, 12).map(s => (
                <button key={s.code} onClick={() => confirmStock(s.code, s.name)}
                  style={{ padding: '4px 12px', borderRadius: 6, fontSize: 12, cursor: 'pointer',
                           background: scCode === s.code ? '#eff6ff' : T.panel,
                           border: `1px solid ${scCode === s.code ? T.blue : T.border}`,
                           color: scCode === s.code ? T.blue : T.mute }}>
                  {s.name}
                </button>
              ))}
            </div>
          )}

          <div style={{ display: 'flex', gap: 8 }}>
            <input value={searchInput} onChange={e => setSearchInput(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleDetect()}
              placeholder="输入股票名称或代码..." style={inputStyle} />
            <button onClick={handleDetect} disabled={detecting}
              style={{ padding: '10px 18px', borderRadius: 8, fontSize: 13, cursor: 'pointer',
                       background: detecting ? '#93c5fd' : T.blue, color: '#fff', border: 'none',
                       whiteSpace: 'nowrap', display: 'flex', alignItems: 'center', gap: 6 }}>
              {detecting
                ? <Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} />
                : <Search size={14} />}
              检测
            </button>
          </div>

          {searchOpen && searchHits.length > 0 && (
            <div style={{ marginTop: 8, background: T.card, border: `1px solid ${T.border}`,
                          borderRadius: 8, overflow: 'hidden', boxShadow: '0 4px 12px rgba(0,0,0,0.08)' }}>
              {searchHits.map((h: any) => (
                <div key={h.code}
                  onClick={() => confirmStock(h.code, h.name)}
                  style={{ padding: '10px 14px', cursor: 'pointer', fontSize: 13,
                           borderBottom: `1px solid ${T.borderSm}`,
                           display: 'flex', justifyContent: 'space-between', color: T.dim }}
                  onMouseEnter={e => (e.currentTarget.style.background = T.panel)}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                  <span>{h.name}</span>
                  <span style={{ color: T.mute, fontFamily: 'monospace' }}>{h.code}</span>
                </div>
              ))}
            </div>
          )}

          {scCode && (
            <div style={{ marginTop: 10, padding: '8px 14px', borderRadius: 8, background: '#eff6ff',
                          border: '1px solid #bfdbfe', display: 'flex', alignItems: 'center',
                          justifyContent: 'space-between' }}>
              <span style={{ color: T.text, fontSize: 13 }}>
                <b>{scName}</b>
                <span style={{ color: T.mute, marginLeft: 8, fontFamily: 'monospace', fontSize: 12 }}>{scCode}</span>
              </span>
              <button onClick={() => { setScCode(''); setScName(''); setResult(null); setLogs([]); setThesis(''); setKillConditions([]) }}
                style={{ background: 'none', border: 'none', cursor: 'pointer', color: T.mute, padding: 0 }}>
                <XCircle size={14} />
              </button>
            </div>
          )}
        </div>

        {/* ── 看好理由 + 卖出红线（股票确认后显示，分析时隐藏） ── */}
        {showThesisPanel && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginBottom: 14 }}>

            {/* 看好理由 */}
            <div style={{ background: T.card, border: `1px solid ${T.border}`, borderRadius: 12, padding: 20 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 7,
                              color: T.mute, fontSize: 12, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                  <TrendingUp size={12} color={T.green} />看好理由
                </div>
                {genLoading && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, color: T.violet }}>
                    <Sparkles size={12} style={{ animation: 'spin 2s linear infinite' }} />
                    AI 生成中…
                  </div>
                )}
              </div>
              {genLoading ? (
                <div style={{ height: 80, background: T.panel, borderRadius: 8,
                              display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <span style={{ color: T.mute, fontSize: 13 }}>正在分析 {scName} 的投资逻辑…</span>
                </div>
              ) : (
                <textarea
                  value={thesis}
                  onChange={e => setThesis(e.target.value)}
                  placeholder={`输入看好 ${scName} 的理由，AI 将据此判断持仓逻辑是否动摇…`}
                  rows={4}
                  style={{ ...inputStyle, resize: 'vertical', lineHeight: 1.7, fontSize: 13 }}
                />
              )}
            </div>

            {/* 卖出红线 */}
            <div style={{ background: T.card, border: `1px solid ${T.border}`, borderRadius: 12, padding: 20 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 7,
                              color: T.mute, fontSize: 12, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                  <AlertTriangle size={12} color={T.red} />卖出红线
                  <span style={{ fontSize: 11, fontWeight: 400, color: T.mute, textTransform: 'none', letterSpacing: 0 }}>
                    · 触发即买入逻辑破裂
                  </span>
                </div>
              </div>

              {genLoading ? (
                <div style={{ height: 60, background: T.panel, borderRadius: 8,
                              display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <span style={{ color: T.mute, fontSize: 13 }}>正在生成红线条件…</span>
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
                  {killConditions.map((kc, idx) => (
                    <div key={idx} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      {editingKillIdx === idx ? (
                        <>
                          <input
                            autoFocus
                            value={kc}
                            onChange={e => {
                              const next = [...killConditions]
                              next[idx] = e.target.value
                              setKillConditions(next)
                            }}
                            onBlur={() => setEditingKillIdx(null)}
                            onKeyDown={e => e.key === 'Enter' && setEditingKillIdx(null)}
                            style={{ ...inputStyle, flex: 1, padding: '7px 12px', fontSize: 13 }}
                          />
                        </>
                      ) : (
                        <>
                          <div style={{ flex: 1, padding: '8px 12px', borderRadius: 7, fontSize: 13,
                                        background: '#fef9f9', border: '1px solid #fecaca', color: T.dim,
                                        cursor: 'text' }}
                            onClick={() => setEditingKillIdx(idx)}>
                            <span style={{ color: T.red, marginRight: 6 }}>✕</span>{kc}
                          </div>
                          <button onClick={() => setEditingKillIdx(idx)}
                            style={{ padding: 6, background: 'none', border: 'none', cursor: 'pointer', color: T.mute }}>
                            <Edit3 size={13} />
                          </button>
                          <button onClick={() => setKillConditions(killConditions.filter((_, i) => i !== idx))}
                            style={{ padding: 6, background: 'none', border: 'none', cursor: 'pointer', color: T.mute }}>
                            <Trash2 size={13} />
                          </button>
                        </>
                      )}
                    </div>
                  ))}

                  {addingKill ? (
                    <div style={{ display: 'flex', gap: 7 }}>
                      <input
                        autoFocus
                        value={newKillText}
                        onChange={e => setNewKillText(e.target.value)}
                        onKeyDown={e => {
                          if (e.key === 'Enter' && newKillText.trim()) {
                            setKillConditions([...killConditions, newKillText.trim()])
                            setNewKillText(''); setAddingKill(false)
                          }
                          if (e.key === 'Escape') { setNewKillText(''); setAddingKill(false) }
                        }}
                        placeholder="输入红线条件后按 Enter 确认…"
                        style={{ ...inputStyle, flex: 1, padding: '7px 12px', fontSize: 13 }}
                      />
                      <button
                        onClick={() => {
                          if (newKillText.trim()) {
                            setKillConditions([...killConditions, newKillText.trim()])
                            setNewKillText(''); setAddingKill(false)
                          }
                        }}
                        style={{ padding: '7px 14px', borderRadius: 7, fontSize: 13, cursor: 'pointer',
                                 background: T.blue, color: '#fff', border: 'none' }}>
                        确认
                      </button>
                    </div>
                  ) : (
                    <button onClick={() => setAddingKill(true)}
                      style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '7px 12px',
                               borderRadius: 7, fontSize: 12, cursor: 'pointer', color: T.mute,
                               background: T.panel, border: `1px dashed ${T.border}`, width: 'fit-content' }}>
                      <Plus size={13} />添加红线条件
                    </button>
                  )}
                </div>
              )}
            </div>
          </div>
        )}

        {startErr && (
          <div style={{ marginBottom: 12, padding: '9px 14px', borderRadius: 8,
                        background: '#fef2f2', border: '1px solid #fca5a5', color: T.red, fontSize: 13 }}>
            {startErr}
          </div>
        )}

        {/* ── 启动 / 重新分析 按钮 ── */}
        <button
          onClick={result ? handleReset : handleStart}
          disabled={running || genLoading || (!scCode && !result)}
          style={{ width: '100%', padding: '13px', borderRadius: 10, fontSize: 15, fontWeight: 700,
                   cursor: (running || genLoading || (!scCode && !result)) ? 'not-allowed' : 'pointer',
                   background: running ? T.border : result ? T.panel : (scCode && !genLoading) ? T.blue : T.border,
                   color: running ? T.mute : result ? T.dim : '#fff',
                   border: result ? `1px solid ${T.border}` : 'none',
                   display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
          {running
            ? <><Loader2 size={16} style={{ animation: 'spin 1s linear infinite' }} />分析中…</>
            : genLoading
            ? <><Sparkles size={16} style={{ animation: 'spin 2s linear infinite' }} />AI 生成看好理由中…</>
            : result
            ? <><RotateCcw size={16} />重新分析（切换股票）</>
            : <><Activity size={17} />启动分析</>}
        </button>

        {/* ── 分析中进度（内联） ── */}
        {running && (
          <div style={{ marginTop: 20 }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {phases.map((p, i) => {
                const done = phaseIdx > i, active = phaseIdx === i, pending = phaseIdx < i
                return (
                  <div key={p.key} style={{
                    display: 'flex', alignItems: 'center', gap: 12, padding: '11px 16px',
                    borderRadius: 10, background: active ? '#eff6ff' : T.card,
                    border: `1px solid ${active ? T.blue : done ? T.green : T.border}`,
                    opacity: pending ? 0.45 : 1,
                  }}>
                    <div style={{ width: 22, height: 22, borderRadius: '50%', flexShrink: 0,
                                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                                  background: done ? T.green : active ? T.blue : T.border }}>
                      {done   ? <CheckCircle2 size={12} color="#fff" /> :
                       active ? <Loader2 size={12} color="#fff" style={{ animation: 'spin 1s linear infinite' }} /> :
                                <span style={{ color: '#fff', fontSize: 11, fontWeight: 600 }}>{i + 1}</span>}
                    </div>
                    <span style={{ color: active ? T.blue : done ? T.green : T.mute,
                                   fontSize: 13, fontWeight: active ? 600 : 400 }}>
                      {p.label}
                    </span>
                  </div>
                )
              })}
            </div>
            {logs.length > 0 && (
              <div style={{ marginTop: 12, padding: '12px 16px', borderRadius: 10,
                            background: T.card, border: `1px solid ${T.border}` }}>
                {logs.map((line, i) => (
                  <div key={i} style={{ fontSize: 12, color: i === logs.length - 1 ? T.blue : T.mute,
                                        lineHeight: 1.9, display: 'flex', alignItems: 'center', gap: 8 }}>
                    {i === logs.length - 1 && (
                      <Loader2 size={11} color={T.blue} style={{ animation: 'spin 1s linear infinite', flexShrink: 0 }} />
                    )}
                    {line}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* ── 结果（内联） ── */}
        {result && !running && (
          <div ref={resultRef} style={{ marginTop: 20 }}>
            {(() => {
              const cfg = DECISION_CFG[result.decision] || DECISION_CFG.HOLD
              const pct = Math.round(result.confidence * 100)
              return (
                <>
                  {/* 决策横幅 */}
                  <div style={{ background: T.card, border: `1px solid ${T.border}`, borderRadius: 12,
                                padding: '18px 22px', marginBottom: 14, borderLeft: `4px solid ${cfg.color}` }}>
                    <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 12, marginBottom: 10 }}>
                      <span style={{ fontSize: 17, fontWeight: 700, color: T.text }}>{scName}</span>
                      <span style={{ color: T.mute, fontFamily: 'monospace', fontSize: 12 }}>{scCode}</span>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 7, background: cfg.bg,
                                    border: `1px solid ${cfg.border}`, borderRadius: 8, padding: '4px 12px' }}>
                        <cfg.Icon size={15} color={cfg.color} />
                        <span style={{ color: cfg.color, fontWeight: 700, fontSize: 14 }}>{cfg.label}</span>
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span style={{ fontSize: 12, color: T.mute }}>置信度</span>
                        <div style={{ width: 60, height: 4, background: T.border, borderRadius: 2, overflow: 'hidden' }}>
                          <div style={{ width: `${pct}%`, height: '100%', background: cfg.color, borderRadius: 2 }} />
                        </div>
                        <span style={{ fontSize: 13, fontWeight: 600, color: cfg.color }}>{pct}%</span>
                      </div>
                    </div>
                    <p style={{ margin: 0, fontSize: 13, color: T.dim, lineHeight: 1.65 }}>{result.key_reason}</p>
                    {result.sentinel_conflicts && (
                      <div style={{ marginTop: 10, padding: '7px 12px', borderRadius: 7, background: '#fffbeb',
                                    border: `1px solid #fcd34d`, display: 'flex', alignItems: 'center', gap: 8 }}>
                        <AlertTriangle size={13} color={T.amber} />
                        <span style={{ color: T.amber, fontSize: 12 }}>Sentinel 新闻与技术面存在矛盾，置信度已修正</span>
                      </div>
                    )}
                  </div>

                  {/* 技术面 + Sentinel */}
                  <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : '1fr 1fr', gap: 12, marginBottom: 12 }}>
                    <Card title="市场技术面" icon={<BarChart2 size={12} />} accent={T.cyan}>
                      {result.market_report
                        ? <CollapseText text={result.market_report} maxLines={7} />
                        : <span style={{ color: T.mute, fontSize: 13 }}>技术面数据暂不可用</span>}
                    </Card>
                    <Card title="Sentinel · 新闻情报" icon={<Shield size={12} />} accent={T.purple}>
                      <CollapseText text={result.sentinel_summary || '（无新闻数据）'} maxLines={7} />
                    </Card>
                  </div>

                  {/* Bull vs Bear */}
                  <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : '1fr 1fr', gap: 12, marginBottom: 12 }}>
                    <Card title="多头观点" icon={<TrendingUp size={12} />} accent={T.green}>
                      <CollapseText text={result.bull_summary || '（暂无多头论据）'} maxLines={6} />
                    </Card>
                    <Card title="空头观点" icon={<TrendingDown size={12} />} accent={T.red}>
                      <CollapseText text={result.bear_summary || '（暂无空头论据）'} maxLines={6} />
                    </Card>
                  </div>

                  {/* 综合建议 */}
                  <Card title="综合操作建议" icon={<Scale size={12} />} accent={T.gold}>
                    <div style={{ display: 'grid', gridTemplateColumns: result.stop_loss ? '1fr auto' : '1fr', gap: 18 }}>
                      <p style={{ margin: 0, color: T.dim, fontSize: 13, lineHeight: 1.8, whiteSpace: 'pre-wrap' }}>
                        {result.investment_plan || '（建议生成中）'}
                      </p>
                      {result.stop_loss && (
                        <div style={{ textAlign: 'center', padding: '12px 18px', borderRadius: 8,
                                      background: '#fef2f2', border: '1px solid #fca5a5', minWidth: 100 }}>
                          <div style={{ color: T.mute, fontSize: 11, marginBottom: 4 }}>止损参考</div>
                          <div style={{ color: T.red, fontSize: 18, fontWeight: 700 }}>
                            ¥{result.stop_loss.toFixed(2)}
                          </div>
                        </div>
                      )}
                    </div>
                  </Card>

                  <div style={{ height: 40 }} />
                </>
              )
            })()}
          </div>
        )}

        <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      </div>
    </div>
  )
}
