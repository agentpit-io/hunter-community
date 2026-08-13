'use client'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useEffect, useState } from 'react'
import { LayoutGrid, Activity, Bell, Plus, X, Loader2, Shield, History, LogOut, ChevronDown, ClipboardList, Globe, Zap, TrendingUp, Settings, Briefcase, Gift, Sparkles, Users } from 'lucide-react'
import { isSingleUser } from '../lib/localSession'

type Stock = { code: string; name: string; market: string; exchange: string; asset_type?: string }

// 资产类型 → 默认 market + 子市场选项
type AssetType = 'stock' | 'etf' | 'fund'
const ASSET_OPTIONS: { value: AssetType; label: string }[] = [
  { value: 'stock', label: '股票' },
  { value: 'etf',   label: 'ETF' },
  { value: 'fund',  label: '场外基金' },
]
const STOCK_MARKETS = ['A', 'HK', 'US']

function autoExchange(code: string, market: string, assetType: AssetType): string {
  if (assetType === 'fund') return 'OF'                                 // 场外公募
  if (assetType === 'etf')  return code.startsWith('5') ? 'SH' : 'SZ'   // ETF: 5xx=上交所，1xx/15xx=深交所
  if (market === 'HK') return 'HK'
  if (market === 'US') return 'US'
  return code.startsWith('6') ? 'SH' : 'SZ'
}

function normalizeMarket(assetType: AssetType, market: string): string {
  if (assetType === 'fund') return 'FUND'
  if (assetType === 'etf')  return 'A'      // ETF 当 A 股板块归类
  return market
}

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
  const [form, setForm] = useState<{
    code: string; name: string; market: string; exchange: string; asset_type: AssetType
  }>({ code: '', name: '', market: 'A', exchange: 'SZ', asset_type: 'stock' })
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

  const handleAssetTypeChange = (asset_type: AssetType) => {
    setForm(f => {
      const market = normalizeMarket(asset_type, f.market === 'FUND' ? 'A' : f.market)
      return {
        ...f,
        asset_type,
        market,
        exchange: autoExchange(f.code, market, asset_type),
      }
    })
  }

  const handleMarketChange = (market: string) => {
    setForm(f => ({ ...f, market, exchange: autoExchange(f.code, market, f.asset_type) }))
  }

  const handleAdd = async () => {
    if (!form.code.trim() || !form.name.trim()) { setErr('代码和名称不能为空'); return }
    setSaving(true); setErr('')
    try {
      const market   = normalizeMarket(form.asset_type, form.market)
      const exchange = autoExchange(form.code, market, form.asset_type)
      const r = await fetch('/api/watchlist', {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify({ ...form, market, exchange }),
      })
      if (!r.ok) { const d = await r.json(); throw new Error(d.detail || '添加失败') }
      setShowAdd(false)
      setForm({ code: '', name: '', market: 'A', exchange: 'SZ', asset_type: 'stock' })
      load()
    } catch (e: any) { setErr(e.message) }
    finally { setSaving(false) }
  }

  const NavItem = ({ href, icon, label, badge }: { href: string; icon: React.ReactNode; label: string; badge?: string }) => (
    <Link href={href}
      className="flex items-center justify-between px-3 py-2.5 rounded-lg text-sm font-medium transition-colors mb-0.5"
      style={{ background: isActive(href) ? 'rgba(37,99,235,0.1)' : 'transparent', color: isActive(href) ? 'var(--blue)' : 'var(--text)' }}>
      <div className="flex items-center gap-2.5">
        <span className="w-4 h-4 shrink-0">{icon}</span>
        {label}
      </div>
      {badge && (
        <span className="text-xs px-1.5 py-0.5 rounded font-medium"
          style={{ background: 'rgba(37,99,235,0.1)', color: 'var(--blue)' }}>
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
                                        { bg: 'rgba(37,99,235,0.1)',   fg: 'var(--blue)' }
            return (
              <Link key={s.code} href={`/stock/${s.code}`}
                className="flex items-center justify-between px-3 py-2 rounded-lg text-sm transition-colors mb-0.5 group"
                style={{ background: active ? 'rgba(37,99,235,0.1)' : 'transparent', color: active ? 'var(--blue)' : 'var(--text)' }}
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
                style={{ background: 'rgba(37,99,235,0.15)', color: 'var(--blue)' }}>
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

      {/* 添加股票弹窗 */}
      {showAdd && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
          onClick={e => { if (e.target === e.currentTarget) setShowAdd(false) }}>
          <div className="rounded-xl p-5 w-80 shadow-xl" style={{ background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="font-semibold text-sm" style={{ color: 'var(--text)' }}>添加自选股</h2>
              <button onClick={() => setShowAdd(false)} style={{ color: 'var(--text-muted)' }}><X className="w-4 h-4" /></button>
            </div>

            <div className="space-y-2.5">
              {/* 资产类型 */}
              <div className="flex gap-2">
                {ASSET_OPTIONS.map(opt => (
                  <button key={opt.value} onClick={() => handleAssetTypeChange(opt.value)}
                    className="flex-1 py-1.5 rounded-lg text-xs font-medium border transition-colors"
                    style={{ background: form.asset_type === opt.value ? 'var(--blue)' : 'transparent', color: form.asset_type === opt.value ? '#fff' : 'var(--text)', borderColor: form.asset_type === opt.value ? 'var(--blue)' : 'var(--border)' }}>
                    {opt.label}
                  </button>
                ))}
              </div>

              {/* 股票才需要选市场（A/HK/US）；ETF 和场外基金不选 */}
              {form.asset_type === 'stock' && (
                <>
                  <div className="flex gap-2">
                    {STOCK_MARKETS.map(m => (
                      <button key={m} onClick={() => handleMarketChange(m)}
                        className="flex-1 py-1.5 rounded-lg text-xs font-medium border transition-colors"
                        style={{ background: form.market === m ? 'var(--blue)' : 'transparent', color: form.market === m ? '#fff' : 'var(--text)', borderColor: form.market === m ? 'var(--blue)' : 'var(--border)' }}>
                        {m === 'A' ? 'A股' : m === 'HK' ? '港股' : '美股'}
                      </button>
                    ))}
                  </div>
                  {(form.market === 'US' || form.market === 'HK') && (
                    <p className="text-xs text-center" style={{ color: '#d97706' }}>
                      当前仅支持A股，{form.market === 'HK' ? '港股' : '美股'}系统正在上线中，敬请期待
                    </p>
                  )}
                </>
              )}

              <input value={form.code} onChange={e => setForm(f => ({ ...f, code: e.target.value }))}
                onBlur={() => setForm(f => ({ ...f, exchange: autoExchange(f.code, f.market, f.asset_type) }))}
                placeholder={
                  form.asset_type === 'etf'  ? 'ETF 代码，如 510300' :
                  form.asset_type === 'fund' ? '基金代码，如 005827' :
                  form.market === 'US' ? '股票代码，如 AAPL' :
                  form.market === 'HK' ? '股票代码，如 02333' : '股票代码，如 000001'
                }
                className="w-full px-3 py-2 rounded-lg text-sm border"
                style={{ background: 'var(--bg)', color: 'var(--text)', borderColor: 'var(--border)' }} />

              <input value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                placeholder={
                  form.asset_type === 'etf'  ? 'ETF 名称，如 沪深300ETF' :
                  form.asset_type === 'fund' ? '基金名称，如 易方达蓝筹精选' :
                  '股票名称，如 平安银行'
                }
                className="w-full px-3 py-2 rounded-lg text-sm border"
                style={{ background: 'var(--bg)', color: 'var(--text)', borderColor: 'var(--border)' }} />

              {err && <p className="text-xs" style={{ color: '#ef4444' }}>{err}</p>}

              <button onClick={handleAdd}
                disabled={saving || (form.asset_type === 'stock' && (form.market === 'US' || form.market === 'HK'))}
                className="w-full py-2 rounded-lg text-sm font-medium flex items-center justify-center gap-1.5"
                style={{ background: 'var(--blue)', color: '#fff', opacity: (saving || (form.asset_type === 'stock' && (form.market === 'US' || form.market === 'HK'))) ? 0.35 : 1, cursor: (form.asset_type === 'stock' && (form.market === 'US' || form.market === 'HK')) ? 'not-allowed' : 'pointer' }}>
                {saving && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                {saving ? '添加中...' : '确认添加'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
