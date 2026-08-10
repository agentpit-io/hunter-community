import { Suspense } from 'react'
import WxAx from './WxAx'

export default function WxAxPage() {
  return (
    <Suspense fallback={
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#F7F3EC' }}>
        <span style={{ color: '#7A6F63' }}>加载中...</span>
      </div>
    }>
      <WxAx />
    </Suspense>
  )
}
