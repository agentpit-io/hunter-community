'use client'
import { Suspense, useEffect, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { Loader2, CheckCircle, XCircle } from 'lucide-react'

const API = process.env.NEXT_PUBLIC_API_URL || ''

function SsoInner() {
  const router = useRouter()
  const params = useSearchParams()
  const [status, setStatus] = useState<'loading' | 'ok' | 'error'>('loading')
  const [msg, setMsg] = useState('')

  useEffect(() => {
    const apt = params.get('apt')
    if (!apt) {
      setStatus('error')
      setMsg('缺少授权参数，请重新从 AgentPit 跳转')
      return
    }

    fetch(`${API}/api/auth/agentpit-sso?apt=${encodeURIComponent(apt)}`)
      .then(r => r.json())
      .then(d => {
        if (d.ok && d.token) {
          localStorage.setItem('hunter_token', d.token)
          setStatus('ok')
          setTimeout(() => router.replace('/'), 800)
        } else {
          setStatus('error')
          setMsg(d.error || '授权失败，请重新登录')
        }
      })
      .catch(() => {
        setStatus('error')
        setMsg('网络错误，请稍后重试')
      })
  }, [params, router])

  return (
    <div style={{
      minHeight: '100vh',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'var(--bg)',
      fontFamily: '-apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif',
    }}>
      <div style={{ textAlign: 'center', padding: '32px' }}>
        {status === 'loading' && (
          <>
            <Loader2 size={40} style={{ color: 'var(--accent)', margin: '0 auto 16px', animation: 'spin 1s linear infinite' }} />
            <p style={{ color: 'var(--text-muted)', fontSize: 15 }}>正在验证 AgentPit 账号…</p>
          </>
        )}
        {status === 'ok' && (
          <>
            <CheckCircle size={40} style={{ color: '#10B981', margin: '0 auto 16px' }} />
            <p style={{ color: 'var(--text)', fontSize: 15 }}>登录成功，正在跳转…</p>
          </>
        )}
        {status === 'error' && (
          <>
            <XCircle size={40} style={{ color: '#EF4444', margin: '0 auto 16px' }} />
            <p style={{ color: 'var(--text)', fontSize: 15, marginBottom: 16 }}>{msg}</p>
            <a href="/login" style={{
              display: 'inline-block', padding: '10px 24px',
              background: 'var(--accent)', color: '#fff',
              borderRadius: 8, fontSize: 14, textDecoration: 'none',
            }}>
              手动登录
            </a>
          </>
        )}
      </div>
    </div>
  )
}

export default function SsoPage() {
  return (
    <Suspense fallback={
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Loader2 size={32} style={{ color: 'var(--accent)', animation: 'spin 1s linear infinite' }} />
      </div>
    }>
      <SsoInner />
    </Suspense>
  )
}
