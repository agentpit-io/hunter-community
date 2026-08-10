import { Suspense } from 'react'
import WxAxMap from './WxAxMap'

export default function WxAxMapPage() {
  return (
    <Suspense fallback={
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#F7F3EC' }}>
        <span style={{ color: '#7A6F63', fontSize: 15 }}>加载中...</span>
      </div>
    }>
      <WxAxMap />
    </Suspense>
  )
}
