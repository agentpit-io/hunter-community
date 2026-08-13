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
  for (const h of ['authorization', 'content-type', 'accept']) {
    const v = req.headers.get(h)
    if (v) headers.set(h, v)
  }

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
