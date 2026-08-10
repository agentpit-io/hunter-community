'use client'
import { useDeviceUI } from '../../useDeviceUI'
import { LoadingSkeleton } from '../../LoadingSkeleton'
import DesktopTaskPage from '../../ui-desktop/TaskPage'
import MobileTaskPage from '../../ui-mobile/TaskPage'

export default function TaskEntry() {
  const { mounted, ui } = useDeviceUI()
  if (!mounted) return <LoadingSkeleton />
  return ui === 'mobile' ? <MobileTaskPage /> : <DesktopTaskPage />
}
