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
      if (res.status !== 401) return res

      // Only act on OUR API's structured 401 · avoid trapping third-party 401s
      const url = (typeof args[0] === 'string' ? args[0] : (args[0] as Request).url) || ''
      if (!url.includes('/api/')) return res
      // 换 token 本身返回的 401 不能再进来,否则会打转
      if (url.includes('/api/auth/')) return res

      let needLogin = false
      try {
        // Peek body without consuming (clone)
        const body = await res.clone().json()
        needLogin = body?.needLogin === true || body?.error === 'INVALID_TOKEN' || body?.error === 'UNAUTHORIZED'
      } catch {
        // Non-JSON 401 · treat as needLogin too when we have a stored token
        needLogin = !!localStorage.getItem('hunter_token')
      }
      if (!needLogin) return res

      // 单用户模式:静默续期并重放。成功了用户完全无感。
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

    return () => {
      window.fetch = original
      w.__hunterAuthGuardInstalled = false
    }
  }, [])
  return null
}
