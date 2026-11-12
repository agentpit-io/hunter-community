// Next.js BFF 代理：转发文件详情/预览/删除请求至 FastAPI /api/chat/files/{fileId}
const HERMES_API = process.env.HERMES_API_URL || 'http://127.0.0.1:8000'

interface RouteCtx {
  params: Promise<{ fileId: string }>
}

function forwardHeaders(req: Request): Record<string, string> {
  const auth = req.headers.get('authorization') || ''
  const userKey = req.headers.get('x-user-key') || req.headers.get('x-hunter-user-id') || ''
  const headers: Record<string, string> = {}
  if (auth) headers['Authorization'] = auth
  if (userKey) headers['X-Hunter-User-Id'] = userKey
  return headers
}

export async function GET(req: Request, ctx: RouteCtx) {
  const { fileId } = await ctx.params
  const url = new URL(req.url)
  const qs = url.search || ''
  const upstream = await fetch(
    `${HERMES_API}/api/chat/files/${encodeURIComponent(fileId)}/preview${qs}`,
    {
      method: 'GET',
      headers: forwardHeaders(req),
      cache: 'no-store',
    },
  )
  const text = await upstream.text()
  return new Response(text, {
    status: upstream.status,
    headers: { 'Content-Type': upstream.headers.get('content-type') || 'application/json' },
  })
}

export async function DELETE(req: Request, ctx: RouteCtx) {
  const { fileId } = await ctx.params
  const upstream = await fetch(
    `${HERMES_API}/api/chat/files/${encodeURIComponent(fileId)}`,
    {
      method: 'DELETE',
      headers: forwardHeaders(req),
    },
  )
  const text = await upstream.text()
  return new Response(text, {
    status: upstream.status,
    headers: { 'Content-Type': upstream.headers.get('content-type') || 'application/json' },
  })
}
