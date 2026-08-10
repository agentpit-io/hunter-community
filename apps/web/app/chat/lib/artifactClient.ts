/**
 * Artifact 发布 API 客户端 · 走 hermes-api /api/artifacts/*
 */

function getToken(): string {
  if (typeof window === 'undefined') return ''
  return localStorage.getItem('hunter_token') || ''
}

function authHeaders(): Record<string, string> {
  const t = getToken()
  const h: Record<string, string> = { 'Content-Type': 'application/json' }
  if (t) h['Authorization'] = `Bearer ${t}`
  return h
}

async function req<T = any>(method: string, path: string, body?: any): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: authHeaders(),
    body: body ? JSON.stringify(body) : undefined,
    cache: 'no-store',
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    let msg = text.slice(0, 200)
    try {
      const j = JSON.parse(text)
      msg = j?.detail || j?.message || msg
    } catch {}
    throw new Error(`${res.status}: ${msg}`)
  }
  return res.json()
}

export interface ArtifactStatus {
  published: boolean
  short_id?: string
  public_url?: string
  published_at?: string
  unpublished_at?: string
  republish_blocked?: boolean
}

export interface PublishResp {
  short_id: string
  public_url: string
  published_at: string
  title: string
}

export async function getStatus(messageId: string): Promise<ArtifactStatus> {
  return req('GET', `/api/artifacts/status?message_id=${encodeURIComponent(messageId)}`)
}

export async function publishArtifact(args: {
  sessionId?: string
  messageId: string
  title: string
  artifactType?: 'markdown' | 'html'   // Sprint E · 默认 markdown
  contentMd?: string                    // markdown 时必填
  contentHtml?: string                  // html 时必填
  metadata?: Record<string, any>
}): Promise<PublishResp> {
  return req('POST', '/api/artifacts/publish', {
    session_id: args.sessionId,
    message_id: args.messageId,
    title: args.title,
    artifact_type: args.artifactType || 'markdown',
    content_md: args.contentMd,
    content_html: args.contentHtml,
    metadata: args.metadata,
  })
}

export async function unpublishArtifact(shortId: string): Promise<{ ok: boolean; unpublished_at?: string }> {
  return req('POST', `/api/artifacts/${encodeURIComponent(shortId)}/unpublish`)
}

/**
 * 匿名访问 · 用于 /public/artifacts/[shortId] 页
 * 不带 auth · 直调即可
 */
export async function getPublicArtifact(shortId: string): Promise<{
  short_id: string
  title: string
  content_md: string
  published_at: string
  view_count: number
}> {
  const res = await fetch(`/api/public/artifacts/${encodeURIComponent(shortId)}`, {
    cache: 'no-store',
  })
  if (!res.ok) {
    if (res.status === 404) throw new Error('此报告不存在或已被撤销发布')
    throw new Error(`加载失败: ${res.status}`)
  }
  return res.json()
}
