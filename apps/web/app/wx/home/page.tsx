import { Suspense } from 'react'
import WxHome from './WxHome'

export default function WxHomePage() {
  return (
    <Suspense fallback={
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#f5f5f5' }}>
        <span style={{ color: '#aaa', fontSize: 15 }}>加载中...</span>
      </div>
    }>
      <WxHome />
    </Suspense>
  )
}
