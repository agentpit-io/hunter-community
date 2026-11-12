/**
 * `AGENT_BACKEND=atomcode` 分支：把前端用的那 14 个 opencode 端点 + 1 条 SSE
 * 落到 AtomCode daemon 上。逐条映射见 docs/web-adapter-design.md §2.1。
 *
 * 会话归属、用户身份、OCR、画像注入这些**与底座无关**的逻辑不在这里重写 ——
 * 由 route.ts 以 `AdapterDeps` 传进来，两条分支共用同一段代码。
 */

import { daemonFetch, projectHash } from './daemon.ts'
import { projectHistory } from './history.ts'
import { stripInjected } from './events.ts'
import { forgetSession, isBusy, runTurn, stopTurn, subscribe } from './live-hub.ts'
import { labelFor } from './labels.ts'

export interface AdapterDeps {
  /** 从 Authorization 头或 ?token= 取用户 JWT */
  bearerOf: (req: Request) => string
  /** 调 hermes-api（会话归属的权威数据源） */
  hermes: (method: string, path: string, token: string, body?: unknown)
    => Promise<{ ok: boolean; status: number; data: any }>
  json: (body: unknown, status?: number) => Response
  /** 图片 part → OCR 文本 part */
  imagePartsToText: (parts: any[]) => Promise<any[]>
}

const WORKSPACE = process.env.ATOMCODE_WORKSPACE || '/workspace'

/** daemon 的时间戳有秒和毫秒两种口径（列表是秒、详情是毫秒），统一成毫秒。 */
function toMs(v: any): number {
  const n = Number(v)
  if (!Number.isFinite(n) || n <= 0) return 0
  return n < 1e12 ? Math.round(n * 1000) : Math.round(n)
}

function isUuid(s: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(s)
}

/** AtomCode 的会话 id 是 UUID（opencode 是 `ses_` 前缀），路径识别要按这个来。 */
export function atomcodeSessionIdOf(segs: string[]): string | null {
  const i = segs.indexOf('session')
  if (i < 0) return null
  if (i !== 0 && !(i === 1 && segs[0] === 'api')) return null
  const id = segs[i + 1]
  return id && isUuid(id) ? id : null
}

// ── 会话 ────────────────────────────────────────────────────

interface SessionView {
  id: string
  title: string
  time: { created: number; updated: number }
  location: { directory: string }
}

function viewOf(id: string, raw: any): SessionView {
  const created = toMs(raw?.created_at)
  return {
    id,
    title: String(raw?.name || '新对话'),
    time: { created, updated: toMs(raw?.updated_at) || created },
    location: { directory: String(raw?.working_dir || WORKSPACE) },
  }
}

/**
 * 会话列表。**以 hermes 的归属表为准**，daemon 只补元数据。
 *
 * 为什么不用 daemon 的 `GET /sessions`：本轮实测它**只列有消息的会话**
 * （`message_count>0`），而前端「新建对话」之后立刻要在左栏看到它。
 * 顺带也不把 daemon 的全量列表发给浏览器，少一个跨用户泄露面。
 */
async function listSessions(ids: string[]): Promise<SessionView[]> {
  const hash = await projectHash()
  const bulk = await daemonFetch<any[]>('GET', '/sessions', undefined, 20_000)
  const known = new Map<string, any>()
  for (const s of bulk.data || []) {
    if (s?.id) known.set(String(s.id), s)
  }
  const missing = ids.filter((id) => !known.has(id))
  // 归属表按 last_used_at 倒序，取前 50 条补详情就够左栏用了
  const detailed = await Promise.all(
    missing.slice(0, 50).map(async (id) => {
      const r = await daemonFetch<any>('GET', `/projects/${hash}/sessions/${encodeURIComponent(id)}`, undefined, 15_000)
      return r.ok && r.data ? ([id, r.data] as const) : null
    }),
  )
  for (const d of detailed) {
    if (d) known.set(d[0], d[1])
  }
  return ids
    .map((id) => (known.has(id) ? viewOf(id, known.get(id)) : null))
    .filter((s): s is SessionView => s !== null)
}

// ── 配置 / provider / agent ────────────────────────────────

async function configView(): Promise<Record<string, any>> {
  const r = await daemonFetch<any>('GET', '/config', undefined, 15_000)
  const cfg = r.data || {}
  const name = String(cfg.default_provider || '')
  const p = (cfg.providers || []).find((x: any) => x?.name === name) || (cfg.providers || [])[0]
  const model = p?.model ? `${p.name}/${p.model}` : ''
  return { model, provider: p?.name || '', workdir: cfg.default_workdir || WORKSPACE }
}

async function providersView(): Promise<Record<string, any>> {
  const r = await daemonFetch<any[]>('GET', '/models', undefined, 15_000)
  const byProvider = new Map<string, Record<string, any>>()
  const def: Record<string, string> = {}
  const modelIds: string[] = []
  for (const m of r.data || []) {
    const pid = String(m?.provider || '')
    const mid = String(m?.model || '')
    if (!pid || !mid) continue
    if (!byProvider.has(pid)) byProvider.set(pid, {})
    if (!modelIds.includes(mid)) modelIds.push(mid)
    byProvider.get(pid)![mid] = { id: mid, name: mid }
    if (m?.is_default || !def[pid]) def[pid] = mid
  }
  const providerIds = Array.from(byProvider.keys())
  // 显示名：**请求用的模型 ID 一个字都不改**（`id` 保持 `hunter-chat`，
  // `resolveModelKey` 比的是 id），只改给人看的 `name`。见 labels.ts 的说明。
  for (const [pid, models] of byProvider) {
    for (const mid of Object.keys(models)) {
      // 名字后面挂一句「全局生效」：**已拍板决策 9** 要的是"让用户看懂这个选择器切的是什么"。
      // 前端把 agent 选择器藏起来了（`InputBox.tsx:503`：AgentPicker 已隐藏），
      // 用户实际看得到的是这个 model picker；而在本发行版里切模型走的是
      // `POST /live/provider` —— daemon 是单例，**换的是所有人的模型**，不是只换自己这一个会话。
      // 前端用 `m.name || mid` 当显示名（`AgentModelPicker.tsx:178`），所以改 name 即可，
      // 前端一行都不用动。
      const label = labelFor(process.env.HCA_LLM_MODEL_LABEL, mid, modelIds)
      models[mid] = { id: mid, name: `${label}（全局生效）` }
    }
  }
  return {
    providers: providerIds.map((id) => ({
      id,
      // 分组标题：provider id（`oneapi`/`official`/`ollama`）对用户没意义，
      // 换成 `HCA_LLM_PROVIDER_LABEL` 给的名字（例：Google Gemini）。
      name: labelFor(process.env.HCA_LLM_PROVIDER_LABEL, id, providerIds),
      models: byProvider.get(id)!,
    })),
    default: def,
  }
}

/**
 * AtomCode 没有 agent，只有权限档。P0 **只暴露 `build` 一个**（设计文档 §7 差异 1、2）：
 * 人格由 `.atomcode.md` 决定，本来就只有一个；而切权限档是**全局**的，
 * 多人同时用网页时 A 切档会影响 B。
 *
 * 名字与说明按**已拍板决策 9**（总控规则，2026-09-22 12:15，M3 U-10 的答复）写：
 * 选择器保留，但要让用户看懂它切的是**审批档**而不是"换一个助手"。
 * 前端把 `name` 显示在按钮上、`description` 显示在下拉项里，所以这两段文字
 * 就是那个"标注" —— 这样不用改前端（决策 1：前端尽量零改动），
 * opencode 那条分支的语义也不受影响（它那边这里确实是 agent）。
 */
function agentsView(): Array<Record<string, any>> {
  return [{
    name: '投研助手 · 审批档 build',
    description:
      'AtomCode 这里切的是**审批档**（能否写文件），不是换助手 —— 全局生效，' +
      '多人同时用时互相影响。本发行版只暴露 build 一档：工作区内写文件不弹审批，' +
      '真正的边界由 guard hook 守（只放行 reports/ 与 theses/）。',
    mode: 'primary',
    native: true,
  }]
}

// ── 入口 ────────────────────────────────────────────────────

export async function handleAtomcode(
  req: Request,
  segs: string[],
  deps: AdapterDeps,
): Promise<Response> {
  const { json } = deps
  const token = deps.bearerOf(req)
  const path = segs.join('/')
  const sid = atomcodeSessionIdOf(segs)
  const isCollection = path === 'session' || path === 'api/session'
  const isEvent = path === 'event' || path === 'api/event'

  // ── 公共资源 ──
  if (path === 'config') return json(await configView())
  if (path === 'config/providers') return json(await providersView())
  if (path === 'agent') return json(agentsView())

  if (!token) return json({ error: 'unauthorized', message: '需要登录' }, 401)

  // ── 事件流 ──
  if (isEvent) return eventStream(req, token, deps)

  // ── 会话集合 ──
  if (isCollection && req.method === 'GET') {
    const r = await deps.hermes('GET', '/api/chat/sessions', token)
    if (r.status === 401) return json({ error: 'INVALID_TOKEN', needLogin: true }, 401)
    if (!r.ok) return json({ error: 'ownership_unavailable', message: '会话服务暂不可用' }, 503)
    return json(await listSessions(r.data?.session_ids ?? []))
  }

  if (isCollection && req.method === 'POST') {
    let title = '新对话'
    try {
      const body = await req.json().catch(() => null)
      if (body?.title) title = String(body.title)
    } catch { /* 没给标题就用默认名 */ }

    const created = await daemonFetch<any>('POST', '/sessions', {}, 30_000)
    const newId = created.data?.id
    if (!created.ok || !newId) {
      return json({ error: 'upstream_unreachable', message: `建会话失败：HTTP ${created.status}` }, 502)
    }
    // `POST /sessions` 忽略请求体里的 name（本轮实测），标题得单独改一次
    const hash = await projectHash()
    await daemonFetch('PATCH', `/projects/${hash}/sessions/${encodeURIComponent(newId)}/rename`, { name: title }, 15_000)

    const claim = await deps.hermes('POST', '/api/chat/sessions', token, { session_id: newId, title })
    if (!claim.ok) {
      // 登记失败 = 孤儿会话（谁也看不到），必须让用户知道（与 opencode 分支同一判断）
      console.error('[atomcode] 会话归属登记失败：', newId, claim.status)
      return json({ error: 'claim_failed', message: '会话创建成功但归属登记失败，请重试' }, 500)
    }
    return json(viewOf(newId, { ...created.data, name: title }))
  }

  if (!sid) return json({ error: 'forbidden', message: '该接口未开放' }, 403)

  // ── 单会话：先验归属 ──
  const owned = await deps.hermes('GET', `/api/chat/sessions/${encodeURIComponent(sid)}/owned`, token)
  if (owned.status === 401) return json({ error: 'INVALID_TOKEN', needLogin: true }, 401)
  if (!owned.ok) return json({ error: 'ownership_unavailable', message: '会话服务暂不可用' }, 503)
  if (!owned.data?.owned) return json({ error: 'forbidden', message: '无权访问该对话' }, 403)

  const hash = await projectHash()
  const tail = segs[segs.length - 1]

  if (tail === 'message' && req.method === 'GET') {
    const r = await daemonFetch<any>('GET', `/projects/${hash}/sessions/${encodeURIComponent(sid)}`, undefined, 30_000)
    if (!r.ok) return json({ error: 'upstream_unreachable', message: `拉历史失败：HTTP ${r.status}` }, 502)
    return json(projectHistory(sid, r.data?.messages))
  }

  if (tail === 'message' && req.method === 'POST') {
    return sendMessage(req, sid, token, deps)
  }

  if (tail === 'abort' && req.method === 'POST') {
    return json(await stopTurn(sid))
  }

  if (tail === 'agent' && req.method === 'POST') {
    // 权限档是全局的；P0 只暴露 build，这里照样落一次，方便本机部署手工改
    const body = await req.json().catch(() => ({} as any))
    const mode = String(body?.agent || 'build')
    const r = await daemonFetch('POST', '/live/mode', { mode }, 15_000)
    return json(r.data ?? { ok: r.ok })
  }

  if (tail === 'model' && req.method === 'POST') {
    const body = await req.json().catch(() => ({} as any))
    const provider = String(body?.model?.providerID || '')
    if (!provider) return json({ ok: false, error: '缺 providerID' }, 400)
    const r = await daemonFetch('POST', '/live/provider', { provider }, 20_000)
    return json(r.data ?? { ok: r.ok })
  }

  if (tail === sid && req.method === 'GET') {
    const r = await daemonFetch<any>('GET', `/projects/${hash}/sessions/${encodeURIComponent(sid)}`, undefined, 20_000)
    if (!r.ok) return json({ error: 'upstream_unreachable', message: `读会话失败：HTTP ${r.status}` }, 502)
    return json(viewOf(sid, r.data))
  }

  if (tail === sid && req.method === 'PATCH') {
    const body = await req.json().catch(() => ({} as any))
    const title = String(body?.title || '').trim()
    if (!title) return json({ ok: false, error: '标题不能为空' }, 400)
    const r = await daemonFetch('PATCH', `/projects/${hash}/sessions/${encodeURIComponent(sid)}/rename`, { name: title }, 15_000)
    if (r.ok) void deps.hermes('PATCH', `/api/chat/sessions/${encodeURIComponent(sid)}`, token, { title })
    return json({ ok: r.ok, title })
  }

  if (tail === sid && req.method === 'DELETE') {
    if (isBusy(sid)) return json({ error: 'busy', message: '这个对话正在生成，先停止再删' }, 409)
    const r = await daemonFetch('DELETE', `/projects/${hash}/sessions/${encodeURIComponent(sid)}`, undefined, 20_000)
    forgetSession(sid)
    if (r.ok) void deps.hermes('DELETE', `/api/chat/sessions/${encodeURIComponent(sid)}`, token)
    return json({ ok: r.ok })
  }

  return json({ error: 'forbidden', message: '该接口未开放' }, 403)
}

// ── 发消息 ──────────────────────────────────────────────────

/**
 * `/live/message` **没有 `system` 字段**（opencode 有），所以画像与技能指令只能进正文。
 * 用 `<hunter-profile>` / `<hunter-skill>` 包起来，投影与历史恢复时再剥掉
 * （events.ts 的 `stripInjected`），用户看到的气泡仍然只有自己打的那句话。
 *
 * **不注入 opencode 分支那一大段 baseSystem**：那段里列的 11 个工具名是 opencode 侧的，
 * 与 AtomCode 的 28 个 MCP 工具对不上；语言 / 合规 / 工具纪律已经写在 `.atomcode.md` 里
 * （M2 实测 C 维度 108.7%、「全中文」与「不给买卖指令」两项 23 次全满分）。
 */
async function sendMessage(req: Request, sid: string, token: string, deps: AdapterDeps): Promise<Response> {
  const { json } = deps
  let body: any = {}
  try {
    const raw = await req.text()
    body = raw ? JSON.parse(raw) : {}
  } catch (e) {
    return json({ error: 'bad_request', message: `请求体解析失败：${(e as Error).message}` }, 400)
  }

  let parts: any[] = Array.isArray(body.parts) ? body.parts : []
  if (parts.some((p) => p?.type === 'file' && typeof p?.mime === 'string' && p.mime.toLowerCase().startsWith('image/'))) {
    parts = await deps.imagePartsToText(parts)
  }
  const userText = parts
    .filter((p) => p?.type === 'text')
    .map((p) => String(p.text || ''))
    .join('\n')
    .trim()
  if (!userText) return json({ error: 'bad_request', message: '消息为空' }, 400)

  let prompt = userText

  // 若包含上传的文档 file_ids，向 hermes-api 拉取文档结构化上下文包与关联股票清单并包入 <hunter-doc-context>
  if (Array.isArray(body.file_ids) && body.file_ids.length > 0) {
    const ctxRes = await deps.hermes('POST', '/api/internal/files/context_bundle', token, {
      session_id: sid,
      file_ids: body.file_ids,
    })
    delete body.file_ids
    if (ctxRes.ok && ctxRes.data?.context_md) {
      const docContextBlock = ctxRes.data.context_md
      let detectedStocksHint = ''
      if (Array.isArray(ctxRes.data.detected_stocks) && ctxRes.data.detected_stocks.length > 0) {
        const stocksStr = ctxRes.data.detected_stocks
          .map((s: any) => `${s.name}(${s.code})`)
          .join('、')
        detectedStocksHint = `\n【文档关联股票识别】系统已从用户上传的文档中识别出关联标的：${stocksStr}。`
      }
      const docInstruction =
        `<uploaded-documents>\n${docContextBlock}\n</uploaded-documents>${detectedStocksHint}\n` +
        `【上传文档分析与交叉核验铁律（最高优先级）】\n` +
        `1. **数字零编造与页码溯源**：凡引用上述上传文档中的财务数字、表格指标或原文观点，**必须原样引用表格数值**，并在句末附上角标 \`[来源：文件名 第X页]\`（例如：\`营业收入 834.51 亿元 [来源：贵州茅台2025半年报摘要.pdf 第2页]\`）。\n` +
        `2. **长文档按需翻页**：若文档标注为 \`paged\`（超长文档摘要模式），当摘要和目录中未包含具体明细时，严禁凭空猜测未展开页面的数字！可使用 \`doc_search\` 工具按页查询。\n` +
        `3. **文档观点 × 实时盘面交叉核验（Hunter 独有优势）**：当用户要求分析研报/财报、评估投资价值或对比盘面时，若文档中识别出了关联股票代码，请**主动调用内置金融工具**（如 \`mcp__watchlist__stock_quickview\` / \`mcp__uzi__stock_deep_analysis\`），将「文档内的历史/预测数据」与「最新实时股价、估值分位」进行对比分析！`
      prompt += `\n\n<hunter-doc-context>\n${docInstruction}\n</hunter-doc-context>`
    }
  }

  const sp = await deps.hermes('GET', '/api/chat/system-prompt', token)
  const profile = sp.ok ? String(sp.data?.system || '').trim() : ''
  if (profile) prompt += `\n\n<hunter-profile>\n${profile}\n</hunter-profile>`

  const skillKey = typeof body.skillKey === 'string' ? body.skillKey.trim() : ''
  if (skillKey) {
    prompt += `\n\n<hunter-skill>\n用户在能力库里明确点选了技能 \`${skillKey}\`（不是随口提问）。` +
      `请先用 use_skill 读这个技能的方法论正文，再严格按它的步骤与输出结构执行本轮分析；` +
      `找不到就如实说「没找到这个能力」，不要假装按它做了。\n</hunter-skill>`
  }

  const reqModelStr = body.model
    ? `${body.model.providerID || ''}/${body.model.modelID || ''}`
    : 'default'
  const tMsg0 = Date.now()
  console.info(`[llm-trace] chat_message session=${sid} model=${reqModelStr} status=start`)
  const result = await runTurn(sid, prompt, stripInjected(userText))
  const statusCode = !result.ok && result.error && !result.text ? (result.stopReason === 'timeout' ? 504 : 502) : 200
  console.info(
    `[llm-trace] chat_message session=${sid} model=${reqModelStr} status=${statusCode} latency_ms=${Date.now() - tMsg0}`,
  )
  void deps.hermes('PATCH', `/api/chat/sessions/${encodeURIComponent(sid)}`, token, {})

  if (!result.ok && result.error && !result.text) {
    return json({
      error: result.stopReason === 'timeout' ? 'upstream_timeout' : 'upstream_unreachable',
      message: result.error,
      id: result.messageId,
    }, statusCode)
  }
  return json({
    id: result.messageId,
    sessionID: sid,
    role: 'assistant',
    stop_reason: result.stopReason,
  })
}

// ── 事件流 ──────────────────────────────────────────────────

/**
 * `GET /api/opencode/event` —— 前端的 EventSource 连这里。
 *
 * 与 opencode 分支的差别：opencode 的 `/event` 是 daemon 的**全量**流，要逐帧解析再按
 * 归属丢弃；这里的事件是 LiveHub 自己造的，**按归属集合直接决定推不推**，
 * 别人的对话从来没进过这条连接。
 *
 * 前端不用改：`useSSE` 本来就会按 `properties.sessionID` 挑出当前会话的事件
 * （见 chat/lib/useSSE.ts），所以这里不需要它多传一个 `session_id` 参数。
 */
async function eventStream(req: Request, token: string, deps: AdapterDeps): Promise<Response> {
  const r = await deps.hermes('GET', '/api/chat/sessions', token)
  if (r.status === 401) return deps.json({ error: 'INVALID_TOKEN', needLogin: true }, 401)
  if (!r.ok) {
    // 归属服务不可用时**不降级为不过滤** —— 宁可报错也不泄露别人的会话
    return deps.json({ error: 'ownership_unavailable', message: '会话服务暂不可用' }, 503)
  }
  const owned = new Set<string>(r.data?.session_ids ?? [])
  return sseResponse((push) => {
    push({ type: 'server.connected', properties: {} })
    return subscribe(owned, push)
  })
}

function sseResponse(attach: (push: (ev: any) => void) => () => void): Response {
  const encoder = new TextEncoder()
  let unsubscribe: (() => void) | null = null
  let heartbeat: ReturnType<typeof setInterval> | null = null

  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      let closed = false
      const push = (ev: any) => {
        if (closed) return
        try {
          controller.enqueue(encoder.encode(`data: ${JSON.stringify(ev)}\n\n`))
        } catch {
          closed = true
        }
      }
      unsubscribe = attach(push)
      // 15 秒一个注释行心跳：中间有反代时，闲置连接不会被悄悄掐掉
      heartbeat = setInterval(() => {
        if (closed) return
        try {
          controller.enqueue(encoder.encode(': ping\n\n'))
        } catch {
          closed = true
        }
      }, 15_000)
    },
    cancel() {
      if (heartbeat) clearInterval(heartbeat)
      if (unsubscribe) unsubscribe()
    },
  })

  return new Response(stream, {
    status: 200,
    headers: {
      'Content-Type': 'text/event-stream; charset=utf-8',
      'Cache-Control': 'no-cache, no-transform',
      Connection: 'keep-alive',
      'X-Accel-Buffering': 'no',
    },
  })
}
