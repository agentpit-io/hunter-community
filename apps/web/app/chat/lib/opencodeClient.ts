// opencode HTTP 客户端 · 走 BFF /api/opencode/*
// 复用 hermes 现有 hunter_token JWT

import type { Session, Message } from './types'

const BFF_BASE = '/api/opencode'

function getToken(): string {
  if (typeof window === 'undefined') return ''
  return localStorage.getItem('hunter_token') || ''
}

function authHeaders(): Record<string, string> {
  const token = getToken()
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) headers['Authorization'] = `Bearer ${token}`
  return headers
}

async function req<T = any>(method: string, path: string, body?: any): Promise<T> {
  const url = `${BFF_BASE}${path}`
  let res: Response
  try {
    res = await fetch(url, {
      method,
      headers: authHeaders(),
      body: body ? JSON.stringify(body) : undefined,
      credentials: 'same-origin',
      cache: 'no-store',
    })
  } catch (e: any) {
    // TypeError: Failed to fetch · 常见: 网络离线 · CORS · 服务端崩溃
    console.error(`[opencodeClient] ${method} ${url} network err:`, e)
    throw new Error(`网络请求失败 · ${method} ${path} · ${e?.message || e}`)
  }
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    console.error(`[opencodeClient] ${method} ${url} status ${res.status}:`, text.slice(0, 200))
    // 尝试把 BFF 的 typed error 转成对用户友好的中文提示
    // (2026-08-10 · 之前把整段 JSON 抛给用户 · 看不懂)
    let friendly = ''
    try {
      const j = JSON.parse(text)
      if (j?.error === 'upstream_timeout') {
        const sec = Math.round((j.duration_ms || 0) / 1000)
        friendly = `处理超时(${sec} 秒 · 上限 10 分钟)· 请拆分或简化问题后重试`
      } else if (j?.error === 'upstream_unreachable') {
        friendly = '会话服务暂不可用 · 请稍后重试'
      } else if (j?.error === 'ownership_unavailable') {
        friendly = '会话服务暂不可用 · 请稍后重试'
      } else if (j?.error === 'forbidden') {
        friendly = j?.message || '无权访问该对话'
      } else if (j?.error === 'unauthorized') {
        friendly = '需要登录后重试'
      } else if (j?.message) {
        friendly = j.message
      } else if (j?.detail) {
        friendly = String(j.detail).slice(0, 120)
      }
    } catch {
      /* 非 JSON body · 用原文兜底 */
    }
    const msg = friendly || text.slice(0, 120)
    throw new Error(`${method} ${path} · HTTP ${res.status} · ${msg}`)
  }
  const contentType = res.headers.get('content-type') || ''
  if (contentType.includes('application/json')) return res.json()
  return (await res.text()) as any
}

// ─── Sessions ───────────────────────────────────────

export async function listSessions(): Promise<Session[]> {
  const data = await req<Session[] | { data: Session[] }>('GET', '/session')
  return Array.isArray(data) ? data : (data?.data ?? [])
}

export async function createSession(title?: string): Promise<Session> {
  return req<Session>('POST', '/session', { title: title || '新对话' })
}

export async function getSession(id: string): Promise<Session> {
  return req<Session>('GET', `/session/${encodeURIComponent(id)}`)
}

export async function deleteSession(id: string): Promise<void> {
  await req('DELETE', `/session/${encodeURIComponent(id)}`)
}

// ─── Messages ──────────────────────────────────────

export async function listMessages(sessionId: string): Promise<Message[]> {
  const data = await req<any>(
    'GET',
    `/session/${encodeURIComponent(sessionId)}/message`,
  )
  const raw = Array.isArray(data) ? data : (data?.data ?? [])
  // opencode 返回 { info: {id, sessionID, role, time, ...}, parts: [...] }
  // 我们的 Message 类型是 { id, role, parts, ... } · 需展平 info 到顶层
  // 否则 reduceEvents 找不到 m.id · 收到 SSE server.connected 后 · filter 会把整个 messages 过滤空
  return raw.map((m: any) => {
    if (m && m.info) {
      return { ...m.info, parts: m.parts || [] }
    }
    return m
  })
}

export interface SendMessageArgs {
  sessionId: string
  text: string
  agent?: string
  model?: { providerID: string; modelID: string }
}

export async function sendMessage(args: SendMessageArgs): Promise<any> {
  const body: any = {
    parts: [{ type: 'text', text: args.text }],
  }
  if (args.agent) body.agent = args.agent
  if (args.model) body.model = args.model
  return req(
    'POST',
    `/session/${encodeURIComponent(args.sessionId)}/message`,
    body,
  )
}

export async function abortSession(sessionId: string): Promise<void> {
  await req('POST', `/session/${encodeURIComponent(sessionId)}/abort`, {})
}

// ─── Providers / Agents (for pickers) ─────────────

export interface ProviderInfo {
  id: string
  name: string
  models: Record<string, { id?: string; name?: string; [k: string]: any }>
}

export async function listProviders(): Promise<ProviderInfo[]> {
  try {
    const data = await req<any>('GET', '/config/providers')
    // 真实结构: {providers: [{id, name, models: {...}}]}
    if (data?.providers && Array.isArray(data.providers)) {
      return data.providers.map((p: any) => ({
        id: p.id,
        name: p.name || p.id,
        models: p.models || {},
      }))
    }
    if (Array.isArray(data)) return data
  } catch (e) {
    console.warn('[opencodeClient] listProviders failed:', e)
  }
  return []
}

export interface AgentInfo {
  name: string
  description?: string
  mode?: string
  native?: boolean
  model?: { providerID?: string; modelID?: string }
}

export async function listAgents(): Promise<AgentInfo[]> {
  try {
    // 真实 endpoint 是 /agent (不是 /config/agents)
    const data = await req<AgentInfo[]>('GET', '/agent')
    if (Array.isArray(data)) return data
  } catch (e) {
    console.warn('[opencodeClient] listAgents failed:', e)
  }
  // 兜底
  return [
    { name: 'build', description: '任务执行 · 默认 agent' },
    { name: 'plan', description: '规划 · 拆解 · 不执行' },
  ]
}

// ─── Session config ops ─────────────────────────

export async function switchSessionAgent(sessionId: string, agentName: string): Promise<any> {
  return req('POST', `/api/session/${encodeURIComponent(sessionId)}/agent`, {
    agent: agentName,
  })
}

export async function switchSessionModel(
  sessionId: string,
  providerID: string,
  modelID: string,
): Promise<any> {
  return req('POST', `/api/session/${encodeURIComponent(sessionId)}/model`, {
    model: { providerID, modelID },
  })
}

export async function renameSession(sessionId: string, title: string): Promise<any> {
  return req('PATCH', `/session/${encodeURIComponent(sessionId)}`, { title })
}
