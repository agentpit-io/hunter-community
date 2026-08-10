/**
 * 公开 Artifact 页 · /public/artifacts/[shortId]
 *
 * SSR fetch → 匿名可访问 → 顶栏 Hunter 品牌 + 底部 "注册体验" CTA
 * 模仿 https://claude.ai/public/artifacts/xxxx 的产品形态
 */
import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import PublicArtifactView from './PublicArtifactView'
import PublicHtmlArtifactView from './PublicHtmlArtifactView'

interface Artifact {
  short_id: string
  title: string
  artifact_type?: 'markdown' | 'html'
  content_md?: string | null
  content_html?: string | null
  published_at: string
  view_count: number
}

async function fetchArtifact(shortId: string, host: string, proto: string): Promise<Artifact | null> {
  const base = process.env.HERMES_API_INTERNAL_URL || `${proto}://${host}`
  try {
    const res = await fetch(`${base}/api/public/artifacts/${encodeURIComponent(shortId)}`, {
      cache: 'no-store',
    })
    if (!res.ok) return null
    return res.json()
  } catch {
    return null
  }
}

async function resolveHost(): Promise<{ host: string; proto: string }> {
  // Next 15 headers 是 async
  const { headers } = await import('next/headers')
  const h = await headers()
  const host = h.get('host') || 'localhost:3000'
  const proto = h.get('x-forwarded-proto') || (host.startsWith('localhost') ? 'http' : 'https')
  return { host, proto }
}

export async function generateMetadata({ params }: { params: Promise<{ shortId: string }> }): Promise<Metadata> {
  const { shortId } = await params
  const { host, proto } = await resolveHost()
  const art = await fetchArtifact(shortId, host, proto)
  if (!art) {
    return { title: '内容不存在 · Hunter' }
  }
  // HTML artifact 从 title 摘 · markdown 从正文摘
  const src = art.artifact_type === 'html' ? (art.title || '') : (art.content_md || '')
  const desc = src
    .replace(/^#+\s*/gm, '')
    .replace(/[*_`>#\-\[\]()]/g, '')
    .split('\n')
    .filter(Boolean)
    .join(' ')
    .slice(0, 160)
  return {
    title: `${art.title} · Hunter`,
    description: desc,
    openGraph: {
      title: art.title,
      description: desc,
      type: 'article',
      publishedTime: art.published_at,
    },
    twitter: {
      card: 'summary_large_image',
      title: art.title,
      description: desc,
    },
  }
}

export default async function PublicArtifactPage({ params }: { params: Promise<{ shortId: string }> }) {
  const { shortId } = await params
  const { host, proto } = await resolveHost()
  const art = await fetchArtifact(shortId, host, proto)
  if (!art) notFound()

  // Sprint E · HTML 类型走独立 view (iframe sandbox)
  if (art.artifact_type === 'html' && art.content_html) {
    return <PublicHtmlArtifactView artifact={{
      short_id: art.short_id,
      title: art.title,
      content_html: art.content_html,
      published_at: art.published_at,
      view_count: art.view_count,
    }} />
  }

  // markdown 分支保留原行为
  return <PublicArtifactView artifact={{
    short_id: art.short_id,
    title: art.title,
    content_md: art.content_md || '',
    published_at: art.published_at,
    view_count: art.view_count,
  }} />
}
