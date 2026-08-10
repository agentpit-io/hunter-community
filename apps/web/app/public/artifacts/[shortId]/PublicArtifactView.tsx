'use client'
/**
 * 匿名访客 · 公开 Artifact 展示
 * · 顶栏 Hunter logo · 底部 "注册体验" CTA
 * · 复用 ReportViewer 渲染 markdown
 */
import Link from 'next/link'
import { ArrowRight, ExternalLink, Copy, Check } from 'lucide-react'
import { useState } from 'react'
import { HUNTER } from '../../../lib/hunter-theme'
import ReportViewer from '../../../chat/components/ReportViewer'

interface Artifact {
  short_id: string
  title: string
  content_md: string
  published_at: string
  view_count: number
}

function fmtDate(iso: string): string {
  try {
    const d = new Date(iso)
    return d.toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' })
  } catch {
    return ''
  }
}

export default function PublicArtifactView({ artifact }: { artifact: Artifact }) {
  const [copied, setCopied] = useState(false)

  const handleCopyLink = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {}
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        background: HUNTER.BG,
        fontFamily: HUNTER.SANS,
        color: HUNTER.INK,
      }}
    >
      {/* 顶栏 · 品牌 + 复制链接 */}
      <header
        style={{
          background: HUNTER.HEADER_BG,
          color: '#F1EAD6',
          padding: '14px 24px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          borderBottom: '1px solid rgba(255,255,255,0.08)',
        }}
      >
        <Link
          href="/"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            textDecoration: 'none',
            color: '#F1EAD6',
          }}
        >
          <div
            style={{
              width: 32,
              height: 32,
              borderRadius: 8,
              background: HUNTER.THEME,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 16,
              fontWeight: 800,
              color: '#fff',
              fontFamily: HUNTER.SERIF,
            }}
          >
            猎
          </div>
          <div>
            <div style={{ fontFamily: HUNTER.SERIF, fontSize: 16, fontWeight: 700, letterSpacing: '0.02em' }}>
              Hunter · 猎鹿人
            </div>
            <div style={{ fontSize: 10, color: 'rgba(241,234,214,0.6)', marginTop: 1 }}>
              AI 财经聚合 · 一份公开报告
            </div>
          </div>
        </Link>
        <button
          type="button"
          onClick={handleCopyLink}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            padding: '6px 12px',
            background: 'rgba(255,255,255,0.08)',
            border: '1px solid rgba(255,255,255,0.15)',
            borderRadius: 6,
            color: copied ? '#B6E4B7' : '#F1EAD6',
            fontSize: 12,
            fontFamily: 'inherit',
            fontWeight: 600,
            cursor: 'pointer',
          }}
        >
          {copied ? <Check size={13} /> : <Copy size={13} />}
          {copied ? '已复制' : '复制链接'}
        </button>
      </header>

      {/* 元信息条 */}
      <div
        style={{
          maxWidth: 880,
          margin: '0 auto',
          padding: '20px 40px 0',
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          fontSize: 12,
          color: HUNTER.INK_F,
          borderBottom: `1px solid ${HUNTER.LINE}`,
          paddingBottom: 14,
          marginBottom: 0,
        }}
      >
        <span
          style={{
            padding: '2px 8px',
            background: '#EAF3EB',
            color: HUNTER.DN,
            borderRadius: 3,
            fontSize: 10.5,
            fontWeight: 700,
            letterSpacing: '0.05em',
          }}
        >
          PUBLIC ARTIFACT
        </span>
        {artifact.published_at && (
          <span>发布于 {fmtDate(artifact.published_at)}</span>
        )}
      </div>

      {/* 正文 */}
      <main
        style={{
          background: '#ffffff',
          maxWidth: 880,
          margin: '0 auto',
          minHeight: 'calc(100vh - 240px)',
        }}
        className="artifact-body hunter-report"
      >
        <ReportViewer text={artifact.content_md} title={artifact.title} />
      </main>

      {/* 底部 CTA · 注册体验 */}
      <section
        style={{
          maxWidth: 880,
          margin: '0 auto',
          padding: '32px 40px 48px',
          background: '#ffffff',
          borderTop: `1px dashed ${HUNTER.LINE}`,
        }}
      >
        <div
          style={{
            background: HUNTER.PAPER3,
            border: `1px solid ${HUNTER.LINE}`,
            borderRadius: 12,
            padding: '20px 24px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 16,
            flexWrap: 'wrap',
          }}
        >
          <div style={{ flex: 1, minWidth: 240 }}>
            <div style={{ fontFamily: HUNTER.SERIF, fontSize: 17, fontWeight: 700, color: HUNTER.INK, marginBottom: 4 }}>
              喜欢这份报告?
            </div>
            <div style={{ fontSize: 13, color: HUNTER.INK_S, lineHeight: 1.6 }}>
              Hunter 多智能体协同 · A股/港股实时行情 · K线预测 · 微信推送。
              <br />
              免费注册即可让 AI 为你的自选股生成同款深度分析。
            </div>
          </div>
          <Link
            href="/"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              padding: '10px 20px',
              background: HUNTER.THEME,
              color: '#fff',
              borderRadius: 8,
              textDecoration: 'none',
              fontSize: 13.5,
              fontWeight: 700,
              flexShrink: 0,
              boxShadow: '0 2px 6px rgba(176,106,50,0.35)',
            }}
          >
            注册体验 <ArrowRight size={15} />
          </Link>
        </div>

        <div
          style={{
            marginTop: 20,
            fontSize: 11.5,
            color: HUNTER.INK_F,
            textAlign: 'center',
            lineHeight: 1.7,
          }}
        >
          <ExternalLink size={11} style={{ verticalAlign: -1, marginRight: 4 }} />
          此为发布者主动公开的分享链接 · 内容仅代表其个人观点 · 不构成投资建议
          <br />
          Powered by <Link href="/" style={{ color: HUNTER.THEME, textDecoration: 'none', fontWeight: 600 }}>Hunter · 猎鹿人</Link>
        </div>
      </section>
    </div>
  )
}
