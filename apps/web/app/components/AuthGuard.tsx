'use client'
import { useEffect } from 'react'
import { ensureLocalSession } from '../lib/localSession'

/**
 * Client-side auth guard · does two jobs.
 *
 * 1. **单用户模式的开机自动登录**（开源版默认）
 *    挂载时静默换一个 token,用户看不到登录页。见 lib/localSession.ts。
 *
 * 2. **401 处理**
 *    monkey-patch window.fetch 拦下后端的
 *      { error: "INVALID_TOKEN"|"UNAUTHORIZED", needLogin: true }
 *    单用户模式下先**重取 token 并重放这次请求** —— access token 只有 1 小时,
 *    没有这一步的话用户泡杯咖啡回来就被踢回登录页,而这台实例根本没有"登录"可言。
 *    重取失败(多用户实例 / 后端挂了)才走老路:清 token → 跳 /login。
 *
 * 从 root layout 挂一次,全应用的 fetch 都被覆盖,调用方不用改。
 */
/** 用 refresh token 换一把新的 access token。失败返回 null。
 *
 *  并发保护:一个页面同时发好几个请求时,它们会一起撞 401。
 *  没有这个锁的话每个都去 refresh 一次,而后端多半是「一次性 refresh
 *  token」—— 第一个换成功,后面几个拿着已作废的旧 token 全失败,
 *  于是**明明续期成功了,用户还是被踢了出去**。
 */
let refreshInflight: Promise<string | null> | null = null

async function tryRefresh(): Promise<string | null> {
  if (typeof window === 'undefined') return null
  if (refreshInflight) return refreshInflight

  refreshInflight = (async () => {
    let rt: string | null = null
    try { rt = localStorage.getItem('hunter_refresh') } catch { /* 隐私模式 */ }
    if (!rt) return null
    try {
      const base = process.env.NEXT_PUBLIC_API_URL || ''
      const r = await fetch(`${base}/api/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: rt }),
      })
      if (!r.ok) return null
      const d = await r.json()
      const tok = d?.access_token || d?.token
      if (!tok) return null
      try {
        localStorage.setItem('hunter_token', tok)
        // 后端可能轮换 refresh token · 给了就换掉,不给就继续用旧的
        if (d.refresh_token) localStorage.setItem('hunter_refresh', d.refresh_token)
      } catch { /* ignore */ }
      return tok
    } catch {
      return null
    }
  })()

  try { return await refreshInflight } finally { refreshInflight = null }
}

/** 闲置自动登出 · localStorage 键 · 单位分钟 · 0/缺省 = 不自动登出 */
export const IDLE_LOGOUT_KEY = 'hunter_idle_logout_min'

/**
 * 闲置多久自动登出。
 *
 * ## 背景
 *
 * 产品经理反馈"几分钟不动就退出了"。查下来系统里**根本没有闲置登出** ——
 * 真正的原因是 access token 只有 1 小时,而前端把 refresh token 存了
 * 却从来没用过(见 tryRefresh)。用户泡杯咖啡回来点一下就掉线,
 * 主观上就成了"一会儿不动就没了"。
 *
 * refresh 接上之后,不动多久都不会掉线了。但"离开工位自动锁"是个**合理
 * 的安全诉求**,尤其这上面挂着持仓和策略。所以做成可配置,
 * **默认关闭** —— 默认打开会把一个刚修好的问题重新变成问题。
 */
function installIdleLogout(): () => void {
  let timer: any = null

  const minutes = () => {
    try { return Number(localStorage.getItem(IDLE_LOGOUT_KEY) || 0) || 0 }
    catch { return 0 }
  }

  const fire = () => {
    try {
      localStorage.removeItem('hunter_token')
      localStorage.removeItem('hunter_refresh')
    } catch { /* ignore */ }
    const path = window.location.pathname
    if (path !== '/login' && path !== '/register') {
      // 带上原因 —— 否则用户回到登录页只会觉得"又被踢了",
      // 不知道是自己设的闲置登出生效了
      window.location.replace('/login?reason=idle')
    }
  }

  const reset = () => {
    if (timer) clearTimeout(timer)
    const m = minutes()
    if (m > 0) timer = setTimeout(fire, m * 60_000)
  }

  // passive:这些事件触发极频繁,不加会拖慢滚动
  const EVENTS = ['mousemove', 'mousedown', 'keydown', 'touchstart', 'scroll', 'visibilitychange']
  EVENTS.forEach((e) => window.addEventListener(e, reset, { passive: true }))
  reset()

  return () => {
    if (timer) clearTimeout(timer)
    EVENTS.forEach((e) => window.removeEventListener(e, reset))
  }
}

export default function AuthGuard() {
  useEffect(() => {
    if (typeof window === 'undefined') return
    const w = window as unknown as {
      __hunterAuthGuardInstalled?: boolean
      __hunterOriginalFetch?: typeof fetch
    }
    if (w.__hunterAuthGuardInstalled) return
    w.__hunterAuthGuardInstalled = true

    const original = window.fetch
    // localSession 用它绕开这层包装,避免"取 token 的请求"再次触发 401 处理
    w.__hunterOriginalFetch = original

    // 开机先要一把 token（多用户实例会拿到 null,交给下面的 401 分支处理）
    void ensureLocalSession()

    window.fetch = async (...args) => {
      const res = await original.apply(window, args as Parameters<typeof fetch>)
      // 401 · 后端标准未授权
      // 503 ownership_unavailable · BFF 的 /api/opencode 把下游 401 掩盖成 503
      //   (归属服务不可用 ≠ token 坏了 · 但真挂时重试也无伤大雅 · 且 token 坏时不重试用户会卡死)
      if (res.status !== 401 && res.status !== 503) return res

      // Only act on OUR API's structured errors · avoid trapping third-party
      const url = (typeof args[0] === 'string' ? args[0] : (args[0] as Request).url) || ''
      if (!url.includes('/api/')) return res
      // 换 token 本身返回的错误不能再进来,否则会打转
      if (url.includes('/api/auth/')) return res

      let needLogin = false
      try {
        // Peek body without consuming (clone)
        const body = await res.clone().json()
        // 兼容大小写 error code · 兼容 BFF 的 503 ownership_unavailable
        const code = String(body?.error || '').toLowerCase()
        needLogin = body?.needLogin === true
          || code === 'invalid_token'
          || code === 'unauthorized'
          || code === 'ownership_unavailable'
      } catch {
        // Non-JSON 401 · treat as needLogin too when we have a stored token
        needLogin = res.status === 401 && !!localStorage.getItem('hunter_token')
      }
      if (!needLogin) return res

      // ① 多用户模式:拿 refresh token 换一把新的 access token。
      //
      // ⚠️ 这一步之前**完全没有** —— refresh_token 在登录时存进了
      // localStorage('hunter_refresh'),然后就再也没被读过。后端
      // /api/auth/refresh 一直都在,只是前端从没调过。
      //
      // 后果:access token 只有 1 小时(JWT_ACCESS_TTL=3600),
      // 过期后第一个 401 就直接清 token、跳登录页。用户的感受是
      // "几分钟不动就被踢出来了" —— 实际是 1 小时,但如果他中途没发过
      // 请求,再回来点一下就立刻掉线,主观上就是"一会儿不动就没了"。
      // 而手里明明攥着一张有效的 refresh token(7 天)。
      const refreshed = await tryRefresh()
      if (refreshed) {
        const [input, init] = args as [RequestInfo | URL, RequestInit | undefined]
        const headers = new Headers(
          init?.headers ?? (input instanceof Request ? input.headers : undefined),
        )
        headers.set('Authorization', `Bearer ${refreshed}`)
        try {
          return await original.apply(window, [input, { ...(init || {}), headers }] as any)
        } catch {
          return res
        }
      }

      // ② 单用户模式:静默换一把新的并重放。成功了用户完全无感。
      const fresh = await ensureLocalSession(true)
      if (fresh) {
        const [input, init] = args as [RequestInfo | URL, RequestInit | undefined]
        const headers = new Headers(
          init?.headers ?? (input instanceof Request ? input.headers : undefined),
        )
        headers.set('Authorization', `Bearer ${fresh}`)
        try {
          // Request 对象的 body 已经被消费过一次,只能按 (input, init) 重发;
          // 应用里所有带 body 的调用都是这种形式,Request 形式仅用于 GET。
          return await original.apply(window, [input, { ...(init || {}), headers }] as any)
        } catch {
          return res
        }
      }

      // Wipe tokens · redirect once
      const path = window.location.pathname
      const alreadyOnAuth = path === '/login' || path === '/register'
      try {
        localStorage.removeItem('hunter_token')
        localStorage.removeItem('hunter_refresh')
      } catch { /* ignore */ }
      if (!alreadyOnAuth) {
        const returnTo = encodeURIComponent(path + window.location.search)
        window.location.replace(`/login?return_to=${returnTo}`)
      }
      return res
    }

    const stopIdle = installIdleLogout()

    return () => {
      window.fetch = original
      w.__hunterAuthGuardInstalled = false
      stopIdle()
    }
  }, [])
  return null
}
