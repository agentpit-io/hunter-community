import React, { useState } from 'react'
import { T } from './tokens'
import type { UserMsg, AssistantMsg } from './types'

export interface MessageBubbleProps {
  msg: UserMsg | AssistantMsg
  onFeedback?: (msg: AssistantMsg, positive: boolean) => void
}

export function MessageBubble({ msg, onFeedback }: MessageBubbleProps) {
  if (msg.role === 'user') {
    return (
      <div style={{ display: 'flex', justifyContent: 'flex-end', margin: '10px 0' }}>
        <div style={{
          maxWidth: '78%', padding: '9px 13px', fontSize: 13.5, lineHeight: 1.55,
          background: T.PAPER, color: T.INK,
          border: `1.5px solid ${T.COPPER}`,
          borderRadius: '14px 14px 4px 14px',
          whiteSpace: 'pre-wrap', wordBreak: 'break-word',
        }}>{msg.content}</div>
      </div>
    )
  }
  const streaming = (msg as any)._streaming
  return (
    <div style={{ display: 'flex', justifyContent: 'flex-start', margin: '10px 0' }}>
      <div style={{
        maxWidth: '85%', padding: '10px 14px', fontSize: 13.5, lineHeight: 1.6,
        background: '#F5EFDF', color: T.INK,
        border: `1px solid ${T.LINE}`,
        borderRadius: '14px 14px 14px 4px',
        whiteSpace: 'pre-wrap', wordBreak: 'break-word',
      }}>
        {msg.content}
        {streaming && (
          <span style={{ opacity: 0.5, marginLeft: 4 }}>▍</span>
        )}
        {msg.usage && !streaming && (
          <div style={{ marginTop: 8, fontSize: 10, color: T.INK_F, opacity: 0.7,
                          display: 'flex', alignItems: 'center', gap: 8 }}>
            <span>{msg.usage.model} · {msg.usage.tokens_in + msg.usage.tokens_out} tokens
              {msg.usage.cost_cny > 0 && ` · ¥${msg.usage.cost_cny.toFixed(3)}`}
            </span>
            <FeedbackButtons msg={msg} onFeedback={onFeedback} />
          </div>
        )}
      </div>
    </div>
  )
}


function FeedbackButtons({ msg, onFeedback }: {
  msg: AssistantMsg
  onFeedback?: (msg: AssistantMsg, positive: boolean) => void
}) {
  const [voted, setVoted] = useState<'up' | 'down' | null>(null)
  const vote = (positive: boolean) => {
    setVoted(positive ? 'up' : 'down')
    onFeedback?.(msg, positive)
  }
  if (voted) {
    return <span style={{ marginLeft: 'auto', color: voted === 'up' ? T.DN : T.UP }}>
      {voted === 'up' ? '👍 已反馈' : '👎 已反馈'}
    </span>
  }
  return (
    <div style={{ marginLeft: 'auto', display: 'flex', gap: 4 }}>
      <button onClick={() => vote(true)} style={fbBtn} title="有用">👍</button>
      <button onClick={() => vote(false)} style={fbBtn} title="没帮助">👎</button>
    </div>
  )
}

const fbBtn: React.CSSProperties = {
  background: 'transparent', border: 'none', cursor: 'pointer',
  fontSize: 12, padding: '2px 4px', color: '#7A6F63', opacity: 0.7,
}
