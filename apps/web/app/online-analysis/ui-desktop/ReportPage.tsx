'use client'
import { useEffect, useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { TrendingUp, TrendingDown, Minus, Loader2, ArrowLeft, Clock } from 'lucide-react'

const API_BASE = process.env.NEXT_PUBLIC_API_URL || ''

function authHeaders(): Record<string, string> {
  const token = typeof window !== 'undefined' ? localStorage.getItem('hunter_token') || '' : ''
  return token ? { Authorization: `Bearer ${token}` } : {}
}

const DECISION_MAP: Record<string, { label: string; color: string; bg: string; Icon: any }> = {
  BUY:  { label: '买入',  color: '#16a34a', bg: '#f0fdf4', Icon: TrendingUp   },
  HOLD: { label: '持仓',  color: '#b45309', bg: '#fffbeb', Icon: Minus        },
  SELL: { label: '卖出',  color: '#dc2626', bg: '#fef2f2', Icon: TrendingDown },
}

export default function DesktopReportPage() {
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
      <div className="min-h-screen flex items-center justify-center bg-white">
        <Loader2 className="w-6 h-6 animate-spin text-blue-500" />
      </div>
    )
  }

  if (error || !report) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-3 bg-white">
        <div className="text-red-500">报告不存在或加载失败: {error}</div>
        <button onClick={() => router.push('/online-analysis/history')}
                className="px-4 py-2 rounded bg-blue-500 text-white text-sm">
          返回历史列表
        </button>
      </div>
    )
  }

  const fc     = report.final_conclusion || {}
  const status = report.thesis_status || fc.decision || 'HOLD'
  const conf   = report.confidence ?? fc.confidence ?? 0
  const d      = DECISION_MAP[status] || DECISION_MAP['HOLD']
  const Icon   = d.Icon

  return (
    <div className="min-h-screen bg-white">
      {/* 顶部 header */}
      <div className="border-b px-4 py-3 flex items-center gap-3">
        <button onClick={() => router.push('/online-analysis/history')}
                className="p-1.5 rounded hover:bg-gray-100">
          <ArrowLeft className="w-4 h-4 text-gray-500" />
        </button>
        <div className="flex-1">
          <h1 className="text-base font-bold text-gray-900">
            {report.stock_name}
            <span className="ml-2 text-sm font-normal text-gray-400">{report.stock_code}</span>
          </h1>
          <div className="flex items-center gap-3 text-xs text-gray-400 mt-0.5">
            <span>{new Date(report.created_at).toLocaleString('zh-CN')}</span>
            {report.duration_ms && (
              <span className="flex items-center gap-1">
                <Clock className="w-3 h-3" />
                {(report.duration_ms / 1000).toFixed(1)}s
              </span>
            )}
          </div>
        </div>
        {/* 决策标签 */}
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-bold"
             style={{ background: d.bg, color: d.color }}>
          <Icon className="w-4 h-4" />
          {d.label}
          <span className="text-xs font-normal opacity-70">
            {(conf * 100).toFixed(0)}%
          </span>
        </div>
      </div>

      {/* 正文 */}
      <div className="max-w-2xl mx-auto p-4 space-y-4">

        {/* 综合结论 */}
        {(fc.key_reason || fc.summary) && (
          <Section title="综合结论">
            <p className="text-sm text-gray-700 leading-relaxed">{fc.key_reason || fc.summary}</p>
          </Section>
        )}

        {/* 利好证据 */}
        {(fc.bull_summary || (fc.positive_evidence?.length > 0)) && (
          <Section title="利好证据" titleColor="#16a34a">
            {fc.bull_summary
              ? <p className="text-sm text-gray-700 leading-relaxed">{fc.bull_summary}</p>
              : <ul className="space-y-1.5">
                  {fc.positive_evidence.map((e: string, i: number) => (
                    <li key={i} className="text-sm text-gray-700 flex gap-2">
                      <span className="text-green-500 mt-0.5">✓</span>
                      <span>{e}</span>
                    </li>
                  ))}
                </ul>
            }
          </Section>
        )}

        {/* 风险提示 */}
        {(fc.bear_summary || (fc.risk_evidence?.length > 0)) && (
          <Section title="风险提示" titleColor="#b45309">
            {fc.bear_summary
              ? <p className="text-sm text-gray-700 leading-relaxed">{fc.bear_summary}</p>
              : <ul className="space-y-1.5">
                  {fc.risk_evidence.map((e: string, i: number) => (
                    <li key={i} className="text-sm text-gray-700 flex gap-2">
                      <span className="text-amber-500 mt-0.5">⚠</span>
                      <span>{e}</span>
                    </li>
                  ))}
                </ul>
            }
          </Section>
        )}

        {/* 操作建议 */}
        {(fc.investment_plan || fc.action_recommendation) && (
          <div className="p-3 rounded-lg text-sm font-medium"
               style={{ background: '#f0f9ff', color: '#0369a1', border: '1px solid #bae6fd' }}>
            💡 {fc.investment_plan || fc.action_recommendation}
          </div>
        )}

        {/* 止损提示 */}
        {fc.stop_loss && (
          <div className="p-3 rounded-lg text-sm font-medium"
               style={{ background: '#fef2f2', color: '#dc2626', border: '1px solid #fecaca' }}>
            🛡️ 止损参考价：{fc.stop_loss} 元
          </div>
        )}

        {/* 技术面摘要 */}
        {(fc.market_report || fc.market_summary) && (
          <Section title="技术面">
            <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-line">{fc.market_report || fc.market_summary}</p>
          </Section>
        )}

        {/* 新闻情报摘要 */}
        {(fc.sentinel_summary || fc.news_summary) && (
          <Section title="新闻情报">
            <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-line">{fc.sentinel_summary || fc.news_summary}</p>
          </Section>
        )}

      </div>
    </div>
  )
}

function Section({ title, titleColor = '#374151', children }: {
  title: string; titleColor?: string; children: React.ReactNode
}) {
  return (
    <div className="rounded-lg border border-gray-100 overflow-hidden">
      <div className="px-3 py-2 bg-gray-50 border-b border-gray-100">
        <span className="text-xs font-semibold" style={{ color: titleColor }}>{title}</span>
      </div>
      <div className="p-3">{children}</div>
    </div>
  )
}
