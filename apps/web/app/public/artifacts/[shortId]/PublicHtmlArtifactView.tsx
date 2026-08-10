'use client'
/**
 * 公开 HTML Artifact 页 · Sprint E
 * iframe sandbox 包裹用户 HTML · 上方 Hunter 品牌 header · 下方注册 CTA
 */
import Link from 'next/link'
import { Copy, Check } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { HUNTER } from '../../../lib/hunter-theme'

interface Artifact {
  short_id: string
  title: string
  content_html: string
  published_at: string
  view_count: number
}

function fmtDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' })
  } catch { return '' }
}

export default function PublicHtmlArtifactView({ artifact }: { artifact: Artifact }) {
  const [copied, setCopied] = useState(false)
  const iframeRef = useRef<HTMLIFrameElement | null>(null)
  // 默认 900px · 加载期若 postMessage 失效 · iframe 内部可滚(scrolling 默认 auto)
  const [iframeHeight, setIframeHeight] = useState<number>(900)
  const settledRef = useRef(false)

  // 3.5 秒后进入"沉降"态 · 允许精确匹配内容高度(允许缩小 · 消除底部空白)
  useEffect(() => {
    const t = setTimeout(() => { settledRef.current = true }, 3500)
    return () => clearTimeout(t)
  }, [])

  // 监听 iframe 里 HTML 上报的高度 · 动态调整
  useEffect(() => {
    const handler = (e: MessageEvent) => {
      if (e?.data?.type === 'hunter-artifact-height' && typeof e.data.height === 'number') {
        const target = Math.max(400, Math.ceil(e.data.height) + 32)
        setIframeHeight((prev) =>
          settledRef.current ? target : Math.max(prev, target)
        )
      }
    }
    window.addEventListener('message', handler)
    return () => window.removeEventListener('message', handler)
  }, [])

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
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      {/* 品牌顶栏 */}
      <header
        style={{
          background: HUNTER.HEADER_BG,
          color: '#F1EAD6',
          padding: '12px 24px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          borderBottom: '1px solid rgba(255,255,255,0.08)',
          flexShrink: 0,
        }}
      >
        <Link
          href="/"
          style={{ display: 'flex', alignItems: 'center', gap: 10, textDecoration: 'none', color: '#F1EAD6' }}
        >
          <div
            style={{
              width: 30, height: 30, borderRadius: 8,
              background: HUNTER.THEME, display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 14, fontWeight: 800, color: '#fff', fontFamily: HUNTER.SERIF,
            }}
          >猎</div>
          <div>
            <div style={{ fontFamily: HUNTER.SERIF, fontSize: 15, fontWeight: 700 }}>
              Hunter · 猎鹿人
            </div>
            <div style={{ fontSize: 10, color: 'rgba(241,234,214,0.6)', marginTop: 1 }}>
              交互式报告 · 公开分享
            </div>
          </div>
        </Link>
        <button
          type="button"
          onClick={handleCopyLink}
          style={{
            display: 'flex', alignItems: 'center', gap: 6,
            padding: '5px 11px', background: 'rgba(255,255,255,0.08)',
            border: '1px solid rgba(255,255,255,0.15)', borderRadius: 6,
            color: copied ? '#B6E4B7' : '#F1EAD6', fontSize: 12,
            fontFamily: 'inherit', fontWeight: 600, cursor: 'pointer',
          }}
        >
          {copied ? <Check size={12} /> : <Copy size={12} />}
          {copied ? '已复制' : '复制链接'}
        </button>
      </header>

      {/* 元信息条 */}
      <div
        style={{
          padding: '12px 24px',
          background: HUNTER.PAPER,
          borderBottom: `1px solid ${HUNTER.LINE}`,
          display: 'flex', alignItems: 'center', gap: 10,
          fontSize: 11.5, color: HUNTER.INK_F, flexShrink: 0,
        }}
      >
        <span style={{
          padding: '2px 7px', background: '#EAF3EB', color: HUNTER.DN,
          borderRadius: 3, fontSize: 10, fontWeight: 700, letterSpacing: '0.05em',
        }}>
          PUBLIC HTML ARTIFACT
        </span>
        <span style={{ fontFamily: HUNTER.SERIF, fontWeight: 700, color: HUNTER.INK, fontSize: 13 }}>
          {artifact.title}
        </span>
        {artifact.published_at && <span>· 发布于 {fmtDate(artifact.published_at)}</span>}
      </div>

      {/* HTML iframe · sandbox 隔离 · 只开 allow-scripts
          · 高度动态跟随内容 · 保留 iframe 内滚动作为兜底(postMessage 失效时) */}
      <div style={{ background: '#fff', width: '100%', flexShrink: 0 }}>
        <iframe
          ref={iframeRef}
          srcDoc={artifact.content_html}
          sandbox="allow-scripts"
          title={artifact.title}
          style={{
            width: '100%',
            height: `${iframeHeight}px`,
            border: 'none',
            display: 'block',
          }}
        />
      </div>
    </div>
  )
}
