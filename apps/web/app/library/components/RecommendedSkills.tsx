'use client'

// 推荐安装的 SKILL —— `_24` §5
//
// 老板:「给一个入口就是推荐加的 skill,**也不写我们的**,你可以去看看
//        github 有哪些 star 多的,然后点一下就能加上,
//        **这个 skill 算是用户自己加的**」
//
// 最后半句是这个组件的全部设计前提:推荐 ≠ 内置。装完它出现在「你装的」
// 组里,和用户自己贴 URL 装的没有任何区别 —— 我们只是省掉他找和贴的过程。
//
// ## 为什么卡片上要写「N 个 · M 个可直接用 · K 个需改写」
//
// `_24` §5.3:一键安装如果有一半装完是坏的,比没有这个入口更伤 ——
// 用户会认为是我们的产品坏了。所以每条推荐都预先跑过 portability(),
// 结果如实写在卡上,而不是「一键安装」四个字了事。
//
// UZI 是 5 个里只有 1 个能直接装的,那条就得写出来。
//
// ## 为什么把「没推荐谁」也显示出来
//
// 「为什么不推荐 30500 star 那个」是用户会问的。答案是它有 0 个 SKILL.md,
// 点了装不上任何东西 —— 这件事说出来,比默默不列它更能说明我们在按什么标准挑。

import { useEffect, useState } from 'react'
import { Star, Download, Check, ChevronDown, ChevronRight, ExternalLink, AlertTriangle } from 'lucide-react'
import { HUNTER } from '../../lib/hunter-theme'

interface RecItem {
  repo: string
  title: string
  summary: string
  stars: number
  total: number
  clean: number
  lang: string
  tags: string[]
  why: string
  installed?: boolean
}
interface Rejected { repo: string; stars: number; reason: string }
interface RecResponse {
  items: RecItem[]
  checked_at: string
  note?: string
  rejected?: Rejected[]
  missing?: boolean
}
interface Candidate { path: string; name?: string }

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

export default function RecommendedSkills({ onDone }: { onDone: (msg: string) => void }) {
  const [data, setData] = useState<RecResponse | null>(null)
  const [err, setErr] = useState('')
  const [open, setOpen] = useState('')
  /** 展开某条时先 inspect,拿到它到底有哪些 SKILL 让用户勾 */
  const [cands, setCands] = useState<Record<string, Candidate[]>>({})
  const [picked, setPicked] = useState<Record<string, Record<string, boolean>>>({})
  const [busy, setBusy] = useState('')
  const [showRejected, setShowRejected] = useState(false)

  useEffect(() => {
    api('GET', '/chat/skills/recommended')
      .then(setData)
      .catch((e) => setErr(e.message))
  }, [])

  async function expand(it: RecItem) {
    if (open === it.repo) { setOpen(''); return }
    setOpen(it.repo)
    if (cands[it.repo]) return
    setBusy(it.repo); setErr('')
    try {
      const d = await api('POST', '/chat/skills/inspect', { repo: it.repo })
      const list: Candidate[] = (d.candidates || []).map((c: any) =>
        typeof c === 'string' ? { path: c } : c)
      setCands((s) => ({ ...s, [it.repo]: list }))
      // 默认全勾 —— 用户点进来就是想装,不是来做选择题的
      const on: Record<string, boolean> = {}
      list.forEach((c) => { on[c.path] = true })
      setPicked((s) => ({ ...s, [it.repo]: on }))
    } catch (e: any) {
      setErr(`${it.repo}:${e.message}`)
      setOpen('')
    } finally {
      setBusy('')
    }
  }

  async function install(it: RecItem) {
    const paths = Object.entries(picked[it.repo] || {})
      .filter(([, v]) => v).map(([k]) => k)
    if (!paths.length) { setErr('至少勾一个'); return }
    setBusy(it.repo); setErr('')
    try {
      const d = await api('POST', '/chat/skills/install', { repo: it.repo, paths })
      const n = (d.installed || []).length
      // opencode 只在启动时扫 skill 目录 —— 没同步上要说出来,
      // 否则表现是"侧栏有了、模型说没有",用户完全无从判断
      const tail = d.synced === false
        ? ` ⚠️ ${d.message || '需要重启 opencode 才会对模型生效'}`
        : ''
      onDone(`从 ${it.repo} 装了 ${n} 个能力 —— 它们现在在「自定义安装」里。${tail}`)
      setData((s) => s && ({
        ...s,
        items: s.items.map((x) => x.repo === it.repo ? { ...x, installed: true } : x),
      }))
    } catch (e: any) {
      setErr(`装 ${it.repo} 失败:${e.message}`)
    } finally {
      setBusy('')
    }
  }

  if (err && !data) return <div style={errBox}>{err}</div>
  if (!data) return <div style={hint}>加载推荐清单…</div>
  if (data.missing || !data.items.length) {
    return <div style={hint}>还没有推荐清单(<code>data/recommended-skills.json</code>)。</div>
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={hint}>
        这些都是 GitHub 上的开源 SKILL,<b>不是我们的</b>。装完它们属于你,
        和你自己贴 URL 装的没有区别。
        {data.checked_at && <> · 可用性实测于 {data.checked_at}</>}
      </div>

      {data.items.map((it) => {
        const isOpen = open === it.repo
        const list = cands[it.repo] || []
        const on = picked[it.repo] || {}
        const nPick = Object.values(on).filter(Boolean).length
        // 「几个能直接用」是这张卡最要紧的一行 —— 全干净和一半坏
        // 是两种完全不同的东西,不能都显示成「可一键安装」
        const allClean = it.clean === it.total
        return (
          <div key={it.repo} style={card}>
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 13, fontWeight: 600, color: HUNTER.INK }}>{it.title}</span>
                  {it.stars > 0 && (
                    <span style={tagStar}>
                      <Star size={9} strokeWidth={2.4} style={{ verticalAlign: -1 }} /> {fmt(it.stars)}
                    </span>
                  )}
                  <span style={allClean ? tagOk : tagWarn}>
                    {it.total} 个 · {it.clean} 个可直接用
                    {it.clean < it.total && ` · ${it.total - it.clean} 个需模型改写`}
                  </span>
                  {it.installed && <span style={tagDone}>已装过</span>}
                </div>
                <div style={{ fontSize: 11.5, color: HUNTER.INK_S, marginTop: 3 }}>{it.summary}</div>
                <div style={{ ...urlText, marginTop: 3 }}>
                  {it.repo}
                  <a href={`https://github.com/${it.repo}`} target="_blank" rel="noreferrer"
                     style={{ marginLeft: 5, color: HUNTER.THEME }}>
                    <ExternalLink size={9} strokeWidth={2} style={{ verticalAlign: -1 }} />
                  </a>
                </div>
              </div>
              <button style={miniBtn} disabled={!!busy}
                      onClick={() => expand(it)}>
                {busy === it.repo && !isOpen ? '读取中…'
                  : isOpen ? <><ChevronDown size={11} style={{ verticalAlign: -1 }} /> 收起</>
                  : <><ChevronRight size={11} style={{ verticalAlign: -1 }} /> 看看有什么</>}
              </button>
            </div>

            {/* why 一直显示,不折叠 —— 它解释的正是「可直接用几个」那个数字,
                而那个数字是用户决定装不装的依据 */}
            <div style={whyBox}>{it.why}</div>

            {isOpen && (
              <div style={{ marginTop: 8 }}>
                {list.length === 0 && <div style={hint}>这个仓库里没找到 SKILL.md。</div>}
                {list.length > 0 && (
                  <>
                    <div style={{ ...fieldLabel, marginBottom: 5 }}>
                      装哪些({nPick}/{list.length})
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                      {list.map((c) => (
                        <label key={c.path} style={checkRow}>
                          <input type="checkbox" checked={!!on[c.path]}
                                 onChange={(e) => setPicked((s) => ({
                                   ...s, [it.repo]: { ...on, [c.path]: e.target.checked },
                                 }))} />
                          <span style={{ ...urlText, flex: 1 }}>{c.path}</span>
                        </label>
                      ))}
                    </div>
                    {it.clean < it.total && (
                      <div style={warnBox}>
                        <AlertTriangle size={11} strokeWidth={1.9} style={{ marginRight: 4, verticalAlign: -1 }} />
                        这个仓库里有 {it.total - it.clean} 个 SKILL 依赖作者自己的脚本或缓存目录,
                        装进来能看但跑不通。装完之后可以在对话里让模型把它们改写成可移植版本。
                      </div>
                    )}
                    <button style={{ ...btnPrimary, marginTop: 8, width: '100%' }}
                            disabled={!!busy || !nPick} onClick={() => install(it)}>
                      {busy === it.repo
                        ? '安装中…'
                        : <><Download size={12} strokeWidth={2.2} style={{ verticalAlign: -2, marginRight: 4 }} />
                            装这 {nPick} 个</>}
                    </button>
                  </>
                )}
              </div>
            )}
          </div>
        )
      })}

      {err && <div style={errBox}>{err}</div>}

      {/* 没推荐谁 + 为什么 —— 折叠,但要有 */}
      {!!data.rejected?.length && (
        <div>
          <button style={linkBtn} onClick={() => setShowRejected((v) => !v)}>
            {showRejected ? <ChevronDown size={11} style={{ verticalAlign: -1 }} />
                          : <ChevronRight size={11} style={{ verticalAlign: -1 }} />}
            {' '}为什么没推荐 star 更高的那些?
          </button>
          {showRejected && (
            <div style={{ marginTop: 6, display: 'flex', flexDirection: 'column', gap: 5 }}>
              <div style={hint}>
                实测下来 star 和「能不能装上」几乎无关 —— 所以这个列表不按 star 排,
                按「能装上 + 跟投研相关」排。
              </div>
              {data.rejected!.map((r) => (
                <div key={r.repo} style={rejectRow}>
                  <span style={{ ...urlText, flex: 1 }}>{r.repo}</span>
                  {r.stars > 0 && <span style={tagStar}>{fmt(r.stars)}</span>}
                  <span style={{ flex: 2, fontSize: 10.5, color: HUNTER.INK_F }}>{r.reason}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

const fmt = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n))

const card: React.CSSProperties = {
  padding: '10px 12px', border: `1px solid ${HUNTER.LINE}`, borderRadius: 9,
  background: HUNTER.PAPER,
}
const whyBox: React.CSSProperties = {
  marginTop: 6, fontSize: 10.5, lineHeight: 1.65, color: HUNTER.INK_F,
}
const rejectRow: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 6,
  padding: '5px 8px', borderRadius: 6, background: HUNTER.PAPER,
  border: `1px solid ${HUNTER.LINE}`,
}
const checkRow: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 6, fontSize: 12,
  color: HUNTER.INK, cursor: 'pointer',
}
const fieldLabel: React.CSSProperties = {
  fontSize: 11, color: HUNTER.INK_S, fontWeight: 600,
}
const miniBtn: React.CSSProperties = {
  padding: '4px 10px', fontSize: 10.5, borderRadius: 6, whiteSpace: 'nowrap',
  background: 'transparent', color: HUNTER.THEME,
  border: `1px solid ${HUNTER.LINE}`, cursor: 'pointer', fontFamily: 'inherit',
}
const btnPrimary: React.CSSProperties = {
  padding: '7px 0', background: HUNTER.THEME, color: '#fff', border: 'none',
  borderRadius: 7, fontSize: 12, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit',
}
const linkBtn: React.CSSProperties = {
  padding: 0, background: 'transparent', border: 'none', textAlign: 'left',
  color: HUNTER.THEME, fontSize: 11, cursor: 'pointer', fontFamily: 'inherit',
}
const urlText: React.CSSProperties = {
  fontSize: 10, lineHeight: 1.5, color: HUNTER.INK_F,
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
  wordBreak: 'break-all',
}
const tagStar: React.CSSProperties = {
  padding: '0 5px', fontSize: 10, borderRadius: 4,
  background: HUNTER.LINE, color: HUNTER.INK_S,
}
const tagOk: React.CSSProperties = {
  padding: '0 5px', fontSize: 10, borderRadius: 4,
  background: '#EAF4EE', color: '#2F6A4F',
}
const tagWarn: React.CSSProperties = {
  padding: '0 5px', fontSize: 10, borderRadius: 4,
  background: HUNTER.TAG_WARN_BG, color: HUNTER.TAG_WARN_FG,
}
const tagDone: React.CSSProperties = {
  padding: '0 5px', fontSize: 10, borderRadius: 4,
  background: HUNTER.LINE, color: HUNTER.INK_F,
}
const hint: React.CSSProperties = { fontSize: 10.5, color: HUNTER.INK_F, lineHeight: 1.65 }
const warnBox: React.CSSProperties = {
  marginTop: 7, padding: '7px 9px', borderRadius: 6, fontSize: 10.5, lineHeight: 1.6,
  background: HUNTER.TAG_WARN_BG, color: HUNTER.TAG_WARN_FG,
}
const errBox: React.CSSProperties = {
  padding: '7px 9px', borderRadius: 6, fontSize: 11.5,
  background: '#FBEEEA', color: '#9B3A22', lineHeight: 1.6,
}
