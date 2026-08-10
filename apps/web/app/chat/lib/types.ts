// opencode API 数据类型 (最小子集 · 按需扩展)

export interface Session {
  id: string
  title: string
  projectID?: string
  location?: { directory?: string }
  cost?: number
  tokens?: {
    input: number
    output: number
    reasoning: number
    cache: { read: number; write: number }
  }
  time?: { created: number; updated: number }
}

export interface MessagePartText {
  type: 'text'
  text: string
}

export interface MessagePartTool {
  type: 'tool'
  tool: string
  callID: string
  state?: {
    status: 'pending' | 'running' | 'completed' | 'error'
    input?: any
    output?: string
    time?: { start?: number; end?: number }
  }
}

export type MessagePart = MessagePartText | MessagePartTool | { type: string; [k: string]: any }

export interface Message {
  id: string
  sessionID: string
  role: 'user' | 'assistant' | 'system'
  parts: MessagePart[]
  time?: { created: number; updated?: number }
}

// SSE event 常见类型 (opencode 官方 event 系统)
export type OpencodeEvent =
  | { type: 'server.connected'; properties?: any }
  | { type: 'session.updated'; properties: { info: Session } }
  | { type: 'message.updated'; properties: { info: Message } }
  | { type: 'message.part.updated'; properties: { part: MessagePart; sessionID: string; messageID: string } }
  | { type: 'session.error'; properties: { error?: any } }
  | { type: string; properties?: any } // catch-all
