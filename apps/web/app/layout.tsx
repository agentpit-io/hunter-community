import type { Metadata, Viewport } from 'next'
import './globals.css'
export const metadata: Metadata = {
  title: '猎鹿人 · Hunter | agentpit.io',
  description: 'agentpit.io 猎鹿人 · Hunter · 实时行情 · 资金流向 · 持仓预警',
  icons: {
    icon: [
      { url: '/icon.png', type: 'image/png' },
      { url: '/logo.png', type: 'image/png' },
    ],
    apple: '/icon.png',
    shortcut: '/icon.png',
  },
}
export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  viewportFit: 'cover',
}
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen" style={{ background: 'var(--bg)', color: 'var(--text)' }}>
        {children}
      </body>
    </html>
  )
}
