'use client'

// 接自己的工具(MCP)—— 开在工具箱那一行,不用跳去 /mcp-config
//
// 后端 CRUD 早就有了(含 AES 加密存 key、连通性测试、调用统计),
// 缺的只是**入口贴着工具箱放**,以及接完能在侧栏里看见。
//
// 跟 SKILL 那边的区别:SKILL 是文本,装错了顶多答非所问;
// MCP 是**一个会被模型调用的远端服务**,所以这里强制"先测通再保存" ——
// 存一个连不上的 MCP,表现是模型偶尔调用失败,极难排查。

import { useState } from 'react'
import { X, Check, AlertTriangle } from 'lucide-react'
import { HUNTER } from '../../lib/hunter-theme'

interface Props {
  onClose: () => void
  onDone: (msg: string) => void
}

async function api(method: string, path: string, body?: any) {
  const h: Record<string, string> = { 'Content-Type': 'application/json' }
  if (typeof window !== 'undefined') {
    const t = localStorage.getItem('hunter_token') || ''
    if (t) h['Authorization'] = `Bearer ${t}`
  }
  const r = await fetch(`/api${path}`, {
    method, headers: h, body: body ? JSON.stringify(body) : undefined, cache: 'no-store',
  })
  const d = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(d?.detail || d?.message || `HTTP ${r.status}`)
  return d
}

export default function ToolAddPanel({ onClose, onDone }: Props) {
  const [name, setName] = useState('')
  const [slug, setSlug] = useState('')
  const [endpoint, setEndpoint] = useState('')
  const [transport, setTransport] = useState<'sse' | 'http'>('http')
  const [apiKey, setApiKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [tested, setTested] = useState<null | { ok: boolean; msg: string }>(null)

  const ready = name.trim() && slug.trim() && endpoint.trim()

  const save = async () => {
    if (!ready || busy) return
    setBusy(true); setErr(''); setTested(null)
    try {
      const created = await api('POST', '/mcp/user_mcp', {
        name: name.trim(), slug: slug.trim(), transport,
        endpoint: endpoint.trim(), api_key: apiKey || undefined,
      })
      const id = created?.id ?? created?.item?.id
      // 建完立刻测一次 —— 存一个连不上的 MCP 比不存更糟:
      // 模型会去调它然后失败,而用户以为已经接好了
      let msg = `已接入:${name.trim()}`
      try {
        const t = await api('POST', `/mcp/user_mcp/${id}/test`)
        if (t?.ok === false) {
          setTested({ ok: false, msg: t?.error || '连不上' })
          msg = `已保存,但**连不上**:${t?.error || '未知错误'} —— 到「我的工具」里改`
        } else {
          // 顺手拉一次工具清单,否则侧栏只显示"未刷新过工具清单"
          await api('POST', `/mcp/user_mcp/${id}/refresh`).catch(() => {})
        }
      } catch { /* 测试失败不阻断保存,已经在 msg 里说明 */ }
      onDone(msg)
    } catch (e: any) {
      setErr(e?.message || '接入失败')
    } finally { setBusy(false) }
  }

  return (
    <div style={wrap}>
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 9 }}>
        <span style={{ flex: 1, fontSize: 12, fontWeight: 600, color: HUNTER.INK }}>接一个自己的 MCP</span>
        <button onClick={onClose} style={iconBtn}><X size={13} strokeWidth={2} /></button>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
        <input value={name} onChange={(e) => setName(e.target.value)}
          placeholder="显示名,如「我的行情源」" style={input} />
        <input value={slug} onChange={(e) => setSlug(e.target.value.toLowerCase())}
          placeholder="标识(小写字母/数字/下划线)· 模型调用时会带这个前缀" style={input} />
        <div style={{ display: 'flex', gap: 6 }}>
          <select value={transport} onChange={(e) => setTransport(e.target.value as any)}
            style={{ ...input, width: 88 }}>
            <option value="http">HTTP</option>
            <option value="sse">SSE</option>
          </select>
          <input value={endpoint} onChange={(e) => setEndpoint(e.target.value)}
            placeholder="https://…/mcp" style={{ ...input, flex: 1 }} />
        </div>
        <input value={apiKey} onChange={(e) => setApiKey(e.target.value)} type="password"
          placeholder="API key(可选)· 加密存储,不会回显" style={input} />

        <div style={hint}>
          内网地址要填**容器能访问到的** —— 写 localhost 指的是容器自己,
          局域网服务请用宿主机 IP。
        </div>

        {tested && !tested.ok && (
          <div style={warnBox}>
            <AlertTriangle size={11} strokeWidth={1.9} style={{ marginRight: 4, verticalAlign: -1 }} />
            连不上:{tested.msg}
          </div>
        )}
        {err && <div style={errBox}>{err}</div>}

        <div style={{ display: 'flex', gap: 6, marginTop: 2 }}>
          <button onClick={onClose} style={{ ...btnGhost, flex: 1 }}>取消</button>
          <button onClick={save} disabled={!ready || busy}
            style={{ ...btnPrimary, flex: 2, opacity: !ready || busy ? 0.5 : 1 }}>
            {busy ? <>保存并测试中…</> : <><Check size={12} strokeWidth={2.4} style={{ verticalAlign: -2, marginRight: 4 }} />保存并测试</>}
          </button>
        </div>
      </div>
    </div>
  )
}

const wrap: React.CSSProperties = {
  margin: '2px 10px 8px', padding: '10px 11px', borderRadius: 8,
  border: `1px solid ${HUNTER.LINE}`, background: HUNTER.PAPER3,
}
const input: React.CSSProperties = {
  width: '100%', padding: '7px 9px', border: `1px solid ${HUNTER.LINE}`, borderRadius: 7,
  fontSize: 12, color: HUNTER.INK, background: HUNTER.PAPER, fontFamily: 'inherit', outline: 'none',
}
const btnGhost: React.CSSProperties = {
  padding: '7px 0', background: HUNTER.PAPER, color: HUNTER.INK_S,
  border: `1px solid ${HUNTER.LINE}`, borderRadius: 7, fontSize: 12, cursor: 'pointer', fontFamily: 'inherit',
}
const btnPrimary: React.CSSProperties = {
  padding: '7px 0', background: HUNTER.THEME, color: '#fff', border: 'none',
  borderRadius: 7, fontSize: 12, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit',
}
const iconBtn: React.CSSProperties = {
  background: 'none', border: 'none', color: HUNTER.INK_F, cursor: 'pointer', padding: 2, display: 'flex',
}
const hint: React.CSSProperties = {
  fontSize: 10.5, color: HUNTER.INK_F, lineHeight: 1.6,
}
const warnBox: React.CSSProperties = {
  padding: '7px 9px', borderRadius: 6, fontSize: 11.5, lineHeight: 1.6,
  background: HUNTER.TAG_WARN_BG, color: HUNTER.TAG_WARN_FG,
}
const errBox: React.CSSProperties = {
  padding: '7px 9px', borderRadius: 6, fontSize: 11.5,
  background: '#FBEEEA', color: '#9B3A22', lineHeight: 1.6,
}
