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
      } else if (j?.error === 'upstream_unreachable' || j?.error === 'ownership_unavailable') {
        // 502 · opencode 容器不通。绝大多数是 .env 里 LLM_* 三项少填导致 opencode
        // 拒启动(见 scripts/opencode/gen-config.py),用户看到 "稍后重试" 会一直等,
        // 所以明确指路 —— 让人知道要去看 opencode 日志、去补 .env,而不是刷新页面。
        friendly = '对话引擎(opencode)未就绪 · 常见是 .env 里 LLM_API_KEY / LLM_BASE_URL / LLM_DEFAULT_MODEL 少填 · 详见 docker compose logs opencode,补齐后 docker compose up -d'
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

/**
 * opencode 自己声明的默认模型 · 形如 {"hunter-llm": "gemini-3-flash-preview"}。
 *
 * 开源版的 provider id 和模型名都由用户的 .env 决定(见 scripts/opencode/gen-config.py),
 * 前端不能硬编码 —— 猜错了发消息就是 500 UnknownError,而且报错里完全看不出
 * 是模型名对不上。所以问 opencode 要。
 *
 * 返回 "providerID/modelID";拿不到返回空串,调用方此时**不要**传 model 字段,
 * 让 opencode 用配置文件里的默认值。
 */
export async function defaultModelKey(): Promise<string> {
  try {
    const data = await req<any>('GET', '/config/providers')
    const def = data?.default
    if (def && typeof def === 'object') {
      const ids: string[] = (data.providers || []).map((p: any) => p.id)
      // 优先我们自己配的那个 · 'opencode' 是镜像内置的 OpenCode Zen,不是用户想要的
      const pick = ids.find((id) => id !== 'opencode' && def[id]) || ids.find((id) => def[id])
      if (pick) return `${pick}/${def[pick]}`
    }
  } catch (e) {
    console.warn('[opencodeClient] defaultModelKey failed:', e)
  }
  return ''
}

/**
 * 校验存在 localStorage 里的模型选择,失效就换成默认值。
 *
 * 为什么需要:早期版本默认写死 'oneapi/gemini-3.5-flash'(生产实例的 provider id),
 * 已经存进老用户浏览器了。改默认值救不了他们 —— 代码只在 localStorage 为空时才用
 * 新默认。他们每次发消息都是 500 UnknownError,而报错里看不出是模型名对不上,
 * 只能靠"清浏览器缓存"这种没人猜得到的操作。
 *
 * 所以拿实时的 provider 清单校验一遍,对不上就换掉并覆写 localStorage —— 自愈,
 * 用户无感。
 */
export async function resolveModelKey(saved: string | null): Promise<string> {
  try {
    const data = await req<any>('GET', '/config/providers')
    const providers: any[] = data?.providers || []
    if (saved) {
      const [pid, ...rest] = saved.split('/')
      const mid = rest.join('/')
      const p = providers.find((x) => x.id === pid)
      if (p && mid && (p.models || {})[mid]) return saved      // 还有效
    }
    const def = data?.default
    if (def && typeof def === 'object') {
      const ids: string[] = providers.map((p) => p.id)
      // 'opencode' 是镜像内置的 OpenCode Zen,不是用户配的那个,排在最后
      const pick = ids.find((id) => id !== 'opencode' && def[id]) || ids.find((id) => def[id])
      if (pick) return `${pick}/${def[pick]}`
    }
  } catch (e) {
    console.warn('[opencodeClient] resolveModelKey failed:', e)
  }
  return ''
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
