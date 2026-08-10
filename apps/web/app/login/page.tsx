'use client'
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { Activity, Mail, Lock, Loader2, AlertCircle } from 'lucide-react'

const API = process.env.NEXT_PUBLIC_API_URL || ''

/**
 * Hunter Community · local login page.
 *
 * On mount checks /api/auth/status:
 *  - If needs_setup=true (fresh install, no admin yet) forwards to /register
 *    where the first user gets auto-admin role.
 *  - Otherwise renders the plain email + password form.
 */
export default function LoginPage() {
  const router = useRouter()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [checking, setChecking] = useState(true)

  useEffect(() => {
    // SetupWizard · route the very first visitor to /register
    fetch(`${API}/api/auth/status`).then(r => r.json()).then((s) => {
      if (s?.needs_setup) router.replace('/register?setup=1')
      else setChecking(false)
    }).catch(() => setChecking(false))
  }, [router])

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!email || !password) { setError('请填写邮箱和密码'); return }
    setLoading(true); setError('')
    try {
      const r = await fetch(`${API}/api/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email.trim().toLowerCase(), password }),
      })
      const d = await r.json()
      if (r.ok && (d.access_token || d.token)) {
        localStorage.setItem('hunter_token', d.access_token || d.token)
        if (d.refresh_token) localStorage.setItem('hunter_refresh', d.refresh_token)
        router.replace('/')
      } else {
        setError(d.detail || d.error || '邮箱或密码错误')
      }
    } catch {
      setError('网络错误，请稍后重试')
    } finally {
      setLoading(false)
    }
  }

  if (checking) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg)' }}>
        <Loader2 style={{ width: 20, height: 20, color: 'var(--text-muted)', animation: 'spin 1s linear infinite' }} />
        <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
      </div>
    )
  }

  return (
    <div style={{
      minHeight: '100vh',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'var(--bg)',
      fontFamily: '-apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif',
    }}>
      <div style={{
        width: '100%', maxWidth: 380,
        background: 'var(--bg-card)',
        border: '1px solid var(--border)',
        borderRadius: 12,
        padding: '32px 28px',
        boxShadow: '0 8px 32px rgba(0,0,0,0.2)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 28 }}>
          <Activity style={{ width: 24, height: 24, color: 'var(--blue)' }} />
          <span style={{ fontSize: 18, fontWeight: 700, color: 'var(--text)' }}>猎鹿人 · Hunter</span>
        </div>

        <h1 style={{ fontSize: 22, fontWeight: 700, color: 'var(--text)', margin: '0 0 6px' }}>登录</h1>
        <p style={{ fontSize: 13, color: 'var(--text-muted)', margin: '0 0 24px' }}>
          用你在本实例的邮箱与密码登录
        </p>

        <form onSubmit={handleLogin}>
          <div style={{ marginBottom: 14 }}>
            <div style={{ position: 'relative' }}>
              <Mail style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', width: 15, height: 15, color: 'var(--text-muted)' }} />
              <input
                type="email" value={email}
                onChange={e => { setEmail(e.target.value); setError('') }}
                placeholder="邮箱地址" autoComplete="email"
                style={{ width: '100%', padding: '10px 12px 10px 36px', background: 'var(--bg-panel)', border: `1px solid ${error ? 'var(--red)' : 'var(--border)'}`, borderRadius: 8, color: 'var(--text)', fontSize: 14, outline: 'none', boxSizing: 'border-box' }}
              />
            </div>
          </div>

          <div style={{ marginBottom: 20 }}>
            <div style={{ position: 'relative' }}>
              <Lock style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', width: 15, height: 15, color: 'var(--text-muted)' }} />
              <input
                type="password" value={password}
                onChange={e => { setPassword(e.target.value); setError('') }}
                placeholder="密码" autoComplete="current-password"
                style={{ width: '100%', padding: '10px 12px 10px 36px', background: 'var(--bg-panel)', border: `1px solid ${error ? 'var(--red)' : 'var(--border)'}`, borderRadius: 8, color: 'var(--text)', fontSize: 14, outline: 'none', boxSizing: 'border-box' }}
              />
            </div>
          </div>

          {error && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 14 }}>
              <AlertCircle style={{ width: 14, height: 14, color: 'var(--red)', flexShrink: 0 }} />
              <span style={{ fontSize: 13, color: 'var(--red)' }}>{error}</span>
            </div>
          )}

          <button type="submit" disabled={loading}
            style={{ width: '100%', padding: '11px', background: loading ? 'var(--bg-panel)' : 'var(--blue)', color: loading ? 'var(--text-muted)' : '#fff', border: 'none', borderRadius: 8, fontSize: 15, fontWeight: 600, cursor: loading ? 'not-allowed' : 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
            {loading ? <><Loader2 style={{ width: 16, height: 16, animation: 'spin 1s linear infinite' }} />登录中…</> : '登录'}
          </button>
        </form>

        <div style={{ marginTop: 20, textAlign: 'center', fontSize: 13, color: 'var(--text-muted)' }}>
          还没有账号？{' '}
          <Link href="/register" style={{ color: 'var(--blue)', textDecoration: 'none' }}>
            注册本地账号 →
          </Link>
        </div>
      </div>
      <style>{`
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
        input:focus { border-color: var(--blue) !important; box-shadow: 0 0 0 2px rgba(37,99,235,0.15); }
        input::placeholder { color: var(--text-muted); }
      `}</style>
    </div>
  )
}
