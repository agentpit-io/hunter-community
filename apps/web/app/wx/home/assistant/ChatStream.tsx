import React, { useEffect, useRef } from 'react'
import { T } from './tokens'
import { MessageBubble } from './MessageBubble'
import { ToolCallCard } from './ToolCallCard'
import type { ChatItem, ErrorPayload, AssistantMsg } from './types'

export interface ChatStreamProps {
  items: ChatItem[]
  streaming: boolean
  lastError: ErrorPayload | null
  sessionId?: string | null
  token?: string
}

export function ChatStream({ items, streaming, lastError, sessionId, token }: ChatStreamProps) {
  const endRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [items.length, streaming])

  const submitFeedback = (msg: AssistantMsg, positive: boolean) => {
    if (!token || !sessionId) return
    fetch('/api/agent/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json',
                 'Authorization': `Bearer ${token}` },
      body: JSON.stringify({
        session_id: sessionId, message_ts: msg.ts, positive,
      }),
    }).catch(() => {})
  }

  return (
    <div style={{
      flex: 1, overflowY: 'auto', padding: '12px 12px 20px',
      background: T.BG, minHeight: 0,
    }}>
      {items.map((it, i) => {
        if (it.role === 'tool_bundle') {
          return <ToolCallCard key={it.id + i} bundle={it} />
        }
        return (
          <MessageBubble
            key={i + '-' + it.ts} msg={it}
            onFeedback={it.role === 'assistant' ? submitFeedback : undefined}
          />
        )
      })}
      {lastError && (
        <div style={{
          margin: '10px 0', padding: '9px 12px', fontSize: 12,
          background: '#FBEDEC', border: `1px solid ${T.UP}`, borderRadius: 8, color: T.UP,
        }}>
          ⚠ {lastError.code}: {lastError.message}
          {lastError.fallback && <span> · 已降级到 {lastError.fallback}</span>}
        </div>
      )}
      <div ref={endRef} />
    </div>
  )
}
