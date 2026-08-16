'use client'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useEffect, useState } from 'react'
import { LayoutGrid, Activity, Bell, Plus, X, Loader2, Shield, History, LogOut, ChevronDown, ClipboardList, Globe, Zap, TrendingUp, Settings, Briefcase, Gift, Sparkles, Users } from 'lucide-react'
import { isSingleUser } from '../lib/localSession'

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
  const [showAdd, setShowAdd] = useState(false)
  // 搜索式添加:一个输入框 + 下拉候选。老版本要求分别填 code + name,
  // 用户经常只知道其一(比如只记得"紫金矿业")就卡住。现在打后端 suggest
  // 拿完整字段自动落库。见 apps/api/app/routers/watchlist.py:145 search_stocks
  const [query, setQuery] = useState('')
  const [candidates, setCandidates] = useState<Array<{
    code: string; name: string; market: string; exchange: string; asset_type: string; type_name?: string
  }>>([])
  const [searching, setSearching] = useState(false)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')
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

  const handleLogout = () => {
    localStorage.removeItem('hunter_token')
    router.push('/login')
  }

  const isActive = (href: string) => path === href || (href !== '/' && path.startsWith(href + '/'))

  // 打开弹窗时清空 · 关闭时也清 · 确保下次进来是干净状态
  useEffect(() => {
    if (!showAdd) { setQuery(''); setCandidates([]); setErr(''); return }
  }, [showAdd])

  // 搜索:debounce 300ms · 空 query 不调 API · 复用 GET /api/watchlist/search
  useEffect(() => {
    if (!showAdd) return
    const q = query.trim()
    if (!q) { setCandidates([]); setSearching(false); return }
    setSearching(true)
    const t = setTimeout(async () => {
      try {
        const r = await fetch(`/api/watchlist/search?q=${encodeURIComponent(q)}&limit=8`, { headers: authHeaders() })
        if (!r.ok) throw new Error('搜索失败')
        const d = await r.json()
        setCandidates(Array.isArray(d.items) ? d.items : [])
      } catch { setCandidates([]) }
      finally { setSearching(false) }
    }, 300)
    return () => clearTimeout(t)
  }, [query, showAdd])

  // 点击候选:自动落库 · 后端字段一次到位,不需要用户再挑
  const handlePickCandidate = async (it: typeof candidates[number]) => {
    if (saving) return
    setSaving(true); setErr('')
    try {
      const r = await fetch('/api/watchlist', {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({
          code: it.code, name: it.name,
          market: it.market, exchange: it.exchange,
          asset_type: it.asset_type,
        }),
      })
      if (!r.ok) { const d = await r.json(); throw new Error(d.detail || '添加失败') }
      setShowAdd(false)
      load()
    } catch (e: any) { setErr(e.message) }
    finally { setSaving(false) }
  }

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

          {/* 添加自选股按钮 */}
          <button onClick={() => { setShowAdd(true); setErr('') }}
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

      {/* 添加股票弹窗 · 搜索式 · 输入名字或代码,点候选自动落库 */}
      {showAdd && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
          onClick={e => { if (e.target === e.currentTarget) setShowAdd(false) }}>
          <div className="rounded-xl p-5 w-96 shadow-xl" style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
            <div className="flex items-center justify-between mb-3">
              <h2 className="font-semibold text-sm" style={{ color: 'var(--text)' }}>添加自选股</h2>
              <button onClick={() => setShowAdd(false)} style={{ color: 'var(--text-muted)' }}><X className="w-4 h-4" /></button>
            </div>

            <input
              autoFocus
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="股票名称或代码,如 平安银行 / 000001 / AAPL"
              className="w-full px-3 py-2 rounded-lg text-sm border"
              style={{ background: 'var(--bg)', color: 'var(--text)', borderColor: 'var(--border)' }} />

            <div className="mt-2 max-h-72 overflow-y-auto">
              {searching && (
                <div className="flex items-center gap-2 py-3 text-xs" style={{ color: 'var(--text-muted)' }}>
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />搜索中...
                </div>
              )}
              {!searching && query.trim() && candidates.length === 0 && (
                <p className="py-3 text-xs" style={{ color: 'var(--text-muted)' }}>没找到匹配 "{query.trim()}",试试完整名字或代码</p>
              )}
              {!query.trim() && (
                <p className="py-3 text-xs" style={{ color: 'var(--text-muted)' }}>输入名字或代码,自动搜索并添加</p>
              )}
              {candidates.map(it => {
                const tag = it.asset_type === 'etf' ? { label: 'ETF', bg: 'rgba(20,184,166,0.12)', fg: '#0d9488' }
                          : it.asset_type === 'fund' ? { label: '基金', bg: 'rgba(168,85,247,0.12)', fg: '#a855f7' }
                          : it.market === 'HK' ? { label: 'HK', bg: 'rgba(217,119,6,0.12)', fg: 'var(--yellow)' }
                          : it.market === 'US' ? { label: 'US', bg: 'rgba(22,163,74,0.12)', fg: '#16a34a' }
                          : { label: 'A股', bg: 'rgba(176,106,50,0.12)', fg: 'var(--blue)' }
                return (
                  <button
                    key={`${it.market}-${it.code}`}
                    onClick={() => handlePickCandidate(it)}
                    disabled={saving}
                    className="w-full flex items-center justify-between px-3 py-2 rounded-lg text-left transition-colors mb-1"
                    style={{ background: 'transparent', border: '1px solid var(--border)', opacity: saving ? 0.4 : 1, cursor: saving ? 'wait' : 'pointer' }}
                    onMouseEnter={e => (e.currentTarget.style.background = 'rgba(176,106,50,0.06)')}
                    onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                  >
                    <div>
                      <div className="text-sm font-medium" style={{ color: 'var(--text)' }}>{it.name}</div>
                      <div className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>{it.code}{it.type_name ? ` · ${it.type_name}` : ''}</div>
                    </div>
                    <span className="text-xs px-2 py-1 rounded font-medium" style={{ background: tag.bg, color: tag.fg }}>
                      {tag.label}
                    </span>
                  </button>
                )
              })}
            </div>

            {err && <p className="text-xs mt-2" style={{ color: '#ef4444' }}>{err}</p>}
          </div>
        </div>
      )}
    </>
  )
}
