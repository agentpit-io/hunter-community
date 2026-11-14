// 对话框多格式文档上传与预览客户端（支持 .pdf / .xlsx / .xls / .csv / .docx / .md / .txt）

import type { UploadedAttachment } from './types'

function getToken(): string {
  if (typeof window === 'undefined') return ''
  return localStorage.getItem('hunter_token') || ''
}

function authHeaders(includeJson = true): Record<string, string> {
  const token = getToken()
  const headers: Record<string, string> = {}
  if (includeJson) headers['Content-Type'] = 'application/json'
  if (token) headers['Authorization'] = `Bearer ${token}`
  return headers
}

export interface UploadFileResponse {
  ok: boolean
  file_id: string
  filename: string
  file_ext: string
  file_size_bytes: number
  parse_status: 'ready' | 'parsing' | 'error'
  total_pages: number
  total_chars: number
  table_count: number
  ocr_page_count: number
  inject_mode: 'full' | 'paged'
  detected_stocks: Array<{ code: string; name: string; market: string; mentions?: number }>
  extra_meta?: Record<string, any>
  file?: any
}

export async function uploadChatFile(
  file: File,
  sessionId?: string,
): Promise<UploadedAttachment> {
  if (typeof window !== 'undefined' && !getToken()) {
    try {
      const { ensureLocalSession } = await import('../../lib/localSession')
      await ensureLocalSession()
    } catch { /* ignore */ }
  }
  const makeForm = () => {
    const f = new FormData()
    f.append('file', file)
    if (sessionId) f.append('session_id', sessionId)
    return f
  }

  let res = await fetch('/api/chat/files/upload', {
    method: 'POST',
    headers: authHeaders(false),
    body: makeForm(),
    credentials: 'same-origin',
  })
  if (res.status === 401 && typeof window !== 'undefined') {
    try {
      const { ensureLocalSession } = await import('../../lib/localSession')
      const fresh = await ensureLocalSession(true)
      if (fresh) {
        res = await fetch('/api/chat/files/upload', {
          method: 'POST',
          headers: { Authorization: `Bearer ${fresh}` },
          body: makeForm(),
          credentials: 'same-origin',
        })
      }
    } catch { /* ignore */ }
  }

  if (!res.ok) {
    const rawText = await res.text().catch(() => '')
    let errText = ''
    if (rawText) {
      try {
        const j = JSON.parse(rawText)
        errText =
          typeof j?.detail === 'string'
            ? j.detail
            : j?.detail
              ? JSON.stringify(j.detail)
              : j?.message || j?.error || ''
      } catch {
        errText = rawText.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 180)
      }
    }
    throw new Error(errText || `上传失败 (HTTP ${res.status})`)
  }

  const data: UploadFileResponse = await res.json()
  const rec = data.file || data
  const extRaw = String(rec.file_ext || file.name.split('.').pop() || 'pdf').toLowerCase()
  const validExt = (['pdf', 'xlsx', 'xls', 'csv', 'docx', 'md', 'txt'].includes(extRaw)
    ? extRaw
    : 'pdf') as UploadedAttachment['fileExt']

  return {
    id: rec.file_id,
    fileId: rec.file_id,
    kind: 'document',
    filename: rec.filename || file.name,
    fileExt: validExt,
    sizeBytes: Number(rec.file_size_bytes ?? file.size ?? 0),
    status: 'ready',
    totalPages: Number(rec.total_pages ?? 1),
    totalChars: Number(rec.total_chars ?? 0),
    tableCount: Number(rec.table_count ?? 0),
    injectMode: rec.inject_mode || 'full',
    detectedStocks: Array.isArray(rec.detected_stocks) ? rec.detected_stocks : [],
    extraMeta: rec.extra_meta || {},
  }
}

export async function listSessionFiles(sessionId: string): Promise<UploadedAttachment[]> {
  if (!sessionId) return []
  const res = await fetch(
    `/api/chat/files?session_id=${encodeURIComponent(sessionId)}`,
    {
      method: 'GET',
      headers: authHeaders(),
      credentials: 'same-origin',
      cache: 'no-store',
    },
  )
  if (!res.ok) return []
  const data = await res.json().catch(() => ({}))
  const files: any[] = Array.isArray(data?.files) ? data.files : []
  return files.map((rec) => ({
    id: rec.file_id,
    fileId: rec.file_id,
    kind: 'document',
    filename: rec.filename,
    fileExt: (rec.file_ext || 'pdf') as UploadedAttachment['fileExt'],
    sizeBytes: Number(rec.file_size_bytes ?? 0),
    status: (rec.parse_status || 'ready') as UploadedAttachment['status'],
    totalPages: Number(rec.total_pages ?? 1),
    totalChars: Number(rec.total_chars ?? 0),
    tableCount: Number(rec.table_count ?? 0),
    injectMode: rec.inject_mode || 'full',
    detectedStocks: Array.isArray(rec.detected_stocks) ? rec.detected_stocks : [],
    extraMeta: rec.extra_meta || {},
  }))
}

export interface FilePreviewResult {
  ok: boolean
  file_id: string
  filename: string
  total_pages: number
  table_count: number
  toc: Array<{ level: number; title: string; page: number }>
  pages: Array<{
    page_no: number
    page_title: string
    content_md: string
    char_count: number
    table_count: number
    has_table: boolean
  }>
}

export async function fetchFilePreview(
  fileId: string,
  startPage = 1,
  endPage = 5,
): Promise<FilePreviewResult> {
  const qs = `start_page=${encodeURIComponent(String(startPage))}&end_page=${encodeURIComponent(String(endPage))}`
  const res = await fetch(`/api/chat/files/${encodeURIComponent(fileId)}/preview?${qs}`, {
    method: 'GET',
    headers: authHeaders(),
    credentials: 'same-origin',
    cache: 'no-store',
  })
  if (!res.ok) {
    const j = await res.json().catch(() => ({}))
    throw new Error(j?.detail || j?.message || `加载文档预览失败 (${res.status})`)
  }
  return res.json()
}

export async function deleteChatFile(fileId: string): Promise<void> {
  await fetch(`/api/chat/files/${encodeURIComponent(fileId)}`, {
    method: 'DELETE',
    headers: authHeaders(),
    credentials: 'same-origin',
  })
}

export async function importPortfolioFromFile(
  fileId: string,
): Promise<{ ok: boolean; imported_count: number; imported: any[] }> {
  const res = await fetch('/api/portfolio/import-from-file', {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ file_id: fileId }),
    credentials: 'same-origin',
  })
  if (!res.ok) {
    const j = await res.json().catch(() => ({}))
    throw new Error(j?.detail || `导入持仓失败 (${res.status})`)
  }
  return res.json()
}
