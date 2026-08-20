'use client'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useEffect, useState } from 'react'
import { LayoutGrid, Activity, Bell, Plus, X, Loader2, Shield, History, LogOut, ChevronDown, ClipboardList, Globe, Zap, TrendingUp, Settings, Briefcase, Gift, Sparkles, Users } from 'lucide-react'
import { isSingleUser } from '../lib/localSession'
import AddStockModal, { emitWatchlistChanged } from './AddStockModal'

type Stock = { code: string; name: string; market: string; exchange: string; asset_type?: string }

function getToken(): string {
  if (typeof window === 'undefined') return ''
  return localStorage.getItem('hunter_token') || ''
}

function authHeaders(): Record<string, string> {
  const token = getToken()
  return token ? { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' }
}

export default function Sidebar() {
  const path = usePathname()
  const router = useRouter()
  const [stocks, setStocks] = useState<Stock[]>([])
  // 添加自选股弹窗抽到共享组件 AddStockModal · Sidebar 和 /watchlist 页面都用同一份
  const [showAdd, setShowAdd] = useState(false)
  const [hoveredCode, setHoveredCode] = useState('')
  const [removingCode, setRemovingCode] = useState('')
  const [userEmail, setUserEmail] = useState('')
  const [isAdmin, setIsAdmin] = useState(false)
  const [showUserMenu, setShowUserMenu] = useState(false)

  const handleRemove = async (code: string, e: React.MouseEvent) => {
    e.preventDefault(); e.stopPropagation()
    setRemovingCode(code)
    try {
      await fetch(`/api/watchlist/${code}`, { method: 'DELETE', headers: authHeaders() })
      setStocks(prev => prev.filter(s => s.code !== code))
      // 广播 · /watchlist 管理页面同步刷新(它拉的是 /api/watchlist/manage · 独立 state)
      emitWatchlistChanged()
    } catch {}
    finally { setRemovingCode('') }
  }

  const load = () => {
    const token = getToken()
    if (!token && typeof window !== 'undefined') {
      router.push('/login')
      return
    }
    fetch('/api/watchlist', { headers: authHeaders() })
      .then(r => {
        if (r.status === 401) { router.push('/login'); return null }
        return r.json()
      })
      .then(d => d && setStocks(Array.isArray(d) ? d : []))
      .catch(() => {})
  }

  useEffect(() => {
    load()
    const token = getToken()
    if (token) {
      try {
        const payload = JSON.parse(atob(token.split('.')[1]))
        setUserEmail(payload.email || '')
        // 展位后台入口：ADMIN 角色或白名单邮箱可见（后端另有同规则鉴权）
        setIsAdmin(payload.role === 'ADMIN' || payload.email === 'hangeaiagent@gmail.com')
      } catch {}
    }
  }, [])

  // 跨组件事件:任何地方(自己 modal / /watchlist 页面 modal / hard-delete) 改了
  // 自选股都会 dispatch 'watchlist:changed',这里 reload 侧栏列表。
  useEffect(() => {
    const h = () => load()
    window.addEventListener('watchlist:changed', h)
    return () => window.removeEventListener('watchlist:changed', h)
  }, [])

  const handleLogout = () => {
    localStorage.removeItem('hunter_token')
    router.push('/login')
  }

  const isActive = (href: string) => path === href || (href !== '/' && path.startsWith(href + '/'))

  const NavItem = ({ href, icon, label, badge }: { href: string; icon: React.ReactNode; label: string; badge?: string }) => (
    <Link href={href}
      className="flex items-center justify-between px-3 py-2.5 rounded-lg text-sm font-medium transition-colors mb-0.5"
      style={{ background: isActive(href) ? 'rgba(176,106,50,0.1)' : 'transparent', color: isActive(href) ? 'var(--blue)' : 'var(--text)' }}>
      <div className="flex items-center gap-2.5">
        <span className="w-4 h-4 shrink-0">{icon}</span>
        {label}
      </div>
      {badge && (
        <span className="text-xs px-1.5 py-0.5 rounded font-medium"
          style={{ background: 'rgba(176,106,50,0.1)', color: 'var(--blue)' }}>
          {badge}
        </span>
      )}
    </Link>
  )

  return (
    <>
      <aside className="w-52 shrink-0 flex flex-col h-screen border-r fixed left-0 top-0 overflow-y-auto"
        style={{ borderColor: 'var(--border)', background: 'var(--bg-card)' }}>
        <div className="px-4 py-4 border-b" style={{ borderColor: 'var(--border)' }}>
          <div className="flex items-center gap-2">
            <Activity className="w-5 h-5" style={{ color: 'var(--blue)' }} />
            <span className="font-bold text-base" style={{ color: 'var(--text)' }}>猎鹿人 · Hunter</span>
          </div>
          <div className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>agentpit.io</div>
        </div>

        <nav className="flex-1 p-2">
          <NavItem href="/" icon={<LayoutGrid className="w-4 h-4" />} label="总览" />

          <div className="px-3 py-1.5 text-xs font-semibold uppercase tracking-wider mt-2 mb-1"
            style={{ color: 'var(--text-muted)' }}>自选股</div>

          {stocks.map(s => {
            const active = path === `/stock/${s.code}`
            // 标签优先级：基金 > ETF > 市场代码
            const tag =
              s.asset_type === 'fund' ? '基金' :
              s.asset_type === 'etf'  ? 'ETF'  :
              s.market
            const tagColor =
              s.asset_type === 'fund' ? { bg: 'rgba(168,85,247,0.12)', fg: '#a855f7' } : // 紫
              s.asset_type === 'etf'  ? { bg: 'rgba(20,184,166,0.12)', fg: '#0d9488' } : // 青绿
              s.market === 'HK'       ? { bg: 'rgba(217,119,6,0.1)',   fg: 'var(--yellow)' } :
              s.market === 'US'       ? { bg: 'rgba(22,163,74,0.1)',   fg: '#16a34a' } :
                                        { bg: 'rgba(176,106,50,0.1)',   fg: 'var(--blue)' }
            return (
              <Link key={s.code} href={`/stock/${s.code}`}
                className="flex items-center justify-between px-3 py-2 rounded-lg text-sm transition-colors mb-0.5 group"
                style={{ background: active ? 'rgba(176,106,50,0.1)' : 'transparent', color: active ? 'var(--blue)' : 'var(--text)' }}
                onMouseEnter={() => setHoveredCode(s.code)}
                onMouseLeave={() => setHoveredCode('')}>
                <div>
                  <div className="font-medium">{s.name}</div>
                  <div className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>{s.code}</div>
                </div>
                {hoveredCode === s.code ? (
                  <button
                    onClick={e => handleRemove(s.code, e)}
                    className="w-5 h-5 flex items-center justify-center rounded transition-colors flex-shrink-0"
                    style={{ color: removingCode === s.code ? '#475569' : '#ef4444', background: 'rgba(239,68,68,0.1)' }}
                    title="从自选股移除">
                    {removingCode === s.code
                      ? <Loader2 className="w-3 h-3 animate-spin" />
                      : <X className="w-3 h-3" />}
                  </button>
                ) : (
                  <span className="text-xs px-1.5 py-0.5 rounded font-medium flex-shrink-0"
                    style={{ background: tagColor.bg, color: tagColor.fg }}>
                    {tag}
                  </span>
                )}
              </Link>
            )
          })}

          {/* 添加自选股按钮 · 弹窗见 AddStockModal 共享组件 */}
          <button onClick={() => setShowAdd(true)}
            className="flex items-center gap-1.5 w-full px-3 py-2 rounded-lg text-sm transition-colors mt-0.5"
            style={{ color: 'var(--text-muted)' }}>
            <Plus className="w-3.5 h-3.5" />
            <span>添加自选股</span>
          </button>


          <div className="px-3 py-1.5 text-xs font-semibold uppercase tracking-wider mt-3 mb-1"
            style={{ color: 'var(--text-muted)' }}>分析工具</div>
          <NavItem href="/chat" icon={<Sparkles className="w-4 h-4" />} label="AI 对话" badge="新" />
          <NavItem href="/portfolio" icon={<Briefcase className="w-4 h-4" />} label="持仓报告" />
          <NavItem href="/online-analysis" icon={<Shield className="w-4 h-4" />} label="在线分析" badge="抗投毒" />
          <NavItem href="/online-analysis/history" icon={<History className="w-4 h-4" />} label="分析历史" />
          <NavItem href="/kpred" icon={<TrendingUp className="w-4 h-4" />} label="K线预测" badge="AI" />

          <div className="px-3 py-1.5 text-xs font-semibold uppercase tracking-wider mt-3 mb-1"
            style={{ color: 'var(--text-muted)' }}>市场信号</div>
          <NavItem href="/signals" icon={<Globe className="w-4 h-4" />} label="信号看板" badge="新" />
          <NavItem href="/event-analysis" icon={<Zap className="w-4 h-4" />} label="事件分析" />

          {/* P2: /push and /push-manage removed with SaaS strip · P3 will add SMTP/Slack channel UI */}

          {isAdmin && (
            <>
              <div className="px-3 py-1.5 text-xs font-semibold uppercase tracking-wider mt-3 mb-1"
                style={{ color: 'var(--text-muted)' }}>模型回测</div>
              <NavItem href="/backtest" icon={<Activity className="w-4 h-4" />} label="回测看板" badge="新" />

              <div className="px-3 py-1.5 text-xs font-semibold uppercase tracking-wider mt-3 mb-1"
                style={{ color: 'var(--text-muted)' }}>用户洞察</div>
              <NavItem href="/user-insight" icon={<Users className="w-4 h-4" />} label="用户画像" badge="新" />
              {/* P2: /booth-admin (AdventureX SaaS) removed */}
            </>
          )}
        </nav>

        {/* 底部用户信息 */}
        {userEmail && (
          <div className="border-t p-2 relative" style={{ borderColor: 'var(--border)' }}>
            <button
              onClick={() => setShowUserMenu(v => !v)}
              className="flex items-center gap-2 w-full px-3 py-2 rounded-lg text-sm transition-colors"
              style={{ color: 'var(--text)' }}>
              <div className="w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0"
                style={{ background: 'rgba(176,106,50,0.15)', color: 'var(--blue)' }}>
                {userEmail[0].toUpperCase()}
              </div>
              <span className="flex-1 text-left truncate text-xs" style={{ color: 'var(--text-muted)' }}>{userEmail}</span>
              <ChevronDown className="w-3.5 h-3.5 flex-shrink-0" style={{ color: 'var(--text-muted)' }} />
            </button>
            {showUserMenu && (
              <>
                <div className="fixed inset-0 z-40" onClick={() => setShowUserMenu(false)} />
                <div className="absolute left-2 right-2 bottom-full mb-1 rounded-xl shadow-lg z-50 overflow-hidden"
                  style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
                  <div className="px-4 py-3 border-b" style={{ borderColor: 'var(--border)' }}>
                    <div className="text-xs font-medium truncate" style={{ color: 'var(--text)' }}>{userEmail}</div>
                    <div className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>已登录</div>
                  </div>
                  <Link
                    href="/preference"
                    onClick={() => setShowUserMenu(false)}
                    className="flex items-center gap-2.5 w-full px-4 py-2.5 text-sm transition-colors"
                    style={{ color: 'var(--text)' }}>
                    <Settings className="w-4 h-4" />
                    投资偏好
                  </Link>
                  {/* 单用户模式下清了 token 会被立刻换回来,这个入口没有意义,藏掉 */}
                  {!isSingleUser() && (
                    <button
                      onClick={handleLogout}
                      className="flex items-center gap-2.5 w-full px-4 py-2.5 text-sm transition-colors"
                      style={{ color: 'var(--red, #ef4444)' }}>
                      <LogOut className="w-4 h-4" />
                      退出登录
                    </button>
                  )}
                </div>
              </>
            )}
          </div>
        )}
      </aside>

      {showAdd && <AddStockModal onClose={() => setShowAdd(false)} />}
    </>
  )
}
