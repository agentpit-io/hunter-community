import { Suspense } from 'react'
import WxLoginForm from './WxLoginForm'

export default function WxLoginPage() {
  return (
    <Suspense fallback={
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#f5f5f5' }}>
        <span style={{ color: '#888' }}>加载中...</span>
      </div>
    }>
      <WxLoginForm />
    </Suspense>
  )
}
