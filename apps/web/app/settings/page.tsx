'use client'
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { Activity, User as UserIcon, Zap, Info, Loader2, ExternalLink, Save, Check, X, Cpu, Lock, Wand2 } from 'lucide-react'

const API = process.env.NEXT_PUBLIC_API_URL || ''

interface Me {
  id: string
  email: string
  role: string
  display_name?: string | null
  created_at?: string | null
  last_login?: string | null
}

interface SaasConfig {
  data_url?: string | null
  data_key_masked?: string | null
  llm_url?: string | null
  llm_key_masked?: string | null
  llm_model?: string | null
  kronos_url?: string | null
  kronos_key_masked?: string | null
}

type TabId = 'account' | 'llm' | 'saas' | 'about'

export default function SettingsPage() {
  const router = useRouter()
  const [tab, setTab] = useState<TabId>('account')
  const [me, setMe] = useState<Me | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (typeof window !== 'undefined') {
      const qTab = new URLSearchParams(window.location.search).get('tab')
      if (qTab === 'account' || qTab === 'llm' || qTab === 'saas' || qTab === 'about') {
        setTab(qTab)
      }
    }
    const token = typeof window !== 'undefined' ? localStorage.getItem('hunter_token') : ''
    if (!token) { router.replace('/login'); return }
    fetch(`${API}/api/auth/me`, { headers: { Authorization: `Bearer ${token}` } })
      .then(r => r.json())
      .then(m => { setMe(m); setLoading(false) })
      .catch(() => { router.replace('/login') })
  }, [router])

  if (loading) return <Centered><Loader2 style={{ animation: 'spin 1s linear infinite', color: 'var(--text-muted)' }} /></Centered>

  return (
    <div style={{ minHeight: '100vh', background: 'var(--bg)', fontFamily: '-apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif' }}>
      <TopBar me={me} />
      <div style={{ maxWidth: 900, margin: '0 auto', padding: '32px 24px', display: 'grid', gridTemplateColumns: '200px 1fr', gap: 24 }}>
        <nav style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <TabButton active={tab === 'account'} onClick={() => setTab('account')} icon={<UserIcon size={15} />} label="账户" />
          <TabButton active={tab === 'llm'} onClick={() => setTab('llm')} icon={<Cpu size={15} />} label="大模型" />
          <TabButton active={tab === 'saas'} onClick={() => setTab('saas')} icon={<Zap size={15} />} label="SaaS 加速" />
          <TabButton active={tab === 'about'} onClick={() => setTab('about')} icon={<Info size={15} />} label="关于" />
        </nav>
        <div>
          {tab === 'account' && <AccountTab me={me} />}
          {tab === 'llm' && <LlmTab />}
          {tab === 'saas' && <SaasTab />}
          {tab === 'about' && <AboutTab />}
        </div>
      </div>
      <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
    </div>
  )
}

// ── Tabs ─────────────────────────────────────────────────────────

function AccountTab({ me }: { me: Me | null }) {
  return (
    <Card title="账户信息">
      <Row label="邮箱" value={me?.email || '-'} />
      <Row label="显示名" value={me?.display_name || '-'} />
      <Row label="角色" value={me?.role || 'user'} />
      <Row label="用户 ID" value={<code style={{ fontSize: 12 }}>{me?.id || '-'}</code>} />
      <Row label="注册时间" value={me?.created_at ? new Date(me.created_at).toLocaleString('zh-CN') : '-'} />
      <Row label="最近登录" value={me?.last_login ? new Date(me.last_login).toLocaleString('zh-CN') : '-'} />
      <div style={{ marginTop: 24, padding: 12, background: 'var(--bg-panel)', border: '1px dashed var(--border)', borderRadius: 8, fontSize: 12, color: 'var(--text-muted)' }}>
        密码修改与登出所有会话功能待补 · 目前请通过 `/api/auth/refresh` 或重新登录。
      </div>
    </Card>
  )
}

function SaasTab() {
  const [cfg, setCfg] = useState<SaasConfig | null>(null)
  const [form, setForm] = useState({
    data_url: '', data_key: '',
    llm_url: '', llm_key: '', llm_model: '',
    kronos_url: '', kronos_key: '',
  })
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState<{ tone: 'ok' | 'err'; text: string } | null>(null)
  const [testing, setTesting] = useState<string | null>(null)

  const token = typeof window !== 'undefined' ? localStorage.getItem('hunter_token') : ''

  useEffect(() => {
    fetch(`${API}/api/users/me/saas-config`, { headers: { Authorization: `Bearer ${token}` } })
      .then(r => r.json())
      .then((c: SaasConfig) => {
        setCfg(c)
        setForm(f => ({
          ...f,
          data_url: c.data_url || '',
          llm_url: c.llm_url || '',
          llm_model: c.llm_model || '',
          kronos_url: c.kronos_url || '',
        }))
      })
      .catch(() => setMsg({ tone: 'err', text: '无法加载配置' }))
  }, [token])

  const save = async () => {
    setSaving(true); setMsg(null)
    const body: Record<string, string> = {}
    // Only send fields the user actually changed · empty strings clear
    for (const [k, v] of Object.entries(form)) {
      if (v !== '') body[k] = v
    }
    try {
      const r = await fetch(`${API}/api/users/me/saas-config`, {
        method: 'PATCH',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const c = await r.json()
      if (r.ok) {
        setCfg(c)
        setForm(f => ({ ...f, data_key: '', llm_key: '', kronos_key: '' }))
        setMsg({ tone: 'ok', text: '已保存' })
      } else {
        setMsg({ tone: 'err', text: c.detail || '保存失败' })
      }
    } catch {
      setMsg({ tone: 'err', text: '网络错误' })
    } finally {
      setSaving(false)
    }
  }

  const test = async (service: 'data' | 'llm' | 'kronos') => {
    setTesting(service); setMsg(null)
    try {
      const body: Record<string, string> = { service }
      const url = ({ data: form.data_url, llm: form.llm_url, kronos: form.kronos_url }[service] || '').trim()
      const key = ({ data: form.data_key, llm: form.llm_key, kronos: form.kronos_key }[service] || '')
      if (url) body.url = url
      if (key) body.key = key
      const r = await fetch(`${API}/api/users/me/saas-config/test`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const d = await r.json()
      setMsg(d.ok
        ? { tone: 'ok', text: `${service} 连接 OK · HTTP ${d.status} · ${d.latency_ms}ms` }
        : { tone: 'err', text: `${service} 连接失败 · ${d.error || `HTTP ${d.status}`}` }
      )
    } catch {
      setMsg({ tone: 'err', text: '测试失败' })
    } finally {
      setTesting(null)
    }
  }

  return (
    <Card title="SaaS 加速服务 · 可选">
      <p style={{ fontSize: 13, color: 'var(--text-muted)', margin: '0 0 20px', lineHeight: 1.6 }}>
        本地实例默认走开源 provider（akshare · 你自己的 LLM key）。
        接上 hunter.agentpit.io 的 SaaS 就能免自己维护数据源 / 免部署 Kronos GPU。
        <a href="https://hunter.agentpit.io/dev/api-keys" target="_blank" rel="noopener noreferrer"
           style={{ color: 'var(--blue)', marginLeft: 6 }}>申请免费 Key <ExternalLink size={11} style={{ display: 'inline', verticalAlign: -1 }} /></a>
      </p>

      <ProviderBlock
        title="📊 数据加速"
        stored={cfg?.data_key_masked || null}
        url={form.data_url}
        onUrl={v => setForm(f => ({ ...f, data_url: v }))}
        keyVal={form.data_key}
        onKey={v => setForm(f => ({ ...f, data_key: v }))}
        onTest={() => test('data')}
        testing={testing === 'data'}
      />

      <ProviderBlock
        title="🧠 LLM 加速"
        stored={cfg?.llm_key_masked || null}
        url={form.llm_url}
        onUrl={v => setForm(f => ({ ...f, llm_url: v }))}
        keyVal={form.llm_key}
        onKey={v => setForm(f => ({ ...f, llm_key: v }))}
        onTest={() => test('llm')}
        testing={testing === 'llm'}
        extra={
          <div style={{ marginTop: 8 }}>
            <input
              type="text"
              placeholder="model (可选，如 gemini-2.5-flash)"
              value={form.llm_model}
              onChange={e => setForm(f => ({ ...f, llm_model: e.target.value }))}
              style={inputStyle()}
            />
          </div>
        }
      />

      <ProviderBlock
        title="📈 Kronos 预测"
        stored={cfg?.kronos_key_masked || null}
        url={form.kronos_url}
        onUrl={v => setForm(f => ({ ...f, kronos_url: v }))}
        keyVal={form.kronos_key}
        onKey={v => setForm(f => ({ ...f, kronos_key: v }))}
        onTest={() => test('kronos')}
        testing={testing === 'kronos'}
      />

      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 20 }}>
        <button onClick={save} disabled={saving}
          style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '9px 18px', background: saving ? 'var(--bg-panel)' : 'var(--blue)', color: saving ? 'var(--text-muted)' : '#fff', border: 'none', borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: saving ? 'not-allowed' : 'pointer' }}>
          {saving ? <Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} /> : <Save size={14} />}
          {saving ? '保存中…' : '保存'}
        </button>
        {msg && (
          <span style={{ fontSize: 13, color: msg.tone === 'ok' ? 'var(--green,#10b981)' : 'var(--red)', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            {msg.tone === 'ok' ? <Check size={14} /> : <X size={14} />}
            {msg.text}
          </span>
        )}
      </div>
    </Card>
  )
}

// ── 大模型(M2 · 设计方案 4.1「设置页入口」)──────────────────────
//
// 两件事:看当前配置、重新跑一遍初始化向导(换模型用)。
//
// **环境变量锁定时只读展示并说明为什么改不了** —— 演示站这类 .env 里锁死配置的
// 部署,这里给一个点不动的按钮比给一个点了报 409 的按钮好。
function LlmTab() {
  const router = useRouter()
  const [st, setSt] = useState<any>(null)
  const [quota, setQuota] = useState<any>(null)
  const [diag, setDiag] = useState<any>(null)
  const [diagLoading, setDiagLoading] = useState(false)
  const [pinging, setPinging] = useState(false)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  const fetchDiag = async (ping = false) => {
    if (ping) setPinging(true)
    else setDiagLoading(true)
    let token = ''
    try { token = localStorage.getItem('hunter_token') || '' } catch { /* 隐私模式 */ }
    const auth: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {}
    try {
      const r = await fetch(`/api/setup/llm/logs${ping ? '?ping=1' : ''}`, { headers: auth, cache: 'no-store' })
      const d = await r.json().catch(() => null)
      if (r.ok && d) setDiag(d)
    } catch {
      /* 忽略单次日志拉取错误 */
    } finally {
      setPinging(false)
      setDiagLoading(false)
    }
  }

  const downloadDiag = async () => {
    let token = ''
    try { token = localStorage.getItem('hunter_token') || '' } catch { /* 隐私模式 */ }
    const auth: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {}
    try {
      const r = await fetch('/api/setup/llm/logs/download', { headers: auth, cache: 'no-store' })
      if (!r.ok) return
      const blob = await r.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      const cd = r.headers.get('Content-Disposition') || ''
      const m = cd.match(/filename="([^"]+)"/)
      a.href = url
      a.download = m ? m[1] : `hunter-llm-diagnostics-${Date.now()}.log`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch {
      /* 忽略下载异常 */
    }
  }

  useEffect(() => {
    let token = ''
    try { token = localStorage.getItem('hunter_token') || '' } catch { /* 隐私模式 */ }
    const auth: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {}
    fetch('/api/setup/status', { headers: auth, cache: 'no-store' })
      .then(async r => {
        const d = await r.json().catch(() => null)
        if (!r.ok) throw new Error(d?.detail?.message || d?.message || `HTTP ${r.status}`)
        setSt(d)
      })
      .catch(e => setErr(String(e?.message || e)))
    // 额度单独一条 —— 它要跨公网问网关,不该拖慢这张卡片的其余部分。
    // **失败不设 err**:一个额度数字取不到,不该把整张卡打成红色报错。
    fetch('/api/setup/llm/quota', { headers: auth, cache: 'no-store' })
      .then(async r => setQuota(await r.json().catch(() => null)))
      .catch(() => setQuota({ builtin: true, ok: false, message: '请求没发出去:连不上这台服务器' }))
    void fetchDiag(false)
  }, [])

  const rerun = async () => {
    setBusy(true); setErr('')
    let token = ''
    try { token = localStorage.getItem('hunter_token') || '' } catch { /* 隐私模式 */ }
    try {
      const r = await fetch('/api/setup/reopen', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: '{}',
      })
      if (!r.ok) {
        const d = await r.json().catch(() => null)
        throw new Error(d?.detail?.message || d?.message || `HTTP ${r.status}`)
      }
      router.push('/setup')
    } catch (e: any) {
      setErr(String(e?.message || e)); setBusy(false)
    }
  }

  if (err) return <Card title="大模型"><span style={{ fontSize: 13, color: 'var(--red)' }}>{err}</span></Card>
  if (!st) return <Card title="大模型"><Loader2 size={15} style={{ animation: 'spin 1s linear infinite' }} /></Card>

  const llm = st.llm || {}
  const locked = !!llm.env_locked
  const ENV_NAME: Record<string, string> = {
    base_url: 'LLM_BASE_URL', model: 'LLM_DEFAULT_MODEL', api_key: 'LLM_API_KEY',
  }
  const SRC_CN: Record<string, string> = {
    env: '环境变量(.env)', db: '向导写入(数据库)', none: '未配置',
  }
  const rawModel = llm.model || ''
  const isArgonModel =
    rawModel === 'hunter-chat' ||
    rawModel === 'hunter-deep' ||
    rawModel === 'gemini-4-argon' ||
    String(llm.display_name || '').includes('Gemini 4 Argon')
  const modelDisplay = isArgonModel
    ? `Hunter-Gemini 4 Argon（谷歌 4.0 · gemini-4-argon）`
    : (llm.display_name || rawModel || '-')

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <Card title="大模型">
        <p style={{ fontSize: 13, color: 'var(--text-muted)', margin: '0 0 16px', lineHeight: 1.6 }}>
          对话、分析、报告都用这台实例配的大模型。key 加密存在本机数据库里,这里只显示末 4 位。
        </p>
        <Row label="状态" value={llm.configured
          ? <span style={{ color: 'var(--green,#10b981)' }}>已配置</span>
          : <span style={{ color: 'var(--red)' }}>未配置</span>} />
        <Row label="模型" value={
          <div>
            <strong style={{ color: 'var(--text)' }}>{modelDisplay}</strong>
            {rawModel && rawModel !== modelDisplay && (
              <span style={{ marginLeft: 8, fontSize: 12, color: 'var(--text-muted)' }}>
                （配置别名: <code>{rawModel}</code>）
              </span>
            )}
          </div>
        } />
        <Row label="接口地址" value={llm.base_url || '-'} />
        <Row label="API key" value={llm.api_key_masked || '-'} />
        <Row label="schema 清洗" value={llm.sanitize || '-'} />
        {(llm.builtin || quota?.builtin) && <QuotaRows q={quota} />}
        {(['base_url', 'model', 'api_key'] as const).map(k => (
          <Row key={k} label={ENV_NAME[k]}
               value={<span style={{ fontSize: 13 }}>{SRC_CN[(llm.locked_items || {})[k]] || '未配置'}</span>} />
        ))}

        {locked ? (
          <div style={{ marginTop: 20, padding: 12, background: 'var(--bg-panel)', border: '1px dashed var(--border)', borderRadius: 8, fontSize: 13, color: 'var(--text-muted)', lineHeight: 1.7 }}>
            <strong style={{ color: 'var(--text)', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <Lock size={13} /> 这台实例的大模型配置已锁定
            </strong>
            <div style={{ marginTop: 6 }}>
              配置写在环境变量里,向导改不了它。要换模型请改部署目录的 <code>.env</code>,
              然后 <code>docker compose up -d</code>（<strong>不是 restart</strong> —— restart 不会重新读 .env）。
            </div>
          </div>
        ) : (
          <div style={{ marginTop: 20 }}>
            <button onClick={rerun} disabled={busy}
              style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '9px 18px', background: busy ? 'var(--bg-panel)' : 'var(--blue)', color: busy ? 'var(--text-muted)' : '#fff', border: 'none', borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: busy ? 'not-allowed' : 'pointer' }}>
              <Wand2 size={14} /> 重新运行初始化向导
            </button>
            <div style={{ marginTop: 8, fontSize: 12.5, color: 'var(--text-muted)' }}>
              换模型走这里:向导会当场检测连通 / 对话 / 工具调用三项,通过才让保存。
              已保存的配置不会被清掉,你可以在向导里看到当前值。
            </div>
          </div>
        )}
      </Card>

      <Card title="大模型调用日志与问题跟踪">
        <p style={{ fontSize: 13, color: 'var(--text-muted)', margin: '0 0 14px', lineHeight: 1.6 }}>
          实时跟踪大模型请求与响应中的实际模型名称（如 <code>gemini-4-argon</code>）、耗时与状态码。导出日志已自动脱敏 API Key 与对话正文，可直接上传给开发者排查问题。
        </p>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginBottom: 16 }}>
          <button
            type="button"
            onClick={() => fetchDiag(true)}
            disabled={pinging}
            style={{
              padding: '7px 14px',
              background: 'var(--blue)',
              color: '#fff',
              border: 'none',
              borderRadius: 7,
              fontSize: 12.5,
              fontWeight: 600,
              cursor: pinging ? 'not-allowed' : 'pointer',
            }}
          >
            {pinging ? '正在实测大模型…' : '⚡ 实测一次大模型并记录日志'}
          </button>
          <button
            type="button"
            onClick={() => fetchDiag(false)}
            disabled={diagLoading}
            style={{
              padding: '7px 14px',
              background: 'var(--bg-panel)',
              color: 'var(--text)',
              border: '1px solid var(--border)',
              borderRadius: 7,
              fontSize: 12.5,
              cursor: diagLoading ? 'not-allowed' : 'pointer',
            }}
          >
            {diagLoading ? '刷新中…' : '🔄 刷新日志'}
          </button>
          <button
            type="button"
            onClick={downloadDiag}
            style={{
              padding: '7px 14px',
              background: 'var(--bg-panel)',
              color: 'var(--text)',
              border: '1px solid var(--border)',
              borderRadius: 7,
              fontSize: 12.5,
              fontWeight: 600,
              cursor: 'pointer',
            }}
          >
            📥 下载诊断日志文件（用于上传反馈）
          </button>
        </div>

        {diag?.probe_ping && (
          <div style={{
            marginBottom: 14,
            padding: 12,
            borderRadius: 8,
            background: 'var(--bg-panel)',
            border: `1px solid ${diag.probe_ping.ok ? 'var(--green,#10b981)' : 'var(--red)'}`,
            fontSize: 12.5,
            lineHeight: 1.6,
          }}>
            <strong>实测结果：</strong>
            {diag.probe_ping.ok ? (
              <span>
                HTTP {diag.probe_ping.status} · 请求模型 <code>{diag.probe_ping.req_model}</code> → 实际响应模型{' '}
                <strong style={{ color: 'var(--green,#10b981)' }}>{diag.probe_ping.resp_model}</strong> · 耗时 {diag.probe_ping.latency_ms}ms · 回复「{diag.probe_ping.reply}」
              </span>
            ) : (
              <span style={{ color: 'var(--red)' }}>
                失败（HTTP {diag.probe_ping.status} · {diag.probe_ping.latency_ms}ms）：{diag.probe_ping.error}
              </span>
            )}
          </div>
        )}

        {diag && (
          <>
            <Row
              label="opencode 快照"
              value={
                <span style={{ fontSize: 12.5 }}>
                  {diag.opencode_provider?.reachable
                    ? `在线 · 当前生效模型 ${diag.opencode_provider.current_model || '-'}`
                    : `未连接（${diag.opencode_provider?.error || '无响应'}）`}
                </span>
              }
            />
            {Array.isArray(diag.gateway_models) && diag.gateway_models.length > 0 && (
              <Row
                label="网关模型映射"
                value={
                  <div style={{ fontSize: 12.5, lineHeight: 1.7 }}>
                    {diag.gateway_models.map((gm: any) => (
                      <div key={gm.id}>
                        <code>{gm.id}</code> → <strong>{gm.display_name || gm.id}</strong>
                        {gm.upstream_model ? <span style={{ color: 'var(--text-muted)' }}>（底层: <code>{gm.upstream_model}</code>）</span> : null}
                      </div>
                    ))}
                  </div>
                }
              />
            )}

            <div style={{ marginTop: 14 }}>
              <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8, color: 'var(--text)' }}>
                最近大模型调用记录（API 跟踪 {diag.api_traces?.length || 0} 条 · Shim 转发 {diag.shim_traces?.length || 0} 条）
              </div>
              <div style={{ maxHeight: 280, overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 8, padding: 8, background: 'var(--bg-panel)', fontSize: 12, fontFamily: 'monospace' }}>
                {(!diag.api_traces?.length && !diag.shim_traces?.length) ? (
                  <div style={{ color: 'var(--text-muted)', padding: 8 }}>
                    暂无调用记录。点击上方「⚡ 实测一次大模型并记录日志」或发起一次对话即可生成跟踪记录。
                  </div>
                ) : (
                  <>
                    {(diag.api_traces || []).map((t: any, idx: number) => (
                      <div key={`api-${idx}`} style={{ padding: '4px 0', borderBottom: '1px dashed var(--border)' }}>
                        [{t.ts}] [API:{t.caller}] req={t.req_model} → resp=<strong>{t.resp_model || '-'}</strong> status={t.status} {t.latency_ms}ms tokens={t.tokens_in}/{t.tokens_out} {t.error ? `err=${t.error}` : ''}
                      </div>
                    ))}
                    {(diag.shim_traces || []).map((t: any, idx: number) => (
                      <div key={`shim-${idx}`} style={{ padding: '4px 0', borderBottom: '1px dashed var(--border)' }}>
                        [{t.ts}] [SHIM:{t.method} {t.path}] req={t.req_model || '-'} → resp=<strong>{t.resp_model || '-'}</strong> status={t.status} {t.duration_ms}ms {t.error ? `err=${t.error}` : ''}
                      </div>
                    ))}
                  </>
                )}
              </div>
            </div>
          </>
        )}
      </Card>
    </div>
  )
}

// ── 内置额度 · 今日剩余(P2 · 方案 4.8「设置页显示今日剩余额度」)──────
//
// 数字一律来自 `GET /api/setup/llm/quota`(它转发网关的 `/api/saas/llm/quota`),
// **一个都不在前端算、不补默认值** —— 取不到就显示「—」并说明原因,
// 免得用户按一个编出来的剩余量去安排今天的用量(仓内铁律:空的比假的好)。
function QuotaRows({ q }: { q: any }) {
  if (!q) return <Row label="今日额度" value={<span style={{ color: 'var(--text-muted)' }}>读取中…</span>} />
  if (q.ok === false) {
    return (
      <Row label="今日额度" value={
        <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>
          — · 取不到:{q.message || '未知原因'}
        </span>
      } />
    )
  }
  const d = q.quota || {}
  const n = (x: any) => (Number.isFinite(Number(x)) ? Number(x).toLocaleString('en-US') : '—')
  const pct = (Number.isFinite(Number(d.limit_daily)) && Number(d.limit_daily) > 0
    && Number.isFinite(Number(d.remaining)))
    ? Math.max(0, Math.min(100, (Number(d.remaining) / Number(d.limit_daily)) * 100))
    : null
  // ⚠️ **按网关给的时区渲染,不按浏览器所在时区**。额度是「北京时间 0 点重置」,
  // 而服务器 / 海外用户的浏览器常常不在 +08 —— 直接 toLocaleString() 会显示成
  // 「2026/9/19 16:00」,旁边却标着 Asia/Shanghai,自相矛盾(2026-09-19 截图里看出来的)。
  let reset = '—'
  if (typeof d.reset_at === 'string' && d.reset_at) {
    const t = new Date(d.reset_at)
    if (Number.isNaN(t.getTime())) {
      reset = d.reset_at
    } else {
      try {
        reset = t.toLocaleString('zh-CN', { timeZone: d.reset_tz || 'Asia/Shanghai' })
      } catch {
        reset = t.toLocaleString('zh-CN')   // 时区名不认识就退回本地,不编一个时间出来
      }
    }
  }
  return (
    <>
      <Row label="今日额度" value={
        <div>
          <span style={{ color: d.exhausted ? 'var(--red)' : 'var(--text)' }}>
            剩余 <strong>{n(d.remaining)}</strong> / 上限 {n(d.limit_daily)} token
          </span>
          <span style={{ fontSize: 12.5, color: 'var(--text-muted)', marginLeft: 8 }}>
            已用 {n(d.used_today)}
          </span>
          {pct !== null && (
            <div style={{ marginTop: 6, height: 6, borderRadius: 3, background: 'var(--bg-panel)' }}>
              <div style={{
                width: `${pct}%`, height: '100%', borderRadius: 3,
                background: d.exhausted ? 'var(--red)' : 'var(--blue)',
              }} />
            </div>
          )}
          {d.exhausted && (
            <div style={{ marginTop: 6, fontSize: 12.5, color: 'var(--red)' }}>
              今天的内置额度已用完。到重置时间自动恢复;等不了就用「重新运行初始化向导」
              换成你自己的大模型 key(任何 OpenAI 兼容服务都行),之后随时能切回来。
            </div>
          )}
        </div>
      } />
      <Row label="重置时间" value={
        <span>{reset}{d.reset_tz ? <span style={{ fontSize: 12.5, color: 'var(--text-muted)' }}> · {d.reset_tz}</span> : null}</span>
      } />
      <Row label="计量口径" value={
        <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>
          {d.unit || 'token'}
          {Number.isFinite(Number(d.rate_per_min)) && <> · 每分钟 {d.rate_per_min} 次</>}
          {Number.isFinite(Number(d.max_concurrency)) && <> · 并发 {d.max_concurrency}</>}
          <br />网关只记 token 数与模型名,不记任何对话内容。
        </span>
      } />
    </>
  )
}


function AboutTab() {
  return (
    <Card title="关于 Hunter Community">
      <p style={{ fontSize: 14, color: 'var(--text)', lineHeight: 1.7, margin: '0 0 12px' }}>
        Hunter Community Edition · 开源自部署金融 AI 平台 · Apache 2.0
      </p>
      <ul style={{ paddingLeft: 20, fontSize: 13, color: 'var(--text-muted)', lineHeight: 1.9 }}>
        <li>源码：<a href="https://github.com/agentpit-io/hunter-community" target="_blank" rel="noopener noreferrer" style={{ color: 'var(--blue)' }}>github.com/agentpit-io/hunter-community <ExternalLink size={11} style={{ display: 'inline', verticalAlign: -1 }} /></a></li>
        <li>商业 SaaS 版：<a href="https://hunter.agentpit.io" target="_blank" rel="noopener noreferrer" style={{ color: 'var(--blue)' }}>hunter.agentpit.io <ExternalLink size={11} style={{ display: 'inline', verticalAlign: -1 }} /></a></li>
        <li>版本：v0.1.0-alpha (P4 provider layer)</li>
      </ul>
    </Card>
  )
}

// ── UI atoms ─────────────────────────────────────────────────────

function TopBar({ me }: { me: Me | null }) {
  return (
    <header style={{ borderBottom: '1px solid var(--border)', padding: '12px 24px', display: 'flex', alignItems: 'center', gap: 12 }}>
      <Link href="/" style={{ display: 'inline-flex', alignItems: 'center', gap: 8, color: 'var(--text)', textDecoration: 'none' }}>
        <Activity size={18} style={{ color: 'var(--blue)' }} />
        <span style={{ fontWeight: 600 }}>猎鹿人 · Hunter</span>
      </Link>
      <span style={{ flex: 1 }} />
      <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>{me?.email}</span>
    </header>
  )
}

function TabButton({ active, onClick, icon, label }: { active: boolean; onClick: () => void; icon: React.ReactNode; label: string }) {
  return (
    <button onClick={onClick}
      style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '9px 12px', background: active ? 'var(--bg-panel)' : 'transparent', border: 'none', borderRadius: 8, color: active ? 'var(--text)' : 'var(--text-muted)', fontSize: 14, fontWeight: active ? 600 : 500, cursor: 'pointer', textAlign: 'left', fontFamily: 'inherit' }}>
      {icon}<span>{label}</span>
    </button>
  )
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 12, padding: 24 }}>
      <h2 style={{ fontSize: 16, fontWeight: 700, color: 'var(--text)', margin: '0 0 16px' }}>{title}</h2>
      {children}
    </div>
  )
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  // 标签列 90px 装不下 `LLM_DEFAULT_MODEL` 这类环境变量名,取值会叠在上面
  // (2026-09-19 截图里看出来的)。加宽 + 不许压缩 + 留出间距。
  return (
    <div style={{ display: 'flex', padding: '8px 0', borderBottom: '1px solid var(--border)' }}>
      <div style={{
        width: 148, flexShrink: 0, paddingRight: 10, fontSize: 13,
        color: 'var(--text-muted)', wordBreak: 'break-all',
      }}>{label}</div>
      <div style={{ flex: 1, minWidth: 0, fontSize: 14, color: 'var(--text)' }}>{value}</div>
    </div>
  )
}

function ProviderBlock({ title, stored, url, onUrl, keyVal, onKey, onTest, testing, extra }: {
  title: string
  stored: string | null
  url: string
  onUrl: (v: string) => void
  keyVal: string
  onKey: (v: string) => void
  onTest: () => void
  testing: boolean
  extra?: React.ReactNode
}) {
  return (
    <div style={{ padding: '14px 0', borderTop: '1px solid var(--border)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <strong style={{ fontSize: 14 }}>{title}</strong>
        {stored && <span style={{ fontSize: 11, color: 'var(--text-muted)', padding: '2px 6px', background: 'var(--bg-panel)', borderRadius: 4 }}>已配置 · {stored}</span>}
      </div>
      <input type="text" placeholder="URL (https://...)" value={url} onChange={e => onUrl(e.target.value)} style={inputStyle()} />
      <div style={{ height: 8 }} />
      <input type="password" placeholder={stored ? '留空保持不变 · 填入覆盖' : 'API Key'} value={keyVal} onChange={e => onKey(e.target.value)} style={inputStyle()} />
      {extra}
      <div style={{ marginTop: 8, display: 'flex', gap: 8 }}>
        <button onClick={onTest} disabled={testing}
          style={{ padding: '6px 12px', background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 6, fontSize: 12, color: 'var(--text)', cursor: testing ? 'not-allowed' : 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          {testing ? <Loader2 size={12} style={{ animation: 'spin 1s linear infinite' }} /> : null}
          测试连接
        </button>
      </div>
    </div>
  )
}

function inputStyle(): React.CSSProperties {
  return {
    width: '100%', padding: '9px 12px',
    background: 'var(--bg-panel)', border: '1px solid var(--border)',
    borderRadius: 6, color: 'var(--text)', fontSize: 13,
    outline: 'none', boxSizing: 'border-box',
    fontFamily: 'inherit',
  }
}

function Centered({ children }: { children: React.ReactNode }) {
  return <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg)' }}>{children}</div>
}
