// 用户画像 + 记忆体 API 客户端 · 走 hermes-api /api/chat/*

export interface Profile {
  risk_style: string
  max_drawdown: number | null
  horizon: string
  markets: string[]
  sectors: string[]
  cap_pref: string
  weight_order: string[]
  verbosity: string
  taboos: string[]
  onboarded: boolean
}

export interface ProfileOptions {
  risk_styles: string[]
  horizons: string[]
  markets: string[]
  cap_prefs: string[]
  weights: string[]
  verbosity: string[]
  labels: Record<string, string>
}

export interface MemoryResp {
  memory: {
    mentioned_symbols?: { code: string; name?: string; count: number }[]
    recurring_topics?: { topic: string; count: number }[]
    stated_positions?: { symbol: string; note: string; source?: string }[]
  }
  session_count: number
  system_prompt_preview?: string
}

function authHeaders(): Record<string, string> {
  const h: Record<string, string> = { 'Content-Type': 'application/json' }
  if (typeof window !== 'undefined') {
    const t = localStorage.getItem('hunter_token') || ''
    if (t) h['Authorization'] = `Bearer ${t}`
  }
  return h
}

async function req<T = any>(method: string, path: string, body?: any): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    headers: authHeaders(),
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: 'no-store',
  })
  if (!res.ok) {
    const t = await res.text().catch(() => '')
    let msg = t.slice(0, 160)
    try {
      msg = JSON.parse(t)?.detail || msg
    } catch {
      /* 非 JSON 错误体 */
    }
    throw new Error(msg || `HTTP ${res.status}`)
  }
  return res.json()
}

export const getProfile = () => req<{ profile: Profile; options: ProfileOptions }>('GET', '/chat/profile')
export const saveProfile = (patch: Partial<Profile>) =>
  req<{ profile: Profile; msg: string }>('PUT', '/chat/profile', patch)

export const getMemory = () => req<MemoryResp>('GET', '/chat/memory')
export const clearMemory = () => req('DELETE', '/chat/memory')

/** 会话结束时把用户说过的话交给后端浓缩 */
export const condenseMemory = (session_id: string, texts: string[], symbols?: Record<string, string>) =>
  req('POST', '/chat/memory/condense', { session_id, texts, symbols })

// 行业选项 —— 与选股页的产业链口径保持一致,避免用户填出五花八门的自定义词
export const SECTOR_PRESETS = [
  'AI算力', '半导体', '新能源', '医药生物', '消费', '金融', '军工',
  '机器人', '汽车', '光伏', '储能', '有色', '化工', '传媒', '房地产',
]

export const TABOO_PRESETS = ['ST股', '次新股', '高负债', '亏损股', '强周期']
