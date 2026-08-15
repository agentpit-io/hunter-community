'use client'

// 加 SKILL 的面板 —— 两种方式,都在侧栏里完成
//
// 用户反馈过两次:
//   ① 「从 GitHub 装」藏在「管理 → 滚到最底下」,太难找
//   ② 「新建」也属于 SKILL 的事,不该留在通用「管理」里
//
// 所以两种方式并到一处,挂在 SKILL 块标题的 ＋ 上。
// **加东西的入口要贴着那类东西放**,不是丢进通用设置。

import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { HUNTER } from '../../lib/hunter-theme'
import { createSkill, type SkillWriteResp } from '../lib/skillClient'
import SkillInstallCard from './SkillInstallCard'

interface Props {
  onClose: () => void
  onDone: (msg: string) => void
  categories: string[]
}

export default function SkillAddPanel({ onClose, onDone, categories }: Props) {
  const [mode, setMode] = useState<'github' | 'write'>('github')
  return (
    <div style={wrap}>
      <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
        <Tab on={mode === 'github'} onClick={() => setMode('github')}>从 GitHub 装</Tab>
        <Tab on={mode === 'write'} onClick={() => setMode('write')}>自己写一个</Tab>
        <button onClick={onClose} style={iconBtn}><X size={13} strokeWidth={2} /></button>
      </div>
      {mode === 'github'
        ? <SkillInstallCard bare onClose={onClose}
            onInstalled={(names, msg) => onDone(msg || `已装好:${names.join('、')}`)} />
        : <WriteForm categories={categories} onClose={onClose} onDone={onDone} />}
    </div>
  )
}

function Tab({ on, onClick, children }: { on: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick} style={{
      padding: '4px 9px', borderRadius: 6, cursor: 'pointer', fontSize: 11.5, fontFamily: 'inherit',
      background: on ? HUNTER.THEME : 'transparent',
      color: on ? '#fff' : HUNTER.INK_F,
      border: `1px solid ${on ? HUNTER.THEME : HUNTER.LINE}`,
    }}>{children}</button>
  )
}


// ── 自己写 ────────────────────────────────────────────────────

const ICONS = ['⭐', '📊', '📈', '🔍', '💡', '🧭', '📐', '🧮', '⚖️', '🎯', '📋', '🔎']

function WriteForm({ categories, onClose, onDone }:
  { categories: string[]; onClose: () => void; onDone: (m: string) => void }) {
  const [name, setName] = useState('')
  const [icon, setIcon] = useState('⭐')
  const [tpl, setTpl] = useState('')
  const [desc, setDesc] = useState('')
  const [cat, setCat] = useState('')
  const [tools, setTools] = useState<string[]>([])
  const [body, setBody] = useState('')
  const [all, setAll] = useState<{ key: string; name: string }[]>([])
  const [more, setMore] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  // 工具清单**只在展开高级区时才拉** —— 多数人只想要个提问快捷方式,
  // 不该为此多打一个请求
  useEffect(() => {
    if (!more || all.length) return
    fetch('/api/catalog/toolbox', { cache: 'no-store' })
      .then((r) => r.json())
      .then((d) => setAll((d.groups || []).flatMap((g: any) =>
        (g.tools || []).map((t: any) => ({ key: t.key, name: t.name })))))
      .catch(() => {})
  }, [more, all.length])

  const submit = async () => {
    if (busy || !name.trim() || !tpl.trim()) return
    setBusy(true); setErr('')
    try {
      const r: SkillWriteResp = await createSkill({
        name: name.trim(), prompt_tpl: tpl.trim(), icon,
        description: desc || undefined, category: cat || undefined,
        needs_tools: tools, body: body || undefined,
      })
      // synced=false 时文件写好了但模型还看不到 —— 这两种情况用户要做的事
      // 不一样,不能用同一句"已保存"糊过去
      onDone(r.synced === false && r.message ? r.message : `已创建:${name.trim()}`)
    } catch (e: any) {
      setErr(e?.message || '创建失败')
    } finally { setBusy(false) }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
      <div style={{ display: 'flex', gap: 6 }}>
        <select value={icon} onChange={(e) => setIcon(e.target.value)} style={{ ...input, width: 54, padding: '7px 4px' }}>
          {ICONS.map((i) => <option key={i} value={i}>{i}</option>)}
        </select>
        <input value={name} onChange={(e) => setName(e.target.value)} maxLength={20}
          placeholder="名称,如「我的估值复核」" style={{ ...input, flex: 1 }} />
      </div>
      <textarea value={tpl} onChange={(e) => setTpl(e.target.value)} rows={2} maxLength={500}
        placeholder="点击卡片时填进输入框的话,如:用我的清单复核 {股票} 的估值"
        style={{ ...input, resize: 'vertical', fontFamily: 'inherit' }} />

      <button onClick={() => setMore((v) => !v)} style={linkBtn}>
        {more ? '收起' : '写成完整 SKILL（方法论 + 依赖）'} {more ? '▴' : '▾'}
      </button>

      {more && (
        <>
          <input value={desc} onChange={(e) => setDesc(e.target.value)}
            placeholder="一句话说明 —— 模型据此判断什么时候用它" style={input} />
          <select value={cat} onChange={(e) => setCat(e.target.value)} style={input}>
            <option value="">分类(默认「其他」)</option>
            {categories.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <div>
            <div style={{ fontSize: 11, color: HUNTER.INK_F, marginBottom: 4 }}>需要哪些工具（勾选,别手打）</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, maxHeight: 96, overflowY: 'auto' }}>
              {all.map((t) => {
                const on = tools.includes(t.key)
                return (
                  <button key={t.key} title={t.key}
                    onClick={() => setTools((p) => on ? p.filter((x) => x !== t.key) : [...p, t.key])}
                    style={{
                      padding: '2px 7px', borderRadius: 5, cursor: 'pointer', fontSize: 11, fontFamily: 'inherit',
                      background: on ? HUNTER.THEME : HUNTER.PAPER, color: on ? '#fff' : HUNTER.INK_S,
                      border: `1px solid ${on ? HUNTER.THEME : HUNTER.LINE}`,
                    }}>{t.name}</button>
                )
              })}
              {!all.length && <span style={{ fontSize: 11, color: HUNTER.INK_F }}>加载中…</span>}
            </div>
          </div>
          <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={7}
            placeholder={'方法论正文（Markdown）\n\n## 什么时候用\n\n## 怎么做\n\n## 什么时候不适用\n\n—— 最后一节最有用:说清什么情况下不该用它'}
            style={{ ...input, resize: 'vertical', fontFamily: 'ui-monospace, monospace', fontSize: 11.5, lineHeight: 1.6 }} />
        </>
      )}

      {err && <div style={errBox}>{err}</div>}
      <div style={{ display: 'flex', gap: 6 }}>
        <button onClick={onClose} style={{ ...btnGhost, flex: 1 }}>取消</button>
        <button onClick={submit} disabled={busy || !name.trim() || !tpl.trim()}
          style={{ ...btnPrimary, flex: 2, opacity: busy || !name.trim() || !tpl.trim() ? 0.5 : 1 }}>
          {busy ? '保存中…' : '保存'}
        </button>
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
  marginLeft: 'auto', background: 'none', border: 'none', color: HUNTER.INK_F,
  cursor: 'pointer', padding: 2, display: 'flex',
}
const linkBtn: React.CSSProperties = {
  background: 'none', border: 'none', color: HUNTER.THEME, fontSize: 11.5,
  cursor: 'pointer', padding: 0, textAlign: 'left', fontFamily: 'inherit',
}
const errBox: React.CSSProperties = {
  padding: '7px 9px', borderRadius: 6, fontSize: 11.5,
  background: '#FBEEEA', color: '#9B3A22', lineHeight: 1.6,
}
