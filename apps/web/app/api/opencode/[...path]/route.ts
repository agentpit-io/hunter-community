// BFF · 反代 opencode-server:3901
// 塞 basic auth (对 opencode 走基础认证) · 转发用户 JWT 供 hunter-auth plugin 读
// 支持 SSE 流转 · body pipe
//
// ⚠️ 本文件同时是「用户隔离」的执行点:
//   opencode 的 session 按 project(目录)分组,没有"用户"概念,
//   `GET /session` 谁调都返回全部。若原样透传,所有人都能看到彼此的对话。
//   因此这里做三件事:
//     1. 列表 → 只返回本人拥有的会话
//     2. 新建 → 立刻到 hermes-api 登记归属
//     3. 单会话操作 → 先验归属,非本人一律 403
//   鉴权必须在服务端完成 —— 前端过滤只是"看不见",直连 API 照样拿得到。

import { Agent } from 'undici'

const OPENCODE_URL = process.env.OPENCODE_URL || 'http://127.0.0.1:3901'
const OPENCODE_USER = process.env.OPENCODE_SERVER_USERNAME || 'opencode'
const OPENCODE_PW = process.env.OPENCODE_SERVER_PASSWORD || ''
// hermes-api:会话归属的权威数据源(它已有 JWT 中间件,鉴权只在一处做)
const HERMES_API = process.env.HERMES_API_URL || 'http://127.0.0.1:8000'

// undici 默认 headersTimeout=300s(5 分钟)· LLM+多 tool 编排单条 message 可能更长
// 用自定义 Agent 加长到 10 分钟 · 与文件末尾 maxDuration=600 对齐
// (2026-08-10 · 修复用户实测 5 min 超时后 502 upstream_unreachable)
const OPENCODE_DISPATCHER = new Agent({
  headersTimeout: 600_000,
  bodyTimeout: 600_000,
  connectTimeout: 30_000,
})

function toBase64(input: string): string {
  if (typeof Buffer !== 'undefined') return Buffer.from(input).toString('base64')
  return btoa(input)
}

const BASIC_AUTH = `Basic ${toBase64(`${OPENCODE_USER}:${OPENCODE_PW}`)}`

/**
 * 取用户 token。
 * 普通请求走 Authorization header;SSE 走 ?token= —— EventSource 没有设置 header 的 API,
 * 见 chat/lib/useSSE.ts。两处都要认,否则事件流会被当成未登录。
 */
function bearerOf(req: Request): string {
  const raw = req.headers.get('authorization') || ''
  if (raw.startsWith('Bearer ')) return raw.slice(7).trim()
  try {
    return new URL(req.url).searchParams.get('token')?.trim() || ''
  } catch {
    return ''
  }
}

function buildHeaders(req: Request, extra?: Record<string, string>): Headers {
  const headers = new Headers()
  const userToken = bearerOf(req)

  headers.set('Authorization', BASIC_AUTH)
  if (userToken) headers.set('X-Hunter-User-Token', userToken)

  const ct = req.headers.get('content-type')
  if (ct) headers.set('Content-Type', ct)

  const accept = req.headers.get('accept')
  if (accept) headers.set('Accept', accept)

  if (extra) for (const [k, v] of Object.entries(extra)) headers.set(k, v)
  return headers
}

function buildUpstreamUrl(pathSegs: string[], req: Request): string {
  const base = OPENCODE_URL.replace(/\/$/, '')
  const path = pathSegs.map(encodeURIComponent).join('/')
  const url = new URL(req.url)
  const qs = url.search || ''
  return `${base}/${path}${qs}`
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

// ─── 归属:全部委托给 hermes-api(它已有 JWT 中间件) ───────────────

async function hermes(
  method: string,
  path: string,
  token: string,
  body?: unknown,
): Promise<{ ok: boolean; status: number; data: any }> {
  try {
    const res = await fetch(`${HERMES_API}${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: 'no-store',
    })
    const data = await res.json().catch(() => null)
    return { ok: res.ok, status: res.status, data }
  } catch (e) {
    console.error('[bff] hermes-api unreachable:', path, e)
    return { ok: false, status: 503, data: null }
  }
}

/** 我拥有的 session id 集合 */
async function ownedIds(token: string): Promise<Set<string> | 'unauthorized' | null> {
  const r = await hermes('GET', '/api/chat/sessions', token)
  // 区分下游 401(token 过期/无效)vs 真正的下游不可用 · 让 AuthGuard 能自愈
  // 之前不管 401 还是别的 · ownedIds 都返 null · BFF 一律回 503 · AuthGuard 抓不到 · token 死锁
  if (r.status === 401) return 'unauthorized'
  if (!r.ok) return null
  return new Set<string>(r.data?.session_ids ?? [])
}

/** 是否拥有该会话 */
async function owns(token: string, sessionId: string): Promise<boolean | 'unauthorized' | null> {
  const r = await hermes('GET', `/api/chat/sessions/${encodeURIComponent(sessionId)}/owned`, token)
  if (r.status === 401) return 'unauthorized'
  if (!r.ok) return null
  return !!r.data?.owned
}

// ─── 路径识别 ────────────────────────────────────────────────

/**
 * 从路径段里认出「这是对某个具体 session 的操作」。
 * opencode 的会话路径有两种前缀:
 *   /session/{id}/...        (主要)
 *   /api/session/{id}/...    (agent / model 切换)
 */
function sessionIdOf(segs: string[]): string | null {
  const i = segs.indexOf('session')
  if (i < 0) return null
  // 只认 session 或 api/session 开头,避免误伤其他含 session 字样的路径
  if (i !== 0 && !(i === 1 && segs[0] === 'api')) return null
  const id = segs[i + 1]
  return id && id.startsWith('ses_') ? id : null
}

/** 是否为「会话集合」端点(GET=列表, POST=新建) */
function isSessionCollection(segs: string[]): boolean {
  return (
    (segs.length === 1 && segs[0] === 'session') ||
    (segs.length === 2 && segs[0] === 'api' && segs[1] === 'session')
  )
}

/**
 * 兜底防线:这条路径是否"跟会话有关但我们没认出来"。
 *
 * opencode 一共有 48 条含 session 的路径,前端只用其中 6 条。剩下的里有几条会越过
 * 上面两个识别函数,例如:
 *   /experimental/session/{id}/background   前缀不是 session/api,sessionIdOf 返回 null
 *   /session/status · /api/session/active   聚合型,可能跨用户
 *   /experimental/control-plane/move-session  id 在 body 里,路径上看不出来
 * 这些一旦按"公共资源"放行,隔离就有洞。而且上游随时可能加新路由。
 *
 * 所以采取 deny-by-default:凡是沾会话又没被显式认出的,一律拒绝。
 * 前端用不到它们,拒了不影响功能。
 */
function isUnrecognizedSessionPath(segs: string[]): boolean {
  if (segs[0] === 'experimental') return true                 // 实验接口一律不开放
  if (segs.some((s) => s.startsWith('ses_'))) return true     // 带会话 id 却没被认出
  const i = segs.indexOf('session')
  if (i === 0 || (i === 1 && segs[0] === 'api')) return true  // session 命名空间下的其他端点
  return false
}

// ─── 转发 ────────────────────────────────────────────────────

async function forward(
  req: Request,
  pathSegs: string[],
  overrideBody?: Uint8Array,
): Promise<Response> {
  const upstreamUrl = buildUpstreamUrl(pathSegs, req)

  const hasBody = req.method !== 'GET' && req.method !== 'HEAD'
  let bodyBytes: ArrayBuffer | Uint8Array | null = overrideBody ?? null
  if (hasBody && !overrideBody) {
    try {
      bodyBytes = await req.arrayBuffer()
    } catch (e) {
      console.error('[bff] read body failed:', e)
      bodyBytes = null
    }
  }

  const fetchOpts: RequestInit = {
    method: req.method,
    headers: buildHeaders(req),
  }
  if (bodyBytes && bodyBytes.byteLength > 0) {
    fetchOpts.body = bodyBytes as BodyInit
  }

  let upstream: Response
  const t0 = Date.now()
  try {
    // dispatcher 塞进 fetch 让 undici 用我们的 Agent(long-timeout)
    upstream = await fetch(upstreamUrl, { ...fetchOpts, dispatcher: OPENCODE_DISPATCHER } as any)
  } catch (err: any) {
    const dur = Date.now() - t0
    const causeCode = err?.cause?.code || ''
    const isTimeout =
      causeCode === 'UND_ERR_HEADERS_TIMEOUT' || causeCode === 'UND_ERR_BODY_TIMEOUT'
    console.error(
      `[bff] upstream fetch ${isTimeout ? 'TIMEOUT' : 'FAILED'} after ${dur}ms:`,
      upstreamUrl, causeCode || String(err),
    )
    return json({
      error: isTimeout ? 'upstream_timeout' : 'upstream_unreachable',
      detail: String(err?.cause || err),
      cause_code: causeCode,
      duration_ms: dur,
      url: upstreamUrl,
    }, 502)
  }

  // 保留大多数 header · 但剪掉可能影响 Next 响应的
  // 关键: content-encoding 必须剪 · Node fetch 已自动解压 body ·
  //       若透传 gzip header 浏览器再解压会 ERR_CONTENT_DECODING_FAILED
  const respHeaders = new Headers()
  upstream.headers.forEach((v, k) => {
    const lk = k.toLowerCase()
    if (
      lk === 'content-length' ||
      lk === 'transfer-encoding' ||
      lk === 'connection' ||
      lk === 'content-encoding' ||
      lk === 'keep-alive'
    ) return
    respHeaders.set(k, v)
  })

  // SSE 时 · 需要禁用 Next 缓存
  const ct = respHeaders.get('content-type') || ''
  if (ct.includes('text/event-stream')) {
    respHeaders.set('Cache-Control', 'no-cache, no-transform')
    respHeaders.set('X-Accel-Buffering', 'no')
  }

  return new Response(upstream.body, {
    status: upstream.status,
    headers: respHeaders,
  })
}

// ─── SSE 事件流过滤 ──────────────────────────────────────────

/**
 * 从一条 SSE 事件里取出它属于哪个会话。
 * 事件形如 {type, properties:{info:{sessionID}, part:{sessionID}, sessionID}},
 * 三个位置都可能有(见 chat/components/ChatWorkspace.tsx 的 reduceEvents)。
 * 返回 null 表示与具体会话无关(心跳 / server.connected 之类),放行。
 */
function eventSessionId(payload: any): string | null {
  const p = payload?.properties
  if (!p) return null
  return p.info?.sessionID || p.part?.sessionID || p.sessionID || null
}

/**
 * opencode 的 /event 是**全局**事件流 —— 一条连接里混着所有人所有会话的消息内容。
 * 原样透传等于实时泄露别人的对话,比列表泄露更严重。
 * 这里逐帧解析,凡是属于"不是我的会话"的事件直接丢掉。
 *
 * 注意必须跨 chunk 缓冲:一个 SSE 帧可能被 TCP 切成两半到达。
 */
function sseOwnershipFilter(mine: Set<string>): TransformStream<Uint8Array, Uint8Array> {
  const decoder = new TextDecoder()
  const encoder = new TextEncoder()
  let buf = ''

  const allow = (frame: string): boolean => {
    for (const line of frame.split('\n')) {
      if (!line.startsWith('data:')) continue
      const raw = line.slice(5).trim()
      if (!raw || raw === '[DONE]') continue
      try {
        const sid = eventSessionId(JSON.parse(raw))
        // 认不出归属的事件放行(心跳 / server.connected);认得出但不是我的 → 丢
        if (sid && !mine.has(sid)) return false
      } catch {
        // 解析不了的帧保守放行 —— 它不含可识别的会话内容
      }
    }
    return true
  }

  return new TransformStream<Uint8Array, Uint8Array>({
    transform(chunk, controller) {
      buf += decoder.decode(chunk, { stream: true })
      let idx: number
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const frame = buf.slice(0, idx + 2)
        buf = buf.slice(idx + 2)
        if (allow(frame)) controller.enqueue(encoder.encode(frame))
      }
    },
    flush(controller) {
      if (buf && allow(buf)) controller.enqueue(encoder.encode(buf))
    },
  })
}

// ─── 带隔离的分发 ────────────────────────────────────────────

/** 全局事件流:/event · /api/event */
function isEventStream(segs: string[]): boolean {
  return (
    (segs.length === 1 && segs[0] === 'event') ||
    (segs.length === 2 && segs[0] === 'api' && segs[1] === 'event')
  )
}

async function handle(req: Request, segs: string[]): Promise<Response> {
  const token = bearerOf(req)
  const sid = sessionIdOf(segs)
  const collection = isSessionCollection(segs)
  const eventStream = isEventStream(segs)

  // 兜底:沾会话但没被识别的路径一律拒绝(见 isUnrecognizedSessionPath 说明)
  if (!sid && !collection && isUnrecognizedSessionPath(segs)) {
    console.warn('[bff] 拒绝未识别的会话相关路径:', segs.join('/'))
    return json({ error: 'forbidden', message: '该接口未开放' }, 403)
  }

  // 事件流:必须按归属逐帧过滤,否则实时泄露别人的对话
  if (eventStream) {
    if (!token) return json({ error: 'unauthorized', message: '需要登录' }, 401)
    const mine = await ownedIds(token)
    if (mine === 'unauthorized') return json({ error: 'INVALID_TOKEN', needLogin: true }, 401)
    if (mine === null) return json({ error: 'ownership_unavailable', message: '会话服务暂不可用' }, 503)
    const upstream = await forward(req, segs)
    if (!upstream.ok || !upstream.body) return upstream
    return new Response(upstream.body.pipeThrough(sseOwnershipFilter(mine)), {
      status: upstream.status,
      headers: upstream.headers,
    })
  }

  // 非会话相关的公共资源(/agent /skill /config/* /doc …)原样转发
  if (!sid && !collection) return forward(req, segs)

  if (!token) return json({ error: 'unauthorized', message: '需要登录' }, 401)

  // ① 会话列表:只返回本人拥有的
  if (collection && req.method === 'GET') {
    const mine = await ownedIds(token)
    if (mine === 'unauthorized') return json({ error: 'INVALID_TOKEN', needLogin: true }, 401)
    if (mine === null) {
      // 归属服务不可用时**不降级为全量返回** —— 宁可报错也不泄露别人的会话
      return json({ error: 'ownership_unavailable', message: '会话服务暂不可用' }, 503)
    }
    const upstream = await forward(req, segs)
    if (!upstream.ok) return upstream
    let list: any
    try {
      list = await upstream.json()
    } catch {
      return json({ error: 'bad_upstream', message: '会话列表解析失败' }, 502)
    }
    const arr: any[] = Array.isArray(list) ? list : (list?.data ?? [])
    const filtered = arr.filter((s) => s?.id && mine.has(s.id))
    return json(Array.isArray(list) ? filtered : { ...list, data: filtered })
  }

  // ② 新建会话:转发后立刻登记归属
  if (collection && req.method === 'POST') {
    const upstream = await forward(req, segs)
    if (!upstream.ok) return upstream
    let created: any
    try {
      created = await upstream.json()
    } catch {
      return json({ error: 'bad_upstream', message: '创建会话失败' }, 502)
    }
    const newId: string | undefined = created?.id ?? created?.data?.id
    if (newId) {
      const r = await hermes('POST', '/api/chat/sessions', token, {
        session_id: newId,
        title: created?.title ?? created?.data?.title ?? '',
      })
      if (!r.ok) {
        // 登记失败 = 这个会话会变成孤儿(谁也看不到),必须让用户知道
        console.error('[bff] claim session failed:', newId, r.status)
        return json({ error: 'claim_failed', message: '会话创建成功但归属登记失败,请重试' }, 500)
      }
    }
    return json(created)
  }

  // ③ 单会话操作:先验归属
  if (sid) {
    const ok = await owns(token, sid)
    if (ok === 'unauthorized') return json({ error: 'INVALID_TOKEN', needLogin: true }, 401)
    if (ok === null) return json({ error: 'ownership_unavailable', message: '会话服务暂不可用' }, 503)
    if (!ok) return json({ error: 'forbidden', message: '无权访问该对话' }, 403)

    const last = segs[segs.length - 1]

    // 发消息时把 token 塞进 opencode 认识的位置。
    // hunter-auth plugin 只认 message.metadata.hermes_token(见 plugins/hunter-auth.ts),
    // 光传 X-Hunter-User-Token header 它读不到 —— 那样 hunter-budget 也无法按真实用户计费。
    if (req.method === 'POST' && last === 'message') {
      try {
        const raw = await req.text()
        const body = raw ? JSON.parse(raw) : {}

        // token 必须塞进 **parts[0].metadata**,不能只放 body 顶层 ——
        // opencode 的 PromptInput schema 没有顶层 metadata 字段,会被直接剥掉,
        // hunter-auth plugin 因此永远读不到,日志刷"无 hermes_token",
        // 下游所有 hunter tool 退化到 fallback_user_id(开源版没有这个值)→
        // 自选股/持仓类工具全部拿不到用户身份。
        // TextPartInput.metadata 是 Record<String, Any>,自定义键能活下来。
        // (plugin 侧取值顺序见 huntercode plugins/hunter-auth.ts 的 chat.message)
        body.metadata = { ...(body.metadata || {}), hermes_token: token }
        if (Array.isArray(body.parts) && body.parts.length > 0) {
          body.parts = body.parts.map((p: any, i: number) =>
            i === 0 ? { ...p, metadata: { ...(p?.metadata || {}), hermes_token: token } } : p,
          )
        }

        // 注入用户画像作 system prompt —— 这是"记忆体"真正起作用的地方。
        // 走 system 字段而不是塞进 parts:后者会出现在可见对话里,既难看又会被
        // 当成用户自己说的话。没设置过画像的用户返回空串,不注入、行为不变。
        // 前端没传 system 时才注入,避免覆盖调用方的显式设置。
        //
        // 【硬性基础引导】无论用户 profile 是否为空 · 总注入:
        //   1. 语言 · 强制中文思考 + 回答(修 Gemini 默认英文思考)
        //   2. 工具 · 遇金融数据必须调 tool(修"说要调却没真调"的问题)
        //   3. 合规 · 不给买卖建议(hunter-guard 前置)
        if (!body.system) {
          const sp = await hermes('GET', '/api/chat/system-prompt', token)
          const userProfile = sp.ok ? (sp.data?.system || '') : ''

          const baseSystem = `你是"猎鹿人 · Hunter" · 一位专业的中文金融投研助手。

【语言 · 硬性】
- **始终用中文** · 用户看到的所有内容都要是中文
- **禁止输出思考过程** · 不写"让我..." · "首先..." · "Let's..." · "First..." · "OK, let's..."
- 用户只想看结果 · 直接给分析和结论 · 不要暴露内部推理
- 若 tool 返回英文 · 必须**翻译成中文**呈现

【工具 · 硬性 · 允许下列 9 个 · 其他一律禁用】

⚠️ 这份清单**必须与镜像里 .opencode/opencode.jsonc 注册的 MCP 一致**。
   工具名 = {MCP 名}_{tool 名},MCP 名当前是 watchlist / portfolio / uzi / hunter_user。
   列了不存在的工具,模型会先调一次、拿到 "Model tried to call invalid tool" 再重来,
   白烧一轮 token,用户还会在界面上看到一张红色的 invalid 卡片。

── A. 单股（3 个 · 用户说了具体股票时用）──
- \`watchlist_stock_quickview\` · 行情富卡片(股价+涨跌+开高低+52周分位+AI 短评+3 按钮 CTA) ·
   参数 {"code":"600519"} 或 {"code":"0700.HK"} ·
   **问「{股票} 多少钱/今天怎么样/值不值得买/现在如何」一律用它**
- \`watchlist_stock_news\` · 5 条精选新闻+每条 AI 影响 chip(利好/利空/中性/强利好) ·
   参数 {"code":"601899","limit":5} · 用户问「{股票} 有什么新闻/公告/利好利空」时用此
- \`uzi_stock_deep_analysis\` · 深度分析(基本面+技术面+资金面) · 参数 {"code":"600519"} ·
   用户问「深度分析/详细看看/基本面如何」时用此

── B. 我的自选与组合（4 个 · 涉及"我的自选/持仓/组合/风险画像"时必用）──
- \`watchlist_watchlist_digest\` · 当前用户自选清单今日 Top3 涨/跌+AI 归因富卡片 ·
   无参数 · 用户问「我的自选/我的股票 今天谁最强/最弱/自选股日报」时用此
- \`portfolio_portfolio_rebalance\` · 组合级建议富卡片(当前权重 vs 目标+加/减仓动作) ·
   参数 {"cash_available":50000} 可选 · 用户问「我持仓怎么调/调仓/仓位建议/组合建议」时用此
- \`portfolio_portfolio_stress\` · 情景模拟富卡片(3 张损失卡+减半建议) ·
   参数 {"shock_code":"601899","shock_pct":-20} · 用户问「如果 {股票} 跌 X% 我组合亏多少/万一 {股票} 崩了/极端情况」时用此
- \`portfolio_update_risk_profile\` · 读/改风险画像富卡片(保守/稳健/进取 + 现金 + 单票上限) ·
   写入参数按需给 {"risk_tolerance":"low","cash_balance":50000,"max_position":0.20}；
   只读用 {"read_only":true} · 用户说「我风险偏好是啥/我风险偏保守/现金还有 X 万/单票别超过 X%」时用此

── C. 用户自建数据源（2 个 · 内置工具覆盖不到的数据时用）──
- \`hunter_user_list_my_sources\` · 无参数 · 列出该用户在「MCP 组件」里接入的外部数据源及其工具
- \`hunter_user_invoke\` · 调用其中某个工具 · 参数 {"source":"来自上一步","tool":"来自上一步","args":{...}}

【C 组使用规则】
- 触发条件:问题涉及**上面 7 个工具覆盖不到的数据**(加密货币、美股逐笔、第三方新闻、
  海外另类数据等)。此时**先调 \`hunter_user_list_my_sources\` 看用户配了什么**,不要直接说"不支持"
- 若返回 sources 为空 → 才可以回答"暂未接入,可在「MCP 组件」页面添加数据源"
- 若有可用工具 → 用 \`hunter_user_invoke\` 调用,再把结果翻译成中文呈现
- A/B 组能覆盖的(A股/港股/美股行情、我的自选持仓)一律优先用 A/B 组,不要绕到 C 组

【工具选择规则】
- 【优先 B 组】只要用户句子出现"我的/我持仓/我组合/我的自选/我的股票/我风险画像/我现金" → 一律走 B 组 hunter tool
- 【B 组不适用时才用 A 组】用户明确说了具体股票代码/名称 且 不涉及"我的" → 用 A 组
  · 举例: 「茅台走势」→ A 组; 「我持仓里的茅台该不该卖」→ B 组 portfolio_portfolio_rebalance
- 【组合类禁止 A 组降级】若用户问「我的自选谁最强」而你降级到「请提供股票代码」= 严重违规,
  必须调 watchlist_watchlist_digest。B 组 tool 内部会读当前登录用户的自选清单,你不需要问用户要代码。

【工具 · 严禁】
- **禁止用** \`bash\` / \`read\` / \`write\` / \`edit\` / \`glob\` / \`grep\` / \`webfetch\` / \`task\` 等**开发类工具**
- **禁止**用 bash 跑 python / yfinance / yahoo API / urllib 等**自己写代码查金融数据**
- 这些工具是给 code 场景 · 用它们查股票 = 严重违规
- 若 13 个专用工具都不可用 → 直接告诉用户"数据源暂不可用" · 不要绕过
- 注意:回绝前**必须先确认 C 组也不可用**(即 hunter_user_list_my_sources 返回空),不能凭印象说"不支持"

【Tool 输出使用铁律 · 反幻觉 · 最高优先级】
- 生成最终答案时 · **只能引用本轮最新一次 tool 的返回数据** · 严禁引用会话历史里前几轮 tool 的输出
- **股票代码/名称白名单硬规则**（列自选股/持仓/组合时 · 必守）:
  · 若 tool 返回含 \`all_positions\` 字段 → 你能提及的股票 **只能来自 all_positions 数组**
    · 5 个字段（code / name / market / change_pct / price）逐字引用 · 不许改
    · 严禁引用任何不在 all_positions 里的股票 · 即使你"记得"某只是常见热门股（中芯国际/宁德时代/工业富联/恒瑞医药/贵州茅台等）
  · 若 tool 返回 top_gainers=[] · 直接说"今日全部自选无显著涨跌" · 不要"回忆"具体代码
  · 若 tool 返回 total_count=N 但用户组合可能不止 N 只 · 直接引用 total_count · 不要脑补
- **绝对禁止编造**:
  · 每个股票代码 / 名字 / 价格 / 涨跌幅 · 必须来自本轮 tool 输出 · 不得从记忆里编
  · "常见 A 股龙头" / "热门股" 之类的先验知识 · 一律不许用来补充列表
  · 违规样例（严重错误）:
    ✗ tool 返回 all_positions=[紫金/宏桥/长城/神火/新和成/豪迈/世华] · 你回答"中芯/宁德/工业富联/紫金/恒瑞"
    ✗ tool 返回 top_gainers=[] · 你说"最强是紫金 +2.3%"（无数据出处）
- 若 tool 输出的 data_freshness.all_zero_change_pct=true · 提示用户"行情数据源可能陈旧 · 涨跌幅数据不准"
- 若前几轮 tool 输出与本轮矛盾 · **以本轮为准** · 不要合并 · 不要"综合考虑"
- 若 tool 返回的 warnings 数组非空 · 每条 warning 必须原样反映给用户

【Tool Calling · 静默调用铁律】
- 需要调 tool 时 · **立即调用** · **不解释、不预告、不列步骤**
- **绝对禁止**在 tool_call 之前吐任何解释文本 · 包括但不限于:
  · "我将..."/"接下来我会..."/"让我先..."/"我需要先查..."/"首先我要..."
  · "I will..."/"Let me..."/"First, I need to..."/"Let's..."/"OK, let's..."
  · "为了回答这个问题..."/"To answer this..."/"我要用 xx 工具..."
- 用户 UI 会自动展示 tool 卡片(工具名+参数+进度)· 你的解释是**冗余的**
- B 组 tool 返回的是**已经渲染好的富卡片 JSON** · 你**不需要复述卡片里的数字** ·
  只需一句简短总结 + 邀请用户下一步互动("要不要看情景模拟?" 之类) · 保持简洁
- Tool **全部**完成后 · 直接给最终答案(中文) · 不要复述你做了什么
- 若股票代码/名称有歧义 · **直接**调 watchlist_stock_quickview 消歧 · 不解释

【回答风格】
- 结构化 · 分点 · 关键数字加粗 (**1328.36 元**)
- 引用数据来源 · 标注时间戳(如"截至 2026-08-06")
- 客观分析 · 不给买卖建议
- 中式红涨绿跌 · 涨跌幅带正负号(+1.32% / -0.85%)

【合规底线】
- 禁用词: "建议买入/卖出" · "强烈推荐" · "必涨/必跌" · "包赚" · "现在就买"
- 允许: "根据数据..." · "从财务指标看..." · "风险点在..." · "关注 xxx 指标"

【工具调用示例】
✅ 正确: 用户问"茅台多少钱/走势" → 调 \`watchlist_stock_quickview({"code":"600519"})\`;要更深入再调 \`uzi_stock_deep_analysis({"code":"600519"})\`
✅ 正确: 用户问"我的自选谁最强" → 调 \`watchlist_watchlist_digest({})\` (不要问用户要股票代码 · tool 会读自选表)
✅ 正确: 用户问"如果紫金跌 20% 我组合亏多少" → 调 \`portfolio_portfolio_stress({"shock_code":"601899","shock_pct":-20})\`
✅ 正确: 用户问"我持仓怎么调" → 调 \`portfolio_portfolio_rebalance({})\` (前置未录 shares 时 tool 自身会返回引导态)
✅ 正确: 用户说"设置我风险偏保守 · 现金 5 万" → 调 \`portfolio_update_risk_profile({"risk_tolerance":"low","cash_balance":50000})\`
❌ 错误: 用户问"我的自选谁最强" → 回复"请提供股票代码" (违规 · 必须调 watchlist_watchlist_digest)
❌ 错误: 用户问"茅台走势" → 调 \`bash({"command":"python3 -c 'import yfinance...'"})\`
❌ 错误: 调 \`truesource_get_quote\` / \`akshare_ak_spot_a\` / \`kronos_kronos_forecast\` —— 这些工具在本部署里**不存在**,调用必失败`

          body.system = userProfile ? `${baseSystem}\n\n【用户偏好】\n${userProfile}` : baseSystem
        }

        const patched = new TextEncoder().encode(JSON.stringify(body))
        const resp = await forward(req, segs, patched)
        // 发过消息 = 会话活跃,刷新排序时间(失败不影响主流程)
        void hermes('PATCH', `/api/chat/sessions/${encodeURIComponent(sid)}`, token, {})
        return resp
      } catch (e) {
        // 解析失败就原样走,不能因为注入 metadata 把发消息搞挂
        console.error('[bff] patch message metadata failed, 原样转发:', e)
        return forward(req, segs)
      }
    }

    // 改标题时同步一份到归属表(列表页读它,免二次请求 opencode)
    if (req.method === 'PATCH' && last === sid) {
      const raw = await req.text()
      let title = ''
      try {
        title = JSON.parse(raw || '{}')?.title || ''
      } catch {
        /* 标题解析失败不影响转发 */
      }
      const resp = await forward(req, segs, new TextEncoder().encode(raw))
      if (title) void hermes('PATCH', `/api/chat/sessions/${encodeURIComponent(sid)}`, token, { title })
      return resp
    }

    const resp = await forward(req, segs)
    // 删除成功后归档归属记录
    if (req.method === 'DELETE' && last === sid && resp.ok) {
      void hermes('DELETE', `/api/chat/sessions/${encodeURIComponent(sid)}`, token)
    }
    return resp
  }

  return forward(req, segs)
}

interface RouteContext {
  params: Promise<{ path: string[] }>
}

export async function GET(req: Request, ctx: RouteContext) {
  const { path } = await ctx.params
  return handle(req, path)
}
export async function POST(req: Request, ctx: RouteContext) {
  const { path } = await ctx.params
  return handle(req, path)
}
export async function PUT(req: Request, ctx: RouteContext) {
  const { path } = await ctx.params
  return handle(req, path)
}
export async function DELETE(req: Request, ctx: RouteContext) {
  const { path } = await ctx.params
  return handle(req, path)
}
export async function PATCH(req: Request, ctx: RouteContext) {
  const { path } = await ctx.params
  return handle(req, path)
}

// 长会话超时（10 分钟 · Node.js runtime · Edge 不支持 duplex）
export const runtime = 'nodejs'
export const maxDuration = 600
