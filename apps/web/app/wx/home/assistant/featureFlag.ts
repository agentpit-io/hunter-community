/**
 * Agent Chat V2 灰度开关（客户端）
 *
 * 优先级：
 *  1. localStorage['hunter_flag_agent_chat_v2'] === '1'  → 强开（内部/测试）
 *  2. /api/user/flags 返回的 agent_chat_v2 (布尔)         → 由后端灰度百分比控制
 *  3. 默认 off
 *
 * 【SSR 闪屏修复 · 2026-08-04】
 * 返回三态：true / false / null(未确定)。SSR 时永远 null，客户端 hydration
 * 后立即读 localStorage/缓存拿确定值。父组件应根据 null 显示 loading，
 * 避免 render 老组件 → 切新组件的 flicker。
 */
import { useEffect, useState } from 'react'

const LS_KEY = 'hunter_flag_agent_chat_v2'
const CACHE_KEY = 'hunter_flag_cache_v1'
const CACHE_TTL_MS = 5 * 60 * 1000  // 5 min

type FlagMap = { agent_chat_v2?: boolean }

export function useFeatureFlag(name: 'agent_chat_v2', token: string | null): boolean | null {
  // 初始 null → SSR/首帧客户端都是 null，父组件显示 loading
  const [enabled, setEnabled] = useState<boolean | null>(null)

  // hydration 后同步读 localStorage（一次性）
  useEffect(() => {
    if (typeof window === 'undefined') return
    if (window.localStorage.getItem(LS_KEY) === '1') { setEnabled(true); return }
    try {
      const raw = window.localStorage.getItem(CACHE_KEY)
      if (raw) {
        const parsed = JSON.parse(raw) as { at: number; flags: FlagMap }
        if (Date.now() - parsed.at < CACHE_TTL_MS) {
          setEnabled(!!parsed.flags[name])
          return
        }
      }
    } catch {}
    setEnabled(false)  // 无缓存 → 先给 false，下面 fetch 会覆盖
  }, [name])

  // 后端 /api/user/flags 拉取（覆盖 fetch 结果为准）
  useEffect(() => {
    if (!token) return
    if (typeof window !== 'undefined' && window.localStorage.getItem(LS_KEY) === '1') {
      setEnabled(true); return
    }
    let cancelled = false
    fetch('/api/user/flags', {
      headers: { 'Authorization': `Bearer ${token}` },
    }).then(r => r.ok ? r.json() : {}).then((flags: FlagMap) => {
      if (cancelled) return
      const v = !!flags[name]
      setEnabled(v)
      try {
        window.localStorage.setItem(CACHE_KEY, JSON.stringify({ at: Date.now(), flags }))
      } catch {}
    }).catch(() => {})
    return () => { cancelled = true }
  }, [name, token])

  return enabled
}
