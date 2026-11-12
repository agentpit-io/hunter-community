// 首启向导 · /api/setup/* 的调用层(设计方案 4.1)
//
// **一律走同源相对路径** `/api/setup/*`,不用 NEXT_PUBLIC_API_URL 拼绝对地址。
// 两个原因:
//   1. 向导的来源判断依赖 web BFF 注入的 `X-Hunter-Forwarded-For`
//      (apps/web/app/api/[...path]/route.ts)。绕开 web 直连 api 的话,
//      api 看到的对端就是浏览器自己,行为和真实部署不一致;
//   2. 云平台上只有 web 有公网域名,api 根本不对外。
//
// 初始化会话 token 放 **sessionStorage**:它是一把 30 分钟的管理员级凭证,
// 关掉标签页就该失效;localStorage 会一直留着。

export type CheckState = 'ok' | 'warn' | 'fail' | 'unknown'

export interface EnvItem {
  key: string
  label: string
  state: CheckState
  detail: string
  hint: string
}

export interface EnvCheck {
  items: EnvItem[]
  summary: { ok: number; warn: number; fail: number; unknown: number }
  worst: CheckState
}

export interface LlmStatus {
  configured: boolean
  source: 'env' | 'db' | 'none'
  base_url: string
  model: string
  sanitize: string
  api_key_masked: string
  env_locked: boolean
  locked_items: Record<string, string>
  tested_at: string
  /** 走不走 HunterCode 内置额度。设置页据此显示今日剩余额度。 */
  builtin?: boolean
}

export interface SetupStatus {
  authed: boolean
  via?: string
  need_unlock: boolean
  unlock_reason?: string
  message?: string
  token_configured: boolean
  locked_for: number
  single_user: boolean
  source?: { ip: string; kind: string; via: string; peer: string }
  llm: LlmStatus | { source: string; configured: boolean }
  data_supply?: { configured: boolean; masked: string; env_locked: boolean; apply_url: string }
  setup: { completed_at: string; should_run?: boolean }
}

export interface Preset {
  id: string
  title: string
  vendor: string
  base_url: string
  model: string
  sanitize: string
  tags: string[]
  tool_hit: string
  avg_latency_s: number | null
  tested_at: string
  apply_url: string
  key_hint: string
  notes: string[]
  /** true = 这是「HunterCode 内置额度」那张卡(地址与模型名由它带,用户只填 key)。 */
  builtin?: boolean
  /** 内置额度的深度分析模型名,只用于卡片上的说明文字。 */
  deep_model?: string
  /** 服务条款与可接受使用政策。内置额度这张卡必须有 —— 它用的是我们的算力,
   *  用户有权在点之前知道边界(禁止转售、额度可调整、服务可能下线)。 */
  terms_url?: string
}

export interface PresetDoc {
  updated_at: string
  source_doc: string
  note?: string
  presets: Preset[]
  error?: string
}

export interface ProbeCheck {
  name: string
  ok: boolean
  elapsed_ms: number
  code: string
  message: string
  warn?: string
  reply?: string
  sanitize_suggest?: string
  cleaned?: boolean
}

export interface TestResult {
  ok: boolean
  checks: ProbeCheck[]
  models: string[]
  sanitize_suggest: string
  elapsed_ms: number
  test_token: string
  test_token_ttl: number
}

const SESSION_KEY = 'hunter_setup_session'

export function getSession(): string {
  if (typeof window === 'undefined') return ''
  try { return sessionStorage.getItem(SESSION_KEY) || '' } catch { return '' }
}

export function setSession(v: string) {
  try { sessionStorage.setItem(SESSION_KEY, v) } catch { /* 隐私模式 */ }
}

export function clearSession() {
  try { sessionStorage.removeItem(SESSION_KEY) } catch { /* 同上 */ }
}

/** 统一的失败形状 —— 调用方一律按普通失败处理,不用区分"网络挂了"和"接口报错"。
 *  (仓内规矩:post() 必须接住 fetch 的 reject,否则界面永远停在"进行中") */
export interface Fail { ok: false; status: number; message: string; data: any }

function failOf(status: number, data: any): Fail {
  let message = ''
  const d = data?.detail ?? data
  if (typeof d === 'string') message = d
  else if (d && typeof d === 'object') message = d.message || d.detail || ''
  if (!message) {
    message = status === 0
      ? '请求没发出去:浏览器连不上这台服务器,检查 web 容器是否还在运行'
      : `请求失败(HTTP ${status})`
  }
  return { ok: false, status, message, data: d }
}

async function call<T>(path: string, init?: RequestInit): Promise<T | Fail> {
  const headers = new Headers(init?.headers || {})
  headers.set('Accept', 'application/json')
  if (init?.body) headers.set('Content-Type', 'application/json')
  const sess = getSession()
  if (sess) headers.set('X-Hunter-Setup-Session', sess)
  // 已登录的话把 token 也带上 —— 管理员 / 单用户模式走这条路进门
  try {
    const t = localStorage.getItem('hunter_token')
    if (t) headers.set('Authorization', `Bearer ${t}`)
  } catch { /* 隐私模式 */ }

  let r: Response
  try {
    r = await fetch(`/api/setup${path}`, { ...init, headers, cache: 'no-store' })
  } catch (e) {
    return failOf(0, { message: `请求没发出去:${String(e)}` })
  }
  let data: any = null
  try { data = await r.json() } catch { data = null }
  if (!r.ok) return failOf(r.status, data)
  return data as T
}

export const isFail = (x: any): x is Fail => !!x && x.ok === false && typeof x.status === 'number'

export const getStatus = () => call<SetupStatus>('/status')
export const getEnvCheck = () => call<EnvCheck>('/env-check')
export const getPresets = () => call<PresetDoc>('/llm-presets')

export const unlock = (token: string) =>
  call<{ ok: true; session: string; expires_in: number }>('/unlock', {
    method: 'POST', body: JSON.stringify({ token }),
  })

export const testLlm = (base_url: string, api_key: string, model: string) =>
  call<TestResult>('/llm/test', {
    method: 'POST', body: JSON.stringify({ base_url, api_key, model }),
  })

export const saveLlm = (p: {
  base_url: string; api_key: string; model: string; sanitize: string; test_token: string
  builtin?: boolean
}) => call<{ ok: true; saved: any; data_supply?: { adopted: boolean; reason: string; masked?: string } }>(
  '/llm', { method: 'PUT', body: JSON.stringify(p) })

/** 内置额度今日剩余额度。`builtin:false` = 这台实例没走内置额度,不显示这一块。 */
export interface QuotaView {
  builtin: boolean
  ok?: boolean
  code?: string
  message?: string
  quota?: {
    used_today: number
    limit_daily: number
    remaining: number
    exhausted: boolean
    reset_at: string
    reset_tz?: string
    rate_per_min?: number
    max_concurrency?: number
    unit?: string
  }
}

export const getQuota = () => call<QuotaView>('/llm/quota')

/** 用已保存的平台 key 一键切到内置额度(侧栏「Hunter key 管理」弹窗)。
 *  `ok:false` 且没有 status = 三项检测没过,当前配置原样保留,`message` 里写了哪一项。 */
export interface AdoptBuiltinResult {
  ok: boolean
  already?: boolean
  model?: string
  applied?: boolean
  apply_reason?: string
  expect_ready_seconds?: number
  message?: string
  checks?: ProbeCheck[]
}

export const adoptBuiltin = () =>
  call<AdoptBuiltinResult>('/llm/adopt-builtin', { method: 'POST', body: '{}' })

export const applyLlm = () =>
  call<{ ok: boolean; reason: string; model: string; expect_ready_seconds: number }>(
    '/apply', { method: 'POST', body: '{}' })

export const engineReady = () =>
  call<{ ready: boolean; reason: string; current?: string }>('/engine-ready')

export const complete = (skipped: boolean) =>
  call<{ ok: true; completed_at: string }>('/complete', {
    method: 'POST', body: JSON.stringify({ skipped }),
  })

export const reopen = () => call<{ ok: true }>('/reopen', { method: 'POST', body: '{}' })

/** 第 4 步的平台 key —— 复用现成的 /api/hunter/unlock,不另写一套校验。 */
export async function savePlatformKey(key: string): Promise<{ ok: true } | Fail> {
  let token = ''
  try { token = localStorage.getItem('hunter_token') || '' } catch { /* 隐私模式 */ }
  let r: Response
  try {
    r = await fetch('/api/hunter/unlock', {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ key }),
      cache: 'no-store',
    })
  } catch (e) {
    return failOf(0, { message: `请求没发出去:${String(e)}` })
  }
  let data: any = null
  try { data = await r.json() } catch { data = null }
  if (!r.ok) return failOf(r.status, data)
  return { ok: true }
}
