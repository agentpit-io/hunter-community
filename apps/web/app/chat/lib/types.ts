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

export interface UploadedAttachment {
  id: string
  fileId?: string
  kind: 'document' | 'image'
  filename: string
  fileExt: 'pdf' | 'xlsx' | 'xls' | 'csv' | 'docx' | 'md' | 'txt' | 'image'
  sizeBytes: number
  status: 'uploading' | 'parsing' | 'ready' | 'error'
  errorMsg?: string
  totalPages?: number
  totalChars?: number
  tableCount?: number
  injectMode?: 'full' | 'paged' | string
  detectedStocks?: Array<{ code: string; name: string; market: string; mentions?: number }>
  extraMeta?: Record<string, any>
  dataUrl?: string
  mime?: string
}

export interface Message {
  id: string
  sessionID: string
  role: 'user' | 'assistant' | 'system'
  parts: MessagePart[]
  time?: { created: number; updated?: number }
  /** opencode 在 assistant 消息上记的失败(模型 402 / 401 / 用户中止…),见 lib/modelError.ts */
  error?: { name?: string; data?: any; message?: string }
  /** 用户消息附带的已上传文档/图片列表 */
  attachments?: UploadedAttachment[]
}

// SSE event 常见类型 (opencode 官方 event 系统)
export type OpencodeEvent =
  | { type: 'server.connected'; properties?: any }
  | { type: 'session.updated'; properties: { info: Session } }
  | { type: 'message.updated'; properties: { info: Message } }
  | { type: 'message.part.updated'; properties: { part: MessagePart; sessionID: string; messageID: string } }
  | { type: 'session.error'; properties: { error?: any } }
  | { type: string; properties?: any } // catch-all
