'use client'
import { useEffect, useState, useRef } from 'react'
import { useParams, useRouter } from 'next/navigation'
import {
  Shield, Loader2, CheckCircle2, AlertTriangle, XCircle, Sparkles,
  ChevronDown, ChevronUp, RefreshCw, History, ShieldOff, Trash2,
} from 'lucide-react'

const API_BASE = process.env.NEXT_PUBLIC_API_URL || ''

type NewsItem = {
  title: string
  content?: string
  publish_time: string
  source_key: string
  source_name: string
  source_tier: string
  source_weight: number
  importance?: string
  url?: string
}

type Step = {
  name: string
  status: 'done' | 'warn' | 'error'
  detail?: string
  detail_lines?: string[]
}

type FactItem = {
  fact: string
  source: string
  weight: number
  type: string
}

type RejectedItem = {
  text: string
  reason: string
}

type FinalConclusion = {
  title?: string
  thesis_status?: string
  confidence?: number
  summary?: string
  user_message?: string
  positive_evidence?: string[]
  risk_evidence?: string[]
  drivers_check?: any[]
  kill_conditions_check?: any[]
  action_recommendation?: string
  fallback_message?: string
  explanations?: string[]
  diagnosis?: any
}

type NaiveResult = {
  steps: Step[]
  conclusion: {
    title: string
    user_message: string
    recommendation: string
    is_poisoned: boolean
  }
  llm_meta?: {
    raw_text?: string
    tokens_in?: number
    tokens_out?: number
    latency_ms?: number
  }
}

type KillCond = { text: string; type: string; source: 'ai' | 'user' }

export default function DesktopTaskPage() {
  const { id: taskId } = useParams() as { id: string }
  const router = useRouter()

  // ─── SSE 实时状态 ───
  const [progress, setProgress] = useState<string>('初始化...')
  const [newsItems, setNewsItems] = useState<NewsItem[]>([])
  const [naiveSteps, setNaiveSteps] = useState<Step[]>([])
  const [naiveResult, setNaiveResult] = useState<NaiveResult | null>(null)
  const [defenseSteps, setDefenseSteps] = useState<Step[]>([])
  const [acceptedFacts, setAcceptedFacts] = useState<FactItem[]>([])
  const [rejectedItems, setRejectedItems] = useState<RejectedItem[]>([])
  const [finalConc, setFinalConc] = useState<FinalConclusion | null>(null)
  const [done, setDone] = useState(false)
  const [reportId, setReportId] = useState<number | null>(null)
  const [errored, setErrored] = useState<string>('')

  // ─── 场景输入面板（顶部右侧）───
  const [showScenario, setShowScenario] = useState(false)
  const [scStockCode, setScStockCode] = useState('600519.SH')
  const [scStockName, setScStockName] = useState('贵州茅台')
  const [scChangePct, setScChangePct] = useState('-3.0')
  const [scThesis, setScThesis] = useState('')
  const [scKCs, setScKCs] = useState<KillCond[]>([])
  const [newKCText, setNewKCText] = useState('')
  const [generating, setGenerating] = useState(false)
  const [starting, setStarting] = useState(false)
  const [restartErr, setRestartErr] = useState('')

  // ─── 列 1 Tab 状态 ───
  const [newsTab, setNewsTab] = useState<'accepted' | 'rejected' | 'all'>('all')

  // ─── 手机响应式 ───
  const [isMobile, setIsMobile] = useState(false)
  const [mobileTab, setMobileTab] = useState(3) // 默认显示结论列

  useEffect(() => {
    const check = () => setIsMobile(window.innerWidth < 768)
    check()
    window.addEventListener('resize', check)
    return () => window.removeEventListener('resize', check)
  }, [])

  const esRef = useRef<EventSource | null>(null)

  // 进入页面 → 拉报告基本信息回填场景（如果存在历史）+ SSE 订阅
  useEffect(() => {
    if (!taskId) return

    const url = `${API_BASE}/api/online-analysis/stream/${taskId}`
    const es = new EventSource(url)
    esRef.current = es

    // 任务不存在时自动跳历史（task_id 是运行时 ID，结束后会被清理）
    let notFoundTimer: any = null

    const handlers: Record<string, (data: any) => void> = {
      hello:    () => setProgress('已连接，分析启动中...'),
      started:  (d) => {
        setProgress('开始采集多源数据...')
        if (d.stock_code) setScStockCode(d.stock_code)
        if (d.stock_name) setScStockName(d.stock_name)
      },
      stage:    (d) => setProgress(`阶段: ${d.name} ${d.status === 'done' ? '✓' : '...'}`),
      news_arrived: (d) => setNewsItems(prev => [...prev, d as NewsItem]),
      layer_done: (d) => {
        setDefenseSteps(prev => [...prev, d.step as Step])
        // 第 3 层附带的 facts / rejected
        if (d.layer === 3) {
          if (Array.isArray(d.facts))    setAcceptedFacts(d.facts)
          if (Array.isArray(d.rejected)) setRejectedItems(d.rejected)
        }
      },
      naive_done: (d) => {
        setNaiveSteps(d.steps as Step[])
        setNaiveResult(d as NaiveResult)
      },
      defense_done: (d) => {
        setDefenseSteps(d.steps as Step[])
        setFinalConc(d.conclusion as FinalConclusion)
      },
      done:    () => setProgress('归因完成 ✓'),
      saved:   (d) => setReportId(d.report_id),
      complete:() => setDone(true),
      error:   (d) => {
        const err = d.error || '未知错误'
        setErrored(err)
        if (err === 'task_not_found') {
          // 任务已结束，task_id 已从内存清理 — 2 秒后跳历史
          notFoundTimer = setTimeout(() => router.push('/online-analysis/history'), 2000)
        }
      },
    }

    for (const [event, handler] of Object.entries(handlers)) {
      es.addEventListener(event, (e: any) => {
        try { handler(JSON.parse(e.data)) } catch {}
      })
    }
    es.addEventListener('end', () => { es.close(); setDone(true) })
    es.onerror = () => { if (done) es.close() }

    return () => {
      es.close()
      if (notFoundTimer) clearTimeout(notFoundTimer)
    }
  }, [taskId])

  // 拉历史报告完整 thesis/kill_conds 回填场景输入
  useEffect(() => {
    if (!reportId) return
    fetch(`${API_BASE}/api/online-analysis/reports/${reportId}`)
      .then(r => r.json())
      .then(d => {
        if (d.thesis_text) setScThesis(d.thesis_text)
        if (Array.isArray(d.kill_conditions)) setScKCs(d.kill_conditions)
      })
      .catch(() => {})
  }, [reportId])

  const canRestart = scStockCode.trim() && scStockName.trim() && scThesis.length >= 10 && scKCs.length > 0

  const handleAIGenerate = async () => {
    if (!scStockName.trim() || scThesis.length < 10) return
    setGenerating(true)
    try {
      const r = await fetch(`${API_BASE}/api/online-analysis/kill-condition/suggest`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stock_code: scStockCode, stock_name: scStockName, thesis_text: scThesis }),
      })
      const d = await r.json()
      if (Array.isArray(d.suggestions)) setScKCs(prev => [...prev, ...d.suggestions])
    } catch {}
    setGenerating(false)
  }

  const handleRestart = async () => {
    if (!canRestart) return
    setStarting(true)
    setRestartErr('')
    try {
      const r = await fetch(`${API_BASE}/api/online-analysis/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          stock_code:      scStockCode,
          stock_name:      scStockName,
          change_pct:      parseFloat(scChangePct) || 0,
          thesis_text:     scThesis,
          kill_conditions: scKCs,
        }),
      })
      if (!r.ok) throw new Error('启动失败')
      const d = await r.json()
      esRef.current?.close()
      router.push(`/online-analysis/task/${d.task_id}`)
    } catch (e: any) {
      setRestartErr(e.message)
    } finally {
      setStarting(false)
    }
  }

  return (
    <div className="min-h-screen p-4" style={{ background: 'var(--bg)' }}>
      {/* ─── 顶部 ─── */}
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <Shield className="w-6 h-6" style={{ color: 'var(--blue)' }} />
          <div>
            <h1 className="text-lg font-bold" style={{ color: 'var(--text)' }}>
              {scStockName || '持仓研判'} <span className="text-sm font-mono" style={{ color: 'var(--text-muted)' }}>{scStockCode}</span>
            </h1>
            <div className="text-xs" style={{ color: 'var(--text-muted)' }}>
              {done ? '✓ 完成' : (
                <span className="flex items-center gap-1">
                  <Loader2 className="w-3 h-3 animate-spin" /> {progress}
                </span>
              )}
            </div>
          </div>
        </div>

        {/* ─── 场景输入面板（右上角）─── */}
        <div className="flex items-center gap-2">
          <button onClick={() => router.push('/online-analysis/history')}
                  className="text-xs px-3 py-1.5 rounded border flex items-center gap-1"
                  style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}>
            <History className="w-3.5 h-3.5" /> 历史
          </button>
          <button onClick={() => setShowScenario(s => !s)}
                  className="text-sm px-3 py-1.5 rounded flex items-center gap-1.5 font-medium"
                  style={{ background: showScenario ? 'rgba(168,85,247,0.1)' : 'var(--blue)',
                           color: showScenario ? '#a855f7' : '#fff',
                           border: '1px solid ' + (showScenario ? '#a855f7' : 'var(--blue)') }}>
            <RefreshCw className="w-3.5 h-3.5" />
            重新分析
            {showScenario ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
          </button>
        </div>
      </div>

      {/* 场景输入折叠面板 */}
      {showScenario && (
        <div className="mb-3 p-4 rounded-lg space-y-3"
             style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
          {/* 行 1：股票 + 波动 */}
          <div className="grid grid-cols-12 gap-2">
            <input value={scStockName} onChange={e => setScStockName(e.target.value)}
                   placeholder="股票名"
                   className="col-span-3 px-3 py-2 rounded text-sm border"
                   style={{ background: 'var(--bg)', color: 'var(--text)', borderColor: 'var(--border)' }} />
            <input value={scStockCode} onChange={e => setScStockCode(e.target.value)}
                   placeholder="代码（如 600519.SH）"
                   className="col-span-3 px-3 py-2 rounded text-sm border font-mono"
                   style={{ background: 'var(--bg)', color: 'var(--text)', borderColor: 'var(--border)' }} />
            <input value={scChangePct} onChange={e => setScChangePct(e.target.value)}
                   placeholder="波动 %"
                   className="col-span-2 px-3 py-2 rounded text-sm border"
                   style={{ background: 'var(--bg)', color: 'var(--text)', borderColor: 'var(--border)' }} />
            <button onClick={handleRestart} disabled={!canRestart || starting}
                    className="col-span-4 py-2 rounded text-sm font-medium flex items-center justify-center gap-1.5 disabled:opacity-50"
                    style={{ background: 'var(--blue)', color: '#fff' }}>
              {starting ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
              {starting ? '启动中...' : '⚡ 开始重新分析'}
            </button>
          </div>
          {/* 行 2：Thesis */}
          <textarea value={scThesis} onChange={e => setScThesis(e.target.value)}
                    placeholder="Thesis：长期看好这只股的原因（≥10 字）"
                    rows={2} maxLength={500}
                    className="w-full px-3 py-2 rounded text-sm border resize-none"
                    style={{ background: 'var(--bg)', color: 'var(--text)', borderColor: 'var(--border)' }} />
          {/* 行 3：Kill Conditions */}
          <div className="space-y-1.5">
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold" style={{ color: 'var(--text-muted)' }}>Kill Conditions:</span>
              <button onClick={handleAIGenerate} disabled={!scThesis || scThesis.length < 10 || generating}
                      className="text-xs px-2 py-1 rounded flex items-center gap-1 disabled:opacity-50"
                      style={{ background: 'rgba(168,85,247,0.1)', color: '#a855f7', border: '1px solid #a855f7' }}>
                {generating ? <Loader2 className="w-3 h-3 animate-spin" /> : <Sparkles className="w-3 h-3" />}
                {generating ? 'AI 思考中' : 'AI 生成'}
              </button>
            </div>
            {scKCs.map((kc, i) => (
              <div key={i} className="flex items-start gap-2 text-xs px-2 py-1 rounded" style={{ background: 'var(--bg)' }}>
                {kc.source === 'ai' ? <Sparkles className="w-3 h-3 mt-0.5" style={{ color: '#a855f7' }} /> : <span style={{ color: 'var(--text-muted)' }}>·</span>}
                <span className="flex-1" style={{ color: 'var(--text)' }}>{kc.text}</span>
                <span className="text-[10px] px-1.5 rounded" style={{ background: 'var(--bg-card)', color: 'var(--text-muted)' }}>{kc.type}</span>
                <button onClick={() => setScKCs(scKCs.filter((_, k) => k !== i))} className="text-red-500">
                  <Trash2 className="w-3 h-3" />
                </button>
              </div>
            ))}
            <div className="flex gap-2">
              <input value={newKCText} onChange={e => setNewKCText(e.target.value)}
                     placeholder="手动添加触发条件..."
                     className="flex-1 px-2 py-1 text-xs rounded border"
                     style={{ background: 'var(--bg)', color: 'var(--text)', borderColor: 'var(--border)' }}
                     onKeyDown={e => {
                       if (e.key === 'Enter' && newKCText.trim()) {
                         setScKCs([...scKCs, { text: newKCText.trim(), type: '自定义', source: 'user' }])
                         setNewKCText('')
                       }
                     }} />
            </div>
          </div>
          {restartErr && (
            <div className="p-2 rounded text-xs" style={{ background: '#fee', color: '#c00' }}>{restartErr}</div>
          )}
        </div>
      )}

      {errored && (
        <div className="mb-4 p-3 rounded-lg flex items-center justify-between gap-2"
             style={{ background: errored === 'task_not_found' ? '#fff7ed' : '#fee',
                      color: errored === 'task_not_found' ? '#9a3412' : '#c00',
                      border: '1px solid ' + (errored === 'task_not_found' ? '#fb923c' : '#fca5a5') }}>
          <div className="flex items-center gap-2">
            <AlertTriangle className="w-4 h-4" />
            {errored === 'task_not_found' ? (
              <span>任务已结束（运行时 ID 不在内存中），2 秒后跳转到「分析历史」...</span>
            ) : (
              <span>出错: {errored}</span>
            )}
          </div>
          <div className="flex gap-2">
            <button onClick={() => router.push('/online-analysis/history')}
                    className="text-xs px-3 py-1 rounded"
                    style={{ background: '#fb923c', color: '#fff' }}>
              <History className="w-3.5 h-3.5 inline mr-1" /> 打开历史
            </button>
            <button onClick={() => setShowScenario(true)}
                    className="text-xs px-3 py-1 rounded border"
                    style={{ borderColor: 'currentColor' }}>
              新建分析
            </button>
          </div>
        </div>
      )}

      {/* ─── 手机 Tab 栏 ─── */}
      {isMobile && (
        <div className="flex mb-2 rounded-lg overflow-hidden border" style={{ borderColor: 'var(--border)' }}>
          {(['📰 新闻', '🤖 对比', '🛡 防御', '✅ 结论'] as const).map((label, i) => (
            <button key={i} onClick={() => setMobileTab(i)}
                    className="flex-1 py-2.5 text-xs font-medium"
                    style={{ background: mobileTab === i ? 'var(--blue)' : 'var(--bg-card)',
                             color: mobileTab === i ? '#fff' : 'var(--text-muted)',
                             border: 'none', cursor: 'pointer' }}>
              {label}
            </button>
          ))}
        </div>
      )}

      {/* ─── 4 列布局（桌面）/ 单列布局（手机）─── */}
      <div className="grid gap-3"
           style={{
             gridTemplateColumns: isMobile ? '1fr' : '320px 1fr 1fr 380px',
             gridTemplateRows: '1fr',
             height: isMobile
               ? (showScenario ? 'calc(100vh - 260px)' : 'calc(100vh - 140px)')
               : (showScenario ? 'calc(100vh - 320px)' : 'calc(100vh - 96px)'),
           }}>

        {/* 列 1 · 新闻流（3 分区 Tab） */}
        <div style={{ display: isMobile && mobileTab !== 0 ? 'none' : 'contents' }}>
        <Column title="① 新闻 · 信源分类" color="#3b82f6">
          <div className="flex gap-1 p-1 mb-2 rounded" style={{ background: 'var(--bg)' }}>
            <TabBtn active={newsTab === 'accepted'} onClick={() => setNewsTab('accepted')}
                    label={`✓ 已采信 ${acceptedFacts.length}`} color="#22c55e" />
            <TabBtn active={newsTab === 'rejected'} onClick={() => setNewsTab('rejected')}
                    label={`✗ 已拦截 ${rejectedItems.length}`} color="#ef4444" />
            <TabBtn active={newsTab === 'all'} onClick={() => setNewsTab('all')}
                    label={`原始 ${newsItems.length}`} color="#94a3b8" />
          </div>

          {newsTab === 'accepted' && (
            <>
              {acceptedFacts.length === 0 && (
                <Empty text={done ? "无可验证事实" : "F7 事实提取中..."} />
              )}
              {acceptedFacts.map((f, i) => (
                <div key={i} className="p-2 rounded text-xs border-l-2"
                     style={{ background: 'var(--bg)', borderLeftColor: '#22c55e' }}>
                  <div className="flex justify-between mb-1">
                    <span style={{ color: '#22c55e' }} className="font-medium">F{i + 1} · {f.source}</span>
                    <span className="font-mono" style={{ color: '#22c55e' }}>权重 {f.weight.toFixed(2)}</span>
                  </div>
                  <div style={{ color: 'var(--text)' }}>{f.fact}</div>
                  <div className="mt-1 inline-block text-[10px] px-1.5 py-0.5 rounded" style={{ background: 'rgba(34,197,94,0.1)', color: '#22c55e' }}>
                    {f.type}
                  </div>
                </div>
              ))}
            </>
          )}

          {newsTab === 'rejected' && (
            <>
              {rejectedItems.length === 0 && (
                <Empty text={done ? "无可剔除内容" : "等待 F7 完成..."} />
              )}
              {rejectedItems.map((r, i) => (
                <div key={i} className="p-2 rounded text-xs border-l-2"
                     style={{ background: 'var(--bg)', borderLeftColor: '#ef4444', opacity: 0.85 }}>
                  <div className="flex items-center gap-1 mb-1">
                    <ShieldOff className="w-3 h-3" style={{ color: '#ef4444' }} />
                    <span style={{ color: '#ef4444' }} className="font-medium text-[11px]">已拦截</span>
                  </div>
                  <div style={{ color: 'var(--text)', textDecoration: 'line-through', opacity: 0.7 }}>{r.text}</div>
                  <div className="mt-1 text-[11px]" style={{ color: '#ef4444' }}>
                    💡 {r.reason}
                  </div>
                </div>
              ))}
            </>
          )}

          {newsTab === 'all' && (
            <>
              {newsItems.length === 0 && !done && <Empty text="等待数据流入..." />}
              {newsItems.map((n, i) => <RawNewsCard key={i} item={n} />)}
            </>
          )}
        </Column>
        </div>

        {/* 列 2 · 套壳 AI */}
        <div style={{ display: isMobile && mobileTab !== 1 ? 'none' : 'contents' }}>
        <Column title="② 套壳 AI（无防御）" color="#ef4444" subtitle="对比基线">
          {naiveSteps.length === 0 && !done && <Empty text="等待运行..." />}
          {naiveSteps.map((s, i) => <StepCard key={i} step={s} />)}
          {naiveResult && (
            <div className="mt-3 p-3 rounded-lg border-2"
                 style={{ borderColor: naiveResult.conclusion.is_poisoned ? '#ef4444' : '#f59e0b',
                          background: 'rgba(239,68,68,0.05)' }}>
              <div className="text-xs font-mono mb-2" style={{ color: '#ef4444' }}>
                {naiveResult.conclusion.is_poisoned ? '✗ 被投毒成功' : '⚠ 中性结论（无可验证依据）'}
              </div>
              <div className="text-sm font-semibold mb-1" style={{ color: 'var(--text)' }}>
                {naiveResult.conclusion.title}
              </div>
              <div className="text-xs mb-2" style={{ color: 'var(--text-muted)' }}>
                建议：<span className="font-semibold">{naiveResult.conclusion.recommendation}</span>
              </div>
              <div className="text-xs whitespace-pre-wrap" style={{ color: 'var(--text)' }}>
                {naiveResult.conclusion.user_message}
              </div>
              {naiveResult.llm_meta && (
                <div className="mt-2 pt-2 border-t flex gap-3 text-[10px] font-mono"
                     style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}>
                  <span>tokens: {naiveResult.llm_meta.tokens_in}+{naiveResult.llm_meta.tokens_out}</span>
                  <span>耗时: {naiveResult.llm_meta.latency_ms}ms</span>
                </div>
              )}
            </div>
          )}
        </Column>
        </div>

        {/* 列 3 · 五层防御 */}
        <div style={{ display: isMobile && mobileTab !== 2 ? 'none' : 'contents' }}>
        <Column title="③ agentpit.io 五层防御" color="#f2c24b" subtitle="抗投毒体系">
          {defenseSteps.length === 0 && !done && <Empty text="等待运行..." />}
          {defenseSteps.map((s, i) => <StepCard key={i} step={s} />)}
        </Column>
        </div>

        {/* 列 4 · 最终归因 */}
        <div style={{ display: isMobile && mobileTab !== 3 ? 'none' : 'contents' }}>
        <Column title="④ 推送给用户" color="#a855f7" subtitle="最终结论">
          {!finalConc && !done && <Empty text="等待归因..." />}
          {finalConc && <FinalCard fc={finalConc} />}
        </Column>
        </div>
      </div>
    </div>
  )
}

// ─── 子组件 ─────────────────────────────────────────────────────────

function Column({ title, subtitle, color, children }:
  { title: string; subtitle?: string; color: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col rounded-lg overflow-hidden" style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
      <div className="px-3 py-2 border-b" style={{ borderColor: 'var(--border)' }}>
        <div className="text-sm font-semibold" style={{ color }}>{title}</div>
        {subtitle && <div className="text-xs" style={{ color: 'var(--text-muted)' }}>{subtitle}</div>}
      </div>
      <div className="flex-1 overflow-y-auto p-2 space-y-2" style={{ scrollbarWidth: 'thin' }}>
        {children}
      </div>
    </div>
  )
}

function TabBtn({ active, onClick, label, color }: any) {
  return (
    <button onClick={onClick}
            className="flex-1 px-2 py-1 rounded text-xs font-medium transition-colors"
            style={{ background: active ? color + '20' : 'transparent',
                     color: active ? color : 'var(--text-muted)',
                     border: active ? `1px solid ${color}` : '1px solid transparent' }}>
      {label}
    </button>
  )
}

function Empty({ text }: { text: string }) {
  return <div className="text-xs text-center py-8" style={{ color: 'var(--text-muted)' }}>{text}</div>
}

function RawNewsCard({ item }: { item: NewsItem }) {
  const weight = item.source_weight || 0.5
  const isHigh = weight >= 0.7
  const isMid  = weight >= 0.5 && weight < 0.7
  const color  = isHigh ? '#22c55e' : isMid ? '#f59e0b' : '#94a3b8'
  return (
    <div className="p-2 rounded text-xs border-l-2"
         style={{ background: 'var(--bg)', borderLeftColor: color }}>
      <div className="flex justify-between items-center mb-1">
        <span style={{ color: 'var(--text-muted)' }}>{item.source_name}</span>
        <span className="font-mono text-[10px]" style={{ color }}>权重 {weight.toFixed(2)}</span>
      </div>
      <div style={{ color: 'var(--text)' }}>{item.title}</div>
      {item.importance && (
        <span className="inline-block mt-1 px-1.5 py-0.5 rounded text-[10px] font-medium"
              style={{ background: ['HIGH', 'CRITICAL'].includes(item.importance) ? 'rgba(239,68,68,0.1)' : 'rgba(148,163,184,0.1)',
                       color: ['HIGH', 'CRITICAL'].includes(item.importance) ? '#ef4444' : '#64748b' }}>
          {item.importance}
        </span>
      )}
    </div>
  )
}

function StepCard({ step }: { step: Step }) {
  const colorMap: Record<string, string> = { done: '#22c55e', warn: '#f59e0b', error: '#ef4444' }
  const IconMap: Record<string, any> = { done: CheckCircle2, warn: AlertTriangle, error: XCircle }
  const color = colorMap[step.status] || 'var(--text-muted)'
  const Icon  = IconMap[step.status] || CheckCircle2
  const lines = step.detail_lines || (step.detail ? [step.detail] : [])

  return (
    <div className="p-2.5 rounded border-l-2" style={{ background: 'var(--bg)', borderLeftColor: color }}>
      <div className="flex items-center justify-between mb-1.5">
        <div className="text-sm font-medium" style={{ color: 'var(--text)' }}>{step.name}</div>
        <Icon className="w-4 h-4 flex-shrink-0" style={{ color }} />
      </div>
      {lines.map((line, i) => (
        <div key={i} className="text-xs whitespace-pre-line leading-relaxed" style={{ color: 'var(--text-muted)' }}>
          {line}
        </div>
      ))}
    </div>
  )
}

function FinalCard({ fc }: { fc: FinalConclusion }) {
  const status = fc.thesis_status || 'INSUFFICIENT'

  if (status === 'INSUFFICIENT') {
    return (
      <div className="space-y-3">
        <div className="p-3 rounded-lg border-2" style={{ borderColor: '#f59e0b', background: 'rgba(245,158,11,0.05)' }}>
          <div className="text-sm font-semibold mb-2" style={{ color: '#f59e0b' }}>{fc.title || '信号不足'}</div>
          <div className="text-xs mb-2" style={{ color: 'var(--text)' }}>{fc.summary}</div>
          <div className="space-y-1">
            {(fc.explanations || []).map((e, i) => (
              <div key={i} className="text-xs" style={{ color: 'var(--text-muted)' }}>{e}</div>
            ))}
          </div>
          <div className="text-xs mt-3 whitespace-pre-line" style={{ color: 'var(--text-muted)' }}>
            {fc.fallback_message}
          </div>
        </div>
      </div>
    )
  }

  const statusColor = status === 'INTACT' ? '#22c55e' : status === 'WEAKENING' ? '#f59e0b' : '#ef4444'
  const statusEmoji = status === 'INTACT' ? '🟢' : status === 'WEAKENING' ? '🟡' : '🔴'

  return (
    <div className="space-y-3">
      <div className="p-3 rounded-lg border-2" style={{ borderColor: statusColor, background: `${statusColor}15` }}>
        <div className="flex items-center gap-2 mb-2">
          <span className="text-2xl">{statusEmoji}</span>
          <div>
            <div className="text-sm font-bold" style={{ color: statusColor }}>{status}</div>
            <div className="text-xs" style={{ color: 'var(--text-muted)' }}>置信度 {(fc.confidence! * 100).toFixed(0)}%</div>
          </div>
        </div>
        <div className="text-xs mt-2" style={{ color: 'var(--text)' }}>{fc.summary}</div>
      </div>

      <Section title="💬 推送给用户">
        <div className="text-xs whitespace-pre-wrap" style={{ color: 'var(--text)' }}>{fc.user_message}</div>
      </Section>

      {fc.positive_evidence && fc.positive_evidence.length > 0 && (
        <Section title="✓ 利好证据" color="#22c55e">
          <ul className="text-xs space-y-1" style={{ color: 'var(--text)' }}>
            {fc.positive_evidence.map((e, i) => <li key={i}>· {e}</li>)}
          </ul>
        </Section>
      )}

      {fc.risk_evidence && fc.risk_evidence.length > 0 && (
        <Section title="⚠ 风险证据" color="#f59e0b">
          <ul className="text-xs space-y-1" style={{ color: 'var(--text)' }}>
            {fc.risk_evidence.map((e, i) => <li key={i}>· {e}</li>)}
          </ul>
        </Section>
      )}

      {fc.kill_conditions_check && fc.kill_conditions_check.length > 0 && (
        <Section title="🎯 Kill Conditions">
          <ul className="text-xs space-y-1.5">
            {fc.kill_conditions_check.map((kc: any, i: number) => (
              <li key={i} className="flex items-start gap-1.5">
                <span style={{ color: kc.triggered ? '#ef4444' : '#22c55e' }}>{kc.triggered ? '✗' : '✓'}</span>
                <div style={{ color: 'var(--text)' }}>
                  <div>{kc.condition}</div>
                  {kc.evidence && <div className="text-[11px]" style={{ color: 'var(--text-muted)' }}>{kc.evidence}</div>}
                </div>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {fc.action_recommendation && (
        <div className="p-2.5 rounded-lg text-xs font-medium text-center"
             style={{ background: 'rgba(168,85,247,0.1)', color: '#a855f7', border: '1px solid #a855f7' }}>
          💡 {fc.action_recommendation}
        </div>
      )}
    </div>
  )
}

function Section({ title, color = 'var(--text-muted)', children }:
  { title: string; color?: string; children: React.ReactNode }) {
  return (
    <div className="p-2.5 rounded-lg" style={{ background: 'var(--bg)', border: '1px solid var(--border)' }}>
      <div className="text-xs font-semibold mb-1.5" style={{ color }}>{title}</div>
      {children}
    </div>
  )
}
