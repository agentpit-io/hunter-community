'use client'
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { Activity, User as UserIcon, Zap, Info, Loader2, ExternalLink, Save, Check, X } from 'lucide-react'

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

type TabId = 'account' | 'saas' | 'about'

export default function SettingsPage() {
  const router = useRouter()
  const [tab, setTab] = useState<TabId>('account')
  const [me, setMe] = useState<Me | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
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
          <TabButton active={tab === 'saas'} onClick={() => setTab('saas')} icon={<Zap size={15} />} label="SaaS 加速" />
          <TabButton active={tab === 'about'} onClick={() => setTab('about')} icon={<Info size={15} />} label="关于" />
        </nav>
        <div>
          {tab === 'account' && <AccountTab me={me} />}
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
  return (
    <div style={{ display: 'flex', padding: '8px 0', borderBottom: '1px solid var(--border)' }}>
      <div style={{ width: 90, fontSize: 13, color: 'var(--text-muted)' }}>{label}</div>
      <div style={{ flex: 1, fontSize: 14, color: 'var(--text)' }}>{value}</div>
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
