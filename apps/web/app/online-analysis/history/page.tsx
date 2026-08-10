'use client'
import { useDeviceUI } from '../useDeviceUI'
import { LoadingSkeleton } from '../LoadingSkeleton'
import DesktopHistoryPage from '../ui-desktop/HistoryPage'
import MobileHistoryPage from '../ui-mobile/HistoryPage'

export default function HistoryEntry() {
  const { mounted, ui } = useDeviceUI()
  if (!mounted) return <LoadingSkeleton />
  return ui === 'mobile' ? <MobileHistoryPage /> : <DesktopHistoryPage />
}
