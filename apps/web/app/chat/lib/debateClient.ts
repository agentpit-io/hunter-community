/**
 * 多专家辩论 SSE 客户端 · 直调 hermes-api · 不走 opencode/BFF
 *
 * 用户点 [⚖️ 多专家辩论] SKILL 卡 · ChatWorkspace 检测到 skill_key='debate' 后
 * 走这里而不是 sendMessage 正常流程
 */

export type DebateDepth = 'quick' | 'normal' | 'deep'

export interface DebateStartArgs {
  stockQuery: string
  question?: string
  sessionId?: string
  messageId?: string
  depth?: DebateDepth
}

export interface DebateStartResp {
  task_id: string
  stream_url: string
  stock_code: string
  stock_name: string
}

export interface DebateProgressEvent {
  phase: 'technical' | 'news' | 'bull' | 'bear' | 'judge' | 'risk' | 'done'
  pct: number
  text: string
  // done 时附带:
  decision?: 'BUY' | 'HOLD' | 'SELL'
  confidence?: number
  elapsed_sec?: number
  stock_code?: string
  stock_name?: string
}

export interface DebateFinalResult {
  taskId: string
  markdown: string
  decision: 'BUY' | 'HOLD' | 'SELL'
  confidence: number
  stockCode: string
  stockName: string
  elapsedSec: number
}

function getToken(): string {
  if (typeof window === 'undefined') return ''
  return localStorage.getItem('hunter_token') || ''
}

/**
 * 启动辩论任务 · 返 task_id
 * 400 = 股票解析失败(HTTPException from resolve_stock)
 * 429 = rate limit(30 min 3 次已用满)
 */
export async function startDebate(args: DebateStartArgs): Promise<DebateStartResp> {
  const token = getToken()
  const res = await fetch('/api/chat/debate/start', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({
      stock_query: args.stockQuery,
      question: args.question || '',
      session_id: args.sessionId,
      message_id: args.messageId,
      depth: args.depth || 'normal',
    }),
    cache: 'no-store',
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    let msg = text.slice(0, 200)
    try {
      const j = JSON.parse(text)
      msg = j?.detail || j?.message || msg
    } catch {}
    throw new Error(`${res.status}: ${msg}`)
  }
  return res.json()
}

/**
 * 订阅 SSE 事件流 · 逐帧回调 onProgress · 完成时 resolve 最终结果
 *
 * SSE 事件类型:
 *   event: hello       首次连上
 *   event: progress    phase 进度 (6 阶段 pct 0-100)
 *   event: error       后端异常
 *   event: end         正常结束
 *
 * done 阶段的 text 字段就是完整 markdown 报告
 */
export function streamDebate(
  taskId: string,
  onProgress: (ev: DebateProgressEvent) => void,
): Promise<DebateFinalResult> {
  return new Promise((resolve, reject) => {
    // hermes-api /api/public/chat_debate/stream/{task_id} · 走 middleware 白名单 · 无需 token
    const url = `/api/public/chat_debate/stream/${encodeURIComponent(taskId)}`
    const es = new EventSource(url, { withCredentials: false })
    let final: DebateFinalResult | null = null
    let errored = false

    es.addEventListener('hello', () => {
      // no-op · 表示 SSE 连上了
    })

    es.addEventListener('progress', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data) as DebateProgressEvent
        onProgress(data)
        if (data.phase === 'done') {
          final = {
            taskId,
            markdown: data.text || '',
            decision: (data.decision as any) || 'HOLD',
            confidence: data.confidence ?? 0.5,
            stockCode: data.stock_code || '',
            stockName: data.stock_name || '',
            elapsedSec: data.elapsed_sec ?? 0,
          }
        }
      } catch (err) {
        console.error('[debate] progress parse failed:', err, e.data)
      }
    })

    es.addEventListener('error', (e: MessageEvent) => {
      // SSE 层错(网络断开 / 服务器 500)
      let errMsg = '连接中断'
      try {
        if (e.data) {
          const d = JSON.parse(e.data)
          errMsg = d?.error || errMsg
        }
      } catch {}
      if (!errored) {
        errored = true
        es.close()
        reject(new Error(errMsg))
      }
    })

    es.addEventListener('end', () => {
      es.close()
      if (final) resolve(final)
      else if (!errored) reject(new Error('辩论未完成 · 未收到 done 事件'))
    })

    // 底层错误(EventSource 自身)
    es.onerror = () => {
      if (!final && !errored) {
        errored = true
        es.close()
        reject(new Error('SSE 连接异常 · 请稍后重试'))
      }
    }
  })
}

/**
 * 一步式:启动 + 订阅 · 全流程 60-90s
 */
export async function runDebate(
  args: DebateStartArgs,
  onProgress: (ev: DebateProgressEvent) => void,
  onMeta?: (meta: { stockCode: string; stockName: string; taskId: string }) => void,
): Promise<DebateFinalResult> {
  const started = await startDebate(args)
  onMeta?.({
    stockCode: started.stock_code,
    stockName: started.stock_name,
    taskId: started.task_id,
  })
  return streamDebate(started.task_id, onProgress)
}

// ── B2 · 报告持久化 · session 加载时拉回历史辩论 ─────────

export interface SessionDebateReport {
  task_id: string
  stock_code: string
  stock_name: string
  decision: 'BUY' | 'HOLD' | 'SELL'
  confidence: number
  content_md: string
  elapsed_sec: number
  question: string
  created_at: string
}

export async function listSessionDebates(sessionId: string): Promise<SessionDebateReport[]> {
  const token = getToken()
  const res = await fetch(
    `/api/chat/debate/session_reports?session_id=${encodeURIComponent(sessionId)}`,
    {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      cache: 'no-store',
    },
  )
  if (!res.ok) {
    if (res.status === 401) return []   // 未登录 · 静默不显示
    throw new Error(`拉历史辩论失败: ${res.status}`)
  }
  const data = await res.json()
  return data?.items || []
}
