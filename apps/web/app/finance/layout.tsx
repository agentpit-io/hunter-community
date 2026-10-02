'use client'
/**
 * 「智能交易」板块外壳（一期 M1）· `apps/web/app/finance/`
 *
 * 与现仓其它板块同构：左侧沿用全局 `<Sidebar />`（新增了「智能交易」入口），
 * 右侧是板块自己的页头 + 二级导航 + 内容区。二级导航用 Next 的 Link，
 * **刷新保持当前页**（App Router 的路径即状态，不存组件里）。
 *
 * 落点依据：`08-开源产品集成部署方案.md` §1.2（前端 = `apps/web/app/finance/` 板块根
 * + 子路由 overview / auto-trade / account / report / growth / help / setup）。
 */
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { Activity } from 'lucide-react'
import Sidebar from '../components/Sidebar'

const TABS = [
  { href: '/finance/overview', label: '总览' },
  { href: '/finance/auto-trade', label: '自动交易' },
  { href: '/finance/account', label: '我的账户' },
  { href: '/finance/report', label: '每日报告' },
  { href: '/finance/growth', label: '成长与复盘' },
  { href: '/finance/help', label: '安全与帮助' },
  { href: '/finance/setup', label: '设置向导' },
]

export default function FinanceLayout({ children }: { children: React.ReactNode }) {
  const path = usePathname() || ''
  const isOn = (href: string) => path === href || path.startsWith(href + '/')

  return (
    <div className="flex min-h-screen" style={{ background: 'var(--bg)' }}>
      <Sidebar />
      <div className="flex-1 min-w-0 ml-52 flex flex-col">
        {/* 页头 */}
        <div className="px-4 sm:px-6 pt-5 pb-0" style={{ borderBottom: '1px solid var(--border)' }}>
          <div className="flex items-center gap-2.5 flex-wrap">
            <Activity className="w-5 h-5" style={{ color: 'var(--blue)' }} />
            <h1 className="text-xl font-bold" style={{ color: 'var(--text)' }}>智能交易</h1>
            <span className="inline-flex items-center gap-1.5 text-xs font-semibold px-2 py-0.5 rounded-full"
              style={{ background: 'rgba(63,107,64,.10)', color: 'var(--green)' }}>
              <span className="w-1.5 h-1.5 rounded-full" style={{ background: 'currentColor' }} />
              模拟盘 · 不接实盘
            </span>
          </div>
          <nav className="flex gap-1 flex-wrap mt-3 -mb-px">
            {TABS.map(t => {
              const on = isOn(t.href)
              return (
                <Link key={t.href} href={t.href}
                  className="px-3 py-2 text-sm font-semibold whitespace-nowrap rounded-t-lg transition-colors"
                  style={{
                    color: on ? 'var(--blue)' : 'var(--text-muted)',
                    background: on ? 'var(--bg-card)' : 'transparent',
                    border: on ? '1px solid var(--border)' : '1px solid transparent',
                    borderBottomColor: on ? 'var(--bg-card)' : 'transparent',
                  }}>
                  {t.label}
                </Link>
              )
            })}
          </nav>
        </div>

        {/* 内容 */}
        <main className="flex-1 min-w-0 overflow-x-hidden p-4 sm:p-6">{children}</main>
      </div>
    </div>
  )
}
