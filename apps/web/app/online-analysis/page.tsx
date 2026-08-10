'use client'
import { useDeviceUI } from './useDeviceUI'
import { LoadingSkeleton } from './LoadingSkeleton'
import DesktopMainPage from './ui-desktop/MainPage'
import MobileMainPage from './ui-mobile/MainPage'

export default function OnlineAnalysisEntry() {
  const { mounted, ui } = useDeviceUI()
  if (!mounted) return <LoadingSkeleton />
  return ui === 'mobile' ? <MobileMainPage /> : <DesktopMainPage />
}
