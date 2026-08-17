'use client'

// 接自己的数据源 —— `_21` §4 步 2
//
// **这个表单要解决的核心问题是"加数据源为什么比加工具难"。**
// 难的是字段映射:第三方 API 返回结构千奇百怪,我们不知道价格在哪个字段。
//
// 解法是「选来源 = 选模板」:选了 Tushare / AKShare / 东财这些已知来源,
// 映射我们内置,用户只要填地址和 key。只有选「自定义接口」才需要自己填映射
// (那部分是步 6)。这一步把绝大多数用户的路径从"难"拉平到"和加工具一样"。
//
// 与 ToolAddPanel 的区别:那边**强制先测通再保存**,这边测试**可跳过** ——
// 用户明确说过「不需要就不填,调用的时候 AI 会判断」。尊重这个选择,
// 但把测试放在最显眼的位置,并且失败时把上游原始返回摆出来。

import { useEffect, useMemo, useState } from 'react'
import { Check, AlertTriangle, FlaskConical } from 'lucide-react'
import { HUNTER } from '../../lib/hunter-theme'

interface KindOption { value: string; label: string }
interface Template {
  upstream: string
  label: string
  endpoint_hint: string
  requires_key: boolean
  key_in: string
  key_name: string
  key_prefix: string
  note: string
  builtin_map: boolean
  kind_options: KindOption[]
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

const MARKETS = [
  { value: 'a', label: 'A股' }, { value: 'hk', label: '港股' },
  { value: 'us', label: '美股' }, { value: 'global', label: '全球/跨市场' },
]

export default function SourceAddPanel({ presetGroup, onClose, onDone }: Props) {
  const [tpls, setTpls] = useState<Template[] | null>(null)
  const [upstream, setUpstream] = useState('')
  const [name, setName] = useState('')
  const [market, setMarket] = useState('a')
  const [kind, setKind] = useState('')
  const [endpoint, setEndpoint] = useState('')
  const [needKey, setNeedKey] = useState(true)
  const [apiKey, setApiKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [testing, setTesting] = useState(false)
  const [err, setErr] = useState('')
  const [test, setTest] = useState<TestResult | null>(null)

  useEffect(() => {
    api('GET', '/user_sources/templates')
      .then((d) => {
        setTpls(d.templates)
        // 从某组的 ＋ 进来 → 预选那个来源。'user' 是从空组进来的,
        // 没有具体来源意图,让用户自己选
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
    // **直接把推荐地址填进输入框**,不是只当 placeholder。
    //
    // 原来只做 placeholder,结果灰字长得和真填了一模一样,用户看着
    // 一个"已经有地址"的表单,却发现保存按钮是灰的,不知道该填什么 ——
    // 实测第一个用户就卡在这里问「填什么」。
    //
    // 仍然可改(用户可能自建了代理,我们自己用 AKShare 就是这样),
    // 只是默认值从"空"变成"能直接用的那个"。
    // custom 除外:它的 hint 是 `https://你的接口/path/{symbol}` 这种示意,
    // 填进去反而像个真地址,用户可能直接保存了。
    if (t.upstream !== 'custom' && t.endpoint_hint.startsWith('http')) {
      setEndpoint(t.endpoint_hint)
    }
    setNeedKey(t.requires_key)
    setKind((k) => (t.kind_options.some((o) => o.value === k) ? k : t.kind_options[0]?.value || ''))
    setName((n) => n || `我的 ${t.label}`)
    setTest(null)
  }

  function onPickUpstream(v: string) {
    const t = tpls?.find((x) => x.upstream === v)
    if (t) applyTemplate(t)
    else setUpstream(v)
  }

  const ready = !!(upstream && name.trim() && kind && endpoint.trim()
    && (!needKey || apiKey.trim()))

  async function runTest() {
    setTesting(true); setErr(''); setTest(null)
    try {
      setTest(await api('POST', '/user_sources/test', {
        endpoint: endpoint.trim(),
        requires_key: needKey,
        key_in: tpl?.key_in || 'header',
        key_name: tpl?.key_name || 'Authorization',
        key_prefix: tpl?.key_prefix || '',
        api_key: apiKey.trim() || null,
      }))
    } catch (e: any) {
      setErr(e.message)
    } finally {
      setTesting(false)
    }
  }

  async function save() {
    setBusy(true); setErr('')
    try {
      await api('POST', '/user_sources', {
        name: name.trim(), upstream, market, kind,
        endpoint: endpoint.trim(),
        requires_key: needKey,
        key_in: tpl?.key_in || 'header',
        key_name: tpl?.key_name || 'Authorization',
        key_prefix: tpl?.key_prefix || '',
        api_key: apiKey.trim() || null,
      })
      onDone(
        `已接入「${name.trim()}」。取数时会**优先走它**,` +
        `拿不到才回落到我们的源 —— 回落时会明确告诉你。`,
      )
    } catch (e: any) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }

  if (!tpls) return <div style={hint}>加载来源清单…</div>

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {/* ① 来源 —— 第一步就选它,因为它决定了后面所有字段的形态 */}
      <Field label="来源" hint="选了已知来源,字段映射我们内置,你只要填地址和 key">
        <select value={upstream} onChange={(e) => onPickUpstream(e.target.value)} style={input}>
          <option value="">— 请选择 —</option>
          {tpls.map((t) => (
            <option key={t.upstream} value={t.upstream}>
              {t.label}{t.builtin_map ? '' : '(需自己填字段映射)'}
            </option>
          ))}
        </select>
      </Field>

      {tpl && (
        <>
          {tpl.note && <div style={noteBox}>{tpl.note}</div>}

          {!tpl.builtin_map && (
            <div style={warnBox}>
              <AlertTriangle size={11} strokeWidth={1.9} style={{ marginRight: 4, verticalAlign: -1 }} />
              自定义接口需要一层字段映射把返回值对齐到我们的格式,<b>那部分还在做</b>。
              现在保存的话,模型能拿到原始返回但可能读不懂它。
              建议先选一个上面的已知来源,或把你的服务包成 MCP 工具接进来。
            </div>
          )}

          <Row>
            <Field label="名称" flex={2}>
              <input value={name} onChange={(e) => setName(e.target.value)} style={input}
                     placeholder={`我的 ${tpl.label}`} />
            </Field>
            <Field label="市场" flex={1}>
              <select value={market} onChange={(e) => setMarket(e.target.value)} style={input}>
                {MARKETS.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
              </select>
            </Field>
          </Row>

          <Field label="数据类型"
                 hint={`只列了 ${tpl.label} 实际提供的类型 —— 省下一次"填完才发现它没这个数据"的试错`}>
            <select value={kind} onChange={(e) => setKind(e.target.value)} style={input}>
              {tpl.kind_options.map((k) => (
                <option key={k.value} value={k.value}>{k.label}</option>
              ))}
            </select>
          </Field>

          <Field label="接口地址"
                 hint="占位符:{symbol}=600519 · {secid}=1.600519(东财) · {ts_code}=600519.SH(Tushare) · {yahoo}=600519.SS。前后空白会自动清掉">
            <input value={endpoint} onChange={(e) => { setEndpoint(e.target.value); setTest(null) }}
                   style={input} placeholder={tpl.endpoint_hint} spellCheck={false} />
            {/* 改过或清空之后,给一条路回到推荐地址。
                手抄 `fields=f43,f44,...` 这种串,抄错一个字符的表现是
                "连得通但读不懂返回",而用户根本不会怀疑是自己抄错了 */}
            {tpl.endpoint_hint.startsWith('http') && endpoint !== tpl.endpoint_hint && (
              <button onClick={() => { setEndpoint(tpl.endpoint_hint); setTest(null) }}
                      style={fillBtn}>↺ 恢复推荐地址</button>
            )}
          </Field>

          {/* ② 要不要 key 由用户自己勾 —— 用户明确要求的。
              但勾了不填我们会在保存时拦下:AI 判断不了缺 key,
              真正知道的是上游的 401,而那时用户已经离开这个页面了 */}
          <label style={checkRow}>
            <input type="checkbox" checked={needKey}
                   onChange={(e) => { setNeedKey(e.target.checked); setTest(null) }} />
            <span>这个接口需要 key</span>
            {needKey && (
              <span style={{ color: HUNTER.INK_F, fontSize: 10.5 }}>
                → 放在 {tpl.key_in === 'header' ? 'Header' : tpl.key_in === 'query' ? 'Query 参数' : 'Body'}
                {' '}的 <code>{tpl.key_name}</code>
              </span>
            )}
          </label>

          {needKey && (
            <input type="password" value={apiKey} autoComplete="new-password"
                   onChange={(e) => { setApiKey(e.target.value); setTest(null) }}
                   style={input} placeholder="粘贴你的 key · 加密存储,页面上只回显末 4 位" />
          )}

          {/* ③ 测试 —— 可跳过,但结果要摆得足够显眼 */}
          <div style={{ display: 'flex', gap: 6 }}>
            <button onClick={runTest} disabled={!endpoint.trim() || testing}
                    style={{ ...btnGhost, flex: 1, opacity: !endpoint.trim() || testing ? 0.5 : 1 }}>
              <FlaskConical size={12} strokeWidth={2} style={{ verticalAlign: -2, marginRight: 4 }} />
              {testing ? '请求中…' : '测一次(可跳过)'}
            </button>
          </div>

          {test && <TestReport t={test} />}
          {err && <div style={errBox}>{err}</div>}

          <div style={{ display: 'flex', gap: 6 }}>
            <button onClick={onClose} style={{ ...btnGhost, flex: 1 }}>取消</button>
            <button onClick={save} disabled={!ready || busy}
                    style={{ ...btnPrimary, flex: 2, opacity: !ready || busy ? 0.5 : 1 }}>
              <Check size={12} strokeWidth={2.4} style={{ verticalAlign: -2, marginRight: 4 }} />
              {busy ? '保存中…' : '保存并启用'}
            </button>
          </div>
        </>
      )}
    </div>
  )
}

/** 测试结果 —— **原始返回照实摆出来**。
 *
 * 只给一个"成功/失败"是不够的:用户此刻要判断的是地址对不对、key 生没生效,
 * 而这两件事都写在响应体里。这也是步 6 填字段映射时唯一的依据。 */
function TestReport({ t }: { t: TestResult }) {
  return (
    <div style={t.ok ? okBox : warnBox}>
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

function Row({ children }: { children: React.ReactNode }) {
  return <div style={{ display: 'flex', gap: 8 }}>{children}</div>
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
const hint: React.CSSProperties = { fontSize: 10.5, color: HUNTER.INK_F, lineHeight: 1.6 }
const fillBtn: React.CSSProperties = {
  marginTop: 4, padding: '3px 10px', fontSize: 10.5, borderRadius: 6,
  background: 'transparent', color: HUNTER.THEME,
  border: `1px solid ${HUNTER.LINE}`, cursor: 'pointer', fontFamily: 'inherit',
}
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
