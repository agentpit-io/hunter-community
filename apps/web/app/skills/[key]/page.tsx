'use client'

// 内置能力详情页 · 公开可分享 · SEO 友好 URL
//
// 2026-08-10 · 方案 C 落地：
//   用户在能力管理里点"详情"跳过来 · 或直接访问 /skills/forecast 等
//   展示：品牌 header + 长描述 + 底层 tool 列表 + 来源 GitHub 链接 + "在 chat 使用" CTA
//
// 自定义能力（custom:{id}）不进详情页 —— 属于用户私有

import { useEffect, useState, use } from 'react'
import Link from 'next/link'
import { ExternalLink, Wrench, ChevronLeft, Send, AlertCircle } from 'lucide-react'
import { getSkillDetail, type SkillDetail } from '../../chat/lib/skillClient'
import { HUNTER } from '../../lib/hunter-theme'

interface Props {
  params: Promise<{ key: string }>
}

// 品牌颜色映射 · 与 ToolCallCard SKILL badge 保持一致
const BRAND_STYLE: Record<string, { color: string; bg: string }> = {
  'Kronos':        { color: '#7c3aed', bg: '#f3f0ff' },
  'UZI':           { color: '#7C4A22', bg: '#F6EEE7' },
  'TradingAgents': { color: '#0e7490', bg: '#ecfeff' },
  'finance-data':  { color: '#525252', bg: '#f5f5f4' },
  'Hunter 内置':   { color: '#3F6B40', bg: '#EAF3EB' },
}

function brandStyleOf(brand: string) {
  return BRAND_STYLE[brand] || { color: HUNTER.INK_S, bg: HUNTER.PAPER2 }
}

export default function SkillDetailPage({ params }: Props) {
  const { key } = use(params)
  const [data, setData] = useState<SkillDetail | null>(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    getSkillDetail(key)
      .then((d) => { if (!cancelled) setData(d) })
      .catch((e) => { if (!cancelled) setErr(e?.message || '加载失败') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [key])

  const gotoChat = () => {
    if (!data) return
    // 直接把 prompt_tpl 送进 chat autoText 通道
    window.location.href = `/chat?q=${encodeURIComponent(data.prompt_tpl)}`
  }

  if (loading) {
    return (
      <div style={pageStyle}>
        <div style={{ ...cardStyle, padding: 40, textAlign: 'center', color: HUNTER.INK_F }}>
          加载中…
        </div>
      </div>
    )
  }

  if (err || !data) {
    return (
      <div style={pageStyle}>
        <div style={cardStyle}>
          <div style={{ padding: 30, textAlign: 'center' }}>
            <AlertCircle size={40} style={{ color: HUNTER.INK_F, margin: '0 auto 12px' }} />
            <div style={{ fontSize: 15, color: HUNTER.INK, marginBottom: 6 }}>找不到这个能力</div>
            <div style={{ fontSize: 12, color: HUNTER.INK_F }}>{err || 'key 不存在或已下线'}</div>
            <Link href="/chat" style={{
              display: 'inline-flex', alignItems: 'center', gap: 4, marginTop: 20,
              padding: '8px 16px', background: HUNTER.THEME, color: '#fff',
              borderRadius: 8, textDecoration: 'none', fontSize: 13,
            }}>
              <ChevronLeft size={14} /> 返回 Hunter
            </Link>
          </div>
        </div>
      </div>
    )
  }

  const bs = brandStyleOf(data.brand)

  return (
    <div style={pageStyle}>
      <div style={cardStyle}>
        {/* 顶栏 · 返回 */}
        <div style={{ padding: '14px 20px', borderBottom: `1px solid ${HUNTER.LINE}` }}>
          <Link href="/chat" style={{
            display: 'inline-flex', alignItems: 'center', gap: 4,
            color: HUNTER.INK_F, textDecoration: 'none', fontSize: 13,
          }}>
            <ChevronLeft size={14} /> 返回 Hunter
          </Link>
        </div>

        {/* Hero · icon + name + brand badge */}
        <div style={{ padding: '28px 24px 20px', borderBottom: `1px solid ${HUNTER.LINE}` }}>
          <div style={{ fontSize: 44, marginBottom: 10 }}>{data.icon}</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8, flexWrap: 'wrap' }}>
            <h1 style={{
              fontSize: 24, fontWeight: 700, color: HUNTER.INK, margin: 0,
              fontFamily: HUNTER.SERIF,
            }}>
              {data.name}
            </h1>
            {data.brand && (
              <span style={{
                padding: '3px 10px', borderRadius: 5,
                background: bs.bg, color: bs.color,
                fontSize: 11, fontWeight: 700, letterSpacing: '.03em',
              }}>
                {data.brand}
              </span>
            )}
            {data.category && (
              <span style={{
                padding: '3px 10px', borderRadius: 5,
                background: HUNTER.PAPER2, color: HUNTER.INK_S,
                fontSize: 11, fontWeight: 600, letterSpacing: '.02em',
              }}>
                {data.category}
              </span>
            )}
          </div>
          {data.hint && (
            <div style={{ fontSize: 13, color: HUNTER.INK_S }}>{data.hint}</div>
          )}
        </div>

        {/* 详细描述 */}
        {data.long_desc && (
          <div style={{ padding: '18px 24px', borderBottom: `1px solid ${HUNTER.LINE}` }}>
            <div style={sectionTitle}>它能做什么</div>
            <div style={{ fontSize: 14, color: HUNTER.INK, lineHeight: 1.75 }}>
              {data.long_desc}
            </div>
          </div>
        )}

        {/* 底层调用 tools */}
        {data.tools.length > 0 && (
          <div style={{ padding: '18px 24px', borderBottom: `1px solid ${HUNTER.LINE}` }}>
            <div style={sectionTitle}>底层调用</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {data.tools.map((t) => (
                <div key={t} style={{
                  display: 'inline-flex', alignItems: 'center', gap: 6,
                  padding: '7px 10px', background: HUNTER.PAPER2,
                  borderRadius: 6, fontSize: 12.5,
                  fontFamily: 'ui-monospace, "SF Mono", Menlo, monospace',
                  color: HUNTER.INK_S, width: 'fit-content',
                }}>
                  <Wrench size={12} style={{ color: HUNTER.INK_F }} />
                  {t}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 提问模板 */}
        <div style={{ padding: '18px 24px', borderBottom: `1px solid ${HUNTER.LINE}` }}>
          <div style={sectionTitle}>点一下就发出这段话</div>
          <div style={{
            padding: 14, background: HUNTER.BRAND_PALE,
            borderRadius: 8, fontSize: 14, color: HUNTER.INK,
            border: `1px solid ${HUNTER.LINE}`,
          }}>
            {data.prompt_tpl}
          </div>
          <div style={{ fontSize: 11, color: HUNTER.INK_F, marginTop: 6 }}>
            <code style={{ background: HUNTER.PAPER2, padding: '1px 4px', borderRadius: 3 }}>{'{股票}'}</code> 会替换成你填的股票名
          </div>
        </div>

        {/* 来源 */}
        {data.source_url && (
          <div style={{ padding: '18px 24px', borderBottom: `1px solid ${HUNTER.LINE}` }}>
            <div style={sectionTitle}>来源</div>
            <a
              href={data.source_url}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 5,
                color: HUNTER.THEME, textDecoration: 'none', fontSize: 13,
              }}
            >
              {data.source_url}
              <ExternalLink size={12} />
            </a>
          </div>
        )}

        {/* CTA */}
        <div style={{ padding: '20px 24px', display: 'flex', gap: 10 }}>
          <button
            onClick={gotoChat}
            style={{
              flex: 1, padding: '12px 0', background: HUNTER.THEME,
              color: '#fff', border: 'none', borderRadius: 10,
              fontSize: 14, fontWeight: 600, cursor: 'pointer',
              display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 6,
            }}
          >
            <Send size={14} /> 在 Hunter chat 里使用
          </button>
        </div>
      </div>
    </div>
  )
}

const pageStyle: React.CSSProperties = {
  minHeight: '100vh',
  background: HUNTER.PAPER3,
  padding: '30px 16px',
  display: 'flex',
  justifyContent: 'center',
  alignItems: 'flex-start',
}

const cardStyle: React.CSSProperties = {
  width: '100%',
  maxWidth: 640,
  background: HUNTER.PAPER,
  borderRadius: 14,
  border: `1px solid ${HUNTER.LINE}`,
  boxShadow: '0 8px 24px rgba(30,20,10,.10)',
}

const sectionTitle: React.CSSProperties = {
  fontSize: 11,
  color: HUNTER.INK_F,
  fontWeight: 700,
  letterSpacing: '.05em',
  marginBottom: 8,
  textTransform: 'uppercase',
}
