'use client'
// TopNav · Claude 风顶部导航 · 主入口 chat + 5 一级菜单 + 更多下拉 + 头像
// 所有菜单点击新窗口打开(target=_blank) · 保 chat 上下文不被打断
// 对应 doc/codex/主页宣传/02-Chat作为主入口-Claude风改造方案.md §6.2

import { useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { Target, Bell, LogOut, Settings, Gift } from 'lucide-react'
import { HUNTER, HUNTER_LOGO } from '../lib/hunter-theme'


const NAV_HEIGHT = 48
const BG = 'rgba(255,255,255,.78)'
const HOVER = HUNTER.PANEL_2


interface NavProps {
  /** 当前页面 · 用于高亮 · 如 'chat' 'watchlist' */
  active?: string
}


export default function TopNav({ active }: NavProps) {
  const router = useRouter()
  const [userOpen, setUserOpen] = useState(false)
  const [userEmail, setUserEmail] = useState('')
  const [userInitial, setUserInitial] = useState('U')
  const [isAdmin, setIsAdmin] = useState(false)
  const userRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    try {
      const token = localStorage.getItem('hunter_token') || ''
      if (token) {
        const payload = JSON.parse(atob(token.split('.')[1]))
        const email = payload.email || ''
        setUserEmail(email)
        setUserInitial(email ? email[0].toUpperCase() : 'U')
        setIsAdmin(payload.role === 'ADMIN' || email === 'hangeaiagent@gmail.com')
      }
    } catch {
      /* token 损坏时保持默认 */
    }
  }, [])

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (userOpen && !userRef.current?.contains(e.target as Node)) setUserOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [userOpen])

  const handleLogout = () => {
    localStorage.removeItem('hunter_token')
    localStorage.removeItem('hunter_email')
    router.push('/login')
  }

  return (
    <div
      style={{
        height: NAV_HEIGHT,
        padding: '0 20px',
        background: BG,
        backdropFilter: 'blur(12px)',
        WebkitBackdropFilter: 'blur(12px)',
        borderBottom: `1px solid ${HUNTER.LINE}`,
        display: 'flex',
        alignItems: 'center',
        gap: 4,
        position: 'sticky',
        top: 0,
        zIndex: 40,
        fontFamily: HUNTER.SANS,
        flexShrink: 0,
      }}
    >
      {/* 品牌 · 点击回主页(/) */}
      <Link href="/" style={{
        display: 'flex', alignItems: 'center', gap: 8,
        marginRight: 20, color: HUNTER.INK, textDecoration: 'none',
      }}>
        {/* 顶栏原来用的是 emoji 🦌,而侧栏/对话气泡/首页用的都是
            HUNTER_LOGO 那张圆形鹿头图 —— 同一个页面上下两个不同的品牌标,
            用户第一眼会以为是两个产品。统一用 HUNTER_LOGO。
            尺寸取 20:顶栏高度有限,再大会把这一行撑开。 */}
        <img
          src={HUNTER_LOGO}
          alt="猎鹿人"
          width={20}
          height={20}
          style={{ width: 20, height: 20, borderRadius: '50%', display: 'block' }}
        />
        <span style={{
          fontFamily: HUNTER.SERIF, fontSize: 15, fontWeight: 700,
          letterSpacing: '.02em',
        }}>
          猎鹿人
        </span>
        <span style={{
          fontFamily: HUNTER.SERIF, fontSize: 12, color: HUNTER.COPPER3,
        }}>· Hunter</span>
      </Link>

      {/* 一级菜单 · **只剩策略中心一个**(2026-08-30 导航重构)
       *
       * 原来是「自选 / 策略中心 / MCP 组件 / 更多∨」四个,砍到一个。
       * 砍的理由不是"太多了"这种感觉,是每一项都有更好的去处:
       *
       *   自选     → 侧栏新标签「自选股」· 卡片式 · 每张卡带
       *              预测评估/交易成本/概率校准三个入口
       *   MCP 组件 → 侧栏「对话」下的能力区(数据源/工具箱/SKILL)
       *   更多∨    → 里面的页面依赖我们平台的分析逻辑,开源版跑不全,
       *              留着入口只会让用户点了失望
       *
       * ⚠️ **只删入口,不删路由。** /watchlist /mcp-config /kpred
       * /online-analysis 等页面全部保留可直接访问 —— 评委测试说明里的
       * URL 照样能开,而且哪天要恢复入口只是加回几行。
       *
       * 方案见 doc/开源hunter-community/04开源比赛/
       *        2026-08-30_导航重构方案-对话与自选股双栏.md
       */}
      <NavLink href="/strategies/index.html" icon={<Target size={14} />} label="策略中心" active={active === 'strategies'} />

      {/* 弹簧 */}
      <div style={{ flex: 1 }} />

      {/* 头像 · dropdown */}
      <div ref={userRef} style={{ position: 'relative' }}>
        <button
          onClick={() => setUserOpen(v => !v)}
          title={userEmail || '未登录'}
          style={{
            width: 34, height: 34, borderRadius: '50%',
            background: HUNTER.THEME, color: '#fff',
            border: 'none', cursor: 'pointer',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 13, fontWeight: 700, fontFamily: 'inherit',
          }}
        >
          {userInitial}
        </button>
        {userOpen && (
          <div style={{ ...dropdownStyle, right: 0, left: 'auto', minWidth: 220 }}>
            {userEmail && (
              <div style={{
                padding: '10px 12px 8px', fontSize: 11.5, color: HUNTER.INK_F,
                borderBottom: `1px solid ${HUNTER.LINE}`, wordBreak: 'break-all',
              }}>
                {userEmail}
                {isAdmin && <span style={{
                  marginLeft: 6, padding: '1px 6px', borderRadius: 4,
                  border: `1px solid ${HUNTER.LINE}`, color: HUNTER.THEME, fontSize: 10,
                }}>ADMIN</span>}
              </div>
            )}
            <DropdownLink href="/preference" icon={<Settings size={13} />} label="偏好设置" />
            <DropdownLink href="/portfolio" icon={<Target size={13} />} label="我的持仓" />
            <div style={{ height: 1, background: HUNTER.LINE, margin: '4px 6px' }} />
            <button
              onClick={handleLogout}
              style={{
                display: 'flex', alignItems: 'center', gap: 8,
                width: '100%', padding: '9px 12px',
                background: 'transparent', border: 'none', borderRadius: 6,
                color: HUNTER.UP, fontSize: 13, textAlign: 'left', cursor: 'pointer',
                fontFamily: 'inherit',
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = '#fbeaea')}
              onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
            >
              <LogOut size={13} /> 退出登录
            </button>
          </div>
        )}
      </div>
    </div>
  )
}


function NavLink({ href, icon, label, active }: { href: string; icon: React.ReactNode; label: string; active?: boolean }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      title={label}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 5,
        height: 32, padding: '0 12px', marginRight: 4,
        background: active ? HUNTER.BRAND_PALE : 'transparent',
        border: 'none', borderRadius: 8,
        color: active ? HUNTER.COPPER3 : HUNTER.INK_S,
        fontSize: 13, fontWeight: active ? 600 : 400,
        textDecoration: 'none', transition: 'background .1s',
      }}
      onMouseEnter={(e) => { if (!active) e.currentTarget.style.background = HOVER }}
      onMouseLeave={(e) => { if (!active) e.currentTarget.style.background = 'transparent' }}
    >
      {icon} {label}
    </a>
  )
}


function DropdownLink({ href, icon, label, badge }: { href: string; icon: React.ReactNode; label: string; badge?: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '9px 12px', margin: '0 4px', borderRadius: 6,
        color: HUNTER.INK_S, fontSize: 13, textDecoration: 'none',
        transition: 'background .1s',
      }}
      onMouseEnter={(e) => (e.currentTarget.style.background = HOVER)}
      onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
    >
      <span style={{ color: HUNTER.INK_F }}>{icon}</span>
      <span style={{ flex: 1 }}>{label}</span>
      {badge && (
        <span style={{
          padding: '1px 6px', borderRadius: 999,
          background: HUNTER.BRAND_PALE, color: HUNTER.COPPER3,
          fontSize: 10, fontWeight: 600,
        }}>{badge}</span>
      )}
    </a>
  )
}



const dropdownStyle: React.CSSProperties = {
  position: 'absolute',
  top: 'calc(100% + 6px)',
  left: 0,
  minWidth: 200,
  background: '#fff',
  border: `1px solid ${HUNTER.LINE}`,
  borderRadius: 10,
  boxShadow: '0 12px 30px rgba(30,20,10,.12)',
  padding: '4px 0',
  zIndex: 100,
}
