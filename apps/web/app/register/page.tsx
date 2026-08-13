'use client'
import { Suspense, useEffect, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import Link from 'next/link'
import { Activity, Mail, Lock, User, Ticket, Loader2, AlertCircle, Sparkles } from 'lucide-react'
import { ensureLocalSession } from '../lib/localSession'

const API = process.env.NEXT_PUBLIC_API_URL || ''

interface AuthStatus {
  admin_exists?: boolean | null
  needs_setup?: boolean | null
  registration_mode?: 'open' | 'invite' | 'closed'
  user_count?: number
}

// Next 15 SSG requires useSearchParams inside a Suspense boundary.
export default function RegisterPage() {
  return (
    <Suspense fallback={<CardShell title="加载中…"><div /></CardShell>}>
      <RegisterInner />
    </Suspense>
  )
}

function RegisterInner() {
  const router = useRouter()
  const search = useSearchParams()
  const isSetup = search.get('setup') === '1'

  const [status, setStatus] = useState<AuthStatus | null>(null)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [invite, setInvite] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    // 单用户模式(开源版默认)根本不需要注册 —— 换到 token 就直接进主页
    ensureLocalSession().then((token) => {
      if (token) { router.replace('/'); return }
      fetch(`${API}/api/auth/status`).then(r => r.json()).then(setStatus).catch(() => setStatus({}))
    })
  }, [router])

  const mode = status?.registration_mode ?? 'open'
  const isFirst = status?.needs_setup === true
  const requiresInvite = mode === 'invite' && !isFirst
  const closed = mode === 'closed' && !isFirst

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!email || !password) { setError('请填写邮箱和密码'); return }
    if (password.length < 8) { setError('密码至少 8 位'); return }
    if (password !== confirm) { setError('两次密码不一致'); return }
    if (requiresInvite && !invite.trim()) { setError('本实例要求邀请码'); return }

    setLoading(true); setError('')
    try {
      const r = await fetch(`${API}/api/auth/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: email.trim().toLowerCase(),
          password,
          display_name: displayName.trim() || null,
          invite_code: invite.trim() || null,
        }),
      })
      const d = await r.json()
      if (r.ok && (d.access_token || d.token)) {
        localStorage.setItem('hunter_token', d.access_token || d.token)
        if (d.refresh_token) localStorage.setItem('hunter_refresh', d.refresh_token)
        router.replace('/')
      } else {
        setError(d.detail || d.error || '注册失败')
      }
    } catch {
      setError('网络错误，请稍后重试')
    } finally {
      setLoading(false)
    }
  }

  if (closed) {
    return (
      <CardShell title="注册已关闭">
        <p style={{ fontSize: 14, color: 'var(--text-muted)', lineHeight: 1.6 }}>
          本实例已关闭注册。请联系管理员获得邀请或直接创建账号。
        </p>
        <div style={{ marginTop: 20, textAlign: 'center', fontSize: 13, color: 'var(--text-muted)' }}>
          已有账号？{' '}
          <Link href="/login" style={{ color: 'var(--blue)', textDecoration: 'none' }}>去登录 →</Link>
        </div>
      </CardShell>
    )
  }

  return (
    <CardShell title={isSetup || isFirst ? '首次部署 · 创建管理员账号' : '注册本地账号'}>
      {(isSetup || isFirst) && (
        <div style={{
          display: 'flex', gap: 8, alignItems: 'flex-start',
          padding: '10px 12px', marginBottom: 20,
          background: 'rgba(37,99,235,0.08)',
          border: '1px solid rgba(37,99,235,0.25)',
          borderRadius: 8, fontSize: 12, color: 'var(--text)',
        }}>
          <Sparkles style={{ width: 15, height: 15, color: 'var(--blue)', flexShrink: 0, marginTop: 1 }} />
          <span>
            这是这个 Hunter 实例的**第一个账号**，将自动获得管理员权限。
            请用真实邮箱与强密码（8 位以上）。
          </span>
        </div>
      )}

      <form onSubmit={handleRegister}>
        <Field icon={<Mail size={15} />}>
          <input
            type="email" value={email}
            onChange={e => { setEmail(e.target.value); setError('') }}
            placeholder="邮箱地址" autoComplete="email"
            style={inputStyle(!!error)}
          />
        </Field>

        <Field icon={<User size={15} />}>
          <input
            type="text" value={displayName}
            onChange={e => setDisplayName(e.target.value)}
            placeholder="显示名（可选）" autoComplete="nickname" maxLength={80}
            style={inputStyle(false)}
          />
        </Field>

        <Field icon={<Lock size={15} />}>
          <input
            type="password" value={password}
            onChange={e => { setPassword(e.target.value); setError('') }}
            placeholder="密码（至少 8 位）" autoComplete="new-password"
            style={inputStyle(!!error)}
          />
        </Field>

        <Field icon={<Lock size={15} />}>
          <input
            type="password" value={confirm}
            onChange={e => { setConfirm(e.target.value); setError('') }}
            placeholder="再次输入密码" autoComplete="new-password"
            style={inputStyle(!!error)}
          />
        </Field>

        {requiresInvite && (
          <Field icon={<Ticket size={15} />}>
            <input
              type="text" value={invite}
              onChange={e => { setInvite(e.target.value); setError('') }}
              placeholder="邀请码" autoComplete="off"
              style={inputStyle(!!error)}
            />
          </Field>
        )}

        {error && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 14 }}>
            <AlertCircle style={{ width: 14, height: 14, color: 'var(--red)', flexShrink: 0 }} />
            <span style={{ fontSize: 13, color: 'var(--red)' }}>{error}</span>
          </div>
        )}

        <button type="submit" disabled={loading}
          style={{ width: '100%', padding: '11px', background: loading ? 'var(--bg-panel)' : 'var(--blue)', color: loading ? 'var(--text-muted)' : '#fff', border: 'none', borderRadius: 8, fontSize: 15, fontWeight: 600, cursor: loading ? 'not-allowed' : 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
          {loading ? <><Loader2 style={{ width: 16, height: 16, animation: 'spin 1s linear infinite' }} />注册中…</> : (isSetup || isFirst ? '创建管理员账号' : '注册')}
        </button>
      </form>

      {!isSetup && !isFirst && (
        <div style={{ marginTop: 20, textAlign: 'center', fontSize: 13, color: 'var(--text-muted)' }}>
          已有账号？{' '}
          <Link href="/login" style={{ color: 'var(--blue)', textDecoration: 'none' }}>去登录 →</Link>
        </div>
      )}
    </CardShell>
  )
}

// ─── shared shell + input helpers ────────────────────────────────

function CardShell({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{
      minHeight: '100vh',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'var(--bg)',
      fontFamily: '-apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif',
    }}>
      <div style={{
        width: '100%', maxWidth: 420,
        background: 'var(--bg-card)',
        border: '1px solid var(--border)',
        borderRadius: 12, padding: '32px 28px',
        boxShadow: '0 8px 32px rgba(0,0,0,0.2)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 24 }}>
          <Activity style={{ width: 24, height: 24, color: 'var(--blue)' }} />
          <span style={{ fontSize: 18, fontWeight: 700, color: 'var(--text)' }}>猎鹿人 · Hunter</span>
        </div>
        <h1 style={{ fontSize: 20, fontWeight: 700, color: 'var(--text)', margin: '0 0 20px' }}>{title}</h1>
        {children}
      </div>
      <style>{`
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
        input:focus { border-color: var(--blue) !important; box-shadow: 0 0 0 2px rgba(37,99,235,0.15); }
        input::placeholder { color: var(--text-muted); }
      `}</style>
    </div>
  )
}

function Field({ icon, children }: { icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ position: 'relative' }}>
        <span style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)', display: 'flex', alignItems: 'center' }}>{icon}</span>
        {children}
      </div>
    </div>
  )
}

function inputStyle(hasError: boolean): React.CSSProperties {
  return {
    width: '100%',
    padding: '10px 12px 10px 36px',
    background: 'var(--bg-panel)',
    border: `1px solid ${hasError ? 'var(--red)' : 'var(--border)'}`,
    borderRadius: 8,
    color: 'var(--text)',
    fontSize: 14,
    outline: 'none',
    boxSizing: 'border-box',
  }
}
