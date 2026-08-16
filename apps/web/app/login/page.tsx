'use client'
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { Activity, Mail, Lock, Loader2, AlertCircle } from 'lucide-react'
import { ensureLocalSession } from '../lib/localSession'

const API = process.env.NEXT_PUBLIC_API_URL || ''

/**
 * Hunter Community · local login page.
 *
 * 单用户模式(开源版默认)下这一页**不会被看到**:挂载时先静默换一个 token 然后
 * 直接跳回去。之所以还留着它,是因为应用里有若干处"没 token 就跳 /login"的检查,
 * 它们撞过来时这里负责把人送回原页 —— 比逐个改那些检查稳。
 *
 * 多用户模式(HUNTER_SINGLE_USER=0)下才是真的登录页:
 *  - needs_setup=true(全新实例还没有管理员)→ 转 /register,第一个账号自动管理员
 *  - 否则渲染邮箱 + 密码表单
 */
export default function LoginPage() {
  const router = useRouter()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [checking, setChecking] = useState(true)

  useEffect(() => {
    const back = () => {
      const ret = new URLSearchParams(window.location.search).get('return_to')
      router.replace(ret && ret.startsWith('/') ? ret : '/')
    }
    // 单用户模式:换到 token 就原路返回,登录表单一眼都不用看
    ensureLocalSession().then((token) => {
      if (token) { back(); return }
      // 多用户模式 · SetupWizard:全新实例先去建管理员
      fetch(`${API}/api/auth/status`).then(r => r.json()).then((s) => {
        if (s?.needs_setup) router.replace('/register?setup=1')
        else setChecking(false)
      }).catch(() => setChecking(false))
    })
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
        const ret = new URLSearchParams(window.location.search).get('return_to')
        router.replace(ret && ret.startsWith('/') ? ret : '/')
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
        input:focus { border-color: var(--blue) !important; box-shadow: 0 0 0 2px rgba(176,106,50,0.15); }
        input::placeholder { color: var(--text-muted); }
      `}</style>
    </div>
  )
}
