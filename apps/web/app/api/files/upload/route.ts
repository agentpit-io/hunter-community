// Next.js BFF 代理：转发文件上传请求至 FastAPI /api/chat/files/upload
const HERMES_API = (process.env.HERMES_API_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')

export async function POST(req: Request) {
  const auth = req.headers.get('authorization') || ''
  const userId = req.headers.get('x-hunter-user-id') || ''
  const userKey = req.headers.get('x-user-key') || ''
  const ct = req.headers.get('content-type') || ''

  const headers: Record<string, string> = {}
  if (auth) headers['Authorization'] = auth
  if (userId || userKey) headers['X-Hunter-User-Id'] = userId || userKey
  if (userKey) headers['X-User-Key'] = userKey
  if (ct) headers['Content-Type'] = ct

  const bodyBytes = await req.arrayBuffer()
  try {
    const upstream = await fetch(`${HERMES_API}/api/chat/files/upload`, {
      method: 'POST',
      headers,
      body: bodyBytes,
    })

    const respText = await upstream.text()
    return new Response(respText, {
      status: upstream.status,
      headers: { 'Content-Type': upstream.headers.get('content-type') || 'application/json' },
    })
  } catch (e) {
    return new Response(
      JSON.stringify({ ok: false, error: 'upstream_unreachable', detail: `后端文件解析服务暂不可达: ${String(e)}` }),
      { status: 502, headers: { 'Content-Type': 'application/json' } },
    )
  }
}

export const runtime = 'nodejs'
export const maxDuration = 600
