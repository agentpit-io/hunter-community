'use client'
// SKILL · hunter-UZI-Skill 深度分析（Sprint 3 P2 · Phase 1 MVP）
// 展示 stock_deep_analysis tool 的 markdown 结果 + 数据覆盖率 + LLM 元信息
import { Radar, CheckCircle2, AlertCircle, Copy, Loader2 } from 'lucide-react'
import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { HUNTER } from '../../../lib/hunter-theme'

interface UziData {
  type: 'uzi_deep_analysis'
  code: string
  name?: string
  depth: string
  markdown: string
  dims_covered: string[]
  dims_missing: string[]
  duration_ms: number
  model?: string
  note?: string
}

// dim key → 中文标签
const DIM_LABEL: Record<string, string> = {
  quote:        '行情',
  kline:        'K线',
  financials:   '财务',
  lhb:          '龙虎榜',
  fund_holders: '十大股东',
  governance:   '治理',
  news:         '新闻',
  research:     '研报',
}

export default function UziDeepAnalysisCard({ data }: { data: UziData }) {
  const [copied, setCopied] = useState(false)

  // 老 session 里可能存的是缺少覆盖度字段的 output（早期 tool schema · 或只回了 markdown）,
  // 不兜底会 undefined.length 直接把整个 /chat 页崩成白屏 —— 上层 tryRenderRichCard
  // 的 try/catch 只包了 JSX 创建,catch 不到子组件里的 render 异常。
  const dimsCovered = Array.isArray(data.dims_covered) ? data.dims_covered : []
  const dimsMissing = Array.isArray(data.dims_missing) ? data.dims_missing : []

  const coverage = dimsCovered.length + dimsMissing.length > 0
    ? Math.round((dimsCovered.length / (dimsCovered.length + dimsMissing.length)) * 100)
    : 0

  const copyMarkdown = async () => {
    try {
      await navigator.clipboard.writeText(data.markdown || '')
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch (_e) {
      // silent
    }
  }

  return (
    <div style={cardStyle}>
      {/* 头部 */}
      <div style={{
        padding: '14px 18px',
        display: 'flex', alignItems: 'center', gap: 8,
        borderBottom: `1px solid ${HUNTER.LINE}`,
        background: `linear-gradient(90deg, ${HUNTER.BRAND_PALE} 0%, ${HUNTER.PAPER} 100%)`,
      }}>
        <Radar size={16} style={{ color: HUNTER.COPPER3 }} />
        <span style={{ fontFamily: HUNTER.SERIF, fontWeight: 700, fontSize: 14 }}>
          {data.name || data.code} · 深度分析
        </span>
        <span style={{
          padding: '2px 8px',
          borderRadius: 4,
          background: HUNTER.COPPER3,
          color: '#fff',
          fontSize: 10.5,
          letterSpacing: '.03em',
        }}>
          {data.depth?.toUpperCase() || 'LITE'}
        </span>
        <span style={{ marginLeft: 'auto', color: HUNTER.INK_F, fontSize: 11 }}>
          {(data.duration_ms / 1000).toFixed(1)}s · 覆盖率 {coverage}%
        </span>
      </div>

      {/* 数据覆盖度 chips */}
      <div style={{
        padding: '10px 18px',
        display: 'flex',
        flexWrap: 'wrap',
        gap: 6,
        borderBottom: `1px solid ${HUNTER.LINE}`,
        background: HUNTER.PAPER2,
      }}>
        {dimsCovered.map(d => (
          <span key={d} title="数据已 seed" style={{
            display: 'inline-flex', alignItems: 'center', gap: 3,
            padding: '2px 7px', borderRadius: 4, fontSize: 10.5,
            background: '#fde7e0', color: HUNTER.UP, fontWeight: 600,
          }}>
            <CheckCircle2 size={10} /> {DIM_LABEL[d] || d}
          </span>
        ))}
        {dimsMissing.map(d => (
          <span key={d} title="数据未 seed（akshare backfill 中）" style={{
            display: 'inline-flex', alignItems: 'center', gap: 3,
            padding: '2px 7px', borderRadius: 4, fontSize: 10.5,
            background: HUNTER.PAPER, color: HUNTER.INK_F, fontWeight: 500,
            border: `1px dashed ${HUNTER.LINE}`,
          }}>
            <AlertCircle size={10} /> {DIM_LABEL[d] || d}
          </span>
        ))}
      </div>

      {/* markdown 正文 */}
      <div className="uzi-md" style={{
        padding: '16px 20px',
        fontSize: 13.5, lineHeight: 1.75, color: HUNTER.INK,
        maxHeight: 720, overflowY: 'auto',
      }}>
        {data.markdown ? (
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              h3: ({ children }) => (
                <h3 style={{
                  fontFamily: HUNTER.SERIF, fontSize: 15, fontWeight: 700,
                  color: HUNTER.COPPER3, margin: '18px 0 8px', paddingBottom: 4,
                  borderBottom: `1px solid ${HUNTER.LINE}`,
                }}>{children}</h3>
              ),
              strong: ({ children }) => (
                <strong style={{ color: HUNTER.THEME, fontWeight: 700 }}>{children}</strong>
              ),
              ul: ({ children }) => (
                <ul style={{ paddingLeft: 20, margin: '6px 0' }}>{children}</ul>
              ),
              li: ({ children }) => (
                <li style={{ margin: '3px 0' }}>{children}</li>
              ),
              p: ({ children }) => (
                <p style={{ margin: '8px 0' }}>{children}</p>
              ),
            }}
          >
            {data.markdown}
          </ReactMarkdown>
        ) : (
          <div style={{ color: HUNTER.INK_F, fontStyle: 'italic', textAlign: 'center', padding: 20 }}>
            <Loader2 size={16} style={{ verticalAlign: 'middle', marginRight: 6 }} />
            分析生成中 · 请稍候（5-10 秒）
          </div>
        )}
      </div>

      {/* footer */}
      <div style={{
        padding: '10px 18px',
        display: 'flex', alignItems: 'center', gap: 10,
        borderTop: `1px solid ${HUNTER.LINE}`,
        background: HUNTER.PAPER,
        fontSize: 11, color: HUNTER.INK_F,
      }}>
        <span>数据源 · finance-data</span>
        {data.model && <span>· LLM {data.model}</span>}
        <button
          onClick={copyMarkdown}
          style={{
            marginLeft: 'auto',
            display: 'inline-flex', alignItems: 'center', gap: 4,
            padding: '4px 10px', borderRadius: 6,
            background: copied ? HUNTER.UP : HUNTER.THEME, color: '#fff',
            border: 'none', cursor: 'pointer', fontSize: 11, fontWeight: 600,
          }}
        >
          <Copy size={11} /> {copied ? '已复制' : '复制 markdown'}
        </button>
      </div>

      {data.note && (
        <div style={{
          padding: '8px 18px',
          borderTop: `1px dashed ${HUNTER.LINE}`,
          background: HUNTER.PAPER2,
          fontSize: 10.5, color: HUNTER.INK_F, fontStyle: 'italic',
        }}>
          {data.note}
        </div>
      )}
    </div>
  )
}

const cardStyle: React.CSSProperties = {
  margin: '12px 0',
  background: '#fff',
  border: `1px solid ${HUNTER.LINE}`,
  borderRadius: 14,
  overflow: 'hidden',
  boxShadow: '0 4px 18px rgba(40,35,27,.04)',
}
