'use client'
import { useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { History } from 'lucide-react'
import { HUNTER, decisionStyle, DecisionType } from '../../lib/hunter-theme'
import { HunterHeader, HunterCard, HunterBtn, HunterSectionTitle } from '../../lib/hunter-ui'

const API_BASE = process.env.NEXT_PUBLIC_API_URL || ''

function authHeaders(): Record<string, string> {
  const token = typeof window !== 'undefined' ? localStorage.getItem('hunter_token') || '' : ''
  return token ? { Authorization: `Bearer ${token}` } : {}
}

type Stock = { code: string; name: string; market?: string; exchange?: string }
type AgentResult = {
  decision: DecisionType
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

const PHASES = [
  { key: 'init',   label: '引擎初始化' },
  { key: 'phase1', label: '技术面 + Sentinel 情报' },
  { key: 'phase2', label: 'Bull / Bear 辩论' },
  { key: 'phase3', label: '综合裁判' },
]

const inputStyle: React.CSSProperties = {
  width: '100%', padding: '11px 13px', fontSize: 15, borderRadius: HUNTER.R_MD,
  border: `1.5px solid ${HUNTER.LINE}`, background: '#fff', color: HUNTER.INK,
  outline: 'none', boxSizing: 'border-box',
}

export default function MobileMainPage() {
  const [stocks,       setStocks]       = useState<Stock[]>([])
  const [scCode,       setScCode]       = useState('')
  const [scName,       setScName]       = useState('')
  const [searchInput,  setSearchInput]  = useState('')
  const [searchHits,   setSearchHits]   = useState<Stock[]>([])
  const [detecting,    setDetecting]    = useState(false)

  const [thesis,           setThesis]           = useState('')
  const [killConditions,   setKillConditions]   = useState<string[]>([])
  const [genLoading,       setGenLoading]       = useState(false)
  const [newKillText,      setNewKillText]      = useState('')
  const [addingKill,       setAddingKill]       = useState(false)

  const [running,  setRunning]  = useState(false)
  const [loadStep, setLoadStep] = useState('init')
  const [logs,     setLogs]     = useState<string[]>([])
  const [result,   setResult]   = useState<AgentResult | null>(null)
  const [startErr, setStartErr] = useState('')
  const esRef = useRef<EventSource | null>(null)
  const resultRef = useRef<HTMLDivElement>(null)

  // 自选股（用作快选 chips）
  useEffect(() => {
    fetch(`${API_BASE}/api/watchlist`, { headers: authHeaders() })
      .then(r => r.json()).then(d => setStocks(Array.isArray(d) ? d : []))
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (result && resultRef.current) {
      resultRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }, [result])

  const autoGenThesis = async (code: string, name: string) => {
    setGenLoading(true); setThesis(''); setKillConditions([])
    try {
      const r = await fetch(`${API_BASE}/api/online-analysis/generate-thesis-kills`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ stock_code: code, stock_name: name }),
      })
      const d = await r.json()
      setThesis(d.thesis || '')
      setKillConditions(Array.isArray(d.kill_conditions) ? d.kill_conditions : [])
    } catch { /* 静默 */ } finally { setGenLoading(false) }
  }

  const confirmStock = (code: string, name: string) => {
    setScCode(code); setScName(name)
    setSearchInput(''); setSearchHits([])
    setResult(null); setLogs([]); setStartErr('')
    autoGenThesis(code, name)
  }

  // URL query 预填（从助手/持仓研判 sub-tab 跳过来带 symbol=xxx&name=xxx）
  // 必须在 confirmStock 定义之后声明，避免 TDZ；仅首次 mount 触发一次
  useEffect(() => {
    try {
      const usp = new URLSearchParams(window.location.search)
      const sym = usp.get('symbol'), nm = usp.get('name')
      if (sym) {
        // 走 confirmStock 走一整套流程，包含 autoGenThesis 自动生成持仓逻辑 + 止损条件
        confirmStock(sym, nm || sym)
      }
    } catch {}
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleDetect = async () => {
    const q = searchInput.trim()
    if (!q) return
    setDetecting(true); setSearchHits([]); setStartErr('')
    try {
      const r = await fetch(
        `${API_BASE}/api/online-analysis/search-stock?q=${encodeURIComponent(q)}&limit=8`,
        { headers: authHeaders() })
      const d = await r.json()
      const hits: Stock[] = d.items || []
      if (hits.length === 1) confirmStock(hits[0].code, hits[0].name)
      else if (hits.length > 1) setSearchHits(hits)
      else setStartErr('未识别到该股票，请换个关键词试试')
    } catch { setStartErr('检测失败，请稍后重试') }
    finally { setDetecting(false) }
  }

  const handleStart = async () => {
    if (!scCode) { setStartErr('请先选择股票'); return }
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
        es.close(); setResult(JSON.parse(e.data) as AgentResult); setRunning(false)
      })
      es.addEventListener('error', () => {
        es.close(); setRunning(false); setStartErr('分析失败，请重试')
      })
      es.onerror = () => {
        if (es.readyState === EventSource.CLOSED) {
          setRunning(false); setStartErr('连接中断，请重试')
        }
      }
    } catch { setRunning(false); setStartErr('请求失败，请重试') }
  }

  const handleReset = () => {
    esRef.current?.close()
    setResult(null); setRunning(false); setLogs([]); setStartErr('')
  }

  const resetStock = () => {
    setScCode(''); setScName(''); setResult(null); setLogs([])
    setThesis(''); setKillConditions([])
  }

  const phaseIdx = PHASES.findIndex(p => loadStep.includes(p.key))
  const showThesisPanel = !!scCode && !running && !result

  return (
    <div style={{ minHeight: '100vh', background: HUNTER.BG, fontFamily: HUNTER.SANS }}>
      <style>{`@keyframes hunterSpin { to { transform: rotate(360deg); } }
        .hunter-spin { display:inline-block; animation: hunterSpin 1s linear infinite; }`}</style>

      <HunterHeader
        sub="持仓研判 · Bull vs Bear 辩论"
        right={
          <Link href="/online-analysis/history" style={{
            display: 'inline-flex', alignItems: 'center', gap: 5, padding: '7px 12px',
            borderRadius: 8, fontSize: 12, color: HUNTER.COPPER2,
            border: `1px solid ${HUNTER.COPPER2}`, textDecoration: 'none',
            fontFamily: HUNTER.SERIF, fontWeight: 600,
          }}>
            <History size={13} />历史
          </Link>
        } />

      <div style={{ padding: '14px 14px 40px', maxWidth: 520, margin: '0 auto' }}>

        {/* 介绍卡 */}
        <HunterCard style={{ marginBottom: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
            <span style={{ fontSize: 22 }}>🛡</span>
            <span style={{ fontFamily: HUNTER.SERIF, fontSize: 18, fontWeight: 700, color: HUNTER.INK }}>
              持仓研判
            </span>
            <span style={{ fontSize: 11, background: HUNTER.PAPER3, color: HUNTER.THEME,
                           padding: '2px 8px', borderRadius: 6, fontWeight: 600 }}>NEW</span>
          </div>
          <div style={{ fontSize: 13, color: HUNTER.INK_S, lineHeight: 1.85 }}>
            从「已持有」视角对单只股票做深度研判：录入持仓逻辑和止损条件，AI 派出多头（Bull）
            与空头（Bear）3 轮辩论，最后由综合裁判和风险裁判给出 <b style={{ color: HUNTER.THEME }}>BUY / HOLD / SELL</b> 决策建议。
          </div>
        </HunterCard>

        {/* 股票选择卡 */}
        <HunterCard style={{ marginBottom: 12, opacity: running ? 0.6 : 1, pointerEvents: running ? 'none' : 'auto' }}>
          <HunterSectionTitle icon="🎯">选择股票</HunterSectionTitle>

          {stocks.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 12 }}>
              {stocks.slice(0, 12).map(s => {
                const sel = scCode === s.code
                return (
                  <button key={s.code} onClick={() => confirmStock(s.code, s.name)}
                    style={{ padding: '5px 12px', borderRadius: 999, fontSize: 12, cursor: 'pointer',
                             background: sel ? HUNTER.PAPER3 : HUNTER.PAPER2,
                             border: `1px solid ${sel ? HUNTER.THEME : HUNTER.LINE}`,
                             color: sel ? HUNTER.THEME : HUNTER.INK_S,
                             fontWeight: sel ? 600 : 400 }}>
                    {s.name}
                  </button>
                )
              })}
            </div>
          )}

          <div style={{ display: 'flex', gap: 8 }}>
            <input value={searchInput} onChange={e => setSearchInput(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleDetect()}
              placeholder="输入股票名称或代码..." style={{ ...inputStyle, flex: 1 }} />
            <button onClick={handleDetect} disabled={detecting || !searchInput.trim()}
              style={{ padding: '0 16px', borderRadius: HUNTER.R_MD, fontSize: 14, cursor: (detecting || !searchInput.trim()) ? 'not-allowed' : 'pointer',
                       background: (detecting || !searchInput.trim()) ? '#C9B9A5' : HUNTER.THEME,
                       color: '#fff', border: 'none', whiteSpace: 'nowrap', fontWeight: 600 }}>
              {detecting ? '检测中...' : '🔍 检测'}
            </button>
          </div>

          {searchHits.length > 0 && (
            <div style={{ marginTop: 8, border: `1px solid ${HUNTER.LINE}`, borderRadius: HUNTER.R_MD, overflow: 'hidden' }}>
              <div style={{ padding: '6px 12px', background: HUNTER.PAPER2, fontSize: 11, color: HUNTER.INK_F }}>
                找到 {searchHits.length} 个匹配，点击选择：
              </div>
              {searchHits.map((h) => (
                <div key={h.code} onClick={() => confirmStock(h.code, h.name)}
                  style={{ padding: '10px 13px', fontSize: 14, cursor: 'pointer',
                           borderTop: `1px solid ${HUNTER.PAPER2}`, background: '#fff',
                           display: 'flex', justifyContent: 'space-between' }}>
                  <b>{h.name}</b>
                  <span style={{ color: HUNTER.INK_F, fontFamily: 'monospace' }}>{h.code}</span>
                </div>
              ))}
            </div>
          )}

          {scCode && (
            <div style={{ marginTop: 10, padding: '9px 13px', borderRadius: HUNTER.R_MD,
                          background: HUNTER.PAPER3, border: `1px solid ${HUNTER.THEME}`,
                          display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ fontSize: 13, color: HUNTER.INK }}>
                ✅ 已选 · <b>{scName || scCode}</b>
                <span style={{ color: HUNTER.INK_F, marginLeft: 6, fontFamily: 'monospace', fontSize: 12 }}>{scCode}</span>
              </span>
              <button onClick={resetStock}
                style={{ background: 'none', border: 'none', cursor: 'pointer', color: HUNTER.INK_F, fontSize: 13 }}>
                × 换一只
              </button>
            </div>
          )}
        </HunterCard>

        {/* 持仓逻辑 + 止损条件 */}
        {showThesisPanel && (
          <>
            <HunterCard style={{ marginBottom: 12 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
                <HunterSectionTitle icon="📝" style={{ marginBottom: 0 }}>持仓逻辑</HunterSectionTitle>
                {genLoading && (
                  <span style={{ fontSize: 11, color: HUNTER.THEME }}>
                    <span className="hunter-spin">✨</span> AI 生成中…
                  </span>
                )}
              </div>
              {genLoading ? (
                <div style={{ height: 72, background: HUNTER.PAPER2, borderRadius: HUNTER.R_MD,
                              display: 'flex', alignItems: 'center', justifyContent: 'center',
                              fontSize: 12, color: HUNTER.INK_F }}>
                  正在分析 {scName} 的投资逻辑…
                </div>
              ) : (
                <textarea value={thesis} onChange={e => setThesis(e.target.value)}
                  placeholder={`输入看好 ${scName} 的理由…`} rows={4}
                  style={{ ...inputStyle, resize: 'vertical', lineHeight: 1.7, fontSize: 13 }} />
              )}
            </HunterCard>

            <HunterCard style={{ marginBottom: 12 }}>
              <HunterSectionTitle icon="🛑">
                止损条件 <span style={{ fontFamily: HUNTER.SANS, fontSize: 11, color: HUNTER.INK_F, fontWeight: 400 }}>· 触发即买入逻辑破裂</span>
              </HunterSectionTitle>
              {genLoading ? (
                <div style={{ height: 60, background: HUNTER.PAPER2, borderRadius: HUNTER.R_MD,
                              display: 'flex', alignItems: 'center', justifyContent: 'center',
                              fontSize: 12, color: HUNTER.INK_F }}>
                  正在生成红线条件…
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
                  {killConditions.map((kc, idx) => (
                    <div key={idx} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <div style={{ flex: 1, padding: '8px 12px', borderRadius: HUNTER.R_MD, fontSize: 13,
                                    background: '#FBEDEA', border: `1px solid #E6C0BA`, color: HUNTER.INK_S }}>
                        <span style={{ color: HUNTER.UP, marginRight: 6 }}>✕</span>{kc}
                      </div>
                      <button onClick={() => setKillConditions(killConditions.filter((_, i) => i !== idx))}
                        style={{ padding: 6, background: 'none', border: 'none', cursor: 'pointer',
                                 color: HUNTER.INK_F, fontSize: 16 }}>🗑</button>
                    </div>
                  ))}
                  {addingKill ? (
                    <div style={{ display: 'flex', gap: 6 }}>
                      <input autoFocus value={newKillText} onChange={e => setNewKillText(e.target.value)}
                        onKeyDown={e => {
                          if (e.key === 'Enter' && newKillText.trim()) {
                            setKillConditions([...killConditions, newKillText.trim()])
                            setNewKillText(''); setAddingKill(false)
                          }
                          if (e.key === 'Escape') { setNewKillText(''); setAddingKill(false) }
                        }}
                        placeholder="输入红线条件后按 Enter…"
                        style={{ ...inputStyle, flex: 1, padding: '8px 12px', fontSize: 13 }} />
                      <button onClick={() => {
                          if (newKillText.trim()) {
                            setKillConditions([...killConditions, newKillText.trim()])
                            setNewKillText(''); setAddingKill(false)
                          }
                        }}
                        style={{ padding: '8px 14px', borderRadius: HUNTER.R_MD, fontSize: 13, cursor: 'pointer',
                                 background: HUNTER.THEME, color: '#fff', border: 'none', fontWeight: 600 }}>
                        确认
                      </button>
                    </div>
                  ) : (
                    <button onClick={() => setAddingKill(true)}
                      style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '7px 12px',
                               borderRadius: HUNTER.R_MD, fontSize: 12, cursor: 'pointer', color: HUNTER.INK_F,
                               background: HUNTER.PAPER2, border: `1px dashed ${HUNTER.LINE}`, width: 'fit-content' }}>
                      ＋ 添加红线条件
                    </button>
                  )}
                </div>
              )}
            </HunterCard>
          </>
        )}

        {startErr && (
          <div style={{ marginBottom: 12, padding: '10px 14px', borderRadius: HUNTER.R_MD,
                        background: '#FEF2F2', border: '1px solid #FEE2E2', color: HUNTER.UP, fontSize: 13 }}>
            ⚠️ {startErr}
          </div>
        )}

        {/* 启动 / 重来 按钮 */}
        <HunterBtn
          onClick={result ? handleReset : handleStart}
          disabled={running || genLoading || (!scCode && !result)}
          ghost={!!result}>
          {running ? <><span className="hunter-spin">⚙️</span> 分析中…</>
            : genLoading ? <><span className="hunter-spin">✨</span> AI 生成中…</>
            : result ? '↻ 重新分析（换股票）'
            : '⚡ 启动分析'}
        </HunterBtn>

        {/* 分析进度 */}
        {running && (
          <div style={{ marginTop: 16, display: 'flex', flexDirection: 'column', gap: 8 }}>
            {PHASES.map((p, i) => {
              const done = phaseIdx > i, active = phaseIdx === i, pending = phaseIdx < i
              return (
                <div key={p.key} style={{
                  display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px', borderRadius: HUNTER.R_MD,
                  background: active ? HUNTER.PAPER3 : HUNTER.PAPER,
                  border: `1px solid ${active ? HUNTER.THEME : done ? HUNTER.DN : HUNTER.LINE}`,
                  opacity: pending ? 0.5 : 1,
                }}>
                  <div style={{ width: 22, height: 22, borderRadius: '50%',
                                display: 'flex', alignItems: 'center', justifyContent: 'center',
                                background: done ? HUNTER.DN : active ? HUNTER.THEME : HUNTER.LINE,
                                color: '#fff', fontSize: 11, fontWeight: 700, flexShrink: 0 }}>
                    {done ? '✓' : active ? <span className="hunter-spin">⚙</span> : i + 1}
                  </div>
                  <span style={{ fontSize: 13, fontWeight: active ? 600 : 400,
                                 color: active ? HUNTER.THEME : done ? HUNTER.DN : HUNTER.INK_F }}>
                    {p.label}
                  </span>
                </div>
              )
            })}
            {logs.length > 0 && (
              <div style={{ padding: '10px 14px', borderRadius: HUNTER.R_MD,
                            background: HUNTER.PAPER, border: `1px solid ${HUNTER.LINE}` }}>
                {logs.slice(-5).map((line, i, arr) => {
                  const isLast = i === arr.length - 1
                  return (
                    <div key={i} style={{ fontSize: 12, lineHeight: 1.9,
                                          color: isLast ? HUNTER.THEME : HUNTER.INK_F }}>
                      {isLast && <span className="hunter-spin" style={{ marginRight: 6 }}>⌛</span>}{line}
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )}

        {/* 结果 */}
        {result && !running && (
          <div ref={resultRef} style={{ marginTop: 16 }}>
            {(() => {
              const cfg = decisionStyle(result.decision)
              const pct = Math.round(result.confidence * 100)
              return (
                <>
                  {/* 决策横幅 */}
                  <HunterCard style={{ marginBottom: 12, borderLeft: `4px solid ${cfg.color}` }}>
                    <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8, marginBottom: 10 }}>
                      <span style={{ fontFamily: HUNTER.SERIF, fontSize: 17, fontWeight: 700, color: HUNTER.INK }}>
                        {scName}
                      </span>
                      <span style={{ color: HUNTER.INK_F, fontFamily: 'monospace', fontSize: 12 }}>{scCode}</span>
                      <span style={{ padding: '3px 12px', borderRadius: 8, background: cfg.bg,
                                     border: `1px solid ${cfg.border}`, color: cfg.color,
                                     fontWeight: 700, fontSize: 13, fontFamily: HUNTER.SERIF }}>
                        {cfg.label}
                      </span>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                      <span style={{ fontSize: 12, color: HUNTER.INK_F }}>置信度</span>
                      <div style={{ flex: 1, height: 5, background: HUNTER.LINE, borderRadius: 3, overflow: 'hidden' }}>
                        <div style={{ width: `${pct}%`, height: '100%', background: cfg.color }} />
                      </div>
                      <span style={{ fontSize: 14, fontWeight: 700, color: cfg.color, fontFamily: HUNTER.SERIF }}>{pct}%</span>
                    </div>
                    <p style={{ margin: 0, fontSize: 13, color: HUNTER.INK_S, lineHeight: 1.75 }}>
                      {result.key_reason}
                    </p>
                    {result.sentinel_conflicts && (
                      <div style={{ marginTop: 10, padding: '7px 12px', borderRadius: 7,
                                    background: '#FBF1E4', border: `1px solid ${HUNTER.COPPER2}`,
                                    fontSize: 12, color: HUNTER.COPPER3 }}>
                        ⚠️ Sentinel 新闻与技术面存在矛盾，置信度已修正
                      </div>
                    )}
                  </HunterCard>

                  {/* 市场技术 + Sentinel */}
                  <HunterCard style={{ marginBottom: 12 }}>
                    <HunterSectionTitle icon="📊">市场技术面</HunterSectionTitle>
                    <CollapseText text={result.market_report || '（技术面数据暂不可用）'} />
                  </HunterCard>
                  <HunterCard style={{ marginBottom: 12 }}>
                    <HunterSectionTitle icon="🛰">Sentinel · 新闻情报</HunterSectionTitle>
                    <CollapseText text={result.sentinel_summary || '（无新闻数据）'} />
                  </HunterCard>

                  {/* Bull vs Bear */}
                  <HunterCard style={{ marginBottom: 12, borderTop: `3px solid ${HUNTER.UP}` }}>
                    <HunterSectionTitle icon="🐂">Bull · 多头证据</HunterSectionTitle>
                    <CollapseText text={result.bull_summary || '（暂无多头论据）'} />
                  </HunterCard>
                  <HunterCard style={{ marginBottom: 12, borderTop: `3px solid ${HUNTER.DN}` }}>
                    <HunterSectionTitle icon="🐻">Bear · 空头证据</HunterSectionTitle>
                    <CollapseText text={result.bear_summary || '（暂无空头论据）'} />
                  </HunterCard>

                  {/* 综合建议 */}
                  <HunterCard style={{ marginBottom: 12, borderTop: `3px solid ${HUNTER.THEME}` }}>
                    <HunterSectionTitle icon="⚖️">综合操作建议</HunterSectionTitle>
                    <p style={{ margin: '0 0 12px', color: HUNTER.INK_S, fontSize: 13, lineHeight: 1.85, whiteSpace: 'pre-wrap' }}>
                      {result.investment_plan || '（建议生成中）'}
                    </p>
                    {result.stop_loss && (
                      <div style={{ padding: '10px 14px', borderRadius: HUNTER.R_MD,
                                    background: '#FBEDEA', border: `1px solid #E6C0BA`,
                                    display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                        <span style={{ fontSize: 12, color: HUNTER.INK_F }}>止损参考</span>
                        <span style={{ fontFamily: HUNTER.SERIF, fontSize: 20, fontWeight: 800, color: HUNTER.UP }}>
                          ¥{result.stop_loss.toFixed(2)}
                        </span>
                      </div>
                    )}
                  </HunterCard>
                </>
              )
            })()}
          </div>
        )}
      </div>
    </div>
  )
}

// 内联折叠长文本组件
function CollapseText({ text, maxLines = 6 }: { text: string; maxLines?: number }) {
  const [open, setOpen] = useState(false)
  const lines = text.split('\n').filter(Boolean)
  const visible = open ? lines : lines.slice(0, maxLines)
  return (
    <div>
      <p style={{ color: HUNTER.INK_S, fontSize: 13, lineHeight: 1.85, whiteSpace: 'pre-wrap', margin: 0 }}>
        {visible.join('\n')}
      </p>
      {lines.length > maxLines && (
        <button onClick={() => setOpen(o => !o)} style={{
          marginTop: 8, color: HUNTER.THEME, fontSize: 12, background: 'none',
          border: 'none', cursor: 'pointer', padding: 0, fontWeight: 600,
        }}>
          {open ? '▲ 收起' : '▼ 展开全文'}
        </button>
      )}
    </div>
  )
}
