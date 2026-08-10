'use client'
import { useDeviceUI } from '../../useDeviceUI'
import { LoadingSkeleton } from '../../LoadingSkeleton'
import DesktopReportPage from '../../ui-desktop/ReportPage'
import MobileReportPage from '../../ui-mobile/ReportPage'

export default function ReportEntry() {
  const { mounted, ui } = useDeviceUI()
  if (!mounted) return <LoadingSkeleton />
  return ui === 'mobile' ? <MobileReportPage /> : <DesktopReportPage />
}
