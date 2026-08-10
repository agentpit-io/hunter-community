'use client'
import { useEffect, useState } from 'react'
import { MOBILE_BREAKPOINT } from '../lib/hunter-theme'

/** 设备 UI 决策：
 *  - SSR 期间 & hydration 首帧：mounted=false → 上层渲染 LoadingSkeleton
 *  - 挂载后：宽度 <=640 → 'mobile'；>640 → 'desktop'
 *  - URL 支持 ?ui=mobile / ?ui=desktop 强制覆盖（用于 QA 和用户偏好）
 */
export function useDeviceUI(): { mounted: boolean; ui: 'mobile' | 'desktop' } {
  const [mounted, setMounted] = useState(false)
  const [ui, setUI] = useState<'mobile' | 'desktop'>('desktop')

  useEffect(() => {
    const decide = () => {
      let forced: 'mobile' | 'desktop' | null = null
      try {
        const usp = new URLSearchParams(window.location.search)
        const q = usp.get('ui')
        if (q === 'mobile' || q === 'desktop') forced = q
      } catch { /* ignore */ }
      if (forced) { setUI(forced); return }
      setUI(window.innerWidth <= MOBILE_BREAKPOINT ? 'mobile' : 'desktop')
    }
    decide()
    setMounted(true)
    window.addEventListener('resize', decide)
    return () => window.removeEventListener('resize', decide)
  }, [])

  return { mounted, ui }
}
