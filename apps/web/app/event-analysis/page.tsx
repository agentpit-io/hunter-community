'use client'
import { useState, useRef, useEffect, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { Zap, Loader2, ArrowLeft, Send, Clock, ChevronRight } from 'lucide-react'
import Sidebar from '../components/Sidebar'

const PRESETS = [
  '美联储突然宣布加息50bp',
  '中美宣布新一轮关税战，对华商品加征25%关税',
  '人民币单日贬值超1%，突破7.4关口',
  '大宗商品超级周期来临，铜价突破历史新高',
  '国内突发重大经济刺激政策，万亿量化宽松',
  '全球科技股估值泡沫破裂，纳指单日暴跌8%',
]

interface HistoryItem {
  id: number
  event_desc: string
  created_at: string
}

function authHeaders() {
  const token = typeof window !== 'undefined' ? localStorage.getItem('hunter_token') || '' : ''
  return token
    ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
    : { 'Content-Type': 'application/json' }
}

function fmtDate(iso: string) {
  const d = new Date(iso)
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  const hh = String(d.getHours()).padStart(2, '0')
  const mi = String(d.getMinutes()).padStart(2, '0')
  return `${mm}-${dd} ${hh}:${mi}`
}

export default function EventAnalysisPage() {
  const router = useRouter()
  const [event, setEvent] = useState('')
  const [loading, setLoading] = useState(false)
  const [html, setHtml] = useState('')
  const [htmlTitle, setHtmlTitle] = useState('')
  const [error, setError] = useState('')
  const [history, setHistory] = useState<HistoryItem[]>([])
  const [histLoading, setHistLoading] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const iframeRef = useRef<HTMLIFrameElement>(null)

  const loadHistory = useCallback(async () => {
    setHistLoading(true)
    try {
      const r = await fetch('/api/event-analysis/history', { headers: authHeaders() as HeadersInit })
      if (r.ok) setHistory(await r.json())
    } catch { /* silent */ } finally {
      setHistLoading(false)
    }
  }, [])

  useEffect(() => {
    const token = typeof window !== 'undefined' ? localStorage.getItem('hunter_token') : null
    if (!token) { router.push('/login'); return }
    loadHistory()
  }, [router, loadHistory])

  const analyze = async () => {
    if (!event.trim() || loading) return
    setLoading(true)
    setError('')
    setHtml('')
    try {
      const r = await fetch('/api/event-analysis', {
        method: 'POST',
        headers: authHeaders() as HeadersInit,
        body: JSON.stringify({ event: event.trim() }),
      })
      if (r.status === 401) { router.push('/login'); return }
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || '分析失败，请稍后重试')
      }
      const d = await r.json()
      setHtml(d.html || '')
      setHtmlTitle(event.trim())
      loadHistory()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '未知错误')
    } finally {
      setLoading(false)
    }
  }

  const viewHistoryItem = async (item: HistoryItem) => {
    setLoading(true)
    setError('')
    try {
      const r = await fetch(`/api/event-analysis/history/${item.id}`, { headers: authHeaders() as HeadersInit })
      if (!r.ok) throw new Error('获取失败')
      const d = await r.json()
      setHtml(d.html || '')
      setHtmlTitle(item.event_desc)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '未知错误')
    } finally {
      setLoading(false)
    }
  }

  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) analyze()
  }

  return (
    <div className="flex min-h-screen" style={{ background: 'var(--bg)' }}>
      <div className="hidden md:block"><Sidebar /></div>

      <main className="flex-1 md:ml-52 flex flex-col min-h-screen">

        {/* 顶部标题栏 */}
        <div className="flex items-center gap-3 px-4 py-3 border-b md:px-6 md:py-4"
          style={{ background: 'var(--bg-card)', borderColor: 'var(--border)' }}>
          <button onClick={() => router.back()}
            className="md:hidden p-1.5 rounded-lg" style={{ color: 'var(--text-muted)' }}>
            <ArrowLeft className="w-5 h-5" />
          </button>
          <div className="flex items-center gap-2">
            <Zap className="w-5 h-5" style={{ color: 'var(--blue)' }} />
            <div>
              <div className="font-semibold text-sm" style={{ color: 'var(--text)' }}>事件分析</div>
              <div className="text-xs hidden md:block" style={{ color: 'var(--text-muted)' }}>
                输入大事件，分析对你持仓的影响
              </div>
            </div>
          </div>
        </div>

        {/* 报告全屏展示 */}
        {html && (
          <div className="fixed inset-0 z-50 flex flex-col"
            style={{ background: 'var(--bg)', height: '100dvh' }}>
            <div className="flex items-center gap-3 px-4 py-3 border-b shrink-0"
              style={{ background: 'var(--bg-card)', borderColor: 'var(--border)' }}>
              <button onClick={() => setHtml('')}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm"
                style={{ background: 'rgba(37,99,235,0.08)', color: 'var(--blue)' }}>
                <ArrowLeft className="w-4 h-4" />返回
              </button>
              <span className="text-sm font-medium truncate flex-1" style={{ color: 'var(--text)' }}>
                {htmlTitle.length > 40 ? htmlTitle.slice(0, 40) + '…' : htmlTitle}
              </span>
            </div>
            <iframe ref={iframeRef} srcDoc={html} className="flex-1 w-full"
              style={{ border: 'none' }} sandbox="allow-same-origin allow-scripts" title="事件分析报告" />
          </div>
        )}

        {/* 内容区 */}
        <div className="flex-1 flex flex-col px-4 py-6 md:px-8 md:py-8 max-w-2xl w-full mx-auto">

          {/* 输入框 */}
          <div className="rounded-xl border overflow-hidden mb-4"
            style={{ background: 'var(--bg-card)', borderColor: 'var(--border)' }}>
            <textarea ref={textareaRef} value={event}
              onChange={e => setEvent(e.target.value)} onKeyDown={handleKey}
              placeholder="描述你关注的大事件&#10;&#10;例如：美联储突然宣布加息50bp，超出市场预期的鹰派操作..."
              rows={5} className="w-full px-4 py-4 text-sm resize-none outline-none"
              style={{ background: 'transparent', color: 'var(--text)', borderBottom: '1px solid var(--border)' }} />
            <div className="flex items-center justify-between px-4 py-2.5">
              <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
                {event.length > 0 ? `${event.length} 字` : 'Ctrl+Enter 快速发送'}
              </span>
              <button onClick={analyze} disabled={!event.trim() || loading}
                className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-sm font-medium transition-all"
                style={{
                  background: event.trim() && !loading ? 'var(--blue)' : 'rgba(148,163,184,0.2)',
                  color: event.trim() && !loading ? '#fff' : 'var(--text-muted)',
                  cursor: event.trim() && !loading ? 'pointer' : 'not-allowed',
                }}>
                {loading
                  ? <><Loader2 className="w-3.5 h-3.5 animate-spin" />分析中…</>
                  : <><Send className="w-3.5 h-3.5" />分析影响</>}
              </button>
            </div>
          </div>

          {/* 错误提示 */}
          {error && (
            <div className="rounded-lg px-4 py-3 mb-4 text-sm"
              style={{ background: 'rgba(239,68,68,0.08)', color: '#ef4444', border: '1px solid rgba(239,68,68,0.2)' }}>
              {error}
            </div>
          )}

          {/* 加载状态 */}
          {loading && (
            <div className="flex flex-col items-center gap-3 py-12">
              <Loader2 className="w-8 h-8 animate-spin" style={{ color: 'var(--blue)' }} />
              <div className="text-sm" style={{ color: 'var(--text-muted)' }}>正在用 skill 分析事件对你持仓的影响…</div>
              <div className="text-xs" style={{ color: 'var(--text-muted)' }}>通常需要 30-60 秒</div>
            </div>
          )}

          {!loading && (
            <>
              {/* 示例事件 */}
              <div className="mb-6">
                <div className="text-xs font-medium mb-3" style={{ color: 'var(--text-muted)' }}>
                  示例事件（点击填入，可自行修改）
                </div>
                <div className="flex flex-wrap gap-2">
                  {PRESETS.map(p => (
                    <button key={p} onClick={() => { setEvent(p); textareaRef.current?.focus() }}
                      className="text-xs px-3 py-1.5 rounded-full border transition-colors"
                      style={{
                        background: event === p ? 'rgba(37,99,235,0.1)' : 'var(--bg-card)',
                        color: event === p ? 'var(--blue)' : 'var(--text-muted)',
                        borderColor: event === p ? 'rgba(37,99,235,0.3)' : 'var(--border)',
                      }}>
                      {p}
                    </button>
                  ))}
                </div>
              </div>

              {/* 分析历史 */}
              <div>
                <div className="flex items-center gap-2 mb-3">
                  <Clock className="w-3.5 h-3.5" style={{ color: 'var(--text-muted)' }} />
                  <span className="text-xs font-medium" style={{ color: 'var(--text-muted)' }}>
                    我的分析历史
                  </span>
                </div>

                {histLoading && (
                  <div className="flex justify-center py-6">
                    <Loader2 className="w-5 h-5 animate-spin" style={{ color: 'var(--text-muted)' }} />
                  </div>
                )}

                {!histLoading && history.length === 0 && (
                  <div className="text-xs text-center py-6 rounded-xl border"
                    style={{ color: 'var(--text-muted)', borderColor: 'var(--border)', background: 'var(--bg-card)' }}>
                    暂无历史记录，分析完成后会自动保存
                  </div>
                )}

                {!histLoading && history.length > 0 && (
                  <div className="rounded-xl border overflow-hidden" style={{ borderColor: 'var(--border)' }}>
                    {history.map((item, idx) => (
                      <button key={item.id} onClick={() => viewHistoryItem(item)}
                        className="w-full flex items-center gap-3 px-4 py-3 text-left transition-colors"
                        style={{
                          background: 'var(--bg-card)',
                          borderBottom: idx < history.length - 1 ? '1px solid var(--border)' : 'none',
                        }}
                        onMouseEnter={e => (e.currentTarget.style.background = 'rgba(37,99,235,0.04)')}
                        onMouseLeave={e => (e.currentTarget.style.background = 'var(--bg-card)')}>
                        <div className="flex-1 min-w-0">
                          <div className="text-sm truncate" style={{ color: 'var(--text)' }}>
                            {item.event_desc}
                          </div>
                          <div className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
                            {fmtDate(item.created_at)}
                          </div>
                        </div>
                        <ChevronRight className="w-4 h-4 shrink-0" style={{ color: 'var(--text-muted)' }} />
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </main>
    </div>
  )
}
