'use client'
// TopNav · Claude 风顶部导航 · 主入口 chat + 5 一级菜单 + 更多下拉 + 头像
// 所有菜单点击新窗口打开(target=_blank) · 保 chat 上下文不被打断
// 对应 doc/codex/主页宣传/02-Chat作为主入口-Claude风改造方案.md §6.2

import { useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import {
  Star, Target, Bell, Plug, ChevronDown, LogOut, Settings,
  LayoutGrid, Activity, TrendingUp, Zap, Radio, History, Newspaper,
  BarChart3, Users, Gift,
} from 'lucide-react'
import { HUNTER } from '../lib/hunter-theme'


const NAV_HEIGHT = 48
const BG = 'rgba(255,255,255,.78)'
const HOVER = HUNTER.PANEL_2


interface NavProps {
  /** 当前页面 · 用于高亮 · 如 'chat' 'watchlist' */
  active?: string
}


export default function TopNav({ active }: NavProps) {
  const router = useRouter()
  const [moreOpen, setMoreOpen] = useState(false)
  const [userOpen, setUserOpen] = useState(false)
  const [userEmail, setUserEmail] = useState('')
  const [userInitial, setUserInitial] = useState('U')
  const [isAdmin, setIsAdmin] = useState(false)
  const moreRef = useRef<HTMLDivElement>(null)
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
      if (moreOpen && !moreRef.current?.contains(e.target as Node)) setMoreOpen(false)
      if (userOpen && !userRef.current?.contains(e.target as Node)) setUserOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [moreOpen, userOpen])

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
        <span style={{ fontSize: 18 }}>🦌</span>
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

      {/* 一级菜单 · 5 项 · 全部 target=_blank */}
      <NavLink href="/watchlist" icon={<Star size={14} />} label="自选" active={active === 'watchlist'} />
      <NavLink href="/strategies/index.html" icon={<Target size={14} />} label="策略中心" active={active === 'strategies'} />
      <NavLink href="/push" icon={<Bell size={14} />} label="推送" active={active === 'push'} />
      <NavLink href="/mcp-config" icon={<Plug size={14} />} label="MCP 组件" active={active === 'mcp'} />

      {/* 更多 · 下拉 · 覆盖剩余功能 */}
      <div ref={moreRef} style={{ position: 'relative' }}>
        <button
          onClick={() => setMoreOpen(v => !v)}
          style={{
            display: 'inline-flex', alignItems: 'center', gap: 5,
            height: 32, padding: '0 12px', marginRight: 4,
            background: moreOpen ? HOVER : 'transparent',
            border: 'none', borderRadius: 8,
            color: HUNTER.INK_S, fontSize: 13, cursor: 'pointer',
            transition: 'background .1s', fontFamily: 'inherit',
          }}
          onMouseEnter={(e) => { if (!moreOpen) e.currentTarget.style.background = HOVER }}
          onMouseLeave={(e) => { if (!moreOpen) e.currentTarget.style.background = 'transparent' }}
        >
          更多 <ChevronDown size={11} style={{ opacity: 0.6 }} />
        </button>
        {moreOpen && (
          <div style={dropdownStyle}>
            <SectionLabel>深度工具</SectionLabel>
            <DropdownLink href="/overview" icon={<LayoutGrid size={13} />} label="综合总览" />
            <DropdownLink href="/online-analysis" icon={<Activity size={13} />} label="在线分析" badge="抗投毒" />
            <DropdownLink href="/kpred" icon={<TrendingUp size={13} />} label="K 线预测" badge="AI" />
            <DropdownLink href="/signals" icon={<Radio size={13} />} label="信号看板" />
            <DropdownLink href="/event-analysis" icon={<Zap size={13} />} label="事件分析" />
            <DropdownLink href="/online-analysis/history" icon={<History size={13} />} label="分析历史" />
            <DropdownLink href="/news" icon={<Newspaper size={13} />} label="资讯" />

            <SectionLabel>持仓与配置</SectionLabel>
            <DropdownLink href="/portfolio" icon={<Target size={13} />} label="持仓报告" />
            <DropdownLink href="/push-manage" icon={<Settings size={13} />} label="推送管理" />

            {isAdmin && <>
              <SectionLabel>管理后台</SectionLabel>
              <DropdownLink href="/backtest" icon={<BarChart3 size={13} />} label="回测看板" badge="admin" />
              <DropdownLink href="/user-insight" icon={<Users size={13} />} label="用户画像" badge="admin" />
              <DropdownLink href="/booth-admin" icon={<Gift size={13} />} label="展位后台" badge="admin" />
            </>}
          </div>
        )}
      </div>

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


function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      padding: '8px 12px 4px', fontSize: 10.5, fontWeight: 600,
      color: '#a0a098', textTransform: 'uppercase', letterSpacing: '.06em',
    }}>{children}</div>
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
