// 用户自定义数据源的客户端 · `_21` 步 2/3
//
// 单独一个文件而不是塞进 catalogClient:那个走 /api/catalog(只读、免登录、
// 描述"这套部署能拿到什么"),这个走 /api/user_sources(读写、要登录、
// 带用户凭证)。两者的鉴权与缓存策略都不同,混在一起早晚出错。

function authHeaders(): Record<string, string> {
  const h: Record<string, string> = { 'Content-Type': 'application/json' }
  if (typeof window !== 'undefined') {
    const t = localStorage.getItem('hunter_token') || ''
    if (t) h['Authorization'] = `Bearer ${t}`
  }
  return h
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(`/api/user_sources${path}`, {
    method,
    headers: authHeaders(),
    body: body ? JSON.stringify(body) : undefined,
    cache: 'no-store',
  })
  const d = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error((d as any)?.detail || (d as any)?.message || `HTTP ${r.status}`)
  return d as T
}

export interface UserSourceItem {
  id: number
  name: string
  upstream: string
  market: string
  kind: string
  endpoint: string
  enabled: boolean
  has_api_key: boolean
  api_key_hint: string
  last_err: string
}

export interface UserSourcesResponse {
  sources: UserSourceItem[]
  max: number
  enabled_count: number
  /** 有没有配平台 key —— 决定「一键用官方」是"去填 key"还是"停用你的源" */
  platform_key: boolean
}

export const listUserSources = () => req<UserSourcesResponse>('GET', '')

/** 批量停用/启用。**停用不是删除** —— 用户点「一键用官方」多半是在排查
 *  "我自己配的是不是坏了",排查完要能一键切回去。 */
export const bulkEnableUserSources = (enabled: boolean) =>
  req<{ changed: number; total: number; enabled: boolean }>('POST', '/bulk-enable', { enabled })
