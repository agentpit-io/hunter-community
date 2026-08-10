'use client'
import { useEffect, useRef, useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { HUNTER, decisionStyle, DecisionType } from '../../lib/hunter-theme'
import { HunterHeader, HunterCard, HunterBtn, HunterSectionTitle } from '../../lib/hunter-ui'

const API_BASE = process.env.NEXT_PUBLIC_API_URL || ''

type NewsItem = {
  title: string
  publish_time?: string
  source_name?: string
}

type StepItem = {
  name: string
  status: 'done' | 'warn' | 'error'
  detail?: string
}

type FinalConclusion = {
  thesis_status?: DecisionType
  confidence?: number
  summary?: string
  user_message?: string
  action_recommendation?: string
  positive_evidence?: string[]
  risk_evidence?: string[]
}

export default function MobileTaskPage() {
  const { id: taskId } = useParams() as { id: string }
  const router = useRouter()

  const [progress, setProgress] = useState('初始化…')
  const [newsCount, setNewsCount] = useState(0)
  const [naiveSteps, setNaiveSteps] = useState<StepItem[]>([])
  const [defenseSteps, setDefenseSteps] = useState<StepItem[]>([])
  const [stockCode, setStockCode] = useState('')
  const [stockName, setStockName] = useState('')
  const [finalConc, setFinalConc] = useState<FinalConclusion | null>(null)
  const [done, setDone] = useState(false)
  const [reportId, setReportId] = useState<number | null>(null)
  const [errored, setErrored] = useState('')
  const esRef = useRef<EventSource | null>(null)

  useEffect(() => {
    if (!taskId) return
    const es = new EventSource(`${API_BASE}/api/online-analysis/stream/${taskId}`)
    esRef.current = es
    let notFoundTimer: any = null

    const on = (evt: string, fn: (d: any) => void) => {
      es.addEventListener(evt, (e: any) => { try { fn(JSON.parse(e.data)) } catch {} })
    }

    on('hello',    () => setProgress('已连接，分析启动中…'))
    on('started',  (d) => {
      setProgress('开始采集多源数据…')
      if (d.stock_code) setStockCode(d.stock_code)
      if (d.stock_name) setStockName(d.stock_name)
    })
    on('stage',    (d) => setProgress(`阶段: ${d.name} ${d.status === 'done' ? '✓' : '…'}`))
    on('news_arrived', () => setNewsCount(c => c + 1))
    on('layer_done', (d) => setDefenseSteps(prev => [...prev, d.step as StepItem]))
    on('naive_done', (d) => setNaiveSteps((d.steps || []) as StepItem[]))
    on('defense_done', (d) => {
      setDefenseSteps((d.steps || []) as StepItem[])
      setFinalConc(d.conclusion as FinalConclusion)
    })
    on('done', () => setProgress('归因完成 ✓'))
    on('saved', (d) => setReportId(d.report_id))
    on('complete', () => setDone(true))
    on('error', (d) => {
      const err = d.error || '未知错误'
      setErrored(err)
      if (err === 'task_not_found') {
        notFoundTimer = setTimeout(() => router.push('/online-analysis/history'), 2000)
      }
    })
    es.addEventListener('end', () => { es.close(); setDone(true) })
    es.onerror = () => { if (done) es.close() }

    return () => { es.close(); if (notFoundTimer) clearTimeout(notFoundTimer) }
  }, [taskId, router, done])

  const status: DecisionType = (finalConc?.thesis_status || 'HOLD') as DecisionType
  const cfg = decisionStyle(status)
  const conf = finalConc?.confidence ?? 0

  return (
    <div style={{ minHeight: '100vh', background: HUNTER.BG, fontFamily: HUNTER.SANS }}>
      <style>{`@keyframes hunterSpin { to { transform: rotate(360deg); } }
        .hunter-spin { display:inline-block; animation: hunterSpin 1s linear infinite; }`}</style>

      <HunterHeader
        sub={done ? '研判完成' : '研判进行中'}
        right={
          <button onClick={() => router.push('/online-analysis')} style={{
            padding: '7px 12px', borderRadius: 8, fontSize: 12, color: HUNTER.COPPER2,
            border: `1px solid ${HUNTER.COPPER2}`, background: 'transparent',
            cursor: 'pointer', fontFamily: HUNTER.SERIF, fontWeight: 600,
          }}>
            ← 返回
          </button>
        } />

      <div style={{ padding: '14px 14px 40px', maxWidth: 520, margin: '0 auto' }}>

        {/* 错误态 */}
        {errored && (
          <HunterCard style={{ marginBottom: 12, borderLeft: `4px solid ${HUNTER.UP}` }}>
            <div style={{ fontSize: 14, color: HUNTER.UP, marginBottom: 6, fontWeight: 600 }}>
              ⚠️ 分析出错
            </div>
            <div style={{ fontSize: 12, color: HUNTER.INK_S, lineHeight: 1.7 }}>
              {errored === 'task_not_found'
                ? '该任务已结束，正在跳转到历史记录…'
                : `错误信息：${errored}`}
            </div>
          </HunterCard>
        )}

        {/* 顶部股票 + 进度 */}
        <HunterCard style={{ marginBottom: 12 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 22 }}>🛡</span>
            <span style={{ fontFamily: HUNTER.SERIF, fontSize: 18, fontWeight: 700, color: HUNTER.INK }}>
              {stockName || '持仓研判'}
            </span>
            {stockCode && (
              <span style={{ fontSize: 12, color: HUNTER.INK_F, fontFamily: 'monospace' }}>{stockCode}</span>
            )}
          </div>
          <div style={{ fontSize: 13, color: done ? HUNTER.DN : HUNTER.THEME, display: 'flex', alignItems: 'center', gap: 8 }}>
            {!done && !errored && <span className="hunter-spin">⚙</span>}
            {done && <span>✓</span>}
            <span>{progress}</span>
          </div>
          {newsCount > 0 && !done && (
            <div style={{ marginTop: 8, fontSize: 12, color: HUNTER.INK_F }}>
              📰 已采集 {newsCount} 条情报
            </div>
          )}
        </HunterCard>

        {/* 分析步骤（naive → defense） */}
        {(naiveSteps.length > 0 || defenseSteps.length > 0) && (
          <HunterCard style={{ marginBottom: 12 }}>
            <HunterSectionTitle icon="⚙️">分析步骤</HunterSectionTitle>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {[...naiveSteps, ...defenseSteps].map((s, i) => (
                <div key={i} style={{
                  display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px', borderRadius: HUNTER.R_MD,
                  background: HUNTER.PAPER2,
                  borderLeft: `3px solid ${s.status === 'done' ? HUNTER.DN : s.status === 'warn' ? HUNTER.COPPER2 : HUNTER.UP}`,
                }}>
                  <span style={{ fontSize: 14 }}>
                    {s.status === 'done' ? '✓' : s.status === 'warn' ? '⚠' : '✕'}
                  </span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 13, color: HUNTER.INK, fontWeight: 500 }}>{s.name}</div>
                    {s.detail && (
                      <div style={{ fontSize: 11, color: HUNTER.INK_F, marginTop: 2 }}>{s.detail}</div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </HunterCard>
        )}

        {/* 最终结论 */}
        {finalConc && (
          <>
            <HunterCard style={{ marginBottom: 12, borderLeft: `4px solid ${cfg.color}` }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 10 }}>
                <HunterSectionTitle icon="🎯" style={{ marginBottom: 0, flex: 1 }}>综合裁判</HunterSectionTitle>
                <span style={{ padding: '3px 12px', borderRadius: 8, background: cfg.bg,
                               border: `1px solid ${cfg.border}`, color: cfg.color, fontSize: 13,
                               fontWeight: 700, fontFamily: HUNTER.SERIF }}>
                  {cfg.label}
                </span>
              </div>
              {conf > 0 && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                  <span style={{ fontSize: 12, color: HUNTER.INK_F }}>置信度</span>
                  <div style={{ flex: 1, height: 5, background: HUNTER.LINE, borderRadius: 3, overflow: 'hidden' }}>
                    <div style={{ width: `${Math.round(conf * 100)}%`, height: '100%', background: cfg.color }} />
                  </div>
                  <span style={{ fontSize: 13, fontWeight: 700, color: cfg.color, fontFamily: HUNTER.SERIF }}>
                    {(conf * 100).toFixed(0)}%
                  </span>
                </div>
              )}
              {(finalConc.summary || finalConc.user_message) && (
                <p style={{ margin: 0, fontSize: 13, color: HUNTER.INK_S, lineHeight: 1.8, whiteSpace: 'pre-wrap' }}>
                  {finalConc.summary || finalConc.user_message}
                </p>
              )}
            </HunterCard>

            {finalConc.positive_evidence && finalConc.positive_evidence.length > 0 && (
              <HunterCard style={{ marginBottom: 12, borderTop: `3px solid ${HUNTER.UP}` }}>
                <HunterSectionTitle icon="🐂">利好证据</HunterSectionTitle>
                <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {finalConc.positive_evidence.map((e, i) => (
                    <li key={i} style={{ fontSize: 13, color: HUNTER.INK_S, display: 'flex', gap: 8, lineHeight: 1.75 }}>
                      <span style={{ color: HUNTER.UP, flexShrink: 0 }}>✓</span>
                      <span>{e}</span>
                    </li>
                  ))}
                </ul>
              </HunterCard>
            )}

            {finalConc.risk_evidence && finalConc.risk_evidence.length > 0 && (
              <HunterCard style={{ marginBottom: 12, borderTop: `3px solid ${HUNTER.DN}` }}>
                <HunterSectionTitle icon="🐻">风险提示</HunterSectionTitle>
                <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {finalConc.risk_evidence.map((e, i) => (
                    <li key={i} style={{ fontSize: 13, color: HUNTER.INK_S, display: 'flex', gap: 8, lineHeight: 1.75 }}>
                      <span style={{ color: HUNTER.COPPER2, flexShrink: 0 }}>⚠</span>
                      <span>{e}</span>
                    </li>
                  ))}
                </ul>
              </HunterCard>
            )}

            {finalConc.action_recommendation && (
              <div style={{
                marginBottom: 12, padding: '13px 16px', borderRadius: HUNTER.R_LG,
                background: HUNTER.PAPER3, border: `1px solid ${HUNTER.COPPER2}`,
                fontSize: 13, color: HUNTER.COPPER3, lineHeight: 1.8, fontWeight: 500,
              }}>
                💡 {finalConc.action_recommendation}
              </div>
            )}
          </>
        )}

        {/* 底部 CTA */}
        {done && reportId && (
          <div style={{ marginTop: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>
            <HunterBtn onClick={() => router.push(`/online-analysis/report/${reportId}`)}>
              📜 查看完整报告
            </HunterBtn>
            <HunterBtn ghost onClick={() => router.push('/online-analysis')}>
              🛡 新建研判
            </HunterBtn>
          </div>
        )}
      </div>
    </div>
  )
}
