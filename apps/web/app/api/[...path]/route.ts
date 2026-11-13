// BFF · 把 /api/* 原样转发到 api 容器（HERMES_API_URL）
//
// 为什么需要它:前端有两套写法 —— 登录/新闻等页面用 NEXT_PUBLIC_API_URL 拼绝对地址,
// 而 /chat 下的 skillClient / profileClient / unlockClient 走**同源相对路径** `/api/*`。
// 演示站前面有 nginx 把 /api/ 转给后端,所以相对路径能通;但用户 clone 下来
// `docker compose up` 是没有 nginx 的,web(3100) 收到 /api/chat/skills 后自己
// 没有对应路由 → 404 → 能力面板空白、画像读不出来。
//
// 加这条兜底代理后,两种部署形态下相对路径都能通,README 里也就不必要求用户装反代。
// 更具体的 /api/opencode/* 有自己的路由文件(带会话归属校验),Next 会优先匹配它,
// 不会被这里截胡。
//
// 只做转发,不做鉴权:后端 api 自己有 JWT 中间件,鉴权只在一处做。

const API = (process.env.HERMES_API_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')

// 逐条剪掉会让浏览器解码出错或与 Next 响应冲突的 header
const DROP = new Set([
  'content-length',
  'transfer-encoding',
  'connection',
  'content-encoding',
  'keep-alive',
])

async function handle(req: Request, segs: string[]): Promise<Response> {
  const url = new URL(req.url)
  const target = `${API}/api/${segs.map(encodeURIComponent).join('/')}${url.search}`

  const headers = new Headers()
  for (const h of ['authorization', 'content-type', 'accept', 'x-hunter-user-id', 'x-user-key']) {
    const v = req.headers.get(h)
    if (v) headers.set(h, v)
  }
  // 初始化会话(首启向导第 0 步签发的)· 不带的话向导每一步都是 401
  const setupSession = req.headers.get('x-hunter-setup-session')
  if (setupSession) headers.set('X-Hunter-Setup-Session', setupSession)

  // ── 真实来源地址 ────────────────────────────────────────────────
  // api 容器看到的对端永远是 web 容器(172.x),拿它判断"用户是不是在本机"
  // 一定是错的 —— 而首启向导的第 0 步正要这个判断(设计方案 4.2)。
  //
  // 这个头从哪来:
  //   · 前面有 nginx 的部署(演示站)—— nginx 写的 X-Forwarded-For;
  //   · 裸 docker compose(没有反代)—— **Next.js 自己补**:`next start` 对
  //     x-forwarded-for 做 `??=`,值取 req.socket.remoteAddress。
  //
  // ⚠️ 用单独的头名转给 api,而不是原样透传 x-forwarded-for:
  //    后者会和真实的反代链混在一起,api 那边分不清"这一跳是谁加的"。
  //
  // ⚠️ 这个值**是客户端可以伪造的**(Next 的 `??=` 不会覆盖客户端自带的头)。
  //    所以 api 侧的门禁不靠它决定安全性 —— 设了 HUNTER_SETUP_TOKEN 就一律要
  //    口令,不管来源看起来是什么。详见 apps/api/app/services/setup_guard.py 的
  //    模块文档。这里转发它,是为了让"本机开箱即用"和第 1 步的环境自检能工作。
  // ⚠️ **X-Real-IP 优先于 X-Forwarded-For**,这不是随手排的顺序:
  //   · nginx 的 `proxy_set_header X-Real-IP $remote_addr` 是**覆盖**写 ——
  //     客户端自己带的会被丢掉,所以有反代时这个头是可信的;
  //   · 而 `X-Forwarded-For $proxy_add_x_forwarded_for` 是**追加**写 ——
  //     客户端带的 `127.0.0.1` 会原样留在最左边。
  // 演示站实测(2026-09-18):只看 XFF 最左边的话,公网访问者加一个
  // `X-Forwarded-For: 127.0.0.1` 就会被判成本机。
  const real = req.headers.get('x-real-ip')
  const fwd = real || req.headers.get('x-forwarded-for')
  if (fwd) headers.set('X-Hunter-Forwarded-For', fwd)

  const init: RequestInit = { method: req.method, headers, cache: 'no-store' }
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    const body = await req.arrayBuffer()
    if (body.byteLength > 0) init.body = body
  }

  let upstream: Response
  try {
    upstream = await fetch(target, init)
  } catch (e) {
    console.error('[bff] api unreachable:', target, e)
    return new Response(
      JSON.stringify({ error: 'upstream_unreachable', detail: String(e), url: target }),
      { status: 502, headers: { 'Content-Type': 'application/json' } },
    )
  }

  const respHeaders = new Headers()
  upstream.headers.forEach((v, k) => {
    if (!DROP.has(k.toLowerCase())) respHeaders.set(k, v)
  })
  // SSE(在线分析 / 辩论 / Kronos 进度)必须关缓存与代理缓冲,否则前端收不到增量
  if ((respHeaders.get('content-type') || '').includes('text/event-stream')) {
    respHeaders.set('Cache-Control', 'no-cache, no-transform')
    respHeaders.set('X-Accel-Buffering', 'no')
  }

  return new Response(upstream.body, { status: upstream.status, headers: respHeaders })
}

interface Ctx { params: Promise<{ path: string[] }> }

export async function GET(req: Request, ctx: Ctx)    { return handle(req, (await ctx.params).path) }
export async function POST(req: Request, ctx: Ctx)   { return handle(req, (await ctx.params).path) }
export async function PUT(req: Request, ctx: Ctx)    { return handle(req, (await ctx.params).path) }
export async function PATCH(req: Request, ctx: Ctx)  { return handle(req, (await ctx.params).path) }
export async function DELETE(req: Request, ctx: Ctx) { return handle(req, (await ctx.params).path) }

export const runtime = 'nodejs'
export const maxDuration = 600
