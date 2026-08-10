'use client'
import { useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'

export default function WxLoginForm() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const state = searchParams.get('state') || ''
  const error = searchParams.get('error') || ''

  const [email,    setEmail]    = useState('')
  const [password, setPassword] = useState('')
  const [msg,      setMsg]      = useState(error ? '微信授权失败，请重新进入' : '')
  const [isOk,     setIsOk]     = useState(false)
  const [loading,  setLoading]  = useState(false)

  if (!state) {
    // 没有 state 说明不是从 OAuth 回调进来的，自动发起授权
    if (typeof window !== 'undefined') window.location.replace('/api/wx/oauth')
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#f5f5f5' }}>
        <div style={{ textAlign: 'center', color: '#888', fontSize: 14 }}>正在跳转授权...</div>
      </div>
    )
  }

  async function doSubmit() {
    if (!email.includes('@')) { setMsg('请填写正确的邮箱地址'); setIsOk(false); return }
    if (password.length < 6)  { setMsg('密码至少 6 位'); setIsOk(false); return }
    setLoading(true); setMsg('')
    try {
      const res  = await fetch('/api/wx/login/submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ state, email, password }),
      })
      const data = await res.json()
      if (res.ok && data.token) {
        localStorage.setItem('hunter_token', data.token)
        setMsg('绑定成功，正在跳转...'); setIsOk(true)
        setTimeout(() => router.replace('/wx/home'), 1500)
      } else {
        setMsg(data.error || '邮箱或密码错误'); setIsOk(false)
      }
    } catch {
      setMsg('网络错误，请稍后重试'); setIsOk(false)
    } finally {
      setLoading(false)
    }
  }

  const inp: React.CSSProperties = {
    width: '100%', height: 46, padding: '0 14px', boxSizing: 'border-box',
    border: '1px solid #ddd', borderRadius: 10, fontSize: 15,
    background: '#fff', outline: 'none', color: '#222',
  }

  return (
    <div style={{
      minHeight: '100vh', background: '#f5f5f5', display: 'flex',
      alignItems: 'center', justifyContent: 'center',
      fontFamily: '-apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif',
    }}>
      <div style={{ width: '100%', maxWidth: 420, padding: '0 20px' }}>
        <div style={{ textAlign: 'center', marginBottom: 28 }}>
          <div style={{ fontSize: 22, fontWeight: 700, color: '#1a1a1a' }}>猎鹿人 · Hunter</div>
          <div style={{ fontSize: 14, color: '#888', marginTop: 6 }}>微信账号绑定</div>
        </div>

        <div style={{
          background: '#fff', borderRadius: 16,
          padding: '28px 24px', boxShadow: '0 2px 20px rgba(0,0,0,.08)',
        }}>
          <div style={{ marginBottom: 16 }}>
            <label style={{ fontSize: 13, color: '#555', display: 'block', marginBottom: 6 }}>AgentPit 邮箱</label>
            <input type="email" value={email} onChange={e => setEmail(e.target.value)}
              placeholder="请输入 AgentPit 账号邮箱" style={inp} />
          </div>

          <div style={{ marginBottom: 20 }}>
            <label style={{ fontSize: 13, color: '#555', display: 'block', marginBottom: 6 }}>密码</label>
            <input type="password" value={password} onChange={e => setPassword(e.target.value)}
              placeholder="请输入 AgentPit 账号密码" style={inp} />
          </div>

          {msg && (
            <p style={{
              color: isOk ? '#07c160' : '#e74c3c',
              fontSize: 13, margin: '0 0 12px', textAlign: 'center',
            }}>{msg}</p>
          )}

          <button onClick={doSubmit} disabled={loading} style={{
            width: '100%', height: 50,
            background: loading ? '#ccc' : '#07c160',
            color: '#fff', border: 'none', borderRadius: 12,
            fontSize: 16, fontWeight: 600, cursor: loading ? 'not-allowed' : 'pointer',
          }}>
            {loading ? '绑定中...' : '绑定微信账号'}
          </button>

          <p style={{ marginTop: 16, fontSize: 12, color: '#aaa', textAlign: 'center', lineHeight: 1.6 }}>
            绑定后，股票行情、持仓预警等消息将推送到您的微信
          </p>
        </div>
      </div>
    </div>
  )
}
