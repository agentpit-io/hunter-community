'use client'
/**
 * 四个正文页共用的取数钩子（M6）。
 *
 * 三条约定（都由验收项倒推出来的）：
 *
 * 1. **错误要能被看见，不能让页面白屏。** 后端停掉 / 502 / 超时都要落到 `error`，
 *    页面据此渲染失败态（`05 §3.2` M-03 验收项「错误态与空态可见」）。
 * 2. **「没有项目」不是错误。** `/overview` 在没有进行中的项目时返回 409 +
 *    `need_project`，页面据此渲染引导去向导的空态，而不是一张红卡。
 * 3. **401 去登录页**，与现仓其它页面同一口径（见 `_ui.finFetch`）。
 */
import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { finFetch } from './_ui'

export type FinPageState<T> = {
  data: T | null
  loading: boolean
  error: string
  /** 后端说「还没有进行中的项目」时为 true（不是错误）。 */
  noProject: boolean
  reload: () => void
}

export function useFinPage<T>(path: string): FinPageState<T> {
  const router = useRouter()
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [noProject, setNoProject] = useState(false)
  const [tick, setTick] = useState(0)

  useEffect(() => {
    let alive = true
    setLoading(true)
    setError('')
    ;(async () => {
      try {
        const d = await finFetch<T>(path)
        if (!alive) return
        setData(d)
        setNoProject(false)
      } catch (e: any) {
        if (!alive) return
        if (e?.status === 401) { router.push('/login'); return }
        if (e?.status === 409) { setNoProject(true); setData(null); return }
        setError(e?.message || '加载失败')
        setData(null)
      } finally {
        if (alive) setLoading(false)
      }
    })()
    return () => { alive = false }
  }, [path, router, tick])

  const reload = useCallback(() => setTick(t => t + 1), [])
  return { data, loading, error, noProject, reload }
}

/** 写操作（开关 / 策略 / 风险档位）：薄封装，只为让调用点读起来一致。 */
export async function finPost<T>(path: string, body: unknown): Promise<T> {
  return finFetch<T>(path, { method: 'POST', body: JSON.stringify(body) })
}
