/**
 * Kronos 走势预测 SKILL · SSE 客户端 · Sprint E
 *
 * 直调 hermes-api /api/chat/kpred/* · 与 debateClient 同构
 * 输出 HTML artifact · 交给 ArtifactPanel iframe 渲染
 */

export interface KpredStartArgs {
  stockQuery: string
  days?: number      // 1-30 · 默认 5
  question?: string
  sessionId?: string
}

export interface KpredStartResp {
  task_id: string
  stream_url: string
  stock_code: string
  stock_name: string
}

export interface KpredProgressEvent {
  phase: 'start' | 'kronos' | 'factor' | 'rendering' | 'done'
  pct: number
  text: string
  // done 时:
  html_content?: string
  stock_code?: string
  stock_name?: string
  days?: number
  composite_score?: number
  rating?: string
  adj_return_pct?: number
  elapsed_sec?: number
  task_id?: string
}

export interface KpredFinalResult {
  taskId: string
  htmlContent: string
  summary: string             // markdown 摘要 · 塞 chat 流
  stockCode: string
  stockName: string
  days: number
  compositeScore: number      // -100 ~ +100
  rating: string
  adjReturnPct: number
  elapsedSec: number
}

function getToken(): string {
  if (typeof window === 'undefined') return ''
  return localStorage.getItem('hunter_token') || ''
}

export async function startKpred(args: KpredStartArgs): Promise<KpredStartResp> {
  const token = getToken()
  const res = await fetch('/api/chat/kpred/start', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({
      stock_query: args.stockQuery,
      days: args.days ?? 5,
      question: args.question || '',
      session_id: args.sessionId,
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
 * 订阅 SSE 事件流 · 逐帧回调 onProgress · 完成时 resolve
 * 与 streamDebate 同构 · 只是 phase 集合不同
 */
export function streamKpred(
  taskId: string,
  onProgress: (ev: KpredProgressEvent) => void,
): Promise<KpredFinalResult> {
  return new Promise((resolve, reject) => {
    const url = `/api/public/chat_kpred/stream/${encodeURIComponent(taskId)}`
    const es = new EventSource(url, { withCredentials: false })
    let final: KpredFinalResult | null = null
    let errored = false

    es.addEventListener('hello', () => { /* no-op */ })

    es.addEventListener('progress', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data) as KpredProgressEvent
        onProgress(data)
        if (data.phase === 'done' && data.html_content) {
          final = {
            taskId,
            htmlContent: data.html_content,
            summary: data.text || '',
            stockCode: data.stock_code || '',
            stockName: data.stock_name || '',
            days: data.days ?? 5,
            compositeScore: data.composite_score ?? 0,
            rating: data.rating || '?',
            adjReturnPct: data.adj_return_pct ?? 0,
            elapsedSec: data.elapsed_sec ?? 0,
          }
        }
      } catch (err) {
        console.error('[kpred] progress parse failed:', err, e.data)
      }
    })

    es.addEventListener('error', (e: MessageEvent) => {
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
      else if (!errored) reject(new Error('预测未完成 · 未收到 done 事件'))
    })

    es.onerror = () => {
      if (!final && !errored) {
        errored = true
        es.close()
        reject(new Error('SSE 连接异常 · 请稍后重试'))
      }
    }
  })
}

/** 一步式:启动 + 订阅 · 全流程 5-10s */
export async function runKpred(
  args: KpredStartArgs,
  onProgress: (ev: KpredProgressEvent) => void,
): Promise<KpredFinalResult> {
  const started = await startKpred(args)
  return streamKpred(started.task_id, onProgress)
}

/**
 * 从用户输入文本抽取预测天数
 * 匹配 "未来 5 天" / "5天" / "10 天走势" / "预测 20 日"
 * 无匹配返 5 · clamp 到 1-30
 */
export function extractDays(text: string): number {
  const m = text.match(/(?:未来\s*|预测\s*)?(\d{1,2})\s*[天日]/)
  const n = m ? parseInt(m[1], 10) : NaN
  if (isNaN(n) || n < 1) return 5
  return Math.min(n, 30)
}

// ── Sprint H · 报告持久化 · session 加载时拉回历史 kpred ─────

export interface SessionKpredReport {
  task_id: string
  stock_code: string
  stock_name: string
  days: number
  composite_score: number
  rating: string
  adj_return_pct: number
  content_html: string
  summary_md: string
  question: string
  elapsed_sec: number
  created_at: string
}

export async function listSessionKpreds(sessionId: string): Promise<SessionKpredReport[]> {
  const token = getToken()
  const res = await fetch(
    `/api/chat/kpred/session_reports?session_id=${encodeURIComponent(sessionId)}`,
    {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      cache: 'no-store',
    },
  )
  if (!res.ok) {
    if (res.status === 401) return []
    throw new Error(`拉历史 kpred 失败: ${res.status}`)
  }
  const data = await res.json()
  return data?.items || []
}
