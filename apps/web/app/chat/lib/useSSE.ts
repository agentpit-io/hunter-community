// SSE hook · 订阅 opencode session 事件流
// EventSource 天然不支持 header · 通过 BFF cookie 或 URL 参数塞 auth

import { useEffect, useRef, useState } from 'react'
import type { OpencodeEvent } from './types'

export type SSEStatus = 'idle' | 'connecting' | 'live' | 'error' | 'closed'

export interface UseSSEResult {
  events: OpencodeEvent[]
  status: SSEStatus
  reconnect: () => void
}

const BFF_BASE = '/api/opencode'

function getToken(): string {
  if (typeof window === 'undefined') return ''
  return localStorage.getItem('hunter_token') || ''
}

export function useOpencodeSSE(sessionId: string | null): UseSSEResult {
  const [events, setEvents] = useState<OpencodeEvent[]>([])
  const [status, setStatus] = useState<SSEStatus>('idle')
  const esRef = useRef<EventSource | null>(null)
  const seqRef = useRef(0)

  const connect = () => {
    if (esRef.current) {
      esRef.current.close()
      esRef.current = null
    }
    if (!sessionId) {
      setStatus('idle')
      return
    }

    // opencode 事件流 endpoint · 全局（不是每 session）
    // 用 query param 塞 token（EventSource 无 header API）
    const token = getToken()
    const url = `${BFF_BASE}/event${token ? `?token=${encodeURIComponent(token)}` : ''}`

    setStatus('connecting')
    const es = new EventSource(url)
    esRef.current = es

    es.onopen = () => {
      seqRef.current = 0
      setStatus('live')
    }

    es.onerror = () => {
      setStatus('error')
      // EventSource 自动重连 · 无需手动
    }

    es.onmessage = (ev) => {
      try {
        const parsed: OpencodeEvent = JSON.parse(ev.data)
        // 全局 event · 过滤属于当前 session 的
        const props: any = (parsed as any).properties || {}
        // 只看真实的 sessionID 字段 · 不用 info.id (那可能是 msg id)
        const sid = props.sessionID || props.part?.sessionID
        // 无 sid 的全局事件 (server.connected · heartbeat) · 也接受
        if (sid && sid !== sessionId) return
        seqRef.current += 1
        setEvents((prev) => [...prev, parsed])
      } catch (e) {
        console.warn('[useSSE] parse fail:', e)
      }
    }
  }

  useEffect(() => {
    setEvents([])
    connect()
    return () => {
      if (esRef.current) {
        esRef.current.close()
        esRef.current = null
      }
      setStatus('closed')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId])

  return {
    events,
    status,
    reconnect: connect,
  }
}
