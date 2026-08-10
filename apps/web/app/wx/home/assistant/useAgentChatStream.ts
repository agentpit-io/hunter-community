/**
 * Agent Chat V2 · SSE 客户端 hook
 *
 * 用 fetch + ReadableStream 手写 SSE 解析（浏览器 EventSource 不支持自定义 Headers/POST）。
 * 契约详见 doc/codex/03-开发需求与技术方案.md §3.2。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import type {
  ChatItem, ToolBundle, ToolItem,
  SessionPayload, RouterDecisionPayload, ToolUsePayload, ToolProgressPayload,
  ToolResultPayload, MessageDeltaPayload, MessageEndPayload, ErrorPayload,
} from './types'

// SSE endpoint（与后端 router prefix 对齐）
const ENDPOINT = '/api/agent/chat/stream'

export interface SendArgs {
  query: string
  stockCode?: string
  stockName?: string
}

export function useAgentChatStream(token: string, initialSessionId?: string) {
  const [items, setItems] = useState<ChatItem[]>([])
  const [streaming, setStreaming] = useState(false)
  const [sessionId, setSessionId] = useState<string | null>(initialSessionId ?? null)
  const [lastError, setLastError] = useState<ErrorPayload | null>(null)

  const abortRef = useRef<AbortController | null>(null)

  // 用 ref 保存当前流的临时组装体，避免频繁 setState
  const bundleRef = useRef<ToolBundle | null>(null)
  const assistantContentRef = useRef<string>('')
  const rafPendingRef = useRef<boolean>(false)

  useEffect(() => () => { abortRef.current?.abort() }, [])

  // ── 批量把 assistantContent flush 到 items（rAF 节流）──
  const scheduleFlush = () => {
    if (rafPendingRef.current) return
    rafPendingRef.current = true
    requestAnimationFrame(() => {
      rafPendingRef.current = false
      const content = assistantContentRef.current
      if (!content) return
      setItems(prev => {
        // 找最后一条 assistant 消息若还是 streaming（无 usage 表示未 end），追加
        const last = prev[prev.length - 1]
        if (last && last.role === 'assistant' && (last as any)._streaming) {
          return [...prev.slice(0, -1), { ...(last as any), content } as ChatItem]
        }
        return [...prev, ({
          role: 'assistant', content, ts: Date.now(),
          tool_bundle_id: bundleRef.current?.id,
          _streaming: true,
        } as unknown) as ChatItem]
      })
    })
  }

  const send = useCallback(async ({ query, stockCode, stockName }: SendArgs) => {
    if (streaming) return
    setLastError(null)
    // 立即 push user 消息
    setItems(prev => [...prev, { role: 'user', content: query, ts: Date.now() }])
    setStreaming(true)
    bundleRef.current = null
    assistantContentRef.current = ''

    const ctrl = new AbortController()
    abortRef.current = ctrl

    let resp: Response
    try {
      resp = await fetch(ENDPOINT, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`,
          'Accept': 'text/event-stream',
        },
        body: JSON.stringify({
          session_id: sessionId, stock_code: stockCode, stock_name: stockName,
          query, client_capabilities: { supports_progress: true },
        }),
        signal: ctrl.signal,
      })
    } catch (e: any) {
      setLastError({ code: 'INTERNAL', message: e?.message || '网络错误' })
      setStreaming(false); return
    }

    if (!resp.ok || !resp.body) {
      setLastError({ code: 'INTERNAL', message: `HTTP ${resp.status}` })
      setStreaming(false); return
    }

    const reader = resp.body.pipeThrough(new TextDecoderStream()).getReader()
    let buf = ''
    try {
      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        buf += value
        let idx: number
        while ((idx = buf.indexOf('\n\n')) >= 0) {
          const raw = buf.slice(0, idx); buf = buf.slice(idx + 2)
          const evt = parseEvent(raw); if (!evt) continue
          applyEvent(evt.name, evt.data)
        }
      }
    } catch (e: any) {
      if (e?.name !== 'AbortError') {
        setLastError({ code: 'INTERNAL', message: e?.message || '流式中断' })
      }
    } finally {
      // 收尾：把仍在 streaming 标记的 assistant 消息 flush 成最终版
      setItems(prev => prev.map(it => {
        if ((it as any)._streaming) {
          return ({ ...(it as any), _streaming: false } as unknown) as ChatItem
        }
        return it
      }))
      setStreaming(false)
      abortRef.current = null
    }
  }, [token, sessionId, streaming])

  function applyEvent(name: string, data: any) {
    switch (name) {
      case 'session': {
        const p = data as SessionPayload
        if (p.session_id && p.session_id !== sessionId) setSessionId(p.session_id)
        return
      }
      case 'router_decision': {
        const p = data as RouterDecisionPayload
        const bundle: ToolBundle = {
          role: 'tool_bundle',
          id: `bundle-${Date.now()}`, reason: p.reason,
          status: 'pending', tools: [], started_at: Date.now(),
        }
        bundleRef.current = bundle
        setItems(prev => [...prev, bundle])
        return
      }
      case 'tool_use': {
        const p = data as ToolUsePayload
        const t: ToolItem = {
          tool_id: p.tool_id, name: p.name, display_name: p.display_name,
          status: 'running', started_at: p.started_at,
        }
        if (bundleRef.current) bundleRef.current.tools.push(t)
        updateBundle()
        return
      }
      case 'tool_progress': {
        const p = data as ToolProgressPayload
        const b = bundleRef.current
        if (!b) return
        const t = b.tools.find(x => x.tool_id === p.tool_id)
        if (t) { t.progress = p.percent; t.progress_detail = p.detail }
        updateBundle()
        return
      }
      case 'tool_result': {
        const p = data as ToolResultPayload
        const b = bundleRef.current
        if (!b) return
        const t = b.tools.find(x => x.tool_id === p.tool_id)
        if (t) {
          t.status = p.status; t.duration_ms = p.duration_ms
          t.summary = p.summary; t.detail_ref = p.detail_ref; t.error = p.error
        }
        const anyErr = b.tools.some(x => x.status === 'error')
        const allDone = b.tools.every(x => x.status !== 'running')
        if (allDone) b.status = anyErr ? 'partial_error' : 'done'
        updateBundle()
        return
      }
      case 'message_delta': {
        const p = data as MessageDeltaPayload
        assistantContentRef.current += p.content
        scheduleFlush()
        return
      }
      case 'message_end': {
        const p = data as MessageEndPayload
        setItems(prev => prev.map(it => {
          if ((it as any)._streaming) {
            return ({ ...(it as any), content: assistantContentRef.current,
                      usage: p.usage, _streaming: false } as unknown) as ChatItem
          }
          return it
        }))
        return
      }
      case 'error': {
        const p = data as ErrorPayload
        setLastError(p)
        return
      }
    }
  }

  function updateBundle() {
    if (!bundleRef.current) return
    const snap: ToolBundle = {
      ...bundleRef.current,
      tools: bundleRef.current.tools.map(t => ({ ...t })),
    }
    bundleRef.current = snap
    setItems(prev => prev.map(it =>
      (it.role === 'tool_bundle' && it.id === snap.id) ? snap : it
    ))
  }

  const abort = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  const reset = useCallback(() => {
    abortRef.current?.abort()
    setItems([]); setSessionId(null); setLastError(null); setStreaming(false)
    bundleRef.current = null; assistantContentRef.current = ''
  }, [])

  return { items, streaming, sessionId, lastError, send, abort, reset }
}


// ────────────────────────── helpers ──────────────────────────
function parseEvent(raw: string): { name: string; data: any } | null {
  const lines = raw.split('\n')
  let name = 'message'; let dataStr = ''
  for (const l of lines) {
    if (l.startsWith('event:')) name = l.slice(6).trim()
    else if (l.startsWith('data:')) dataStr += l.slice(5).trim()
    else if (l.startsWith(':')) return null   // SSE comment (keepalive)
  }
  if (!dataStr) return null
  try { return { name, data: JSON.parse(dataStr) } } catch { return null }
}
