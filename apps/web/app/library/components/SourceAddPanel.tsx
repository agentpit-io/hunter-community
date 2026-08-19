'use client'

// 接自己的数据源 —— `_21` §4 步 2,`_24` §3.2 改两步式
//
// **这个表单要解决的核心问题是"加数据源为什么比加工具难"。**
// 难的是字段映射:第三方 API 返回结构千奇百怪,我们不知道价格在哪个字段。
// 解法是「选来源 = 选模板」:选了已知来源映射我们内置,用户只填地址和 key。
//
// ## `_24` 改了什么:一个来源不是一个接口
//
// 老板点名的问题:「一个源拉 K线、新闻,接口不一样,你看看怎么处理」。
//
// 原来一次只能填一条。用户选「东方财富」填完行情,想再加新闻得**从头再来**:
// 再点添加、再选东财、再填一次 key。四个接口四遍。
//
// 现在:
//     第 1 步 选来源  → 第 2 步 勾接口(地址已预填)→ 一次提交写 N 行
//     需要 key 的**只填一次**,后端复制进选中的每一行
//
// ## 为什么勾选项默认展开地址、但收起编辑框
//
// 地址要看得见 —— 用户得知道我们到底替他填了什么(尤其是 SEC 那个占位邮箱,
// 不看见就不会去改)。但编辑框默认收起,因为**多数人不需要改**,
// 摊开四个长输入框会让这个面板看起来像个高级配置页。

import { useEffect, useMemo, useState } from 'react'
import { Check, AlertTriangle, FlaskConical, ChevronDown, ChevronRight } from 'lucide-react'
import { HUNTER } from '../../lib/hunter-theme'

interface Endpoint {
  market: string
  kind: string
  kind_label: string
  url: string
  label: string
  method: string
  note: string
  headers: Record<string, string>
  verified: boolean
  verified_at: string
  default_on: boolean
}
interface Template {
  upstream: string
  label: string
  requires_key: boolean
  key_in: string
  key_name: string
  key_prefix: string
  note: string
  apply_url: string
  builtin_map: boolean
  commercial: boolean
  free: boolean
  kinds: string[]
  verified_count: number
  endpoints: Endpoint[]
}

interface TestResult {
  ok: boolean
  status: number
  duration_ms: number
  body: string
  truncated?: boolean
  body_len?: number
  hint: string
}

interface BulkResult {
  summary: string
  created: { name: string }[]
  skipped: { name: string; reason: string }[]
  failed: { name: string; reason: string }[]
}

interface Props {
  /** 从某组的 ＋ 点进来时预选的来源 · 'user' 表示没有具体来源 */
  presetGroup?: string
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

const MARKET_LABEL: Record<string, string> = {
  a: 'A股', hk: '港股', us: '美股', global: '全球',
}

export default function SourceAddPanel({ presetGroup, onClose, onDone }: Props) {
  const [tpls, setTpls] = useState<Template[] | null>(null)
  const [upstream, setUpstream] = useState('')
  /** 勾了哪些接口 · 键是 `${market}:${kind}` */
  const [picked, setPicked] = useState<Record<string, boolean>>({})
  /** 用户改过的地址 · 没改的走模板原值 */
  const [urls, setUrls] = useState<Record<string, string>>({})
  /** 展开了哪条的编辑框 */
  const [openRow, setOpenRow] = useState('')
  const [needKey, setNeedKey] = useState(false)
  const [apiKey, setApiKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [testing, setTesting] = useState('')
  const [err, setErr] = useState('')
  const [tests, setTests] = useState<Record<string, TestResult>>({})

  useEffect(() => {
    api('GET', '/user_sources/templates')
      .then((d) => {
        setTpls(d.templates)
        const pick = presetGroup && presetGroup !== 'user'
          ? d.templates.find((t: Template) => t.upstream === presetGroup)
          : null
        if (pick) applyTemplate(pick)
      })
      .catch((e) => setErr(`拿不到来源清单:${e.message}`))
    // presetGroup 只在打开面板时读一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const tpl = useMemo(
    () => tpls?.find((t) => t.upstream === upstream) || null,
    [tpls, upstream],
  )

  function applyTemplate(t: Template) {
    setUpstream(t.upstream)
    setNeedKey(t.requires_key)
    // 默认勾 `default_on` 的那些。冷门接口(SEC 的全量代码表)和
    // 已知不稳的(东财 K线)默认不勾 —— 一次写进去一堆用不上的,
    // 用户回头还得一条条删
    const on: Record<string, boolean> = {}
    t.endpoints.forEach((e) => { on[key(e)] = e.default_on })
    setPicked(on)
    setUrls({})
    setOpenRow('')
    setTests({})
    setApiKey('')
  }

  function onPickUpstream(v: string) {
    const t = tpls?.find((x) => x.upstream === v)
    if (t) applyTemplate(t)
    else { setUpstream(v); setPicked({}) }
  }

  const chosen = useMemo(
    () => (tpl?.endpoints || []).filter((e) => picked[key(e)]),
    [tpl, picked],
  )
  const urlOf = (e: Endpoint) => urls[key(e)] ?? e.url
  const ready = !!(upstream && chosen.length > 0 && (!needKey || apiKey.trim()))

  async function runTest(e: Endpoint) {
    const k = key(e)
    setTesting(k); setErr('')
    try {
      const r = await api('POST', '/user_sources/test', {
        endpoint: urlOf(e).trim(),
        requires_key: needKey,
        key_in: tpl?.key_in || 'header',
        key_name: tpl?.key_name || 'Authorization',
        key_prefix: tpl?.key_prefix || '',
        api_key: apiKey.trim() || null,
      })
      setTests((s) => ({ ...s, [k]: r }))
    } catch (ex: any) {
      setErr(ex.message)
    } finally {
      setTesting('')
    }
  }

  async function save() {
    setBusy(true); setErr('')
    try {
      const r: BulkResult = await api('POST', '/user_sources/bulk', {
        upstream,
        requires_key: needKey,
        api_key: apiKey.trim() || null,
        key_in: tpl?.key_in || 'header',
        key_name: tpl?.key_name || 'Authorization',
        key_prefix: tpl?.key_prefix || '',
        endpoints: chosen.map((e) => ({
          market: e.market, kind: e.kind, label: e.label,
          endpoint: urlOf(e).trim(), headers: e.headers || {},
        })),
      })
      // **三段都要说。**只报成功的那批,用户会以为勾的都生效了,
      // 直到某天发现新闻源根本没接上
      const parts = [r.summary]
      if (r.skipped?.length) {
        parts.push('跳过的:' + r.skipped.map((x) => `${x.name}(${x.reason})`).join('、'))
      }
      if (r.failed?.length) {
        parts.push('失败的:' + r.failed.map((x) => `${x.name}(${x.reason})`).join('、'))
      }
      onDone(parts.join(' · '))
    } catch (e: any) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }

  if (!tpls) return <div style={hint}>加载来源清单…</div>

  // 分三组:免 key / 需 key / 商业授权。
  // 商业授权的**不藏起来** —— 明说"这是商业授权"比让用户白试一场诚实
  const free = tpls.filter((t) => t.free && t.upstream !== 'custom')
  const paid = tpls.filter((t) => !t.free && !t.commercial && t.upstream !== 'custom')
  const comm = tpls.filter((t) => t.commercial)
  const custom = tpls.filter((t) => t.upstream === 'custom')

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {/* ═══ 第 1 步 · 选来源 ═══ */}
      <Field label="第 1 步 · 选来源"
             hint="🟢 免 key 的可以直接添加;🔑 的需要你自己去申请一把 key">
        <select value={upstream} onChange={(e) => onPickUpstream(e.target.value)} style={input}>
          <option value="">— 请选择 —</option>
          <optgroup label="🟢 无需 key">
            {free.map((t) => (
              <option key={t.upstream} value={t.upstream}>
                {t.label}({t.endpoints.length} 个接口)
              </option>
            ))}
          </optgroup>
          <optgroup label="🔑 需要 key(你自己去申请)">
            {paid.map((t) => (
              <option key={t.upstream} value={t.upstream}>
                {t.label}({t.endpoints.length} 个接口)
              </option>
            ))}
          </optgroup>
          <optgroup label="🏢 商业授权(个人多半申请不到)">
            {comm.map((t) => <option key={t.upstream} value={t.upstream}>{t.label}</option>)}
          </optgroup>
          <optgroup label="⚙️ 其他">
            {custom.map((t) => <option key={t.upstream} value={t.upstream}>{t.label}</option>)}
          </optgroup>
        </select>
      </Field>

      {tpl && (
        <>
          {tpl.note && <div style={noteBox}><Md text={tpl.note} /></div>}

          {!tpl.builtin_map && (
            <div style={warnBox}>
              <AlertTriangle size={11} strokeWidth={1.9} style={{ marginRight: 4, verticalAlign: -1 }} />
              这个来源我们**没有内置字段映射** —— 保存后模型能拿到原始返回,
              但可能读不懂它。建议先选一个免 key 的已知来源。
            </div>
          )}

          {/* ═══ key —— 整组只填一次 ═══ */}
          {tpl.requires_key && (
            <Field label="API key"
                   hint={`放在 ${tpl.key_in === 'header' ? 'Header' : tpl.key_in === 'query' ? 'Query 参数' : 'Body'} 的 ${tpl.key_name} · 加密存储,页面上只回显末 4 位`}>
              <input type="password" value={apiKey} autoComplete="new-password"
                     onChange={(e) => { setApiKey(e.target.value); setTests({}) }}
                     style={input}
                     placeholder="粘贴你的 key —— 下面勾几个接口都只填这一次" />
              {tpl.apply_url && (
                <a href={tpl.apply_url} target="_blank" rel="noreferrer" style={linkBtn}>
                  还没有?去申请 →
                </a>
              )}
            </Field>
          )}

          {/* ═══ 第 2 步 · 勾接口 ═══ */}
          <Field label={`第 2 步 · 要哪些接口(${chosen.length}/${tpl.endpoints.length})`}
                 hint="地址我们已经填好了,点开可以改。每条都能单独测一次">
            <div style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
              <button style={miniBtn} onClick={() => {
                const on: Record<string, boolean> = {}
                tpl.endpoints.forEach((e) => { on[key(e)] = true })
                setPicked(on)
              }}>全选</button>
              <button style={miniBtn} onClick={() => setPicked({})}>全不选</button>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
              {tpl.endpoints.map((e) => {
                const k = key(e)
                const open = openRow === k
                const t = tests[k]
                return (
                  <div key={k} style={{
                    ...epBox,
                    borderColor: picked[k] ? HUNTER.THEME : HUNTER.LINE,
                    background: picked[k] ? HUNTER.PAPER : 'transparent',
                  }}>
                    <label style={{ ...checkRow, alignItems: 'flex-start' }}>
                      <input type="checkbox" checked={!!picked[k]} style={{ marginTop: 2 }}
                             onChange={(ev) => setPicked((s) => ({ ...s, [k]: ev.target.checked }))} />
                      <span style={{ flex: 1, minWidth: 0 }}>
                        <span style={{ fontWeight: 600 }}>{e.label}</span>
                        <span style={tagMarket}>{MARKET_LABEL[e.market] || e.market}</span>
                        {e.verified
                          ? <span style={tagOk}>已实测 {e.verified_at}</span>
                          : <span style={tagWarn}>未实测</span>}
                        <span style={{ ...urlText, display: 'block' }}>{urlOf(e)}</span>
                      </span>
                      <button style={expandBtn} onClick={(ev) => {
                        ev.preventDefault(); setOpenRow(open ? '' : k)
                      }}>
                        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                      </button>
                    </label>

                    {/* 坑写在勾选项底下 —— §3.3 那批「HTTP 200 但静默返回空」
                        的原因全靠这句话说清楚,藏进折叠层就等于没写 */}
                    {e.note && <div style={epNote}><Md text={e.note} /></div>}

                    {open && (
                      <div style={{ marginTop: 6 }}>
                        <input value={urlOf(e)} spellCheck={false} style={{ ...input, fontSize: 11 }}
                               onChange={(ev) => {
                                 setUrls((s) => ({ ...s, [k]: ev.target.value }))
                                 setTests((s) => ({ ...s, [k]: undefined as any }))
                               }} />
                        <div style={{ display: 'flex', gap: 6, marginTop: 5 }}>
                          {urls[k] !== undefined && urls[k] !== e.url && (
                            <button style={miniBtn}
                                    onClick={() => setUrls((s) => { const n = { ...s }; delete n[k]; return n })}>
                              ↺ 恢复推荐地址
                            </button>
                          )}
                          <button style={miniBtn} disabled={!!testing}
                                  onClick={() => runTest(e)}>
                            <FlaskConical size={10} strokeWidth={2} style={{ verticalAlign: -1, marginRight: 3 }} />
                            {testing === k ? '请求中…' : '测这一条'}
                          </button>
                        </div>
                        {t && <TestReport t={t} />}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </Field>

          {err && <div style={errBox}>{err}</div>}

          <div style={{ display: 'flex', gap: 6 }}>
            <button onClick={onClose} style={{ ...btnGhost, flex: 1 }}>取消</button>
            <button onClick={save} disabled={!ready || busy}
                    style={{ ...btnPrimary, flex: 2, opacity: !ready || busy ? 0.5 : 1 }}>
              <Check size={12} strokeWidth={2.4} style={{ verticalAlign: -2, marginRight: 4 }} />
              {busy ? '添加中…' : `添加这 ${chosen.length} 个接口`}
            </button>
          </div>

          {needKey && !apiKey.trim() && (
            <div style={hint}>
              勾了需要 key 的来源要先填 key —— 缺 key 的表现是上游返回 401,
              而那时你已经离开这个页面了。
            </div>
          )}
        </>
      )}
    </div>
  )
}

const key = (e: { market: string; kind: string }) => `${e.market}:${e.kind}`

/** 极简 `**粗体**` 渲染 —— 模板 note 里用它标重点(哪个 host 会 502 之类)。
 *  不引 markdown 库:这里只需要一种标记,引一个库进来是给 bundle 加负担。 */
function Md({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g)
  return (
    <>
      {parts.map((p, i) =>
        p.startsWith('**') && p.endsWith('**')
          ? <b key={i}>{p.slice(2, -2)}</b>
          : <span key={i}>{p}</span>,
      )}
    </>
  )
}

/** 测试结果 —— **原始返回照实摆出来**。
 *
 * 只给一个"成功/失败"是不够的:用户此刻要判断的是地址对不对、key 生没生效,
 * 而这两件事都写在响应体里。 */
function TestReport({ t }: { t: TestResult }) {
  return (
    <div style={{ ...(t.ok ? okBox : warnBox), marginTop: 6 }}>
      <div style={{ fontWeight: 600, marginBottom: 4 }}>
        {t.ok ? '✅ 通了' : '⚠️ 没通'} · HTTP {t.status || '—'} · {t.duration_ms}ms
      </div>
      {t.hint && <div style={{ marginBottom: 6 }}>{t.hint}</div>}
      {t.body && (
        <>
          <div style={{ fontSize: 10.5, color: HUNTER.INK_F, marginBottom: 3 }}>
            上游原始返回
            {t.truncated && `(共 ${t.body_len} 字符,只显示前 4000)`}
          </div>
          <pre style={pre}>{t.body}</pre>
        </>
      )}
    </div>
  )
}

function Field({ label, hint: h, flex, children }: {
  label: string; hint?: string; flex?: number; children: React.ReactNode
}) {
  return (
    <div style={{ flex: flex ?? undefined, minWidth: 0 }}>
      <div style={fieldLabel}>{label}</div>
      {children}
      {h && <div style={{ ...hint, marginTop: 3 }}>{h}</div>}
    </div>
  )
}

const input: React.CSSProperties = {
  width: '100%', padding: '7px 9px', border: `1px solid ${HUNTER.LINE}`, borderRadius: 7,
  fontSize: 12, color: HUNTER.INK, background: HUNTER.PAPER, fontFamily: 'inherit', outline: 'none',
}
const fieldLabel: React.CSSProperties = {
  fontSize: 11, color: HUNTER.INK_S, marginBottom: 4, fontWeight: 600,
}
const checkRow: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 6, fontSize: 12,
  color: HUNTER.INK, cursor: 'pointer',
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
const miniBtn: React.CSSProperties = {
  padding: '3px 10px', fontSize: 10.5, borderRadius: 6,
  background: 'transparent', color: HUNTER.THEME,
  border: `1px solid ${HUNTER.LINE}`, cursor: 'pointer', fontFamily: 'inherit',
}
const linkBtn: React.CSSProperties = {
  display: 'inline-block', marginTop: 5, fontSize: 10.5,
  color: HUNTER.THEME, textDecoration: 'none',
}
const expandBtn: React.CSSProperties = {
  padding: 2, background: 'transparent', border: 'none',
  color: HUNTER.INK_F, cursor: 'pointer', lineHeight: 1,
}
const epBox: React.CSSProperties = {
  padding: '7px 9px', border: `1px solid ${HUNTER.LINE}`, borderRadius: 7,
}
const epNote: React.CSSProperties = {
  marginTop: 4, marginLeft: 20, fontSize: 10.5, lineHeight: 1.6,
  color: HUNTER.INK_F,
}
const urlText: React.CSSProperties = {
  marginTop: 3, fontSize: 10, lineHeight: 1.5, color: HUNTER.INK_F,
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
  wordBreak: 'break-all',
}
const tagMarket: React.CSSProperties = {
  marginLeft: 5, padding: '0 5px', fontSize: 10, borderRadius: 4,
  background: HUNTER.LINE, color: HUNTER.INK_S,
}
const tagOk: React.CSSProperties = {
  marginLeft: 4, padding: '0 5px', fontSize: 10, borderRadius: 4,
  background: '#EAF4EE', color: '#2F6A4F',
}
const tagWarn: React.CSSProperties = {
  marginLeft: 4, padding: '0 5px', fontSize: 10, borderRadius: 4,
  background: HUNTER.TAG_WARN_BG, color: HUNTER.TAG_WARN_FG,
}
const hint: React.CSSProperties = { fontSize: 10.5, color: HUNTER.INK_F, lineHeight: 1.6 }
const noteBox: React.CSSProperties = {
  padding: '7px 9px', borderRadius: 6, fontSize: 11.5, lineHeight: 1.65,
  background: HUNTER.PAPER, color: HUNTER.INK_S, border: `1px solid ${HUNTER.LINE}`,
}
const warnBox: React.CSSProperties = {
  padding: '7px 9px', borderRadius: 6, fontSize: 11.5, lineHeight: 1.65,
  background: HUNTER.TAG_WARN_BG, color: HUNTER.TAG_WARN_FG,
}
const okBox: React.CSSProperties = {
  padding: '7px 9px', borderRadius: 6, fontSize: 11.5, lineHeight: 1.65,
  background: '#EAF4EE', color: '#2F6A4F',
}
const errBox: React.CSSProperties = {
  padding: '7px 9px', borderRadius: 6, fontSize: 11.5,
  background: '#FBEEEA', color: '#9B3A22', lineHeight: 1.6,
}
const pre: React.CSSProperties = {
  margin: 0, padding: '6px 8px', borderRadius: 5, maxHeight: 220, overflow: 'auto',
  background: HUNTER.PAPER, border: `1px solid ${HUNTER.LINE}`,
  fontSize: 10.5, lineHeight: 1.5, color: HUNTER.INK_S,
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
  whiteSpace: 'pre-wrap', wordBreak: 'break-all',
}
