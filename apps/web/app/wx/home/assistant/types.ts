// SSE 事件与消息类型（严格对齐 doc/codex/03-开发需求与技术方案.md §3.2）

export type SSEEventName =
  | 'session' | 'router_decision' | 'tool_use' | 'tool_progress'
  | 'tool_result' | 'message_delta' | 'message_end' | 'error'

// ─── SSE payloads ──────────────────────────────────────────────
export interface StockContext { code: string; name?: string }

export interface SessionPayload {
  session_id: string
  message_id: string
  stock_context: StockContext | null
}
export interface RouterDecisionPayload {
  reason: string
  tool_calls: { tool_id: string; name: string; args: Record<string, unknown> }[]
}
export interface ToolUsePayload {
  tool_id: string; name: string; display_name: string; started_at: string
}
export interface ToolProgressPayload {
  tool_id: string; name: string; percent: number; detail?: string; phase?: string
}
export interface ToolErrorInfo { code: string; message: string }
export interface ToolResultPayload {
  tool_id: string; name: string; status: 'ok' | 'error'; duration_ms: number
  summary?: Record<string, unknown>
  detail_ref?: { type: string; id?: number | string }
  error?: ToolErrorInfo
}
export interface MessageDeltaPayload { content: string }
export interface MessageEndPayload {
  finish_reason: string
  usage?: { model: string; tokens_in: number; tokens_out: number; cost_cny: number }
}
export interface ErrorPayload {
  code: 'NO_SESSION' | 'RATE_LIMIT' | 'LLM_FAILED' | 'UPSTREAM_TIMEOUT' | 'INTERNAL'
  message: string
  recoverable?: boolean
  fallback?: string
}

// ─── 前端渲染态 ────────────────────────────────────────────────
export interface UserMsg  { role: 'user';      content: string; ts: number }

export interface AssistantMsg {
  role: 'assistant'
  content: string
  ts: number
  tool_bundle_id?: string
  usage?: MessageEndPayload['usage']
  error?: ErrorPayload
}

export interface ToolItem {
  tool_id: string
  name: string
  display_name: string
  status: 'running' | 'ok' | 'error'
  progress?: number
  progress_detail?: string
  summary?: Record<string, unknown>
  detail_ref?: { type: string; id?: number | string }
  error?: ToolErrorInfo
  duration_ms?: number
  started_at?: string
}

export interface ToolBundle {
  role: 'tool_bundle'
  id: string
  reason: string
  status: 'pending' | 'done' | 'partial_error' | 'error'
  tools: ToolItem[]
  started_at: number
}

export type ChatItem = UserMsg | AssistantMsg | ToolBundle
