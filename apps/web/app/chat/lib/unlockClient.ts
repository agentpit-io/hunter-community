// 平台 key 状态 · 走 api 的 /api/hunter/unlock
//
// 开源版自带 LLM key 就能聊天;左侧那些工具与 SKILL 在 Hunter 服务端执行,
// 需要一把我们签发的 key。这个模块只管"现在解锁了没"和"存一把 key 进去"。
//
// 状态在模块级缓存:侧栏按钮、SKILL 面板、弹窗三处都要读它,
// 每处各请求一次会在首屏打三发。任何写操作(保存/清除)后失效重取。

export interface UnlockTool {
  name: string
  title: string
  desc: string
}

export interface UnlockStatus {
  configured: boolean      // 有没有填过 key
  unlocked: boolean        // 这把 key 现在真的能用吗(吊销过就是 configured && !unlocked)
  masked: string
  env_locked: boolean      // key 来自 .env,UI 改不了
  apply_url: string
  message: string | null
  tools: UnlockTool[]
  upstream_error: boolean  // 连不上 Hunter,不等于没解锁
}

const FALLBACK_APPLY = 'https://hunter.agentpit.io/dev/api-keys'

function authHeaders(): Record<string, string> {
  const h: Record<string, string> = { 'Content-Type': 'application/json' }
  if (typeof window !== 'undefined') {
    const t = localStorage.getItem('hunter_token') || ''
    if (t) h['Authorization'] = `Bearer ${t}`
  }
  return h
}

let cache: UnlockStatus | null = null
let inflight: Promise<UnlockStatus> | null = null
const listeners = new Set<(s: UnlockStatus) => void>()

/** 订阅状态变化 · 返回取消订阅函数 */
export function onUnlockChange(fn: (s: UnlockStatus) => void): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

function publish(s: UnlockStatus) {
  cache = s
  listeners.forEach((fn) => fn(s))
}

const LOCKED: UnlockStatus = {
  configured: false, unlocked: false, masked: '', env_locked: false,
  apply_url: FALLBACK_APPLY, message: null, tools: [], upstream_error: false,
}

export async function getUnlockStatus(force = false): Promise<UnlockStatus> {
  if (!force && cache) return cache
  if (!force && inflight) return inflight
  inflight = (async () => {
    try {
      const r = await fetch('/api/hunter/unlock', { headers: authHeaders(), cache: 'no-store' })
      if (!r.ok) return LOCKED
      const s = (await r.json()) as UnlockStatus
      publish(s)
      return s
    } catch {
      // 拿不到状态就当没解锁 —— 宁可多提示一次申请,也别让用户点了工具才发现不能用
      return LOCKED
    } finally {
      inflight = null
    }
  })()
  return inflight
}

/** 同步读缓存 · 首帧渲染用,没缓存时返回 null 表示"还不知道" */
export function peekUnlockStatus(): UnlockStatus | null {
  return cache
}

export async function saveKey(key: string): Promise<UnlockStatus> {
  const r = await fetch('/api/hunter/unlock', {
    method: 'PUT', headers: authHeaders(), body: JSON.stringify({ key }),
  })
  const d = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(d?.detail || `HTTP ${r.status}`)
  publish(d as UnlockStatus)
  return d as UnlockStatus
}

export async function clearKey(): Promise<void> {
  await fetch('/api/hunter/unlock', { method: 'DELETE', headers: authHeaders() })
  await getUnlockStatus(true)
}

export const APPLY_URL_FALLBACK = FALLBACK_APPLY
