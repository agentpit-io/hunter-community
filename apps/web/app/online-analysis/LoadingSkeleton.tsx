'use client'
import { HUNTER } from '../lib/hunter-theme'

/** SSR 首帧 / hydration 期间的中性占位，用米宣纸底色兜住品牌色 */
export function LoadingSkeleton() {
  return (
    <div style={{ minHeight: '100vh', background: HUNTER.BG }}>
      <div style={{ height: 68, background: HUNTER.HEADER_BG }} />
      <div style={{
        padding: '40px 20px',
        textAlign: 'center',
        color: HUNTER.INK_F,
        fontSize: 13,
        fontFamily: HUNTER.SANS,
      }}>
        加载中…
      </div>
    </div>
  )
}
