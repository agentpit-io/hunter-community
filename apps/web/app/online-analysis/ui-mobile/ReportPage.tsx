'use client'
import { useEffect, useState, type ReactNode } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { HUNTER, decisionStyle, DecisionType } from '../../lib/hunter-theme'
import { HunterHeader, HunterCard, HunterSectionTitle, HunterBtn } from '../../lib/hunter-ui'

const API_BASE = process.env.NEXT_PUBLIC_API_URL || ''

function authHeaders(): Record<string, string> {
  const token = typeof window !== 'undefined' ? localStorage.getItem('hunter_token') || '' : ''
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export default function MobileReportPage() {
  const { id } = useParams() as { id: string }
  const router = useRouter()
  const [report, setReport] = useState<any | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!id) return
    fetch(`${API_BASE}/api/online-analysis/reports/${id}`, { headers: authHeaders() })
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then(setReport)
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }, [id])

  if (loading) {
    return (
      <div style={{ minHeight: '100vh', background: HUNTER.BG, display: 'flex',
                    alignItems: 'center', justifyContent: 'center', fontFamily: HUNTER.SANS }}>
        <span style={{ color: HUNTER.INK_F, fontSize: 13 }}>加载报告中…</span>
      </div>
    )
  }

  if (error || !report) {
    return (
      <div style={{ minHeight: '100vh', background: HUNTER.BG, fontFamily: HUNTER.SANS }}>
        <HunterHeader sub="研判报告" />
        <div style={{ padding: 20, maxWidth: 520, margin: '0 auto' }}>
          <HunterCard style={{ padding: 30, textAlign: 'center' }}>
            <div style={{ fontSize: 32, marginBottom: 8 }}>⚠️</div>
            <div style={{ color: HUNTER.UP, fontSize: 14, marginBottom: 16 }}>
              报告不存在或加载失败{error ? `: ${error}` : ''}
            </div>
            <HunterBtn onClick={() => router.push('/online-analysis/history')} ghost>
              返回历史列表
            </HunterBtn>
          </HunterCard>
        </div>
      </div>
    )
  }

  const fc: any = report.final_conclusion || {}
  const status: DecisionType = (report.thesis_status || fc.decision || 'HOLD') as DecisionType
  const conf: number = report.confidence ?? fc.confidence ?? 0
  const cfg = decisionStyle(status)

  return (
    <div style={{ minHeight: '100vh', background: HUNTER.BG, fontFamily: HUNTER.SANS }}>
      <HunterHeader
        sub="持仓研判 · 研判报告"
        right={
          <button onClick={() => router.push('/online-analysis/history')} style={{
            padding: '7px 12px', borderRadius: 8, fontSize: 12, color: HUNTER.COPPER2,
            border: `1px solid ${HUNTER.COPPER2}`, background: 'transparent',
            cursor: 'pointer', fontFamily: HUNTER.SERIF, fontWeight: 600,
          }}>
            ← 历史
          </button>
        } />

      <div style={{ padding: '14px 14px 40px', maxWidth: 520, margin: '0 auto' }}>

        {/* 顶部结论横幅 */}
        <HunterCard style={{ marginBottom: 12, borderLeft: `4px solid ${cfg.color}` }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
            <span style={{ fontFamily: HUNTER.SERIF, fontSize: 18, fontWeight: 700, color: HUNTER.INK }}>
              {report.stock_name}
            </span>
            <span style={{ fontSize: 12, color: HUNTER.INK_F, fontFamily: 'monospace' }}>
              {report.stock_code}
            </span>
            <span style={{ padding: '3px 12px', borderRadius: 8, background: cfg.bg,
                           border: `1px solid ${cfg.border}`, color: cfg.color, fontSize: 13,
                           fontWeight: 700, fontFamily: HUNTER.SERIF }}>
              {cfg.label}
            </span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
            <span style={{ fontSize: 12, color: HUNTER.INK_F }}>置信度</span>
            <div style={{ flex: 1, height: 5, background: HUNTER.LINE, borderRadius: 3, overflow: 'hidden' }}>
              <div style={{ width: `${Math.round(conf * 100)}%`, height: '100%', background: cfg.color }} />
            </div>
            <span style={{ fontSize: 13, fontWeight: 700, color: cfg.color, fontFamily: HUNTER.SERIF }}>
              {(conf * 100).toFixed(0)}%
            </span>
          </div>
          <div style={{ fontSize: 11, color: HUNTER.INK_F, display: 'flex', gap: 12 }}>
            <span>{new Date(report.created_at).toLocaleString('zh-CN')}</span>
            {report.duration_ms && <span>⏱ {(report.duration_ms / 1000).toFixed(1)}s</span>}
          </div>
        </HunterCard>

        {/* 综合结论 */}
        {(fc.key_reason || fc.summary) && (
          <HunterCard style={{ marginBottom: 12 }}>
            <HunterSectionTitle icon="🎯">综合结论</HunterSectionTitle>
            <p style={{ margin: 0, fontSize: 13, color: HUNTER.INK_S, lineHeight: 1.85 }}>
              {fc.key_reason || fc.summary}
            </p>
          </HunterCard>
        )}

        {/* 利好证据 */}
        {(fc.bull_summary || (fc.positive_evidence?.length > 0)) && (
          <HunterCard style={{ marginBottom: 12, borderTop: `3px solid ${HUNTER.UP}` }}>
            <HunterSectionTitle icon="🐂">利好证据</HunterSectionTitle>
            {fc.bull_summary ? (
              <p style={{ margin: 0, fontSize: 13, color: HUNTER.INK_S, lineHeight: 1.85, whiteSpace: 'pre-wrap' }}>
                {fc.bull_summary}
              </p>
            ) : (
              <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 6 }}>
                {fc.positive_evidence.map((e: string, i: number) => (
                  <li key={i} style={{ fontSize: 13, color: HUNTER.INK_S, display: 'flex', gap: 8, lineHeight: 1.75 }}>
                    <span style={{ color: HUNTER.UP, flexShrink: 0 }}>✓</span>
                    <span>{e}</span>
                  </li>
                ))}
              </ul>
            )}
          </HunterCard>
        )}

        {/* 风险提示 */}
        {(fc.bear_summary || (fc.risk_evidence?.length > 0)) && (
          <HunterCard style={{ marginBottom: 12, borderTop: `3px solid ${HUNTER.DN}` }}>
            <HunterSectionTitle icon="🐻">风险提示</HunterSectionTitle>
            {fc.bear_summary ? (
              <p style={{ margin: 0, fontSize: 13, color: HUNTER.INK_S, lineHeight: 1.85, whiteSpace: 'pre-wrap' }}>
                {fc.bear_summary}
              </p>
            ) : (
              <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 6 }}>
                {fc.risk_evidence.map((e: string, i: number) => (
                  <li key={i} style={{ fontSize: 13, color: HUNTER.INK_S, display: 'flex', gap: 8, lineHeight: 1.75 }}>
                    <span style={{ color: HUNTER.COPPER2, flexShrink: 0 }}>⚠</span>
                    <span>{e}</span>
                  </li>
                ))}
              </ul>
            )}
          </HunterCard>
        )}

        {/* 操作建议 */}
        {(fc.investment_plan || fc.action_recommendation) && (
          <div style={{
            marginBottom: 12, padding: '13px 16px', borderRadius: HUNTER.R_LG,
            background: HUNTER.PAPER3, border: `1px solid ${HUNTER.COPPER2}`,
            fontSize: 13, color: HUNTER.COPPER3, lineHeight: 1.8, fontWeight: 500,
          }}>
            💡 {fc.investment_plan || fc.action_recommendation}
          </div>
        )}

        {/* 止损提示 */}
        {fc.stop_loss && (
          <div style={{
            marginBottom: 12, padding: '13px 16px', borderRadius: HUNTER.R_LG,
            background: '#FBEDEA', border: `1px solid #E6C0BA`,
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          }}>
            <span style={{ fontSize: 13, color: HUNTER.INK_S }}>🛡 止损参考价</span>
            <span style={{ fontFamily: HUNTER.SERIF, fontSize: 20, fontWeight: 800, color: HUNTER.UP }}>
              ¥{Number(fc.stop_loss).toFixed(2)}
            </span>
          </div>
        )}

        {/* 技术面摘要 */}
        {(fc.market_report || fc.market_summary) && (
          <HunterCard style={{ marginBottom: 12 }}>
            <HunterSectionTitle icon="📊">市场技术面</HunterSectionTitle>
            <p style={{ margin: 0, fontSize: 13, color: HUNTER.INK_S, lineHeight: 1.85, whiteSpace: 'pre-wrap' }}>
              {fc.market_report || fc.market_summary}
            </p>
          </HunterCard>
        )}

        {/* 新闻情报摘要 */}
        {(fc.sentinel_summary || fc.news_summary) && (
          <HunterCard style={{ marginBottom: 12 }}>
            <HunterSectionTitle icon="🛰">新闻情报</HunterSectionTitle>
            <p style={{ margin: 0, fontSize: 13, color: HUNTER.INK_S, lineHeight: 1.85, whiteSpace: 'pre-wrap' }}>
              {fc.sentinel_summary || fc.news_summary}
            </p>
          </HunterCard>
        )}
      </div>
    </div>
  )
}
