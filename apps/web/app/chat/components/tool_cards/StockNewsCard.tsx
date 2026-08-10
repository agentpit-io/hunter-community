'use client'
// SKILL 2 · 关键新闻 · 5 条精选 + 每条 AI 影响短评
import { Newspaper, ExternalLink } from 'lucide-react'
import { HUNTER } from '../../../lib/hunter-theme'

interface NewsItem {
  title: string
  source: string
  date: string
  url?: string
  impact: 'positive' | 'negative' | 'neutral' | 'high_impact'
  ai_note: string
}

interface NewsData {
  type: 'stock_news'
  code: string
  name: string
  items: NewsItem[]
}

const IMPACT_STYLE = {
  positive:    { label: '↑ 利好',   bg: '#fde7e0', fg: HUNTER.UP },
  high_impact: { label: '↑↑ 强利好', bg: HUNTER.THEME, fg: '#fff' },
  negative:    { label: '↓ 利空',   bg: '#e0eae0', fg: HUNTER.DN },
  neutral:     { label: '→ 中性',   bg: HUNTER.PAPER2, fg: HUNTER.INK_F },
}

export default function StockNewsCard({ data }: { data: NewsData }) {
  if (!data.items || data.items.length === 0) {
    return (
      <div style={cardStyle}>
        <div style={{ padding: '16px 20px', textAlign: 'center', color: HUNTER.INK_F, fontSize: 13 }}>
          <Newspaper size={22} style={{ opacity: 0.4, marginBottom: 6 }} /><br />
          暂无 <b>{data.name || data.code}</b> 的近期新闻
        </div>
      </div>
    )
  }

  return (
    <div style={cardStyle}>
      {/* 头部 */}
      <div style={{
        padding: '14px 18px', display: 'flex', alignItems: 'center', gap: 8,
        borderBottom: `1px solid ${HUNTER.LINE}`, background: HUNTER.PAPER,
      }}>
        <Newspaper size={16} style={{ color: HUNTER.COPPER3 }} />
        <span style={{ fontFamily: HUNTER.SERIF, fontWeight: 700, fontSize: 14 }}>
          {data.name} · 近期关键新闻
        </span>
        <span style={{ marginLeft: 'auto', color: HUNTER.INK_F, fontSize: 11 }}>
          {data.items.length} 条 · AI 影响标注
        </span>
      </div>

      {/* 新闻列表 */}
      {data.items.map((it, i) => {
        const style = IMPACT_STYLE[it.impact] || IMPACT_STYLE.neutral
        return (
          <div key={i} style={{
            padding: '14px 18px',
            borderBottom: i < data.items.length - 1 ? `1px solid ${HUNTER.LINE}` : 'none',
          }}>
            <div style={{
              display: 'flex', alignItems: 'center', gap: 8,
              fontSize: 11, color: HUNTER.INK_F, marginBottom: 6,
            }}>
              <span>{it.date}</span>
              {it.source && <span>· {it.source}</span>}
              <span style={{
                marginLeft: 'auto',
                padding: '1px 7px', borderRadius: 5,
                fontSize: 10.5, fontWeight: 600, letterSpacing: '.02em',
                background: style.bg, color: style.fg,
              }}>
                {style.label}
              </span>
            </div>
            <div style={{ fontSize: 14, lineHeight: 1.55, fontWeight: 600, marginBottom: 6 }}>
              {it.title}
            </div>
            {it.ai_note && (
              <div style={{
                fontSize: 12, color: HUNTER.COPPER3,
                background: HUNTER.BRAND_PALE, padding: '6px 10px', borderRadius: 8,
                display: 'flex', gap: 6, lineHeight: 1.5,
              }}>
                <span style={{ flexShrink: 0 }}>💬</span>
                <span>{it.ai_note}</span>
              </div>
            )}
            {it.url && (
              <a
                href={it.url}
                target="_blank" rel="noopener noreferrer"
                style={{
                  display: 'inline-flex', alignItems: 'center', gap: 4,
                  marginTop: 6, color: HUNTER.THEME, fontSize: 11, textDecoration: 'none',
                }}
              >
                查看原文 <ExternalLink size={10} />
              </a>
            )}
          </div>
        )
      })}
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
