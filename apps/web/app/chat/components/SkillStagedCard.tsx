'use client'

// 待确认的 SKILL · `_23` 步 4
//
// 模型读完作者的说明、决定装什么之后,把结果放进**暂存区**(内存,没落盘)。
// 这张卡是用户看到的最后一关:看清楚要装什么,再决定。
//
// 与 `SkillInstallCard`(`_19`)的区别:那张是**用户驱动** ——
// 用户填地址、点探测、勾选、安装。这张是**模型驱动之后的确认** ——
// 装什么已经由模型定了,用户做的是审阅。
//
// 为什么必须有这一关:`_18` 的原则「装之前必须让用户看见内容」。
// 模型直接写盘的话,"确认"就成了走过场 —— 东西已经生效了。

import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, Check, X, ChevronDown, ChevronRight } from 'lucide-react'
import { HUNTER } from '../../lib/hunter-theme'

interface Coupling { why: string; excerpt: string }
interface Risk { why: string; excerpt: string }
interface StagedItem {
  name: string
  content: string
  source_path: string
  note: string
  lines: number
  risks: Risk[]
  coupling: Coupling[]
}
interface Staged {
  repo: string
  items: StagedItem[]
  total: number
  risk_count?: number
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

interface Props {
  /** 装完之后把可抄的用法回给聊天 */
  onInstalled: (msg: string) => void
  onDismiss: () => void
}

export default function SkillStagedCard({ onInstalled, onDismiss }: Props) {
  const [data, setData] = useState<Staged | null>(null)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [open, setOpen] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    api('GET', '/chat/skills/staged')
      .then((d: Staged) => {
        setData(d)
        // 默认全选 —— 模型已经筛过一轮了,再让用户从零勾一遍是重复劳动。
        // 他要做的是**排除**不想要的,不是从头挑
        setPicked(new Set(d.items.map((i) => i.name)))
      })
      .catch((e) => setErr(e.message))
  }, [])

  const toggle = useCallback((n: string) => {
    setPicked((s) => {
      const x = new Set(s)
      x.has(n) ? x.delete(n) : x.add(n)
      return x
    })
  }, [])

  async function confirm() {
    if (!data || picked.size === 0) return
    setBusy(true); setErr('')
    try {
      const r = await api('POST', '/chat/skills/staged/commit', { names: [...picked] })
      // 装完给**能直接抄的一句话**,不是"已保存"。
      // `_18` 的教训:用户不知道接下来该说什么才能用上它
      const tips = data.items
        .filter((i) => picked.has(i.name))
        .map((i) => {
          const m = /prompt_tpl:\s*["']?(.+?)["']?\s*$/m.exec(i.content)
          return m ? m[1] : null
        })
        .filter(Boolean)
      const first = tips[0]
      let msg = `已安装 ${r.count} 个:${r.written.join(' · ')}`
      if (r.failed?.length) {
        // 部分失败必须说 —— 悄悄跳过的话用户以为都装好了
        msg += `\n⚠ ${r.failed.length} 个没装上:` +
          r.failed.map((f: any) => `${f.name}(${f.error})`).join('、')
      }
      if (r.synced === false) {
        // 文件写好了但 opencode 没重扫 —— 这时说"能用了"是骗人
        msg += `\n⚠ opencode 还没重新扫描,可能要重启容器后才生效`
      }
      if (first) msg += `\n\n现在可以说:「${first}」`
      onInstalled(msg)
    } catch (e: any) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }

  async function discard() {
    setBusy(true)
    try { await api('POST', '/chat/skills/staged/discard', {}) } catch { /* 忽略 */ }
    setBusy(false)
    onDismiss()
  }

  if (err && !data) return <div style={errBox}>读不到待确认列表:{err}</div>
  if (!data || data.total === 0) return null

  const dirty = data.items.filter((i) => i.coupling.length > 0).length

  return (
    <div style={wrap}>
      <div style={head}>
        <span style={{ flex: 1, fontWeight: 600, fontSize: 13, color: HUNTER.INK }}>
          待确认 · {data.total} 个 SKILL
        </span>
        <span style={{ fontSize: 11, color: HUNTER.INK_F }}>{data.repo}</span>
      </div>

      <div style={hint}>
        <b>还没有写入磁盘。</b>确认之后才会安装。
        {dirty > 0 && (
          <>
            {' '}其中 <b style={{ color: HUNTER.TAG_WARN_FG }}>{dirty} 个仍引用原仓库的脚本</b> ——
            装进来后模型可能会去跑不存在的东西。
          </>
        )}
      </div>

      {data.items.map((i) => {
        const isOpen = open === i.name
        const bad = i.coupling.length > 0
        return (
          <div key={i.name} style={row(picked.has(i.name))}>
            <label style={{ display: 'flex', alignItems: 'flex-start', gap: 8, cursor: 'pointer' }}>
              <input type="checkbox" checked={picked.has(i.name)}
                     onChange={() => toggle(i.name)} style={{ marginTop: 3 }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 12.5, fontWeight: 600, color: HUNTER.INK }}>{i.name}</span>
                  <span style={{ fontSize: 10.5, color: HUNTER.INK_F }}>{i.lines} 行</span>
                  {bad && (
                    <span style={warnTag}>
                      <AlertTriangle size={9} strokeWidth={2} style={{ verticalAlign: -1 }} />
                      {' '}引用原仓库脚本
                    </span>
                  )}
                  {i.risks.length > 0 && <span style={warnTag}>{i.risks.length} 处风险提示</span>}
                </div>
                {/* 模型为什么装它 —— 这句是用户判断要不要留的主要依据 */}
                {i.note && <div style={noteLine}>{i.note}</div>}
                {i.source_path && (
                  <div style={{ fontSize: 10, color: HUNTER.SOFT }}>← {i.source_path}</div>
                )}
              </div>
            </label>

            <button onClick={() => setOpen(isOpen ? null : i.name)} style={expandBtn}>
              {isOpen ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
              {isOpen ? '收起正文' : '看正文'}
            </button>

            {isOpen && (
              <>
                {bad && (
                  <div style={couplingBox}>
                    {i.coupling.map((c, k) => (
                      <div key={k}>· {c.why}:<code style={code}>{c.excerpt.trim()}</code></div>
                    ))}
                  </div>
                )}
                {/* 正文**全文**,不截断 —— 真正的安全边界是用户看见内容,
                    截断了就看不全(`_18` §6) */}
                <pre style={pre}>{i.content}</pre>
              </>
            )}
          </div>
        )
      })}

      {err && <div style={errBox}>{err}</div>}

      <div style={{ display: 'flex', gap: 6, marginTop: 10 }}>
        <button onClick={discard} disabled={busy} style={{ ...btnGhost, flex: 1 }}>
          <X size={12} strokeWidth={2} style={{ verticalAlign: -2, marginRight: 4 }} />
          全部丢弃
        </button>
        <button onClick={confirm} disabled={busy || picked.size === 0}
                style={{ ...btnPrimary, flex: 2, opacity: busy || !picked.size ? 0.5 : 1 }}>
          <Check size={12} strokeWidth={2.4} style={{ verticalAlign: -2, marginRight: 4 }} />
          {busy ? '安装中…' : `安装选中的 ${picked.size} 个`}
        </button>
      </div>
    </div>
  )
}

const wrap: React.CSSProperties = {
  margin: '10px 0', padding: '12px 14px', borderRadius: 10,
  border: `1px solid ${HUNTER.LINE}`, background: HUNTER.PAPER3,
}
const head: React.CSSProperties = { display: 'flex', alignItems: 'baseline', marginBottom: 6 }
const hint: React.CSSProperties = {
  fontSize: 11.5, color: HUNTER.INK_S, lineHeight: 1.7, marginBottom: 10,
}
const row = (on: boolean): React.CSSProperties => ({
  padding: '8px 10px', marginBottom: 6, borderRadius: 8,
  border: `1px solid ${on ? HUNTER.LINE : 'transparent'}`,
  background: on ? HUNTER.PAPER : 'transparent',
  opacity: on ? 1 : 0.55,
})
const noteLine: React.CSSProperties = {
  fontSize: 11.5, color: HUNTER.INK_S, marginTop: 2, lineHeight: 1.6,
}
const warnTag: React.CSSProperties = {
  fontSize: 10, padding: '1px 6px', borderRadius: 4,
  background: HUNTER.TAG_WARN_BG, color: HUNTER.TAG_WARN_FG,
}
const expandBtn: React.CSSProperties = {
  marginTop: 5, padding: '2px 8px', fontSize: 10.5, borderRadius: 5,
  border: `1px solid ${HUNTER.LINE}`, background: 'transparent',
  color: HUNTER.INK_F, cursor: 'pointer', fontFamily: 'inherit',
}
const couplingBox: React.CSSProperties = {
  margin: '6px 0 0', padding: '6px 8px', borderRadius: 5, fontSize: 10.5,
  lineHeight: 1.7, background: HUNTER.TAG_WARN_BG, color: HUNTER.TAG_WARN_FG,
}
const code: React.CSSProperties = {
  fontSize: 10, fontFamily: 'ui-monospace, Menlo, monospace', wordBreak: 'break-all',
}
const pre: React.CSSProperties = {
  margin: '6px 0 0', padding: '7px 9px', borderRadius: 5, maxHeight: 320, overflow: 'auto',
  background: HUNTER.PAPER, border: `1px solid ${HUNTER.LINE}`,
  fontSize: 10.5, lineHeight: 1.5, color: HUNTER.INK_S,
  fontFamily: 'ui-monospace, Menlo, monospace', whiteSpace: 'pre-wrap',
}
const btnGhost: React.CSSProperties = {
  padding: '7px 0', background: HUNTER.PAPER, color: HUNTER.INK_S,
  border: `1px solid ${HUNTER.LINE}`, borderRadius: 7, fontSize: 12,
  cursor: 'pointer', fontFamily: 'inherit',
}
const btnPrimary: React.CSSProperties = {
  padding: '7px 0', background: HUNTER.THEME, color: '#fff', border: 'none',
  borderRadius: 7, fontSize: 12, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit',
}
const errBox: React.CSSProperties = {
  padding: '7px 9px', borderRadius: 6, fontSize: 11.5, marginTop: 6,
  background: '#FBEEEA', color: '#9B3A22', lineHeight: 1.6,
}
