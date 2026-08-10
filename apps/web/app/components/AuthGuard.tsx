'use client'
import { useEffect } from 'react'

/**
 * Client-side auth guard · monkey-patches window.fetch to detect
 *   { error: "INVALID_TOKEN"|"UNAUTHORIZED", needLogin: true }
 * from our backend and force a full re-auth cycle:
 *   1. wipe hunter_token / hunter_refresh
 *   2. redirect to /login (skip if already there)
 *
 * Mounted once from root layout so every fetch in the app is covered ·
 * no per-caller changes needed.
 */
export default function AuthGuard() {
  useEffect(() => {
    if (typeof window === 'undefined') return
    const w = window as unknown as { __hunterAuthGuardInstalled?: boolean }
    if (w.__hunterAuthGuardInstalled) return
    w.__hunterAuthGuardInstalled = true

    const original = window.fetch
    window.fetch = async (...args) => {
      const res = await original.apply(window, args as Parameters<typeof fetch>)
      if (res.status !== 401) return res

      // Only act on OUR API's structured 401 · avoid trapping third-party 401s
      const url = (typeof args[0] === 'string' ? args[0] : (args[0] as Request).url) || ''
      if (!url.includes('/api/')) return res

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
